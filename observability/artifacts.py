"""Isolated canonical/digest/binding contract over sealed evidence.

Byte-compatible re-implementation of the sealed evidence-loader rules:
UTF-8 JSON, sorted keys, compact separators, ``ensure_ascii=False``,
preserved explicit nulls and array order, integers-only numbers, and
``sha256:<64 lowercase hex>`` digests over exact canonical bytes. This
module never imports ``robot_factory_arena``, ``harness``, or any
third-party package.

Parity notes (PR #29 review): the canonical rule is deep — floats are
rejected inside tuples too, object keys must be strings, and unknown
types fail closed (``tests/observability/test_canonical_parity.py``
proves identical accept/reject/bytes against the core authority).
Row ``status`` must equal the closed harness derivation over
``declined``/``completed``/``aborted``/``evidence_invalid``; an
observability-level "unresolved" projection, if ever wanted, must be an
explicit projection — never faked as the sealed status. Provider tool
calls must match the writer shape ``{"tool", "arguments"}`` exactly.
"""

import hashlib
import json
import re
from collections.abc import Mapping
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
LEADERBOARD_NAME = "leaderboard.json"
LAUNCH_NAME = "launch.json"

EVIDENCE_STATUSES = frozenset({"bound", "unresolved"})

# Read-only mirror of the closed harness leaderboard rule.
LEADERBOARD_STATUSES = ("declined", "completed", "aborted", "evidence_invalid")

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
_LEADERBOARD_REQUIRED = (
    "schema_version",
    "tournament_id",
    "harness_version",
    "core_version",
    "rows",
)
_ROW_REQUIRED = (
    "condition",
    "seed",
    "status",
    "outcome",
    "verified_score",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "latency_ms",
    "attempts_used",
    "declined_launches",
    "core_receipt_digest",
    "llm_receipt_digest",
    "policy_digest",
    "strategy_context_digest",
    "harness_version",
    "core_version",
    "attempt_refs",
)
_ATTEMPT_REF_REQUIRED = ("attempt_id", "attempt_ordinal", "evidence_status")
_NULLABLE_INT_ROW_FIELDS = (
    "verified_score",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "latency_ms",
)
_NULLABLE_DIGEST_ROW_FIELDS = (
    "core_receipt_digest",
    "llm_receipt_digest",
    "policy_digest",
    "strategy_context_digest",
)
_LAUNCH_REQUIRED = (
    "schema_version",
    "attempt_id",
    "registered_before_start",
    "condition",
    "seed",
    "agent_id",
    "attempt_ordinal",
    "argv",
    "context_path",
    "context_digest",
)


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
    if isinstance(value, (list, tuple)):
        return any(contains_float(v) for v in value)
    return False


def _validate_canonical(value, path):
    """Deep canonical rule: only JSON-safe types, string keys, no floats.

    Mirrors the core authority in isolation: ``None``/``str``/``bool``/``int``
    pass, ``float`` (finite or not) fails, lists and tuples recurse, mappings
    require string keys and recurse, and every other type fails closed.
    """
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, float):
        raise _err(NON_CANONICAL_JSON, "floats and NaN are not canonical", path)
    if isinstance(value, int):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_canonical(item, path)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _err(
                    NON_CANONICAL_JSON, "canonical object keys must be strings", path
                )
            _validate_canonical(item, path)
        return
    raise _err(
        NON_CANONICAL_JSON,
        f"unsupported value for canonical JSON: {type(value).__name__}",
        path,
    )


def canonical_json_bytes(value):
    """Canonical UTF-8 bytes: sorted keys, compact, explicit nulls preserved."""
    _validate_canonical(value, "<value>")
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


def _validate_tool_call(tool_call, position, path):
    """The exact writer shape (P4A-REQ-02): ``{"tool", "arguments"}`` only.

    The sealed writer emits exactly these two keys — ``tool`` a non-empty
    action name, ``arguments`` an object. Anything else (empty objects,
    name-keyed shapes, extra keys) is not evidence this contract can speak
    and fails closed instead of exploding later in the model layer.
    """
    if set(tool_call) != {"tool", "arguments"}:
        raise _err(
            UNSUPPORTED_SCHEMA,
            f"request {position} tool_call must hold exactly tool and arguments",
            path,
        )
    if not _nonempty(tool_call["tool"]):
        raise _err(
            UNSUPPORTED_SCHEMA,
            f"request {position} tool_call tool must be a non-empty string",
            path,
        )
    if not isinstance(tool_call["arguments"], dict):
        raise _err(
            UNSUPPORTED_SCHEMA,
            f"request {position} tool_call arguments must be an object",
            path,
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
        tool_call = request.get("tool_call")
        if tool_call is not None:
            _validate_tool_call(tool_call, position, path)


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
    if (
        run_dir.parent.name != receipt["logical_run_id"]
        or run_dir.name != receipt["execution_id"]
    ):
        raise _err(
            IDENTITY_MISMATCH,
            "core/runs path components disagree with the sealed receipt",
            run_dir / RECEIPT_NAME,
        )
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


def leaderboard_path(root):
    return Path(root) / LEADERBOARD_NAME


def launch_path(root, attempt_id):
    return Path(root) / "attempts" / attempt_id / LAUNCH_NAME


def _derived_row_status(row):
    """Isolated re-derivation of the closed harness leaderboard rule.

    No attempt refs -> ``declined``; all refs bound + terminal outcome
    ``completed`` -> ``completed``; all refs bound + any other outcome ->
    ``aborted``; anything else -> ``evidence_invalid``.
    """
    refs = row["attempt_refs"]
    if not refs:
        return "declined"
    if all(ref["evidence_status"] == "bound" for ref in refs):
        return "completed" if row["outcome"] == "completed" else "aborted"
    return "evidence_invalid"


def _validate_row(row, path, document):
    missing = [key for key in _ROW_REQUIRED if key not in row]
    if missing:
        raise _err(MISSING_FIELD, f"row missing fields {missing}", path)
    if not _nonempty(row["condition"]) or not _nonempty(row["seed"]):
        raise _err(
            UNSUPPORTED_SCHEMA, "row condition/seed must be non-empty strings", path
        )
    if not _nonempty(row["status"]):
        raise _err(UNSUPPORTED_SCHEMA, "row status must be a non-empty string", path)
    if row["outcome"] is not None and row["outcome"] not in OUTCOMES:
        raise _err(
            UNSUPPORTED_SCHEMA, "row outcome must be null or a core outcome", path
        )
    for key in _NULLABLE_INT_ROW_FIELDS:
        if row[key] is not None:
            _index(row[key], f"row {key}", path)
    for key in ("attempts_used", "declined_launches"):
        _index(row[key], f"row {key}", path)
    for key in _NULLABLE_DIGEST_ROW_FIELDS:
        if row[key] is not None and not _is_digest(row[key]):
            raise _err(
                UNSUPPORTED_SCHEMA, f"row {key} must be null or a sha256 digest", path
            )
    if (row["core_receipt_digest"] is None) != (row["llm_receipt_digest"] is None):
        raise _err(
            UNSUPPORTED_SCHEMA,
            "row core/llm receipt digests must be bound or null together",
            path,
        )
    bound = row["core_receipt_digest"] is not None
    if not bound:
        claimed = [key for key in ("outcome", "verified_score") if row[key] is not None]
        claimed += [
            key
            for key in ("input_tokens", "output_tokens", "total_tokens", "latency_ms")
            if row[key] is not None
        ]
        claimed += [
            key
            for key in ("policy_digest", "strategy_context_digest")
            if row[key] is not None
        ]
        if claimed:
            raise _err(
                UNSUPPORTED_SCHEMA,
                f"unbound row claims receipt-owned values {claimed}",
                path,
            )
    elif row["outcome"] is None or row["verified_score"] is None:
        raise _err(
            UNSUPPORTED_SCHEMA,
            "bound row must declare receipt-owned outcome and score",
            path,
        )
    refs = row["attempt_refs"]
    if not isinstance(refs, list):
        raise _err(UNSUPPORTED_SCHEMA, "row attempt_refs must be a list", path)
    previous_ordinal = None
    for ref in refs:
        if not isinstance(ref, dict):
            raise _err(UNSUPPORTED_SCHEMA, "attempt ref must be an object", path)
        ref_missing = [key for key in _ATTEMPT_REF_REQUIRED if key not in ref]
        if ref_missing:
            raise _err(MISSING_FIELD, f"attempt ref missing fields {ref_missing}", path)
        if not _nonempty(ref["attempt_id"]):
            raise _err(
                UNSUPPORTED_SCHEMA, "attempt ref attempt_id must be non-empty", path
            )
        ordinal = _index(ref["attempt_ordinal"], "attempt ordinal", path)
        if previous_ordinal is not None and ordinal <= previous_ordinal:
            raise _err(
                UNSUPPORTED_SCHEMA, "attempt refs must be sorted and unique", path
            )
        previous_ordinal = ordinal
        if ref["evidence_status"] not in EVIDENCE_STATUSES:
            raise _err(
                UNSUPPORTED_SCHEMA,
                "attempt ref evidence_status must be bound or unresolved",
                path,
            )
    if row["attempts_used"] != len(refs):
        raise _err(
            UNSUPPORTED_SCHEMA,
            "row attempts_used must equal the attempt_refs length",
            path,
        )
    if bound and not any(ref["evidence_status"] == "bound" for ref in refs):
        raise _err(
            UNSUPPORTED_SCHEMA, "bound row must declare a bound attempt ref", path
        )
    if row["status"] != _derived_row_status(row):
        raise _err(
            UNSUPPORTED_SCHEMA,
            f"row status {row['status']!r} disagrees with the closed "
            f"derivation {_derived_row_status(row)!r}",
            path,
        )
    for key in ("harness_version", "core_version"):
        if not _nonempty(row[key]) or row[key] != document[key]:
            raise _err(
                UNSUPPORTED_SCHEMA,
                f"row {key} must be a non-empty string agreeing with the document",
                path,
            )


def validate_leaderboard(document, path):
    """Row rules: required keys, null-or-digest fields, derived status parity."""
    _check_schema_version(document["schema_version"], "leaderboard", path)
    for key in ("tournament_id", "harness_version", "core_version"):
        if not _nonempty(document[key]):
            raise _err(
                UNSUPPORTED_SCHEMA,
                f"leaderboard {key} must be a non-empty string",
                path,
            )
    rows = document["rows"]
    if not isinstance(rows, list):
        raise _err(UNSUPPORTED_SCHEMA, "leaderboard rows must be a list", path)
    previous_pair = None
    for row in rows:
        if not isinstance(row, dict):
            raise _err(UNSUPPORTED_SCHEMA, "leaderboard row must be an object", path)
        _validate_row(row, path, document)
        pair = (row["condition"], row["seed"])
        if previous_pair is not None and pair <= previous_pair:
            raise _err(
                UNSUPPORTED_SCHEMA,
                "rows must be sorted by unique (condition, seed) pairs",
                path,
            )
        previous_pair = pair


def load_leaderboard(root):
    path = leaderboard_path(root)
    document, _ = read_canonical(path, _LEADERBOARD_REQUIRED)
    validate_leaderboard(document, path)
    return document


def _launch_seed(value, path):
    if isinstance(value, bool):
        raise _err(UNSUPPORTED_SCHEMA, "launch seed must be a string or int", path)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and value >= 0:
        return value
    raise _err(UNSUPPORTED_SCHEMA, "launch seed must be a string or int", path)


def validate_launch(launch, path):
    """Immutable launch registration: required meta, seed, ordinal, digest."""
    _check_schema_version(launch["schema_version"], "launch", path)
    if not _nonempty(launch["attempt_id"]):
        raise _err(UNSUPPORTED_SCHEMA, "launch attempt_id must be non-empty", path)
    registered = launch["registered_before_start"]
    if not isinstance(registered, bool) or not registered:
        raise _err(
            UNSUPPORTED_SCHEMA, "launch registered_before_start must be true", path
        )
    if not _nonempty(launch["condition"]):
        raise _err(UNSUPPORTED_SCHEMA, "launch condition must be non-empty", path)
    _launch_seed(launch["seed"], path)
    if not _nonempty(launch["agent_id"]):
        raise _err(UNSUPPORTED_SCHEMA, "launch agent_id must be non-empty", path)
    _index(launch["attempt_ordinal"], "launch attempt_ordinal", path)
    argv = launch["argv"]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise _err(UNSUPPORTED_SCHEMA, "launch argv must be a list of strings", path)
    if not _nonempty(launch["context_path"]):
        raise _err(UNSUPPORTED_SCHEMA, "launch context_path must be non-empty", path)
    if not _is_digest(launch["context_digest"]):
        raise _err(
            UNSUPPORTED_SCHEMA, "launch context_digest must be a sha256 digest", path
        )


def load_launch(root, attempt_id):
    path = launch_path(root, attempt_id)
    launch, _ = read_canonical(path, _LAUNCH_REQUIRED)
    validate_launch(launch, path)
    return launch


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
