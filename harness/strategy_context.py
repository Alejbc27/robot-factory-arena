"""Canonical StrategyContext: freeze, digest, and audit-chain verification.

Byte-compatible mirror of the core canonical-JSON authority (sorted keys,
compact separators, UTF-8, no trailing newline) for external artifact
validation only; this package never imports ``robot_factory_arena``.

The §2.1 validation below is a DELIBERATE duplicate of the core
AMEND-4A-3 contract (``robot_factory_arena.policies.llm``): harness
isolation forbids importing the core, so every rule is mirrored here and
a parity-test table (tests may import the core) proves the mirror and
the core accept and reject identical samples.

Mandatory audit chain (maintainer acceptance, PR #16 review):
canonical StrategyContext -> SHA-256 -> strategy_context_digest ->
CLI reported digest -> policy_digest -> logical_run_id / receipt.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from . import versions

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GUIDANCE_KEYS = {"guidance_id", "author_id", "guidance_ordinal", "text"}
_DISCOVERY_KEYS = {
    "source_attempt_id",
    "explorer_id",
    "discovery_ordinal",
    "producing_condition",
    "producing_agent_id",
    "producing_seed",
    "producing_attempt_ordinal",
    "evidence",
    "text",
}
_EVIDENCE_KEYS = {"run_status", "core_receipt_digest", "llm_receipt_digest"}
_TOP_KEYS = {"schema_version", "source_tournament_id", "guidance", "discoveries"}


class ContextError(ValueError):
    """A context snapshot or an audit-chain link failed validation."""


def _reject_non_canonical(value):
    """Deep mirror of the core canonical-JSON v0 contract (REQ-07).

    Integers-only: every float is rejected (NaN/Inf included), object keys
    must be strings, and unsupported types fail closed — byte-compatible
    with ``robot_factory_arena.evidence.canonical`` without importing it.
    """
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, float):
        raise ContextError("canonical JSON rejects floating-point values")
    if isinstance(value, int):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_canonical(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContextError("canonical JSON object keys must be strings")
            _reject_non_canonical(item)
        return
    raise ContextError(f"unsupported value for canonical JSON: {type(value).__name__}")


def canonical_json_bytes(value):
    """Byte-compatible mirror of the core canonical-JSON authority."""
    _reject_non_canonical(value)
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


def _nonempty(value):
    return isinstance(value, str) and value != ""


def _natural_int(value, minimum):
    """Mirror of the core ordinal rule: int, never bool, >= minimum."""
    return not isinstance(value, bool) and isinstance(value, int) and value >= minimum


def _require(condition, message):
    if not condition:
        raise ContextError(message)


def validate_context(value):
    """Design §2.1: canonical ``null`` or the exact schema-versioned object.

    Exact mirror of the core ``validate_strategy_context`` (PR #18
    blocker 3): bool-rejecting schema_version, non-empty string identities
    and text, R3-only producing conditions, string seeds, per-field
    ordinal minima (producing_attempt_ordinal >= 1), guidance and
    discovery identity uniqueness, finished-only evidence, and canonical
    digest syntax (fullmatch: no trailing-newline tolerance).
    """
    if value is None:
        return
    if not isinstance(value, dict):
        raise ContextError("context must be JSON null or an object")
    _require(set(value) == _TOP_KEYS, "context keys")
    version = value["schema_version"]
    _require(
        not isinstance(version, bool)
        and isinstance(version, int)
        and version == versions.CONTEXT_SCHEMA_VERSION,
        "schema_version must be exactly the context schema version",
    )
    _require(_nonempty(value["source_tournament_id"]), "source_tournament_id")
    _require(isinstance(value["guidance"], list), "guidance must be a list")
    _require(isinstance(value["discoveries"], list), "discoveries must be a list")
    guidance_identities = set()
    for record in value["guidance"]:
        _require(
            isinstance(record, dict) and set(record) == _GUIDANCE_KEYS,
            "guidance exact keys",
        )
        _require(_nonempty(record["guidance_id"]), "guidance_id")
        _require(_nonempty(record["author_id"]), "author_id")
        _require(_natural_int(record["guidance_ordinal"], 0), "guidance_ordinal")
        _require(_nonempty(record["text"]), "guidance text")
        identity = (record["guidance_ordinal"], record["guidance_id"])
        _require(
            identity not in guidance_identities,
            "guidance identities must be unique",
        )
        guidance_identities.add(identity)
    discovery_identities = set()
    for record in value["discoveries"]:
        _require(
            isinstance(record, dict) and set(record) == _DISCOVERY_KEYS,
            "discovery exact keys",
        )
        _require(_nonempty(record["source_attempt_id"]), "source_attempt_id")
        _require(_nonempty(record["explorer_id"]), "explorer_id")
        _require(_natural_int(record["discovery_ordinal"], 0), "discovery_ordinal")
        _require(
            record["producing_condition"] == "R3",
            "producing_condition must be exactly 'R3'",
        )
        _require(_nonempty(record["producing_agent_id"]), "producing_agent_id")
        _require(_nonempty(record["producing_seed"]), "producing_seed")
        _require(
            _natural_int(record["producing_attempt_ordinal"], 1),
            "producing_attempt_ordinal",
        )
        _require(_nonempty(record["text"]), "discovery text")
        evidence = record["evidence"]
        _require(
            isinstance(evidence, dict) and set(evidence) == _EVIDENCE_KEYS,
            "evidence exact keys",
        )
        _require(
            evidence["run_status"] == "finished",
            "discovery evidence must cite a finished run",
        )
        for name in ("core_receipt_digest", "llm_receipt_digest"):
            digest = evidence[name]
            _require(
                isinstance(digest, str) and bool(DIGEST_RE.fullmatch(digest)),
                f"evidence {name} must be sha256:<64 lowercase hex>",
            )
        identity = (
            record["source_attempt_id"],
            record["explorer_id"],
            record["discovery_ordinal"],
        )
        _require(
            identity not in discovery_identities,
            "discovery identities must be unique",
        )
        discovery_identities.add(identity)


def normalize_context(value):
    """Canonical record order by declared identity; arrival order is never an input."""
    if value is None:
        return None
    validate_context(value)
    return {
        "schema_version": value["schema_version"],
        "source_tournament_id": value["source_tournament_id"],
        "guidance": sorted(
            value["guidance"],
            key=lambda g: (g["guidance_ordinal"], g["guidance_id"]),
        ),
        "discoveries": sorted(
            value["discoveries"],
            key=lambda d: (
                d["source_attempt_id"],
                d["explorer_id"],
                d["discovery_ordinal"],
            ),
        ),
    }


def atomic_write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def freeze_snapshot(value, path):
    """Validate, canonicalize, and atomically write one frozen snapshot (§2.2)."""
    data = canonical_json_bytes(normalize_context(value))
    atomic_write_bytes(path, data)
    return {"path": str(path), "digest": sha256_digest(data)}


def snapshot_digest(path):
    return sha256_digest(Path(path).read_bytes())


def verify_snapshot(path, expected_digest):
    if snapshot_digest(path) != expected_digest:
        raise ContextError(f"context snapshot mutated after hashing: {path}")


def _is_reported_digest(value):
    return value == "null" or (
        isinstance(value, str) and bool(DIGEST_RE.fullmatch(value))
    )


def verify_context_chain(path, launch_meta, binding, core_config, receipt):
    """Fail-closed verification of the full audit chain (PR #18 blocker 1).

    ``binding`` is the MANDATORY post-run record produced by
    :func:`subprocess_adapter.bind_identity`; the chain has no optional
    skip path, so a run whose CLI printed no
    ``strategy_context_digest:`` line can never verify.

    Links: frozen snapshot bytes -> launch-registered digest; binding ->
    registration; CLI-reported strategy_context_digest -> frozen snapshot;
    binding policy_digest -> run config -> receipt; binding
    logical_run_id -> receipt.
    """
    _require(isinstance(binding, dict), "post-run identity binding missing")
    for key in (
        "context_digest",
        "reported_digest",
        "policy_digest",
        "logical_run_id",
    ):
        _require(key in binding, f"post-run binding missing: {key}")
    data = Path(path).read_bytes()
    actual = sha256_digest(data)
    _require(
        actual == launch_meta["context_digest"],
        f"context snapshot mutated after hashing: {path}",
    )
    _require(
        binding["context_digest"] == launch_meta["context_digest"],
        "post-run binding context_digest disagrees with the launch registration",
    )
    reported = binding["reported_digest"]
    _require(
        _is_reported_digest(reported),
        "no CLI-reported strategy_context_digest (the chain requires the binding)",
    )
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ContextError(f"frozen snapshot is not JSON: {path}") from exc
    _require(
        reported == ("null" if value is None else actual),
        "CLI-reported strategy_context_digest disagrees with the frozen snapshot",
    )
    binding_policy = binding["policy_digest"]
    _require(
        isinstance(binding_policy, str) and bool(DIGEST_RE.fullmatch(binding_policy)),
        "binding policy_digest missing or malformed",
    )
    policy_digest = core_config.get("policy_digest")
    _require(
        isinstance(policy_digest, str) and bool(DIGEST_RE.fullmatch(policy_digest)),
        "run config policy_digest missing or malformed",
    )
    _require(
        binding_policy == policy_digest,
        "binding policy_digest disagrees with the run config",
    )
    _require(
        policy_digest == receipt.get("policy_digest"),
        "receipt policy_digest disagrees with the run config",
    )
    _require(_nonempty(binding["logical_run_id"]), "binding logical_run_id empty")
    _require(
        binding["logical_run_id"] == receipt.get("logical_run_id"),
        "binding logical_run_id disagrees with the receipt",
    )


def assert_identity_separation(digest_to_policy):
    """One context digest must bind exactly one policy_digest: context identity
    drives policy identity, so collisions or splits break comparability."""
    for context_digest, policy_digests in digest_to_policy.items():
        _require(DIGEST_RE.fullmatch(context_digest), "context digest syntax")
        _require(
            len(policy_digests) == 1,
            f"context {context_digest} bound to multiple policy digests",
        )
