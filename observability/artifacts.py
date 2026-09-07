"""Isolated canonical/digest/binding contract over sealed evidence.

Byte-compatible re-implementation of the sealed evidence-loader rules:
UTF-8 JSON, sorted keys, compact separators, ``ensure_ascii=False``,
preserved explicit nulls and array order, integers-only numbers, and
``sha256:<64 lowercase hex>`` digests over exact canonical bytes. This
module never imports ``robot_factory_arena``, ``harness``, or any
third-party package.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .secrets import find_secret

MISSING_ARTIFACT = "missing_artifact"
MALFORMED_JSON = "malformed_json"
NON_CANONICAL_JSON = "non_canonical_json"
DIGEST_MISMATCH = "digest_mismatch"
IDENTITY_MISMATCH = "identity_mismatch"
MISSING_FIELD = "missing_field"
SECRET_DETECTED = "secret_detected"
UNSUPPORTED_SCHEMA = "unsupported_schema"

REASONS = (
    MISSING_ARTIFACT,
    MALFORMED_JSON,
    NON_CANONICAL_JSON,
    DIGEST_MISMATCH,
    IDENTITY_MISMATCH,
    MISSING_FIELD,
    SECRET_DETECTED,
    UNSUPPORTED_SCHEMA,
)

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")

RECEIPT_NAME = "receipt.json"
PROVIDER_RUN_NAME = "llm/provider-run.json"
LLM_RECEIPT_NAME = "llm/llm-receipt.json"

# Read-only mirror of the core outcome vocabulary.
OUTCOMES = frozenset(
    {"completed", "duplicate_effect", "safe_abort", "unresolved_outcome"}
)

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
_REQUEST_REQUIRED = (
    "policy_step",
    "provider",
    "model",
    "request_id",
    "usage",
    "latency_ms",
    "raw_visible_response",
)
_USAGE_REQUIRED = ("input_tokens", "output_tokens", "total_tokens")


class ArtifactError(ValueError):
    """A sealed artifact is missing, malformed, non-canonical, or invalid."""

    def __init__(self, reason, detail, path):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.path = str(path)


@dataclass(frozen=True)
class SkipRecord:
    """Safe per-item failure: stable reason plus artifact path only."""

    path: str
    reason: str


def _err(reason, detail, path):
    return ArtifactError(reason, detail, path)


def contains_float(value):
    """Integers-only contract: floats (finite, NaN, inf) are never canonical."""
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(contains_float(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_float(v) for v in value)
    return False


def canonical_json_bytes(value):
    """Canonical UTF-8 bytes: sorted keys, compact, explicit nulls preserved."""
    if contains_float(value):
        raise _err(NON_CANONICAL_JSON, "floats and NaN are not canonical", "<value>")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_json(value):
    return sha256_digest(canonical_json_bytes(value))


def _is_digest(value):
    return isinstance(value, str) and bool(DIGEST_RE.fullmatch(value))


def _nonempty(value):
    return isinstance(value, str) and value != ""


def _index(value, label, path):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _err(
            UNSUPPORTED_SCHEMA, f"non-canonical numeric {label}: {value!r}", path
        )
    return value


def read_canonical(path, required):
    """Read one artifact, enforcing canonical bytes, fields, and secrets."""
    path = Path(path)
    if not path.exists():
        raise _err(MISSING_ARTIFACT, "required artifact absent", path)
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise _err(MALFORMED_JSON, "invalid UTF-8 bytes", path) from None
    except json.JSONDecodeError:
        raise _err(MALFORMED_JSON, "unparseable JSON", path) from None
    if not isinstance(value, dict):
        raise _err(MALFORMED_JSON, "top-level JSON value must be an object", path)
    if contains_float(value):
        raise _err(NON_CANONICAL_JSON, "float or NaN number", path)
    if canonical_json_bytes(value) != raw:
        raise _err(NON_CANONICAL_JSON, "bytes are not canonical JSON", path)
    missing = [key for key in required if key not in value]
    if missing:
        raise _err(MISSING_FIELD, f"missing fields {missing}", path)
    secret = find_secret(raw)
    if secret is not None:
        raise _err(SECRET_DETECTED, f"secret pattern {secret!r}", path)
    return value, raw


def _check_schema_version(value, label, path):
    if isinstance(value, bool) or value != 1:
        raise _err(UNSUPPORTED_SCHEMA, f"{label} schema_version must be 1", path)


def validate_receipt(receipt, path):
    _check_schema_version(receipt["schema_version"], "receipt", path)
    logical_run_id = receipt["logical_run_id"]
    if not isinstance(logical_run_id, str) or not LOGICAL_RUN_ID_RE.fullmatch(
        logical_run_id
    ):
        raise _err(
            UNSUPPORTED_SCHEMA, "receipt logical_run_id must be lr_ plus 64 hex", path
        )
    for key in ("execution_id", "seed", "policy_id"):
        if not _nonempty(receipt[key]):
            raise _err(
                UNSUPPORTED_SCHEMA, f"receipt {key} must be a non-empty string", path
            )
    if not _is_digest(receipt["policy_digest"]):
        raise _err(
            UNSUPPORTED_SCHEMA, "receipt policy_digest must be a sha256 digest", path
        )
    if receipt["outcome"] not in OUTCOMES:
        raise _err(
            UNSUPPORTED_SCHEMA, "outcome must be in the core outcome vocabulary", path
        )
    _index(receipt["verified_score"], "verified_score", path)
    verification = receipt["verification"]
    if not isinstance(verification, dict) or verification.get("status") != "valid":
        raise _err(UNSUPPORTED_SCHEMA, "receipt verification is not valid", path)
    if "verified_score" not in verification:
        raise _err(MISSING_FIELD, "verification missing field: verified_score", path)
    _index(verification["verified_score"], "verification verified_score", path)
    if verification["verified_score"] != receipt["verified_score"]:
        raise _err(
            IDENTITY_MISMATCH, "verification score disagrees with the receipt", path
        )


def validate_provider_run(provider_run, path):
    _check_schema_version(provider_run["schema_version"], "provider-run", path)
    requests = provider_run["requests"]
    if not isinstance(requests, list) or not requests:
        raise _err(
            UNSUPPORTED_SCHEMA, "provider-run requires a non-empty requests list", path
        )
    for position, request in enumerate(requests):
        if not isinstance(request, dict):
            raise _err(
                UNSUPPORTED_SCHEMA, f"request {position} must be an object", path
            )
        missing = [key for key in _REQUEST_REQUIRED if key not in request]
        if missing:
            raise _err(
                MISSING_FIELD, f"request {position} missing fields {missing}", path
            )
        _index(request["policy_step"], "policy_step", path)
        for key in ("provider", "model", "request_id"):
            if not _nonempty(request[key]):
                raise _err(
                    UNSUPPORTED_SCHEMA,
                    f"request {position} {key} must be a non-empty string",
                    path,
                )
        usage = request["usage"]
        if not isinstance(usage, dict):
            raise _err(
                UNSUPPORTED_SCHEMA, f"request {position} usage must be an object", path
            )
        for key in _USAGE_REQUIRED:
            if key not in usage:
                raise _err(
                    MISSING_FIELD,
                    f"request {position} usage missing field: {key}",
                    path,
                )
            _index(usage[key], key, path)
        _index(request["latency_ms"], "latency_ms", path)
        visible = request["raw_visible_response"]
        if visible is not None and not isinstance(visible, str):
            raise _err(
                UNSUPPORTED_SCHEMA,
                f"request {position} raw_visible_response must be a string or null",
                path,
            )
        for key in ("tool_call", "error"):
            if request.get(key) is not None and not isinstance(request[key], dict):
                raise _err(
                    UNSUPPORTED_SCHEMA,
                    f"request {position} {key} must be null or an object",
                    path,
                )


def validate_llm_receipt(llm_receipt, path):
    _check_schema_version(llm_receipt["schema_version"], "llm-receipt", path)
    for key in ("logical_run_id", "execution_id", "provider", "model"):
        if not _nonempty(llm_receipt[key]):
            raise _err(
                UNSUPPORTED_SCHEMA,
                f"llm-receipt {key} must be a non-empty string",
                path,
            )
    for key in _LLM_RECEIPT_REQUIRED[5:]:
        if not _is_digest(llm_receipt[key]):
            raise _err(
                UNSUPPORTED_SCHEMA, f"llm-receipt {key} must be a sha256 digest", path
            )


def bind_run(run_dir):
    """Validate one core run and bind its digest/identity chain."""
    run_dir = Path(run_dir)
    receipt, raw = read_canonical(run_dir / RECEIPT_NAME, _RECEIPT_REQUIRED)
    validate_receipt(receipt, run_dir / RECEIPT_NAME)
    provider_run, _ = read_canonical(
        run_dir / PROVIDER_RUN_NAME, _PROVIDER_RUN_REQUIRED
    )
    validate_provider_run(provider_run, run_dir / PROVIDER_RUN_NAME)
    llm_receipt, _ = read_canonical(run_dir / LLM_RECEIPT_NAME, _LLM_RECEIPT_REQUIRED)
    validate_llm_receipt(llm_receipt, run_dir / LLM_RECEIPT_NAME)
    if llm_receipt["core_receipt_digest"] != sha256_digest(raw):
        raise _err(
            DIGEST_MISMATCH, "does not match receipt bytes", run_dir / RECEIPT_NAME
        )
    if llm_receipt["provider_evidence_digest"] != digest_json(provider_run):
        raise _err(
            DIGEST_MISMATCH, "does not match provider-run", run_dir / PROVIDER_RUN_NAME
        )
    without = {k: v for k, v in llm_receipt.items() if k != "llm_receipt_digest"}
    if llm_receipt["llm_receipt_digest"] != digest_json(without):
        raise _err(DIGEST_MISMATCH, "does not recompute", run_dir / LLM_RECEIPT_NAME)
    for other, name in (
        (provider_run, PROVIDER_RUN_NAME),
        (llm_receipt, LLM_RECEIPT_NAME),
    ):
        if (
            other["logical_run_id"] != receipt["logical_run_id"]
            or other["execution_id"] != receipt["execution_id"]
        ):
            raise _err(
                IDENTITY_MISMATCH,
                f"logical_run_id/execution_id disagree across receipt and {name}",
                run_dir / name,
            )
    pinned = (llm_receipt["provider"], llm_receipt["model"])
    for request in provider_run["requests"]:
        if (request["provider"], request["model"]) != pinned:
            raise _err(
                IDENTITY_MISMATCH,
                "provider/model disagree across llm-receipt and requests",
                run_dir / PROVIDER_RUN_NAME,
            )
    return {
        "receipt": receipt,
        "provider_run": provider_run,
        "llm_receipt": llm_receipt,
        "core_receipt_digest": llm_receipt["core_receipt_digest"],
        "provider_evidence_digest": llm_receipt["provider_evidence_digest"],
        "llm_receipt_digest": llm_receipt["llm_receipt_digest"],
        "execution_id": receipt["execution_id"],
        "logical_run_id": receipt["logical_run_id"],
        "run_dir": run_dir,
    }


def discover_evidence(root):
    """Deterministic discovery over sealed core runs.

    Returns ``(run evidence list, skip list)``. Runs are ordered by stable
    item identity ``(logical_run_id, execution_id)``, never filesystem
    traversal order. Every invalid run yields one safe skip record and never
    blocks independent items.
    """
    root = Path(root)
    skips = []
    runs_root = root / "core" / "runs"
    run_dirs = []
    if runs_root.exists():
        for logical in sorted(runs_root.iterdir(), key=lambda p: p.name):
            if not logical.is_dir():
                continue
            for execution in sorted(logical.iterdir(), key=lambda p: p.name):
                if execution.is_dir():
                    run_dirs.append(execution)
    evidence = []
    for run_dir in run_dirs:
        try:
            evidence.append(bind_run(run_dir))
        except ArtifactError as exc:
            skips.append(SkipRecord(exc.path, exc.reason))
    return evidence, skips
