"""External LLM provider envelope (Phase 4A, P4A-REQ-01).

Standard-library-only provider adapters, external provider evidence, and the
static LLM appendix. This package MUST NOT import ``robot_factory_arena.domain``,
``.runtime``, ``.evidence``, or ``.verification`` — an AST boundary test
enforces it (P4A-REQ-09). Canonical-JSON/digest duties that interoperate with
the frozen core live in the policy layer (``policies/llm.py``), which MAY use
the canonical digest authority, and in ``providers.evidence`` as a local
stdlib re-implementation for the external sidecars.
"""
