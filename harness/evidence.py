"""Fail-closed evidence binding over sealed core artifacts (design §4).

Authority hierarchy: ``verified_score``/``outcome`` come only from the sealed
``receipt.json``; input/output tokens and latency come only from the
digest-bound ``llm/provider-run.json``; condition/attempt/decline/status
fields stay with the harness logs. Plain JSON only — this module never
imports the core and never reads stdout for score/outcome.

Every check fails closed: a missing, malformed, non-canonical, substituted,
or secret-bearing artifact yields an explicit invalid classification with a
reason — metrics are never zero-filled or inferred.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .strategy_context import (
    DIGEST_RE,
    canonical_json_bytes,
    digest_json,
    sha256_digest,
)

RECEIPT_NAME = "receipt.json"
PROVIDER_RUN_NAME = "llm/provider-run.json"
LLM_RECEIPT_NAME = "llm/llm-receipt.json"

# Mirror of the core outcome vocabulary (contracts/outcomes.py), read-only.
OUTCOMES = frozenset(
    {"completed", "duplicate_effect", "safe_abort", "unresolved_outcome"}
)

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")
_RECEIPT_REQUIRED = (
    "schema_version",
    "logical_run_id",
    "execution_id",
    "seed",
    "policy_id",
    "policy_digest",
    "outcome",
    "verified_score",
    "verification",
)
_PROVIDER_RUN_REQUIRED = (
    "schema_version",
    "logical_run_id",
    "execution_id",
    "requests",
)
_LLM_RECEIPT_REQUIRED = (
    "schema_version",
    "logical_run_id",
    "execution_id",
    "provider",
    "model",
    "prompt_digest",
    "tool_schema_digest",
    "provider_evidence_digest",
    "core_receipt_digest",
    "llm_receipt_digest",
)

# Fixed, deterministic secret patterns; any hit fails the artifact closed.
_SECRET_PATTERNS = (
    b"sk-",
    b"api_key",
    b"apikey",
    b"authorization",
    b"bearer",
    b"password",
    b"secret_token",
)


class EvidenceError(ValueError):
    """A sealed artifact is missing, malformed, non-canonical, or invalid."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AttemptEvidence:
    """Classification of one attempt's sealed artifacts.

    ``bound`` means every required artifact existed, every digest and
    identity binding held, and the official metrics below are authoritative.
    ``invalid`` carries the fail-closed reason and null metrics.
    """

    status: str
    reason: str | None
    outcome: str | None = None
    verified_score: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    core_receipt_digest: str | None = None
    llm_receipt_digest: str | None = None
    policy_digest: str | None = None
    logical_run_id: str | None = None
    prompt_digest: str | None = None
    tool_schema_digest: str | None = None


def _nonempty(value):
    return isinstance(value, str) and value != ""


def _is_digest(value):
    return isinstance(value, str) and bool(DIGEST_RE.fullmatch(value))


def _index(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvidenceError(f"non-canonical numeric {label}: {value!r}")
    return value


def scan_secrets(raw, label):
    lowered = raw.lower()
    for pattern in _SECRET_PATTERNS:
        if pattern in lowered:
            raise EvidenceError(f"secret pattern {pattern!r} in {label}")


def _contains_float(value):
    """Mirror of the core writer's integers-only contract (REQ-07): floats
    (finite, NaN, or inf) can never appear in canonical artifacts."""
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_float(v) for v in value)
    return False


def _read_canonical(path, required):
    path = Path(path)
    if not path.exists():
        raise EvidenceError(f"missing artifact: {path.name}")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"invalid UTF-8 bytes: {path.name}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"malformed JSON: {path.name}") from exc
    if _contains_float(value):
        raise EvidenceError(f"non-canonical float value: {path.name}")
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise EvidenceError(f"non-canonical JSON bytes: {path.name}")
    missing = [key for key in required if key not in value]
    if missing:
        raise EvidenceError(f"{path.name} missing fields: {missing}")
    scan_secrets(raw, path.name)
    return value, raw


def load_receipt(run_dir):
    """Public plain-JSON receipt loader; fails closed via :class:`EvidenceError`."""
    return _read_canonical(Path(run_dir) / RECEIPT_NAME, _RECEIPT_REQUIRED)[0]


# Required keys of every provider-run request record (tool_call/error stay
# optional nullable objects, mirroring the core sidecar writer).
_REQUEST_KEYS = (
    "policy_step",
    "provider",
    "model",
    "request_id",
    "usage",
    "latency_ms",
    "raw_visible_response",
)
_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def _validate_receipt(receipt):
    if isinstance(receipt["schema_version"], bool) or receipt["schema_version"] != 1:
        raise EvidenceError("receipt schema_version must be 1")
    logical_run_id = receipt["logical_run_id"]
    if not isinstance(logical_run_id, str) or not _LOGICAL_RUN_ID_RE.fullmatch(
        logical_run_id
    ):
        raise EvidenceError("receipt logical_run_id must be lr_ plus 64 hex")
    for key in ("execution_id", "seed", "policy_id"):
        if not _nonempty(receipt[key]):
            raise EvidenceError(f"receipt {key} must be a non-empty string")
    if not _is_digest(receipt["policy_digest"]):
        raise EvidenceError("receipt policy_digest malformed")
    if not isinstance(receipt["outcome"], str) or receipt["outcome"] not in OUTCOMES:
        raise EvidenceError("outcome must be a string in the core outcome vocabulary")
    _index(receipt["verified_score"], "verified_score")
    verification = receipt["verification"]
    if not isinstance(verification, dict) or verification.get("status") != "valid":
        raise EvidenceError("receipt verification is not valid")
    if "verified_score" not in verification:
        raise EvidenceError("receipt verification missing field: verified_score")
    _index(verification["verified_score"], "verification verified_score")
    if verification["verified_score"] != receipt["verified_score"]:
        raise EvidenceError("verification verified_score disagrees with the receipt")


def _validate_provider_run(provider_run):
    if (
        isinstance(provider_run["schema_version"], bool)
        or provider_run["schema_version"] != 1
    ):
        raise EvidenceError("provider-run schema_version must be 1")
    requests = provider_run["requests"]
    if not isinstance(requests, list) or not requests:
        raise EvidenceError("provider-run requires a non-empty requests list")
    for position, request in enumerate(requests):
        if not isinstance(request, dict):
            raise EvidenceError(f"provider-run request {position} must be an object")
        missing = [key for key in _REQUEST_KEYS if key not in request]
        if missing:
            raise EvidenceError(
                f"provider-run request {position} missing fields: {missing}"
            )
        _index(request["policy_step"], "policy_step")
        for key in ("provider", "model", "request_id"):
            if not _nonempty(request[key]):
                raise EvidenceError(f"provider-run {key} must be a non-empty string")
        usage = request["usage"]
        if not isinstance(usage, dict):
            raise EvidenceError("usage must be an object")
        for key in _USAGE_KEYS:
            if key not in usage:
                raise EvidenceError(f"usage missing field: {key}")
            _index(usage[key], key)
        _index(request["latency_ms"], "latency_ms")
        if not isinstance(request["raw_visible_response"], str):
            raise EvidenceError("raw_visible_response must be a string")
        for key in ("tool_call", "error"):
            if request.get(key) is not None and not isinstance(request[key], dict):
                raise EvidenceError(f"{key} must be null or an object")


def _validate_llm_receipt(llm_receipt):
    if (
        isinstance(llm_receipt["schema_version"], bool)
        or llm_receipt["schema_version"] != 1
    ):
        raise EvidenceError("llm-receipt schema_version must be 1")
    for key in ("logical_run_id", "execution_id", "provider", "model"):
        if not _nonempty(llm_receipt[key]):
            raise EvidenceError(f"llm-receipt {key} must be a non-empty string")
    for key in _LLM_RECEIPT_REQUIRED[5:]:
        if not _is_digest(llm_receipt[key]):
            raise EvidenceError(f"llm-receipt {key} must be a sha256 digest")


def _check_bindings(llm_receipt, provider_run, receipt_raw, expectations):
    if llm_receipt["core_receipt_digest"] != sha256_digest(receipt_raw):
        raise EvidenceError("core_receipt_digest does not match the receipt bytes")
    if llm_receipt["provider_evidence_digest"] != digest_json(provider_run):
        raise EvidenceError("provider_evidence_digest does not match provider-run.json")
    for key in ("prompt_digest", "tool_schema_digest"):
        expected = expectations.get(key)
        if expected is not None and llm_receipt[key] != expected:
            raise EvidenceError(
                f"{key} disagrees with the frozen tournament expectations"
            )
    without = {k: v for k, v in llm_receipt.items() if k != "llm_receipt_digest"}
    if llm_receipt["llm_receipt_digest"] != digest_json(without):
        raise EvidenceError("llm_receipt_digest does not recompute over the receipt")


def _check_identity(receipt, provider_run, llm_receipt, frozen, launch_meta, binding):
    for other, label in (
        (provider_run, "provider-run.json"),
        (llm_receipt, "llm-receipt.json"),
    ):
        if other["logical_run_id"] != receipt["logical_run_id"]:
            raise EvidenceError(f"logical_run_id mismatch across receipt and {label}")
        if other["execution_id"] != receipt["execution_id"]:
            raise EvidenceError(f"execution_id mismatch across receipt and {label}")
    pinned = (frozen["provider"], frozen["model"])
    if (llm_receipt["provider"], llm_receipt["model"]) != pinned:
        raise EvidenceError(
            "provider/model mismatch against the frozen tournament config"
        )
    for request in provider_run["requests"]:
        if (request["provider"], request["model"]) != pinned:
            raise EvidenceError("provider/model mismatch inside provider-run requests")
    if receipt["seed"] != str(launch_meta["seed"]):
        raise EvidenceError("seed disagrees with the launch registration")
    if binding.get("policy_digest") != receipt["policy_digest"]:
        raise EvidenceError("binding policy_digest disagrees with the receipt")
    if binding.get("logical_run_id") != receipt["logical_run_id"]:
        raise EvidenceError("binding logical_run_id disagrees with the receipt")


def _sum_usage(provider_run):
    requests = provider_run["requests"]
    return (
        sum(request["usage"]["input_tokens"] for request in requests),
        sum(request["usage"]["output_tokens"] for request in requests),
        sum(request["usage"]["total_tokens"] for request in requests),
        sum(request["latency_ms"] for request in requests),
    )


def bind_attempt_evidence(run_dir, frozen_config, launch_meta, binding, expectations):
    """Classify one attempt's sealed artifacts; never raises for content."""
    try:
        receipt, receipt_raw = _read_canonical(
            Path(run_dir) / RECEIPT_NAME, _RECEIPT_REQUIRED
        )
        _validate_receipt(receipt)
        provider_run, _ = _read_canonical(
            Path(run_dir) / PROVIDER_RUN_NAME, _PROVIDER_RUN_REQUIRED
        )
        _validate_provider_run(provider_run)
        llm_receipt, _ = _read_canonical(
            Path(run_dir) / LLM_RECEIPT_NAME, _LLM_RECEIPT_REQUIRED
        )
        _validate_llm_receipt(llm_receipt)
        _check_bindings(llm_receipt, provider_run, receipt_raw, expectations)
        _check_identity(
            receipt, provider_run, llm_receipt, frozen_config, launch_meta, binding
        )
        input_tokens, output_tokens, total_tokens, latency_ms = _sum_usage(provider_run)
        return AttemptEvidence(
            "bound",
            None,
            receipt["outcome"],
            receipt["verified_score"],
            input_tokens,
            output_tokens,
            total_tokens,
            latency_ms,
            llm_receipt["core_receipt_digest"],
            llm_receipt["llm_receipt_digest"],
            receipt["policy_digest"],
            receipt["logical_run_id"],
            llm_receipt["prompt_digest"],
            llm_receipt["tool_schema_digest"],
        )
    except EvidenceError as exc:
        return AttemptEvidence("invalid", exc.reason)


def absorb_expectations(expectations, result):
    """First-bound-wins: the first bound evidence freezes prompt/tool identity."""
    if result.status != "bound":
        return dict(expectations)
    merged = dict(expectations)
    for key in ("prompt_digest", "tool_schema_digest"):
        if merged.get(key) is None:
            merged[key] = getattr(result, key)
    return merged
