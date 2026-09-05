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


# --- Fail-closed execution profile (PR #26 review remediation) -------------
#
# The subprocess adapter passes ONLY seed/provider/model/artifacts-root/
# strategy-context to the core, so the core always executes its own locked
# constants — ``robot_factory_arena.cli.GRANTS = ("move", "pick", "place")``
# and ``robot_factory_arena.cli.BUDGET = 100`` — and each provider adapter
# sends exactly one frozen payload. A ``config.json`` declaring anything else
# would freeze an experimental identity that cannot produce the recorded
# score, so validation pins the exact executable profile below and fails
# closed on every other value. Widen these tables ONLY when the core/adapter
# actually executes the new condition.

EXECUTION_BUDGET = 100
EXECUTION_TOOL_GRANTS = ["move", "pick", "place"]
EXECUTION_K_ATTEMPTS = 3

#: Closed generation contract per ``(provider, model)``, mirroring each
#: adapter's actual outbound payload: the OpenAI adapter hardcodes
#: ``reasoning_effort="none"`` for ``gpt-5.6-luna`` and sends no other
#: generation parameter; the fake and GLM adapters send no generation
#: parameters at all (``{}`` = adapter defaults, nothing is sent).
GENERATION_PROFILES = {
    ("fake", "fake-v1"): {},
    ("openai", "gpt-5.6-luna"): {"reasoning_effort": "none"},
    ("glm", "glm-5.3-flash"): {},
}


def _nonempty(value):
    return isinstance(value, str) and value != ""


def validate_config(config):
    missing = [field for field in FROZEN_FIELDS if field not in config]
    if missing:
        raise TournamentError(f"missing frozen fields: {missing}")
    extra = sorted(set(config) - set(FROZEN_FIELDS))
    if extra:
        raise TournamentError(f"unknown config fields: {extra}")
    profile = GENERATION_PROFILES.get((config["provider"], config["model"]))
    if profile is None:
        supported = ", ".join(
            f"{provider}/{model} -> generation {generation!r}"
            for (provider, model), generation in sorted(GENERATION_PROFILES.items())
        )
        raise TournamentError(
            f"no frozen execution profile for provider/model "
            f"{config['provider']!r}/{config['model']!r}; supported "
            f"(closed contract): {supported}"
        )
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
            config["generation"] == profile,
            f"generation settings for {config['provider']!r}/"
            f"{config['model']!r} (the adapter executes exactly "
            f"{profile!r}; any other declared generation settings would "
            f"never be sent to the provider)",
        ),
        (
            config["prompt_policy_version"] == "amend-4a-3/llm-v1",
            "prompt/policy version",
        ),
        (
            config["context_digest_scheme"] == "sha256:canonical-json-v1",
            "StrategyContext digest scheme",
        ),
        (config["budget"] == EXECUTION_BUDGET, "budget (the core executes BUDGET=100)"),
        (
            config["tool_grants"] == EXECUTION_TOOL_GRANTS,
            "tool grants (the core executes exactly ['move', 'pick', 'place'])",
        ),
        (
            config["fault_config"] is None,
            "fault_config (no fault configuration is executed; must be null)",
        ),
        (config["seeds"] == [18, 24, 30], "ordered seeds"),
        (config["conditions"] == ["R1", "R2", "R3"], "conditions"),
        (
            config["k_attempts"] == EXECUTION_K_ATTEMPTS,
            "k_attempts (this release runs exactly K=3)",
        ),
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
