"""Bounded ephemeral Langfuse v3.114.0 PR smoke runtime (stdlib only).

Contract (CI-LF-01..05, corrected design): downstream-only, fixed official
v3.114.0 images, ephemeral headless stack with generated credentials, dynamic
host port and exact-label project isolation, prevent -> mask -> scan -> fail
credential handling, an independent worker proof (in-container `/api/health`
probe plus stable no-restart window), an empty scoped API baseline, exactly
two identical real metadata-only CLI exports with exact counters, scoped API
proof 0 -> 1 -> 1 with identical ID sets and zero growth (HTTP 207 is never
novelty evidence), six-service image provenance, sanitized diagnostics before
unconditional exact-label cleanup, and one authoritative report whose
``cleanup_failed`` reason overrides the overall result. Normal tests never
start Docker; this module is exercised through injected seams.
"""

import base64
import json
import math
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import ids
from .artifacts import canonical_json_bytes

SMOKE_SCHEMA_VERSION = 2
CONTRACT = "CI-LF-01..05"
LANGFUSE_VERSION = "3.114.0"
WORKER_HEALTH_ENDPOINT = "http://${HOSTNAME}:3030/api/health"
WORKER_HEALTH_PROBE = f'wget --no-verbose --tries=1 --spider "{WORKER_HEALTH_ENDPOINT}"'
COMPOSE_LABEL = "com.docker.compose.project"
WEB_HOST_PORT_VAR = "LANGFUSE_WEB_HOST_PORT"
REPO_ROOT = Path(__file__).resolve().parents[1]

SERVICE_NAMES = ("web", "worker", "postgres", "clickhouse", "redis", "minio")
SERVICE_IMAGES = {
    "web": "docker.io/langfuse/langfuse:3.114.0",
    "worker": "docker.io/langfuse/langfuse-worker:3.114.0",
    "postgres": "docker.io/postgres:17.5",
    "clickhouse": "docker.io/clickhouse/clickhouse-server:24.3.18.7",
    "redis": "docker.io/redis:7.2.4",
    "minio": "docker.io/minio/minio:RELEASE.2025-09-07T16-13-09Z",
}
COMPOSE_SERVICE_OF = {
    "web": "langfuse-web",
    "worker": "langfuse-worker",
    "postgres": "postgres",
    "clickhouse": "clickhouse",
    "redis": "redis",
    "minio": "minio",
}
EXPECTED_CLI_COUNTS = {
    "processed": 1,
    "accepted_items": 1,
    "accepted_events": 3,
    "error_events": 0,
    "failed": 0,
    "skipped": 0,
}
PHASE_ORDER = (
    "preflight",
    "dependencies",
    "web",
    "worker",
    "baseline",
    "export_1",
    "api_proof_1",
    "export_2",
    "api_proof_2",
    "diagnostics",
    "cleanup",
)
_PHASE_CEILING_S = {
    "preflight": "preflight_s",
    "dependencies": "dependencies_s",
    "web": "web_s",
    "worker": "worker_s",
    "baseline": "api_poll_s",
    "export_1": "export_s",
    "export_2": "export_s",
    "api_proof_1": "api_poll_s",
    "api_proof_2": "api_poll_s",
}
ALLOWED_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "TELEMETRY_ENABLED",
        "GITHUB_ACTIONS",
        "GITHUB_EVENT_NAME",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
    }
)
_HEADER_PATTERN = re.compile(r"(?i)(authorization|proxy-authorization)\s*:")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+\S+")


class SmokeFailure(Exception):
    """One bounded smoke failure; ``detail`` is sanitized before reporting."""

    def __init__(self, phase, code, detail=None):
        super().__init__(f"{phase}: {code}" + (f": {detail}" if detail else ""))
        self.phase = phase
        self.code = code
        self.detail = detail


class CredentialExposureError(SmokeFailure):
    """Fail-closed credential exposure; redaction is never remediation."""

    def __init__(self, detail=None, redacted_output=None):
        super().__init__("security", "credential_exposure", detail)
        self.redacted_output = redacted_output


class CommandTimeout(SmokeFailure):
    def __init__(self, detail=None):
        super().__init__("command", "command_timeout", detail)


class FixtureMutated(SmokeFailure):
    def __init__(self, detail=None):
        super().__init__("fixture", "fixture_mutated", detail)


class ApiError(Exception):
    """Safe API read failure; never carries a response body."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Bounds:
    global_deadline_s: int = 1800
    preflight_s: int = 60
    dependencies_s: int = 720
    web_s: int = 300
    worker_s: int = 180
    worker_stability_s: int = 30
    export_s: int = 120
    api_poll_s: int = 180
    diagnostics_s: int = 90
    cleanup_s: int = 180
    http_timeout_s: int = 15
    poll_interval_s: int = 2
    poll_max_attempts: int = 90


@dataclass
class SmokeConfig:
    compose_file: Path
    source_env: dict[str, str] = field(default_factory=dict)
    report_path: Path | None = None
    bounds: Bounds = field(default_factory=Bounds)


@dataclass
class SmokeDeps:
    runner: Any
    guard: Any
    api_factory: Callable[[str, str, str], Any]
    sleeper: Callable[[float], None] = time.sleep
    inventory: Any = None
    cleanup: Any = None
    port_is_free: Callable[[], bool] | None = None
    web_probe: Callable[[str], dict] | None = None
    cleanup_factory: Callable[[list[str], Callable[[], bool], dict], Any] | None = None
    inventory_factory: Callable[[list[str], dict], Any] | None = None


class LeakGuard:
    """Registers generated values, masks before first use, scans and redacts."""

    def __init__(self, sink=None):
        self._sink = sink or (lambda line: None)
        self._values: dict[str, str] = {}

    def register(self, value, label):
        if value and value not in self._values:
            self._values[value] = label
            self._sink(f"::add-mask::{value}")

    @property
    def values(self):
        return dict(self._values)

    def redact(self, text):
        cleaned = str(text)
        for value in sorted(self._values, key=len, reverse=True):
            cleaned = cleaned.replace(value, "[REDACTED]")
        return "\n".join(
            _HEADER_PATTERN.sub("authorization: [REDACTED]", line, count=1)
            for line in cleaned.splitlines()
        )

    def leaks(self, text):
        text = str(text)
        found = tuple(
            label for value, label in self._values.items() if value and value in text
        )
        patterns = ()
        if _HEADER_PATTERN.search(text):
            patterns += ("authorization_header",)
        if _BEARER_PATTERN.search(text):
            patterns += ("bearer_token",)
        return found + patterns

    def ensure_clean(self, text):
        if self.leaks(text):
            raise CredentialExposureError(
                "credential value or header pattern detected",
                redacted_output=self.redact(text),
            )


def default_sink(source_env):
    """GitHub mask sink when running in Actions; silent memory sink locally."""

    def sink(line):
        if str(source_env.get("GITHUB_ACTIONS", "")).lower() == "true":
            print(line, flush=True)

    return sink


def ensure_clean_file(guard, path):
    """Scan a runtime-created file after write; fail closed when unscannable."""
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise SmokeFailure(
            "security",
            "artifact_unscannable",
            f"{Path(path).name} is not UTF-8 text and cannot be scanned",
        ) from None
    guard.ensure_clean(text)


def _write_scanned_report(guard, path, text):
    """Write the report file, scan it after write, redact in place on leak."""
    path.write_text(text + "\n", encoding="utf-8")
    try:
        ensure_clean_file(guard, path)
    except SmokeFailure:
        try:
            path.write_text(guard.redact(text) + "\n", encoding="utf-8")
        except OSError:
            pass
        raise


class CommandRunner:
    """Argv-list subprocess runner; rejects secret argv and leaked output."""

    def __init__(self, guard, cwd, base_env):
        self.guard = guard
        self.cwd = Path(cwd)
        self.base_env = dict(base_env)

    def run(self, argv, *, env=None, timeout=None):
        parts = [str(part) for part in argv]
        for value, label in self.guard.values.items():
            if value in parts:
                raise CredentialExposureError(
                    f"registered {label} present in argv; refusing to spawn",
                    redacted_output="",
                )
        full_env = {**self.base_env, **(env or {})}
        try:
            done = subprocess.run(
                parts,
                cwd=str(self.cwd),
                env=full_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CommandTimeout(f"no completion within {timeout}s") from None
        merged = done.stdout + done.stderr
        if self.guard.leaks(merged):
            raise CredentialExposureError(
                "credential value detected in command output",
                redacted_output=self.guard.redact(merged),
            )
        return RunResult(done.returncode, done.stdout, done.stderr)


_LOGICAL_RUN_ID = "lr_" + "3a" * 32
_EXECUTION_ID = "sealed-fixture-01"
_CORE = "sha256:07089cdb36c6dac54695d17f5fba222a50b5e55c7f92b39459b86bcc73b1df40"
_LLM = "sha256:9da9f5925defb8f6c89ae6409f679e96b19e5c746461b7fff5596f0c199f2e97"
_PROVIDER = "sha256:63ccc19f22a27085c6247fc841f81da06a6613d1d700391da581aba2f869afd5"
_PROMPT = "sha256:85835d611a0e022503db66e4ce5bec69ea79556ba0753b5fbed37d0f4bdb81ac"
_TOOL = "sha256:44e2f06d91cc40cff1567cb1686f54d0ddd8ffa89c30db01fcf15a1614b0667f"
_POLICY = "sha256:e5f8b5273e2b1563b3be9cc5c00680b76b5a2c0a8139044d608a85481c481254"
_RECEIPT = {
    "execution_id": _EXECUTION_ID,
    "logical_run_id": _LOGICAL_RUN_ID,
    "outcome": "completed",
    "policy_digest": _POLICY,
    "policy_id": "llm-v1",
    "schema_version": 1,
    "seed": "18",
    "verification": {"status": "valid", "verified_score": 100},
    "verified_score": 100,
}
_LLM_RECEIPT = {
    "core_receipt_digest": _CORE,
    "execution_id": _EXECUTION_ID,
    "llm_receipt_digest": _LLM,
    "logical_run_id": _LOGICAL_RUN_ID,
    "model": "fake-v1",
    "prompt_digest": _PROMPT,
    "provider": "fake",
    "provider_evidence_digest": _PROVIDER,
    "schema_version": 1,
    "tool_schema_digest": _TOOL,
}
_PROVIDER_RUN = {
    "execution_id": _EXECUTION_ID,
    "logical_run_id": _LOGICAL_RUN_ID,
    "requests": [
        {
            "error": None,
            "latency_ms": 42,
            "model": "fake-v1",
            "policy_step": 0,
            "provider": "fake",
            "raw_visible_response": None,
            "request_id": "req-0",
            "tool_call": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
    ],
    "schema_version": 1,
}
_LAUNCH = {
    "schema_version": 1,
    "attempt_id": "attempt-t1",
    "registered_before_start": True,
    "condition": "R1",
    "seed": "18",
    "agent_id": "R1-agent",
    "attempt_ordinal": 0,
    "argv": ["python3", "-m", "robot_factory_arena"],
    "context_path": "contexts/frozen.json",
    "context_digest": "sha256:" + "ab" * 32,
}
_LEADERBOARD = {
    "schema_version": 1,
    "tournament_id": "tournament-observed",
    "harness_version": "h-test",
    "core_version": "c-test",
    "rows": [
        {
            "condition": "R1",
            "seed": "18",
            "status": "completed",
            "outcome": "completed",
            "verified_score": 100,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "latency_ms": None,
            "attempts_used": 1,
            "declined_launches": 0,
            "core_receipt_digest": _CORE,
            "llm_receipt_digest": _LLM,
            "policy_digest": _POLICY,
            "strategy_context_digest": None,
            "harness_version": "h-test",
            "core_version": "c-test",
            "attempt_refs": [
                {
                    "attempt_id": "attempt-t1",
                    "attempt_ordinal": 0,
                    "evidence_status": "bound",
                }
            ],
        }
    ],
}


class FixtureFactory:
    """Writes the deterministic sealed metadata-only fixture to a temp root."""

    def create(self, root):
        root = Path(root)
        run_dir = root / "core" / "runs" / _LOGICAL_RUN_ID / _EXECUTION_ID
        (run_dir / "llm").mkdir(parents=True)
        (root / "attempts" / "attempt-t1").mkdir(parents=True)
        _write_canonical(run_dir / "receipt.json", _RECEIPT)
        _write_canonical(run_dir / "llm" / "provider-run.json", _PROVIDER_RUN)
        _write_canonical(run_dir / "llm" / "llm-receipt.json", _LLM_RECEIPT)
        _write_canonical(root / "attempts" / "attempt-t1" / "launch.json", _LAUNCH)
        _write_canonical(root / "leaderboard.json", _LEADERBOARD)
        return {
            "trace": ids.trace_id(
                "tournament-observed", "attempt-t1", _EXECUTION_ID, _CORE, _LLM
            ),
            "generation": ids.generation_id(_EXECUTION_ID, "req-0", _PROVIDER),
            "score": ids.score_id(
                ids.trace_id(
                    "tournament-observed", "attempt-t1", _EXECUTION_ID, _CORE, _LLM
                ),
                "verified_score",
                _CORE,
            ),
        }

    def snapshot(self, root):
        root = Path(root)
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def assert_unchanged(self, root, snapshot):
        if self.snapshot(root) != snapshot:
            raise FixtureMutated("fixture bytes changed during the smoke")


def _write_canonical(path, value):
    path.write_bytes(canonical_json_bytes(value))


def _urllib_transport(credential, timeout):
    """Bounded Basic-auth GET returning ``(status, body bytes)`` only."""

    def transport(url, request_timeout=None):
        request = urllib.request.Request(url, headers=credential)
        with urllib.request.urlopen(
            request, timeout=timeout if request_timeout is None else request_timeout
        ) as response:
            return response.status, response.read()

    return transport


class LangfuseApi:
    """Authenticated bounded read client for the scoped public v3.114.0 API."""

    def __init__(
        self,
        base_url,
        public_key,
        secret_key,
        *,
        timeout=15.0,
        transport: Callable[..., tuple[int, bytes]] | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        if transport is None:
            token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
            transport = _urllib_transport({"Authorization": f"Basic {token}"}, timeout)
        self._transport = transport

    def _get(self, path_and_query, timeout=None):
        url = self._base + "/api/public/" + path_and_query
        try:
            if timeout is None:
                status, body = self._transport(url)
            else:
                status, body = self._transport(url, timeout)
        except OSError:
            raise ApiError(f"api_unreachable: {path_and_query.split('?')[0]}") from None
        if status != 200:
            raise ApiError(f"unexpected api status {status}")
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError("api response is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise ApiError("api response is not a JSON object")
        return parsed

    def _list(self, resource, timeout=None):
        limit = 100
        entries, total = [], None
        for page in range(1, 11):
            payload = self._get(f"{resource}?limit={limit}&page={page}", timeout)
            if set(payload) != {"data", "meta"} or not isinstance(
                payload["data"], list
            ):
                raise ApiError(f"malformed {resource} envelope")
            meta = payload["meta"]
            required = {"page", "limit", "totalItems", "totalPages"}
            if (
                not isinstance(meta, dict)
                or set(meta) != required
                or not all(
                    isinstance(meta[key], int) and meta[key] >= 0 for key in required
                )
            ):
                raise ApiError(f"malformed {resource} pagination")
            if meta["limit"] != limit or meta["page"] != page:
                raise ApiError(f"unexpected {resource} pagination echo")
            if total is None:
                total = meta["totalItems"]
                if total > 1000:
                    raise ApiError(f"isolation cap exceeded in scoped {resource}")
            if meta["totalPages"] != -(-total // limit):
                raise ApiError(f"inconsistent {resource} pagination")
            entries.extend(payload["data"])
            if page >= meta["totalPages"]:
                break
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                raise ApiError(f"malformed {resource} entry")
        return entries, total or 0

    def snapshot(self, timeout=None):
        traces, trace_total = self._list("traces", timeout)
        observations, observation_total = self._list("observations", timeout)
        scores, score_total = self._list("scores", timeout)
        generations = [
            entry for entry in observations if entry.get("type") == "GENERATION"
        ]
        return {
            "traces": {"count": trace_total, "ids": tuple(e["id"] for e in traces)},
            "generations": {
                "count": len(generations),
                "ids": tuple(e["id"] for e in generations),
            },
            "scores": {"count": score_total, "ids": tuple(e["id"] for e in scores)},
            "observations_total": observation_total,
        }

    def trace(self, trace_id, timeout=None):
        return self._get(f"traces/{trace_id}", timeout)

    def observation(self, observation_id, timeout=None):
        return self._get(f"observations/{observation_id}", timeout)

    def score(self, score_id, timeout=None):
        return self._get(f"scores/{score_id}", timeout)


def baseline_inconsistency(snapshot):
    """Safe reason when a scoped snapshot cannot be a consistent baseline."""
    try:
        for kind in ("traces", "generations", "scores"):
            entry = snapshot[kind]
            count, found = entry["count"], entry["ids"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                return f"{kind} count is not a nonnegative integer"
            if not isinstance(found, (list, tuple)) or not all(
                isinstance(value, str) for value in found
            ):
                return f"{kind} ids are not a string sequence"
            if len(found) != count or len(set(found)) != count:
                return f"{kind} count does not match its unique ID set"
        total = snapshot["observations_total"]
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return "observations total is not a nonnegative integer"
        if total < len(snapshot["generations"]["ids"]):
            return "observations total is below the generation count"
    except (KeyError, TypeError):
        return "baseline snapshot is malformed"
    return None


def probe_web_health(url, timeout=15.0):
    """One bounded unauthenticated health read returning a safe dict."""
    try:
        with urllib.request.urlopen(
            url.rstrip("/") + "/api/public/health", timeout=timeout
        ) as response:
            status, body = response.status, response.read()
    except (OSError, ValueError):
        return {"status": "unreachable"}
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"status": "unparseable", "http": status}
    if isinstance(parsed, dict):
        return {
            "status": parsed.get("status"),
            "version": parsed.get("version"),
            "http": status,
        }
    return {"status": "unexpected", "http": status}


def allocate_loopback_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def host_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def worker_proof(
    runner,
    base,
    stability_s,
    sleeper,
    env=None,
    state_timeout=30,
    health_timeout=60,
):
    """Prove the worker: running, stable, and its in-container health endpoint."""
    listed = runner.run(
        [*base, "ps", "-q", "langfuse-worker"], env=env, timeout=state_timeout
    )
    container = listed.stdout.strip().splitlines()
    if not container:
        raise SmokeFailure("worker", "worker_not_running", "no worker container")
    state = _inspect_state(runner, container[0], timeout=state_timeout)
    if not state.get("Running"):
        raise SmokeFailure("worker", "worker_not_running", "container not running")
    restarts = state.get("RestartCount", 0)
    if stability_s:
        sleeper(stability_s)
    state = _inspect_state(runner, container[0], timeout=state_timeout)
    if not state.get("Running") or state.get("RestartCount", 0) != restarts:
        raise SmokeFailure(
            "worker", "worker_unstable", f"restarts {state.get('RestartCount')}"
        )
    health = runner.run(
        [
            "docker",
            "exec",
            container[0],
            "sh",
            "-c",
            WORKER_HEALTH_PROBE,
        ],
        env=env,
        timeout=health_timeout,
    )
    if health.returncode:
        raise SmokeFailure(
            "worker",
            "worker_readiness_unproven",
            f"health probe failed (exit {health.returncode})",
        )
    return {
        "health_probe": {"endpoint": WORKER_HEALTH_ENDPOINT, "status": "PASS"},
        "restarts": restarts,
    }


def _inspect_state(runner, container, timeout=30):
    inspected = runner.run(
        ["docker", "inspect", "--format", "{{json .State}}", container],
        timeout=timeout,
    )
    try:
        payload = json.loads(inspected.stdout)
        if not isinstance(payload, dict):
            raise TypeError
        return payload
    except (json.JSONDecodeError, TypeError):
        raise SmokeFailure(
            "worker", "worker_inspect_failed", "no State payload"
        ) from None


def unavailable_record(name, reason):
    """Explicit unavailable provenance; a missing record is never allowed."""
    return {
        "requested_image": SERVICE_IMAGES[name],
        "resolved_digest": None,
        "image_id": None,
        "available": False,
        "unavailable_reason": reason,
    }


def unavailable_inventory(reason):
    """All six explicit unavailable records for a failed diagnostic collection."""
    return {name: unavailable_record(name, reason) for name in SERVICE_NAMES}


class ImageInventory:
    """Exactly six provenance records: requested, digest, image id."""

    def __init__(
        self,
        runner: Any,
        base: list[str] | Callable[[], list[str]],
        env: dict[str, str] | None = None,
    ):
        self.runner = runner
        self._base = base
        self._env = env

    def collect(self, timeout=30):
        base = self._base() if callable(self._base) else self._base
        records = {}
        for name in SERVICE_NAMES:
            budget = timeout() if callable(timeout) else timeout
            try:
                records[name] = self._record(base, name, budget)
            except CredentialExposureError:
                raise
            except Exception as exc:  # noqa: BLE001 - per-service safe fallback
                records[name] = unavailable_record(
                    name, f"inventory_failed_{type(exc).__name__}"
                )
        return records

    def _record(self, base, name, timeout):
        record = {
            "requested_image": SERVICE_IMAGES[name],
            "resolved_digest": None,
            "image_id": None,
            "available": False,
            "unavailable_reason": None,
        }
        listed = self.runner.run(
            [*base, "ps", "-q", COMPOSE_SERVICE_OF[name]],
            env=self._env,
            timeout=timeout,
        )
        containers = listed.stdout.strip().splitlines()
        if not containers:
            record["unavailable_reason"] = "not_materialized"
            return record
        try:
            image_id = self.runner.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Image}}",
                    containers[0],
                ],
                timeout=timeout,
            ).stdout.strip()
            if not image_id:
                raise TypeError
            record["image_id"] = image_id
        except (AttributeError, TypeError):
            record["unavailable_reason"] = "inspect_failed"
            return record
        try:
            digests = json.loads(
                self.runner.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{json .RepoDigests}}",
                        record["image_id"],
                    ],
                    timeout=timeout,
                ).stdout
            )
        except (json.JSONDecodeError, TypeError):
            record["unavailable_reason"] = "missing_digest"
            return record
        if (
            not isinstance(digests, list)
            or not digests
            or not isinstance(digests[0], str)
        ):
            record["unavailable_reason"] = "missing_digest"
            return record
        record["resolved_digest"] = digests[0].split("@", 1)[-1]
        record["available"] = True
        return record


class CleanupManager:
    """Exact-label teardown plus residue and released-port validation."""

    def __init__(
        self,
        runner: Any,
        base: list[str] | Callable[[], list[str]],
        port_is_free: Callable[[], bool],
        down_timeout: int = 180,
        env: dict[str, str] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        port_poll_s: float = 5,
        port_poll_interval_s: float = 1,
    ):
        self.runner = runner
        self._base = base
        self._port_is_free = port_is_free
        self.down_timeout = down_timeout
        self._env = env
        self._sleeper = sleeper
        self.port_poll_s = port_poll_s
        self.port_poll_interval_s = port_poll_interval_s

    def execute(self, *, down_timeout=None, query_timeout=30, port_timeout=None):
        def budget(value, default):
            return default if value is None else (value() if callable(value) else value)

        base = self._base() if callable(self._base) else self._base
        project = base[base.index("-p") + 1]
        problems = []
        down = self.runner.run(
            [*base, "down", "-v", "--remove-orphans"],
            env=self._env,
            timeout=budget(down_timeout, self.down_timeout),
        )
        if down.returncode:
            problems.append(f"compose down failed (exit {down.returncode})")
        port_released = self._wait_for_port_release(
            budget(port_timeout, self.port_poll_s)
        )
        label = f"label={COMPOSE_LABEL}={project}"
        for kind, argv in (
            ("containers", ["docker", "ps", "-aq"]),
            ("volumes", ["docker", "volume", "ls", "-q"]),
            ("networks", ["docker", "network", "ls", "-q"]),
        ):
            found = self.runner.run(
                [*argv, "--filter", label], timeout=budget(query_timeout, 30)
            )
            if found.returncode:
                problems.append(
                    f"{kind} residue query failed (exit {found.returncode})"
                )
            elif found.stdout.strip():
                problems.append(f"project-labelled {kind} remain")
        if not port_released:
            problems.append("selected host port is not released")
        return problems

    def _wait_for_port_release(self, poll_budget):
        """Poll release after down without exceeding the supplied budget."""
        poll_budget = max(0.0, poll_budget)
        interval = max(0.001, self.port_poll_interval_s)
        deadline = time.monotonic() + poll_budget
        sleeps = 0
        max_sleeps = math.floor(poll_budget / interval)
        while True:
            if self._port_is_free():
                return True
            if sleeps >= max_sleeps:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._sleeper(min(interval, remaining))
            sleeps += 1


def _generate_runtime_values():
    token = secrets.token_urlsafe
    suffix = secrets.token_hex(12)
    values = {
        "NEXTAUTH_SECRET": token(32),
        "SALT": token(32),
        "ENCRYPTION_KEY": secrets.token_hex(32),
        "POSTGRES_USER": "smoke_" + secrets.token_hex(8),
        "POSTGRES_PASSWORD": token(32),
        "CLICKHOUSE_PASSWORD": token(32),
        "REDIS_AUTH": token(32),
        "MINIO_ROOT_USER": "smoke" + secrets.token_hex(8),
        "MINIO_ROOT_PASSWORD": token(32),
        "LANGFUSE_INIT_ORG_ID": "org-smoke-" + suffix,
        "LANGFUSE_INIT_ORG_NAME": "PR smoke",
        "LANGFUSE_INIT_PROJECT_ID": "project-smoke-" + suffix,
        "LANGFUSE_INIT_PROJECT_NAME": "PR smoke",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "pk-lf-" + secrets.token_hex(24),
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "sk-lf-" + secrets.token_hex(24),
        "TELEMETRY_ENABLED": "false",
    }
    values["LANGFUSE_PUBLIC_KEY"] = values["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"]
    values["LANGFUSE_SECRET_KEY"] = values["LANGFUSE_INIT_PROJECT_SECRET_KEY"]
    return values


_SECRET_LABELS = {
    "NEXTAUTH_SECRET": "nextauth_secret",
    "SALT": "salt",
    "ENCRYPTION_KEY": "encryption_key",
    "POSTGRES_USER": "postgres_user",
    "POSTGRES_PASSWORD": "postgres_password",
    "CLICKHOUSE_PASSWORD": "clickhouse_password",
    "REDIS_AUTH": "redis_auth",
    "MINIO_ROOT_USER": "minio_root_user",
    "MINIO_ROOT_PASSWORD": "minio_root_password",
    "LANGFUSE_INIT_ORG_ID": "init_org_id",
    "LANGFUSE_INIT_PROJECT_ID": "init_project_id",
    "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "public_key",
    "LANGFUSE_INIT_PROJECT_SECRET_KEY": "secret_key",
    "LANGFUSE_PUBLIC_KEY": "public_key",
    "LANGFUSE_SECRET_KEY": "secret_key",
}


def write_bootstrap_override(path, values):
    """Mode-0600 override with variable references only, never values."""
    lines = ["services:", "  langfuse-web:", "    environment:"]
    for name in (
        "LANGFUSE_INIT_ORG_ID",
        "LANGFUSE_INIT_ORG_NAME",
        "LANGFUSE_INIT_PROJECT_ID",
        "LANGFUSE_INIT_PROJECT_NAME",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY",
    ):
        lines.append(f"      {name}: ${{{name}:?runtime smoke value}}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


class ReportAuthority:
    """The only final-status authority: phases, precedence, exit mapping."""

    def __init__(self, guard: Any):
        self.guard = guard
        self.phases: dict[str, dict] = {
            name: {"status": "skipped"} for name in PHASE_ORDER
        }
        self.cli_exports = []
        self.exporter_invocations = 0
        self.primary_failure = None
        self.secondary_failures = []
        self.api_proof = {}
        self.images = {}
        self.bounds_policy: dict = {}
        self.security = {
            "masking": "PASS",
            "scan": "PASS",
            "exposure_detected": False,
            "artifacts_sanitized": True,
        }
        self.cleanup = {
            "status": "PASS",
            "label": None,
            "residue": {"containers": 0, "volumes": 0, "networks": 0},
            "port_released": True,
        }
        self.promotion_evidence = {
            "counted_for_promotion": False,
            "rerun_derived_green": False,
            "cold_start": "unknown",
            "failure_drill": False,
            "cleanup_pass": True,
            "promotion_eligible": False,
        }
        self.run = {}

    def primary(self, failure: SmokeFailure):
        detail = self.guard.redact(str(failure.detail or ""))[:200]
        if self.primary_failure is None:
            self.primary_failure = {
                "phase": failure.phase,
                "code": failure.code,
                "detail": detail,
            }
        elif failure.code == "credential_exposure":
            self.secondary_failures.append(
                {"phase": failure.phase, "code": failure.code}
            )
        if failure.code == "credential_exposure":
            self.security["exposure_detected"] = True
            self.security["scan"] = "FAIL"

    def record_cleanup(self, label, problems):
        self.cleanup["label"] = label
        self.cleanup["port_released"] = not any(
            "port" in problem for problem in problems
        )
        self.cleanup["status"] = "FAIL" if problems else "PASS"
        self.phases["cleanup"] = {
            "status": self.cleanup["status"],
            "problems": problems,
        }
        self.promotion_evidence["cleanup_pass"] = self.cleanup["status"] == "PASS"

    def finalize(self):
        """Compute assertion/cleanup precedence into the authoritative report."""
        assertions = "PASS" if self.primary_failure is None else "FAIL"
        overall = (
            "PASS"
            if assertions == "PASS" and self.cleanup["status"] == "PASS"
            else "FAIL"
        )
        if overall == "PASS":
            reason = None
        elif self.cleanup["status"] == "FAIL":
            reason = "cleanup_failed"
        else:
            reason = (self.primary_failure or {}).get("code") or "smoke_failed"
        self.promotion_evidence["promotion_eligible"] = False
        return {
            "schema_version": SMOKE_SCHEMA_VERSION,
            "contract": CONTRACT,
            "run": self.run,
            "bounds": self.bounds_policy,
            "phases": self.phases,
            "exporter_invocations": self.exporter_invocations,
            "cli_exports": self.cli_exports,
            "smoke_assertions": assertions,
            "primary_failure": self.primary_failure,
            "secondary_failures": self.secondary_failures,
            "cleanup": self.cleanup,
            "overall": overall,
            "reason": reason,
            "security": self.security,
            "images": self.images,
            "api_proof": self.api_proof,
            "promotion_evidence": self.promotion_evidence,
        }

    def emit_text(self, report):
        """Scan the exact report bytes; exposure is terminal before retention."""
        text = canonical_json_bytes(report).decode("utf-8")
        if self.guard.leaks(text):
            self.security["artifacts_sanitized"] = False
            detail = "final report contained a credential value or header pattern"
            self.primary(SmokeFailure("report", "credential_exposure", detail))
            report.clear()
            report.update(self.finalize())
            text = self.guard.redact(canonical_json_bytes(report).decode("utf-8"))
        if any(value and value in text for value in self.guard.values):
            raise CredentialExposureError(
                "redaction left a credential value in the report"
            )
        return text


def exit_code_for(report):
    return 0 if report.get("overall") == "PASS" else 1


class SmokeOrchestrator:
    """Bounded phase machine with the two-real-export invariant."""

    def __init__(self, config, deps):
        self.config = config
        self.deps = deps
        self.bounds = config.bounds
        self._deadline: float = 0.0
        self._phase_deadline: float = 0.0
        self._final_deadline: float = 0.0
        self._base: list[str] = []
        self._env: dict[str, str] = {}
        self._url = ""
        self._port_free: Callable[[], bool] | None = None
        self._cleanup_impl: Any = None
        self._current_phase: str | None = None
        self.report = ReportAuthority(deps.guard)

    def _enter_phase(self, name):
        """Start the one phase deadline instance; never reset within a phase."""
        self._current_phase = name
        ceiling = getattr(self.bounds, _PHASE_CEILING_S[name])
        self._phase_deadline = min(time.monotonic() + ceiling, self._deadline)

    def _remaining(self, ceiling):
        """min(per-call cap, phase remaining, global remaining); bounded failure."""
        now = time.monotonic()
        if self._deadline - now <= 0:
            raise SmokeFailure(
                self._current_phase or "global",
                "global_deadline_exceeded",
                f"global budget of {self.bounds.global_deadline_s}s exhausted",
            )
        phase_left = self._phase_deadline - now
        if phase_left <= 0:
            name = self._current_phase or "global"
            bound = _PHASE_CEILING_S.get(name, "global_deadline_s")
            raise SmokeFailure(
                name,
                "phase_deadline_exceeded",
                f"phase budget of {getattr(self.bounds, bound)}s exhausted",
            )
        return max(1, math.ceil(min(ceiling, phase_left, self._deadline - now)))

    def _finalization_remaining(self, ceiling):
        """One explicit finalization budget; attempts never skip after expiry."""
        left = self._final_deadline - time.monotonic()
        return max(1, math.ceil(min(ceiling, left))) if left > 0 else 1

    def _finalization_sleep_remaining(self, ceiling):
        """Return exact remaining finalization time for sleep-based polling."""
        return max(0.0, min(ceiling, self._final_deadline - time.monotonic()))

    def run(self):
        self._deadline = time.monotonic() + self.bounds.global_deadline_s
        self.report.run = {
            "run_id": secrets.token_hex(8),
            "compose_project": "langfuse-smoke-" + secrets.token_hex(8),
            "event": self.config.source_env.get("GITHUB_EVENT_NAME", "local"),
            "github_run_id": self.config.source_env.get("GITHUB_RUN_ID"),
            "run_attempt": self.config.source_env.get("GITHUB_RUN_ATTEMPT"),
            "started_utc": _utc_now(),
            "ended_utc": None,
        }
        self.report.bounds_policy = asdict(self.bounds)
        temp = Path(tempfile.mkdtemp(prefix="langfuse-smoke-"))
        try:
            try:
                self._business_phases(temp)
            except SmokeFailure as raised:
                self._retag_security_phase(raised)
                self.report.primary(raised)
            except Exception as raised:  # noqa: BLE001
                self._fail_unexpected(raised)
            self._current_phase = None
            self._finalization(temp)
            self.report.run["ended_utc"] = _utc_now()
            return self.report
        finally:
            if self.report.run["ended_utc"] is None:
                # SystemExit/KeyboardInterrupt (or finalization errors): capture
                # safe diagnostics and run exact-label cleanup first, then let
                # the original exception propagate unchanged. Nothing is printed,
                # so no registered credential can leak on this path.
                self._current_phase = None
                self._finalization(temp)
                self.report.run["ended_utc"] = _utc_now()

    def _finalization(self, temp):
        """Diagnostics then unconditional cleanup under one explicit budget."""
        self._final_deadline = time.monotonic() + (
            self.bounds.diagnostics_s + self.bounds.cleanup_s
        )
        self._diagnostics()
        problems = self._cleanup()
        try:
            shutil.rmtree(temp)
        except OSError:
            problems.append("temporary smoke directory could not be removed")
        self.report.record_cleanup(self.report.run["compose_project"], problems)

    def _retag_security_phase(self, raised):
        """Attribute runner/fixture/security failures to the running phase."""
        if raised.phase in ("security", "command", "fixture") and self._current_phase:
            raised.phase = self._current_phase

    def _fail_unexpected(self, raised):
        """Safe primary failure for ApiError and any other unexpected Exception."""
        if isinstance(raised, ApiError):
            code, detail = "api_read_failed", str(raised)
        else:
            code, detail = "unexpected_failure", type(raised).__name__
        self.report.primary(
            SmokeFailure(self._current_phase or "unknown", code, detail)
        )

    def _business_phases(self, temp):
        self._preflight(temp)
        self._dependencies()
        self._web()
        self._worker()
        api = self.deps.api_factory(
            self._url,
            self._env["LANGFUSE_PUBLIC_KEY"],
            self._env["LANGFUSE_SECRET_KEY"],
        )
        baseline = self._baseline(api)
        fixture = temp / "fixture"
        expected = FixtureFactory().create(fixture)
        for artifact in sorted(fixture.rglob("*")):
            if artifact.is_file():
                ensure_clean_file(self.deps.guard, artifact)
        snapshot = FixtureFactory().snapshot(fixture)
        self._export(fixture, 1)
        first_proof = self._api_proof(api, expected, baseline, None, 1)
        self._export(fixture, 2)
        FixtureFactory().assert_unchanged(fixture, snapshot)
        self._api_proof(api, expected, baseline, first_proof, 2)

    def _preflight(self, temp):
        self._enter_phase("preflight")
        self.report.phases["preflight"] = {"status": "PASS"}
        if not self.config.compose_file.is_file():
            raise SmokeFailure(
                "preflight", "preflight_invalid_config", "compose file missing"
            )
        port = allocate_loopback_port()
        self._url = f"http://127.0.0.1:{port}"
        port_free = self.deps.port_is_free or (lambda: host_port_free(port))
        self._port_free = port_free
        if not port_free():
            raise SmokeFailure("preflight", "port_unavailable", f"port {port} in use")
        values = _generate_runtime_values()
        for name, label in _SECRET_LABELS.items():
            self.deps.guard.register(values[name], label)
        override = temp / "bootstrap-override.yml"
        write_bootstrap_override(override, values)
        ensure_clean_file(self.deps.guard, override)
        self._env = {
            key: value
            for key, value in self.config.source_env.items()
            if key in ALLOWED_ENV_KEYS
        } | values
        project = self.report.run["compose_project"]
        self._env["COMPOSE_PROJECT_NAME"] = project
        self._env[WEB_HOST_PORT_VAR] = str(port)
        self._base = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(self.config.compose_file),
            "-f",
            str(override),
        ]
        label = f"label={COMPOSE_LABEL}={project}"
        self._cleanup_impl = self._cleanup_manager(port_free)
        for argv in (
            ["docker", "ps", "-aq"],
            ["docker", "volume", "ls", "-q"],
            ["docker", "network", "ls", "-q"],
        ):
            found = self.deps.runner.run(
                [*argv, "--filter", label], timeout=self._remaining(30)
            )
            if found.returncode:
                raise SmokeFailure(
                    "preflight",
                    "preflight_query_failed",
                    f"label query exit {found.returncode}",
                )
            if found.stdout.strip():
                raise SmokeFailure(
                    "preflight",
                    "preflight_collision",
                    "project-labelled resources pre-exist",
                )

    def _cleanup_manager(self, port_free):
        if self.deps.cleanup_factory is not None:
            return self.deps.cleanup_factory(self._base, port_free, self._env)
        return self.deps.cleanup

    def _read(self, api, method, *args):
        """One bounded API read; real client requests respect the phase budget."""
        if isinstance(api, LangfuseApi):
            return getattr(api, method)(
                *args, timeout=self._remaining(self.bounds.http_timeout_s)
            )
        return getattr(api, method)(*args)

    def _dependencies(self):
        self._enter_phase("dependencies")
        result = self.deps.runner.run(
            [
                *self._base,
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                str(self._remaining(self.bounds.dependencies_s)),
                "postgres",
                "clickhouse",
                "redis",
                "minio",
            ],
            env=self._env,
            timeout=self._remaining(self.bounds.dependencies_s),
        )
        if result.returncode:
            raise SmokeFailure(
                "dependencies", "compose_up_failed", f"exit {result.returncode}"
            )
        self.report.phases["dependencies"] = {"status": "PASS"}

    def _web(self):
        self._enter_phase("web")
        result = self.deps.runner.run(
            [*self._base, "up", "-d", "--no-deps", "langfuse-web"],
            env=self._env,
            timeout=self._remaining(self.bounds.web_s),
        )
        if result.returncode:
            raise SmokeFailure("web", "web_start_failed", f"exit {result.returncode}")
        interval = max(1, self.bounds.poll_interval_s)
        max_attempts = min(
            self.bounds.poll_max_attempts, max(1, self.bounds.web_s // interval)
        )
        for attempt in range(1, max_attempts + 1):
            if self.deps.web_probe is None:
                info = probe_web_health(
                    self._url, self._remaining(self.bounds.http_timeout_s)
                )
            else:
                info = self.deps.web_probe(self._url)
            if info.get("status") == "OK" and info.get("version") == LANGFUSE_VERSION:
                self.report.phases["web"] = {
                    "status": "PASS",
                    "attempts": attempt,
                    "version": info["version"],
                }
                return
            left = min(self._phase_deadline, self._deadline) - time.monotonic()
            if left <= 0:
                break
            self.deps.sleeper(min(self.bounds.poll_interval_s, left))
        raise SmokeFailure("web", "web_unhealthy", "fixed-version health not observed")

    def _worker(self):
        self._enter_phase("worker")
        result = self.deps.runner.run(
            [*self._base, "up", "-d", "--no-deps", "langfuse-worker"],
            env=self._env,
            timeout=self._remaining(self.bounds.worker_s),
        )
        if result.returncode:
            raise SmokeFailure(
                "worker", "worker_start_failed", f"exit {result.returncode}"
            )
        proof = worker_proof(
            self.deps.runner,
            self._base,
            min(self.bounds.worker_stability_s, self._remaining(self.bounds.worker_s)),
            self.deps.sleeper,
            env=self._env,
            state_timeout=self._remaining(30),
            health_timeout=self._remaining(60),
        )
        self.report.phases["worker"] = {"status": "PASS", **proof}

    def _baseline(self, api):
        self._enter_phase("baseline")
        snapshot = self._read(api, "snapshot")
        reason = baseline_inconsistency(snapshot)
        if reason is not None:
            raise SmokeFailure("baseline", "baseline_inconsistent", reason)
        counts = {
            kind: snapshot[kind]["count"]
            for kind in ("traces", "generations", "scores")
        }
        if any(counts.values()) or snapshot["observations_total"]:
            raise SmokeFailure("baseline", "baseline_not_empty", f"counts {counts}")
        self.report.phases["baseline"] = {"status": "PASS", "counts": counts}
        self.report.api_proof.update(
            {
                "baseline_counts": counts,
                "baseline_ids": {
                    kind: sorted(snapshot[kind]["ids"])
                    for kind in ("traces", "generations", "scores")
                },
            }
        )
        return {
            "counts": counts,
            "ids": {
                kind: list(snapshot[kind]["ids"])
                for kind in ("traces", "generations", "scores")
            },
        }

    def _export(self, fixture_root, ordinal):
        phase = f"export_{ordinal}"
        self._enter_phase(phase)
        result = self.deps.runner.run(
            [
                sys.executable,
                "-m",
                "observability",
                "export",
                "--artifacts-root",
                str(fixture_root),
                "--langfuse-url",
                self._url,
            ],
            env=self._env,
            timeout=self._remaining(self.bounds.export_s),
        )
        self.report.exporter_invocations += 1
        if result.returncode != 0:
            raise SmokeFailure(
                phase, "export_command_failed", f"exit {result.returncode}"
            )
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise SmokeFailure(
                phase, "unexpected_export_counters", "report not JSON"
            ) from None
        counts = parsed.get("counts") if isinstance(parsed, dict) else None
        if not isinstance(counts, dict) or counts != EXPECTED_CLI_COUNTS:
            raise SmokeFailure(
                phase, "unexpected_export_counters", "exact counters mismatch"
            )
        summary = {
            "ordinal": ordinal,
            "exit_code": result.returncode,
            **counts,
            "metadata_only": True,
        }
        self.report.cli_exports.append(summary)
        self.report.phases[phase] = {"status": "PASS", **summary}
        return summary

    def _api_proof(self, api, expected, baseline, previous, ordinal):
        phase = f"api_proof_{ordinal}"
        self._enter_phase(phase)
        wanted = {
            "traces": {expected["trace"]},
            "generations": {expected["generation"]},
            "scores": {expected["score"]},
        }
        snapshot = self._poll_until_exact(api, wanted, phase)
        counts = {
            kind: snapshot[kind]["count"]
            for kind in ("traces", "generations", "scores")
        }
        current_ids = {
            kind: sorted(snapshot[kind]["ids"])
            for kind in ("traces", "generations", "scores")
        }
        if snapshot["observations_total"] != counts["generations"]:
            raise SmokeFailure(
                phase, "api_object_proof_failed", "extra non-generation observations"
            )
        self._direct_objects(api, expected, phase)
        reference = baseline if previous is None else previous
        deltas = {kind: counts[kind] - reference["counts"][kind] for kind in counts}
        if ordinal == 2:
            if previous is None or current_ids != previous["ids"]:
                raise SmokeFailure(
                    phase,
                    "api_object_proof_failed",
                    "ID sets changed after second export",
                )
            self.report.api_proof.update(
                {
                    "second_export_counts": counts,
                    "second_export_ids": current_ids,
                    "second_export_deltas": deltas,
                    "id_sets_identical": True,
                    "no_growth": all(value == 0 for value in deltas.values()),
                    "metadata_only": True,
                    "http_status_not_novelty_evidence": True,
                }
            )
        else:
            self.report.api_proof.update(
                {
                    "first_export_counts": counts,
                    "first_export_ids": current_ids,
                    "first_export_deltas": deltas,
                    "expected_ids": dict(expected),
                }
            )
        self.report.phases[phase] = {
            "status": "PASS",
            "counts": counts,
            "deltas": deltas,
        }
        return {"counts": counts, "ids": current_ids}

    def _poll_until_exact(self, api, wanted, phase):
        interval = max(1, self.bounds.poll_interval_s)
        max_attempts = min(
            self.bounds.poll_max_attempts, max(1, self.bounds.api_poll_s // interval)
        )
        for attempt in range(1, max_attempts + 1):
            snapshot = self._read(api, "snapshot")
            exact = all(
                snapshot[kind]["count"] == len(ids_wanted)
                and set(snapshot[kind]["ids"]) == ids_wanted
                for kind, ids_wanted in wanted.items()
            )
            if exact:
                return snapshot
            left = min(self._phase_deadline, self._deadline) - time.monotonic()
            if left <= 0:
                raise SmokeFailure(
                    phase,
                    "global_deadline_exceeded",
                    "phase/global budget exhausted while polling",
                )
            self.deps.sleeper(min(self.bounds.poll_interval_s, left))
        raise SmokeFailure(
            phase,
            "api_object_proof_failed",
            "counts/IDs did not converge within budget",
        )

    def _direct_objects(self, api, expected, phase):
        def bad(reason):
            return SmokeFailure(phase, "api_object_proof_failed", reason)

        trace = self._read(api, "trace", expected["trace"])
        metadata = trace.get("metadata")
        if trace.get("id") != expected["trace"] or not isinstance(metadata, dict):
            raise bad("trace identity or metadata missing")
        if [o.get("id") for o in trace.get("observations", [])] != [
            expected["generation"]
        ]:
            raise bad("trace observation relation mismatch")
        if [s.get("id") for s in trace.get("scores", [])] != [expected["score"]]:
            raise bad("trace score relation mismatch")
        required = ("tournament_id", "condition", "seed", "status", "artifact_digests")
        if any(key not in metadata for key in required) or set(metadata) & {
            "raw_visible_response",
            "input",
            "output",
        }:
            raise bad("trace metadata allowlist violation")
        digests = metadata["artifact_digests"]
        if not isinstance(digests, dict) or "core_receipt" not in digests:
            raise bad("trace artifact digests missing")
        generation = self._read(api, "observation", expected["generation"])
        if (
            generation.get("id") != expected["generation"]
            or generation.get("type") != "GENERATION"
            or generation.get("traceId") != expected["trace"]
            or not generation.get("model")
            or any(generation.get(field) is not None for field in ("input", "output"))
        ):
            raise bad("generation identity/type/text-field violation")
        score = self._read(api, "score", expected["score"])
        if (
            score.get("id") != expected["score"]
            or score.get("traceId") != expected["trace"]
            or score.get("name") != "verified_score"
            or score.get("dataType") != "NUMERIC"
        ):
            raise bad("score identity/metadata violation")

    def _diagnostics(self):
        if not self.report.images:
            try:
                inventory = (
                    self.deps.inventory_factory(self._base, self._env)
                    if self.deps.inventory_factory
                    else self.deps.inventory
                )
                self.report.images = inventory.collect(
                    timeout=lambda: self._finalization_remaining(30)
                )
            except CredentialExposureError as exc:
                self.report.primary(exc)
                self.report.images = unavailable_inventory("inventory_exposure")
            except Exception as exc:  # noqa: BLE001 - six records are mandatory
                self.report.images = unavailable_inventory(
                    f"inventory_failed_{type(exc).__name__}"
                )
        try:
            ps = self.deps.runner.run(
                [*self._base, "ps", "-a"],
                env=self._env,
                timeout=self._finalization_remaining(self.bounds.diagnostics_s),
            )
            logs = self.deps.runner.run(
                [
                    *self._base,
                    "logs",
                    "--no-color",
                    "--tail",
                    "80",
                    "langfuse-web",
                    "langfuse-worker",
                ],
                env=self._env,
                timeout=self._finalization_remaining(self.bounds.diagnostics_s),
            )
            if ps.returncode or logs.returncode:
                raise SmokeFailure(
                    "diagnostics",
                    "diagnostic_capture_failed",
                    f"exit ps={ps.returncode} logs={logs.returncode}",
                )
            guard = self.deps.guard
            self.report.phases["diagnostics"] = {
                "status": "PASS",
                "ps_tail": guard.redact(ps.stdout)[-2000:],
                "logs_tail": guard.redact(logs.stdout)[-2000:],
            }
        except CredentialExposureError as exc:
            self.report.primary(exc)
            self.report.phases["diagnostics"] = {
                "status": "incomplete",
                "reason": type(exc).__name__,
            }
        except Exception as exc:  # noqa: BLE001 - diagnostics never abort cleanup
            self.report.phases["diagnostics"] = {
                "status": "incomplete",
                "reason": type(exc).__name__,
            }

    def _cleanup(self):
        manager = self._cleanup_impl
        if manager is None:
            # Preflight never established the compose identity; the allocated
            # port callback (if any) still validates release explicitly.
            manager = self._cleanup_manager(self._port_free or (lambda: True))
        try:
            execute_kwargs: dict[str, Any] = {
                "down_timeout": lambda: self._finalization_remaining(
                    self.bounds.cleanup_s
                ),
                "query_timeout": lambda: self._finalization_remaining(30),
            }
            if isinstance(manager, CleanupManager):
                execute_kwargs["port_timeout"] = lambda: (
                    self._finalization_sleep_remaining(self.bounds.cleanup_s)
                )
            return manager.execute(**execute_kwargs)
        except Exception as exc:  # noqa: BLE001 - cleanup failure is reported
            return [f"cleanup execution error: {type(exc).__name__}"]


def run_smoke(config):
    """Build the real injectable seams, run the smoke, return report and text."""
    guard = LeakGuard(default_sink(config.source_env))
    runner = CommandRunner(
        guard,
        cwd=REPO_ROOT,
        base_env={
            key: value
            for key, value in config.source_env.items()
            if key in ALLOWED_ENV_KEYS
        },
    )
    deps = SmokeDeps(
        runner=runner,
        guard=guard,
        api_factory=lambda url, public_key, secret_key: LangfuseApi(
            url, public_key, secret_key, timeout=config.bounds.http_timeout_s
        ),
        sleeper=time.sleep,
        port_is_free=None,
        web_probe=None,
        cleanup_factory=lambda base, port_free, env: CleanupManager(
            runner,
            base,
            port_free,
            down_timeout=config.bounds.cleanup_s,
            env=env,
            sleeper=time.sleep,
            port_poll_s=min(5, config.bounds.cleanup_s),
        ),
        inventory_factory=lambda base, env: ImageInventory(runner, base, env=env),
    )
    orchestrator = SmokeOrchestrator(config, deps)
    report = orchestrator.run().finalize()
    text = orchestrator.report.emit_text(report)
    if config.report_path is not None:
        _write_scanned_report(orchestrator.report.guard, config.report_path, text)
    return report, text
