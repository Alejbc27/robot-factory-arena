"""Immutable tournament config: §5 comparability freeze, seal, drift rejection.

``config.json`` is sealed (canonical, once) before any branch launches; every
frozen field rejects drift, and an unknown core version fails closed.
"""

import json
from pathlib import Path

from . import versions
from .strategy_context import atomic_write_bytes, canonical_json_bytes, sha256_digest

FROZEN_FIELDS = (
    "schema_version",
    "tournament_id",
    "harness_version",
    "core_version",
    "provider",
    "model",
    "generation",
    "prompt_policy_version",
    "context_digest_scheme",
    "budget",
    "tool_grants",
    "fault_config",
    "seeds",
    "conditions",
    "k_attempts",
    "verification_version",
    "evidence_schema_version",
    "artifact_root",
    "context_source_policy",
)


class TournamentError(ValueError):
    """Config invalid, core version unknown, drift, or reseal attempted."""


def _nonempty(value):
    return isinstance(value, str) and value != ""


def _int_at_least(value, minimum):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def validate_config(config):
    missing = [field for field in FROZEN_FIELDS if field not in config]
    if missing:
        raise TournamentError(f"missing frozen fields: {missing}")
    extra = sorted(set(config) - set(FROZEN_FIELDS))
    if extra:
        raise TournamentError(f"unknown config fields: {extra}")
    checks = (
        (config["schema_version"] == versions.CONFIG_SCHEMA_VERSION, "schema_version"),
        (
            _nonempty(config["tournament_id"])
            and config["tournament_id"].startswith("tournament-"),
            "tournament_id",
        ),
        (config["harness_version"] == versions.HARNESS_VERSION, "harness_version"),
        (
            versions.is_known_core_version(config["core_version"]),
            "unknown core_version (fail closed)",
        ),
        (
            _nonempty(config["provider"]) and _nonempty(config["model"]),
            "provider/model",
        ),
        (
            isinstance(config["generation"], dict) and bool(config["generation"]),
            "generation settings",
        ),
        (
            config["prompt_policy_version"] == "amend-4a-3/llm-v1",
            "prompt/policy version",
        ),
        (
            config["context_digest_scheme"] == "sha256:canonical-json-v1",
            "StrategyContext digest scheme",
        ),
        (_int_at_least(config["budget"], 1), "budget"),
        (
            isinstance(config["tool_grants"], list)
            and all(_nonempty(grant) for grant in config["tool_grants"]),
            "tool grants",
        ),
        (
            config["fault_config"] is None or isinstance(config["fault_config"], dict),
            "fault configuration",
        ),
        (config["seeds"] == [18, 24, 30], "ordered seeds"),
        (config["conditions"] == ["R1", "R2", "R3"], "conditions"),
        (_int_at_least(config["k_attempts"], 1), "k_attempts"),
        (_nonempty(config["verification_version"]), "verification_version"),
        (_nonempty(config["evidence_schema_version"]), "evidence_schema_version"),
        (_nonempty(config["artifact_root"]), "artifact_root"),
        (
            config["context_source_policy"] is None
            or isinstance(config["context_source_policy"], dict),
            "context source policy",
        ),
    )
    failed = [name for ok, name in checks if not ok]
    if failed:
        raise TournamentError(f"invalid frozen fields: {failed}")


def _dir(root, tournament_id, artifact_root):
    return Path(root) / artifact_root / tournament_id


def seal(root, config):
    """Seal canonical config.json exactly once, before branch launch (§5)."""
    validate_config(config)
    path = _dir(root, config["tournament_id"], config["artifact_root"]) / "config.json"
    if path.exists():
        raise TournamentError("config.json already sealed for this tournament")
    data = canonical_json_bytes(config)
    atomic_write_bytes(path, data)
    return {"path": path, "config_digest": sha256_digest(data)}


def load_sealed(root, tournament_id, artifact_root="artifacts/tournaments"):
    path = _dir(root, tournament_id, artifact_root) / "config.json"
    try:
        config = json.loads(path.read_bytes())
    except (json.JSONDecodeError, OSError) as exc:
        raise TournamentError(f"sealed config unreadable: {path}") from exc
    validate_config(config)
    return config


def reject_drift(sealed, candidate):
    drifted = [
        field for field in FROZEN_FIELDS if sealed.get(field) != candidate.get(field)
    ]
    if drifted:
        raise TournamentError(f"comparability drift in frozen fields: {drifted}")


def expected_layout(root, tournament_id, artifact_root="artifacts/tournaments"):
    base = _dir(root, tournament_id, artifact_root)
    names = (
        "config.json",
        "contexts",
        "checkpoints",
        "decision-log.jsonl",
        "attempt-log.jsonl",
        "discoveries.jsonl",
        "leaderboard.json",
        "attempts",
        "core",
    )
    return {name: base / name for name in names}


def ensure_layout(root, tournament_id, artifact_root="artifacts/tournaments"):
    layout = expected_layout(root, tournament_id, artifact_root)
    for name in ("contexts", "checkpoints", "attempts", "core"):
        layout[name].mkdir(parents=True, exist_ok=True)
    return layout
