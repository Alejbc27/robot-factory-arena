"""Isolated evidence-to-Langfuse observability foundation (stdlib only).

Slices A1+A2a of Unit A (``stage4-langfuse-tracing``): the sealed evidence
validation contract — canonical JSON/digest rules, secret policy, stable skip
reasons, digest/identity binding, deterministic run discovery, and the
leaderboard/launch loaders with authority rules. Runtime imports never touch
``robot_factory_arena``, ``harness``, or third-party packages.
"""

from .artifacts import (
    REASONS,
    ArtifactError,
    SkipRecord,
    canonical_json_bytes,
    digest_json,
    discover_evidence,
    load_launch,
    load_leaderboard,
    sha256_digest,
)
from . import ids
from .secrets import SECRET_PATTERNS, find_secret

__all__ = (
    "ArtifactError",
    "REASONS",
    "SECRET_PATTERNS",
    "SkipRecord",
    "canonical_json_bytes",
    "digest_json",
    "discover_evidence",
    "find_secret",
    "ids",
    "load_launch",
    "load_leaderboard",
    "sha256_digest",
)
