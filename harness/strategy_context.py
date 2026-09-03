"""Canonical StrategyContext: freeze, digest, and audit-chain verification.

Byte-compatible mirror of the core canonical-JSON authority (sorted keys,
compact separators, UTF-8, no trailing newline) for external artifact
validation only; this package never imports ``robot_factory_arena``.

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


def canonical_json_bytes(value):
    """Byte-compatible mirror of the core canonical-JSON authority."""
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


def _ordinal(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require(condition, message):
    if not condition:
        raise ContextError(message)


def validate_context(value):
    """Design §2.1: canonical ``null`` or the exact schema-versioned object.

    Compact foundation form; Unit 5 owns the full per-field TRIANGULATE pass.
    """
    if value is None:
        return
    _require(isinstance(value, dict) and set(value) == _TOP_KEYS, "context keys")
    _require(
        value["schema_version"] == versions.CONTEXT_SCHEMA_VERSION, "schema_version"
    )
    _require(_nonempty(value["source_tournament_id"]), "source_tournament_id")
    for records, keys in (
        (value["guidance"], _GUIDANCE_KEYS),
        (value["discoveries"], _DISCOVERY_KEYS),
    ):
        _require(isinstance(records, list), "context lists")
        for record in records:
            _require(
                isinstance(record, dict) and set(record) == keys, "record exact keys"
            )
            _require(_nonempty(record.get("text", "")), "non-empty text")
            for field in (
                "guidance_ordinal",
                "discovery_ordinal",
                "producing_attempt_ordinal",
            ):
                if field in record:
                    _require(_ordinal(record[field]), f"{field} must be a natural int")
    for record in value["discoveries"]:
        evidence = record["evidence"]
        _require(
            isinstance(evidence, dict) and set(evidence) == _EVIDENCE_KEYS,
            "evidence exact keys",
        )
        _require(
            evidence["run_status"] == "finished",
            "discovery evidence must cite a finished run",
        )
        _require(
            bool(DIGEST_RE.match(evidence["core_receipt_digest"]))
            and bool(DIGEST_RE.match(evidence["llm_receipt_digest"])),
            "evidence digest syntax",
        )


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


def verify_context_chain(path, launch_meta, core_config, receipt):
    """Fail-closed verification of the full audit chain.

    Links: frozen snapshot bytes -> recorded digest; CLI-reported
    strategy_context_digest -> frozen snapshot; run-config policy_digest ->
    receipt policy_digest; receipt -> logical_run_id.
    """
    data = Path(path).read_bytes()
    actual = sha256_digest(data)
    _require(
        actual == launch_meta["context_digest"],
        f"context snapshot mutated after hashing: {path}",
    )
    reported = launch_meta.get("reported_digest")
    if reported is not None:
        try:
            value = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ContextError(f"frozen snapshot is not JSON: {path}") from exc
        _require(
            reported == ("null" if value is None else actual),
            "CLI-reported strategy_context_digest disagrees with the frozen snapshot",
        )
    policy_digest = core_config.get("policy_digest")
    _require(
        isinstance(policy_digest, str) and DIGEST_RE.match(policy_digest),
        "run config policy_digest missing or malformed",
    )
    _require(
        policy_digest == receipt.get("policy_digest"),
        "receipt policy_digest disagrees with the run config",
    )
    _require(_nonempty(receipt.get("logical_run_id")), "receipt logical_run_id")


def assert_identity_separation(digest_to_policy):
    """One context digest must bind exactly one policy_digest: context identity
    drives policy identity, so collisions or splits break comparability."""
    for context_digest, policy_digests in digest_to_policy.items():
        _require(DIGEST_RE.match(context_digest), "context digest syntax")
        _require(
            len(policy_digests) == 1,
            f"context {context_digest} bound to multiple policy digests",
        )
