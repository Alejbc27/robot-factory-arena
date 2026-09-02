"""External provider evidence: secret gate and sidecar serialization (P4A-REQ-05/07).

Sidecars live ONLY under ``<run_dir>/llm/`` as an external envelope next to —
never inside or merged into — the core artifacts: ``provider-run.json`` (one
ordered entry per LLM decision attempt, exact ProviderResult evidence fields,
the three exact normalized tool-call shapes or null) and ``llm-receipt.json``
(five digest bindings: prompt, tool schema, provider evidence, unchanged
sibling core receipt, and the non-self-referential LLM receipt digest).

Every export boundary is scrubbed FIRST and then fail-closed: any string that
still matches an inline credential form or an environment-looking assignment
with a secret-bearing NAME aborts serialization with the deterministic error
``secret_detected`` — and the rejected value is never serialized, logged, or
embedded in an error message. Canonical serialization mirrors the frozen core
authority byte-for-byte (UTF-8, compact separators, sorted keys, explicit
nulls, integers only) with a local stdlib implementation because this package
MUST NOT import ``robot_factory_arena.evidence``.
"""
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

SCHEMA_VERSION = 1
REDACTED = "[REDACTED]"
SECRET_DETECTED = "secret_detected"

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")
_SHELF_ID_RE = re.compile(r"shelf-(0[0-9])")
_ITEM_ID_RE = re.compile(r"item-(0[0-9])")
_ORDER_ID_RE = re.compile(r"order-(0[0-9])")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

# Field names whose VALUES are replaced wholesale (P4A-REQ-07 rule 1).
_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "token",
        "secret",
        "password",
        "credential",
    }
)
_SENSITIVE_SUFFIXES = ("_key", "_token", "_secret", "_password", "_credential")

# Inline credential forms inside strings (rule 2), case-insensitive.
_INLINE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"bearer\s+\S+",
        r"api[_-]?key=\S+",
        r"token=\S+",
        r"secret=\S+",
        r"sk-\S+",
    )
)
# Residual environment-looking assignment (rule 3): NAME=value where NAME
# contains KEY/TOKEN/SECRET/PASSWORD/AUTH/CREDENTIAL.
_ENV_ASSIGNMENT_RE = re.compile(
    r"[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL)[A-Za-z0-9_]*"
    r"\s*=\s*\S+",
    re.IGNORECASE,
)


class SecretDetectedError(ValueError):
    """A residual secret survived scrubbing; the message names no value."""

    def __init__(self, label: str = "provider evidence"):
        self.label = label
        super().__init__(f"{SECRET_DETECTED}: {label}")


def _is_sensitive_field(name) -> bool:
    if not isinstance(name, str):
        return False
    lowered = name.lower()
    return (
        lowered in _SENSITIVE_FIELDS
        or any(lowered.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)
    )


def _scrub_text(text: str) -> str:
    for pattern in _INLINE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def scrub(value: object) -> object:
    """Recursively redact sensitive field values and inline credentials.

    Container shapes are preserved: a mapping in, a mapping out; a list in, a
    list out. Strings are returned with inline credential forms redacted;
    other scalars pass through unchanged.
    """
    if isinstance(value, Mapping):
        return {
            key: REDACTED
            if _is_sensitive_field(key)
            else scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def assert_scrubbed(value: object, label: str = "provider evidence") -> None:
    """Fail closed when any string still looks like a credential (rule 3)."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            assert_scrubbed(item, f"{label}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_scrubbed(item, f"{label}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _INLINE_PATTERNS:
            if pattern.search(value) is not None:
                raise SecretDetectedError(label)
        if _ENV_ASSIGNMENT_RE.search(value) is not None:
            raise SecretDetectedError(label)


def _validate_no_floats(value) -> None:
    """Canonical JSON admits integers only — no float may cross the boundary."""
    if isinstance(value, float):
        raise TypeError("canonical JSON rejects floating-point values")
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_no_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_no_floats(item)


def canonical_json(value) -> str:
    """Byte-compatible mirror of the frozen core canonical serializer."""
    _validate_no_floats(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_bytes(value) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_json(value) -> str:
    return sha256_digest(canonical_json_bytes(value))


def _validate_logical_run_id(value) -> str:
    if not isinstance(value, str) or _LOGICAL_RUN_ID_RE.fullmatch(value) is None:
        raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
    return value


def _validate_execution_id(value) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("execution_id must be a non-empty string")
    return value


def _conforming_tool_call(tool_call):
    """The three exact normalized shapes (P4A-REQ-02) or None."""
    if tool_call is None:
        return None
    tool = getattr(tool_call, "tool", None)
    arguments = getattr(tool_call, "arguments", None)
    if not isinstance(tool, str) or not isinstance(arguments, Mapping):
        return None
    if tool == "move":
        if set(arguments) != {"direction"}:
            return None
        if arguments["direction"] not in ("north", "east", "south", "west"):
            return None
    elif tool == "pick":
        if set(arguments) != {"shelf_id", "item_id"}:
            return None
        if (
            not isinstance(arguments["shelf_id"], str)
            or _SHELF_ID_RE.fullmatch(arguments["shelf_id"]) is None
        ):
            return None
        if (
            not isinstance(arguments["item_id"], str)
            or _ITEM_ID_RE.fullmatch(arguments["item_id"]) is None
        ):
            return None
    elif tool == "place":
        if set(arguments) != {"order_id"}:
            return None
        if (
            not isinstance(arguments["order_id"], str)
            or _ORDER_ID_RE.fullmatch(arguments["order_id"]) is None
        ):
            return None
    else:
        return None
    return {"tool": tool, "arguments": dict(arguments)}


def _decision_entry(record) -> dict:
    """One exact provider-run entry from one retained decision attempt."""
    result = record.result
    error = result.error
    return {
        "policy_step": record.policy_step,
        "provider": result.provider,
        "model": result.model,
        "request_id": record.request_id,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
        },
        "latency_ms": result.latency_ms,
        "raw_visible_response": result.raw_visible_response,
        "tool_call": _conforming_tool_call(result.tool_call),
        "error": None
        if error is None
        else {"code": error.code, "message": error.message},
    }


def build_provider_run(logical_run_id, execution_id, records) -> dict:
    """The exact provider-run envelope; one ordered entry per attempt."""
    _validate_logical_run_id(logical_run_id)
    _validate_execution_id(execution_id)
    entries = [
        _decision_entry(record)
        for record in sorted(records, key=lambda record: record.policy_step)
    ]
    identities = {(entry["provider"], entry["model"]) for entry in entries}
    if len(identities) > 1:
        raise ValueError("decision records disagree on the pinned provider/model")
    return {
        "schema_version": SCHEMA_VERSION,
        "logical_run_id": logical_run_id,
        "execution_id": execution_id,
        "requests": entries,
    }


def build_llm_receipt(
    logical_run_id,
    execution_id,
    provider,
    model,
    prompt_digest,
    tool_schema_digest,
    provider_run,
    core_receipt_bytes,
) -> dict:
    """The exact llm-receipt envelope with all five digest bindings."""
    _validate_logical_run_id(logical_run_id)
    _validate_execution_id(execution_id)
    for name, value in (("provider", provider), ("model", model)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
    for name, value in (
        ("prompt_digest", prompt_digest),
        ("tool_schema_digest", tool_schema_digest),
    ):
        if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
            raise ValueError(f"{name} must be a sha256:<64 hex> digest")
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "logical_run_id": logical_run_id,
        "execution_id": execution_id,
        "provider": provider,
        "model": model,
        "prompt_digest": prompt_digest,
        "tool_schema_digest": tool_schema_digest,
        "provider_evidence_digest": digest_json(provider_run),
        "core_receipt_digest": sha256_digest(core_receipt_bytes),
    }
    # NOT self-referential: the digest covers the receipt WITHOUT itself.
    receipt["llm_receipt_digest"] = digest_json(receipt)
    return receipt


def write_provider_artifacts(
    run_directory,
    logical_run_id,
    execution_id,
    prompt_digest,
    tool_schema_digest,
    records,
    core_receipt_path,
) -> dict:
    """Scrub, verify, and ONLY THEN write both sidecars under ``llm/``.

    Fail-closed: any residual secret raises ``SecretDetectedError`` BEFORE the
    directory is created or a single byte is written. Returns the llm receipt
    mapping.
    """
    # scrub preserves the container shape: dict in, dict out.
    provider_run = cast(
        dict, scrub(build_provider_run(logical_run_id, execution_id, records))
    )
    assert_scrubbed(provider_run, "provider-run.json")
    core_receipt_bytes = Path(core_receipt_path).read_bytes()
    entries = provider_run["requests"]
    provider = entries[0]["provider"] if entries else None
    model = entries[0]["model"] if entries else None
    if not provider or not model:
        raise ValueError("provider evidence requires at least one decision")
    llm_receipt = cast(
        dict,
        scrub(
            build_llm_receipt(
                logical_run_id,
                execution_id,
                provider,
                model,
                prompt_digest,
                tool_schema_digest,
                provider_run,
                core_receipt_bytes,
            )
        ),
    )
    assert_scrubbed(llm_receipt, "llm-receipt.json")
    llm_directory = Path(run_directory) / "llm"
    llm_directory.mkdir(parents=True, exist_ok=False)
    (llm_directory / "provider-run.json").write_bytes(
        canonical_json_bytes(provider_run)
    )
    (llm_directory / "llm-receipt.json").write_bytes(
        canonical_json_bytes(llm_receipt)
    )
    return llm_receipt
