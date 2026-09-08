"""Direct v3.114.0-compatible Langfuse ingestion client (stdlib only).

Design pin (``stage4-langfuse-tracing``): ``POST {LANGFUSE_URL}/api/public/ingestion``
with Basic auth built from ``LANGFUSE_PUBLIC_KEY``/``LANGFUSE_SECRET_KEY``
(environment-only; this client receives the values, the CLI owns the
environment contract), plus a ``GET /api/public/health`` guard that must
answer before any export. Context7 documentation tooling was unavailable in
this session, so no API field beyond the persisted design's pinned contract is
assumed; any response that does not match it fails closed as
``unsupported_response``.

Reliability contract: one atomic batch per :meth:`LangfuseClient.ingest` call;
at most three attempts for network errors, timeouts, HTTP 408/429 and 5xx;
scheduled delays of 250 ms then 500 ms with a valid ``Retry-After`` header
honored up to 2 s; authentication, schema and other 4xx responses are
non-retryable. Classifications carry safe status/reason codes only — never a
response body, credential material, or payload text.
"""

import base64
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .artifacts import canonical_json_bytes

SUPPORTED_LANGFUSE_RELEASE = "3.114.0"
INGESTION_PATH = "/api/public/ingestion"
HEALTH_PATH = "/api/public/health"

CREATED = "created"
UPDATED_OR_REPLAYED = "updated_or_replayed"
UNCHANGED = "unchanged"

ENDPOINT_UNHEALTHY = "endpoint_unhealthy"
INGESTION_FAILED = "ingestion_failed"
INGESTION_UNREACHABLE = "ingestion_unreachable"
UNSUPPORTED_RESPONSE = "unsupported_response"

MAX_ATTEMPTS = 3
BACKOFF_S = (0.25, 0.5)
MAX_RETRY_DELAY_S = 2.0
RETRYABLE_STATUSES = frozenset({408, 429, *range(500, 600)})
_ENVELOPE_KEYS = frozenset({"successes", "failures"})
_EVENT_OUTCOMES = {201: CREATED, 202: UPDATED_OR_REPLAYED}


@dataclass(frozen=True)
class HealthResult:
    """Safe health classification: outcome flag, reason code, HTTP status."""

    healthy: bool
    reason: str | None = None
    status: int | None = None


@dataclass(frozen=True)
class IngestResult:
    """Safe batch classification; never carries bodies, headers, or secrets.

    ``event_outcomes`` aligns with the submitted batch order when accepted and
    is empty otherwise. ``status`` is the bare HTTP status code or None for
    network-level failures.
    """

    accepted: bool
    reason: str | None = None
    status: int | None = None
    event_outcomes: tuple = ()


@dataclass(frozen=True)
class _Answer:
    """One classified exchange outcome; ``body`` exists only with ``status``."""

    status: int | None = None
    body: bytes | None = None
    error: int | None = None


def _classify_envelope(status, body, expected_count):
    """Strict pinned-v3 ingestion envelope; any deviation fails closed.

    ``expected_count`` is the submitted batch size: the response must
    account for exactly that many event outcomes — no fewer (an empty or
    partial accounting invents undocumented success) and no more — before
    any acceptance is reported. Every entry must be a mapping; malformed
    entries classify as ``unsupported_response`` instead of raising.
    """
    try:
        envelope = json.loads(body)
    except ValueError:
        return IngestResult(False, UNSUPPORTED_RESPONSE, status)
    failures = envelope.get("failures", []) if isinstance(envelope, dict) else None
    if (
        not isinstance(envelope, dict)
        or set(envelope) - _ENVELOPE_KEYS
        or not isinstance(envelope.get("successes"), list)
        or not isinstance(failures, list)
    ):
        return IngestResult(False, UNSUPPORTED_RESPONSE, status)
    if failures:
        if not all(isinstance(entry, dict) for entry in failures):
            return IngestResult(False, UNSUPPORTED_RESPONSE, status)
        return IngestResult(False, INGESTION_FAILED, status)
    successes = envelope["successes"]
    if len(successes) != expected_count:
        return IngestResult(False, UNSUPPORTED_RESPONSE, status)
    outcomes = []
    for entry in successes:
        if not isinstance(entry, dict):
            return IngestResult(False, UNSUPPORTED_RESPONSE, status)
        entry_status = entry.get("status")
        if (
            not isinstance(entry.get("id"), str)
            or isinstance(entry_status, bool)
            or not isinstance(entry_status, int)
            or not 200 <= entry_status <= 299
        ):
            return IngestResult(False, UNSUPPORTED_RESPONSE, status)
        outcomes.append(_EVENT_OUTCOMES.get(entry_status, UNCHANGED))
    return IngestResult(True, None, status, tuple(outcomes))


class LangfuseClient:
    """One-shot Basic-auth ``urllib`` client for the pinned ingestion API."""

    def __init__(
        self, base_url, public_key, secret_key, timeout=10.0, sleep=time.sleep
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self.sleep = sleep
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
        self._auth = f"Basic {token}"

    def __repr__(self):
        return f"<LangfuseClient {self._base_url}>"

    def _request(self, method, path, payload):
        headers = {"Authorization": self._auth}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(
            self._base_url + path, data=payload, headers=headers, method=method
        )

    def _attempt(self, request) -> tuple[int, bytes]:
        """One HTTP attempt: ``(status, raw_body)`` or a raised error."""
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return response.status, response.read()

    def _delay(self, attempt, headers):
        """Scheduled backoff for this attempt, or bounded ``Retry-After``.

        Malformed, negative, NaN and infinite ``Retry-After`` values never
        reach sleep: they fall back to the deterministic scheduled backoff.
        Valid values are honored only up to ``MAX_RETRY_DELAY_S``.
        """
        fallback = BACKOFF_S[attempt - 1]
        raw = headers.get("Retry-After") if headers is not None else None
        if raw is None:
            return fallback
        try:
            value = float(raw)
        except ValueError:
            return fallback
        if not math.isfinite(value) or value < 0:
            return fallback
        return min(value, MAX_RETRY_DELAY_S)

    def _send(self, request):
        """Shared bounded-retry loop yielding one terminal ``_Answer``."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                status, body = self._attempt(request)
            except urllib.error.HTTPError as error:
                error.close()
                if error.code in RETRYABLE_STATUSES:
                    if attempt < MAX_ATTEMPTS:
                        self.sleep(self._delay(attempt, error.headers))
                        continue
                yield _Answer(error=error.code)
                return
            except OSError:
                # URLError and socket timeouts both subclass OSError.
                if attempt < MAX_ATTEMPTS:
                    self.sleep(self._delay(attempt, None))
                    continue
                yield _Answer()
                return
            yield _Answer(status=status, body=body)
            return

    def check_health(self):
        """Require a 200 JSON-object answer from the pinned health endpoint."""
        request = self._request("GET", HEALTH_PATH, None)
        for answer in self._send(request):
            if answer.status is None or answer.body is None:
                return HealthResult(False, ENDPOINT_UNHEALTHY, answer.error)
            try:
                parsed = json.loads(answer.body)
            except ValueError:
                return HealthResult(False, ENDPOINT_UNHEALTHY, answer.status)
            if not isinstance(parsed, dict):
                return HealthResult(False, ENDPOINT_UNHEALTHY, answer.status)
            return HealthResult(True)
        return HealthResult(False, ENDPOINT_UNHEALTHY)

    def ingest(self, batch):
        """Submit one atomic item batch; classify the pinned response envelope."""
        if not batch:  # fail closed: no submitted events, no invented success
            return IngestResult(False, UNSUPPORTED_RESPONSE)
        payload = canonical_json_bytes({"batch": batch})
        request = self._request("POST", INGESTION_PATH, payload)
        for answer in self._send(request):
            if answer.status is None or answer.body is None:
                if answer.error is not None:
                    return IngestResult(False, INGESTION_FAILED, answer.error)
                return IngestResult(False, INGESTION_UNREACHABLE)
            return _classify_envelope(answer.status, answer.body, len(batch))
        return IngestResult(False, UNSUPPORTED_RESPONSE)
