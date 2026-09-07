"""Isolated evidence-to-Langfuse observability foundation (stdlib only).

Slices A1+A2a+A2b of Unit A (``stage4-langfuse-tracing``): the sealed evidence
validation contract — canonical JSON/digest rules, secret policy, stable skip
reasons, digest/identity binding, deterministic run discovery, the
leaderboard/launch loaders with authority rules — plus the immutable validated
model: guarded text, authoritative row assembly, and metadata-only trace
records. Runtime imports never touch ``robot_factory_arena``, ``harness``, or
third-party packages.
"""

from . import ids, model
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
from .model import assemble_items, build_trace_records, plain
from .secrets import SECRET_PATTERNS, find_secret

__all__ = (
    "ArtifactError",
    "REASONS",
    "SECRET_PATTERNS",
    "SkipRecord",
    "assemble_items",
    "build_trace_records",
    "canonical_json_bytes",
    "digest_json",
    "discover_evidence",
    "find_secret",
    "ids",
    "load_launch",
    "load_leaderboard",
    "model",
    "plain",
    "sha256_digest",
)
