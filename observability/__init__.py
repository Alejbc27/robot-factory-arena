"""Isolated evidence-to-Langfuse observability foundation (stdlib only).

Slice A1 of Unit A (``stage4-langfuse-tracing``): the sealed evidence
validation contract — canonical JSON/digest rules, secret policy, stable
skip reasons, digest/identity binding, and deterministic run discovery.
Runtime imports never touch ``robot_factory_arena``, ``harness``, or
third-party packages.
"""

from .artifacts import (
    REASONS,
    ArtifactError,
    SkipRecord,
    canonical_json_bytes,
    digest_json,
    discover_evidence,
    sha256_digest,
)
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
    "sha256_digest",
)
