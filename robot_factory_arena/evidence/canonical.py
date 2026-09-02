"""The single canonical JSON and SHA-256 authority (REQ-03, REQ-07).

Every package — domain, runtime, evidence, faults, and the verifier — must
serialize and digest through this module; it is the only intentionally shared
verifier dependency. Rules: lexicographically sorted object keys, compact
separators, UTF-8 bytes, explicit nulls, no injected defaults, arrays only in
their specified semantic order, and numbers restricted to integers —
floating-point values are rejected outright (finite or not) so no float can
enter scoring or digest inputs (REQ-07). JSON artifact files are exact
canonical JSON bytes; JSONL adds exactly one LF after each record.
"""
import hashlib
import json
from collections.abc import Mapping

__all__ = [
    "canonical_json",
    "canonical_json_bytes",
    "sha256_digest",
    "digest_json",
    "canonical_jsonl_line",
]


def _validate(value):
    """Reject anything canonical JSON v0 must not represent (REQ-07)."""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, float):
        # Covers nan/inf too; finite floats are equally forbidden.
        raise TypeError("canonical JSON rejects floating-point values (REQ-07)")
    if isinstance(value, int):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            _validate(item)
        return
    raise TypeError(
        f"unsupported value for canonical JSON: {type(value).__name__}"
    )


def canonical_json(value) -> str:
    """Serialize to the canonical JSON text form."""
    _validate(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_bytes(value) -> bytes:
    """Exact canonical JSON bytes (UTF-8, no trailing newline)."""
    return canonical_json(value).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    """Digest representation ``sha256:<64 lowercase hex>`` (REQ-03)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_json(value) -> str:
    """SHA-256 over the exact canonical JSON bytes of ``value``."""
    return sha256_digest(canonical_json_bytes(value))


def canonical_jsonl_line(record: Mapping) -> bytes:
    """One trace/JSONL record: canonical bytes plus exactly one LF."""
    return canonical_json_bytes(record) + b"\n"
