"""Declarative fault value objects: the resolved tape and its decisions (REQ-05).

A Fault is the RESOLVED declarative fault — exactly {fault_id, operation_key,
hook, kind} — whose concrete operation key is produced by the two-stage
resolution in faults/tape.py (locked decision 4: semantic descriptor first,
concrete ``op/<logical_run_id>/step-<decimal>/intent-<token>`` expansion after
``logical_run_id`` derivation). All value objects are frozen; the tape's
``identity`` is the canonical digest of its own mapping (a property, not a
stored field, so it can never drift), making identity deterministic and
execution-independent. This module knows nothing about runtime objects: the
injector matches by semantic hook plus concrete operation key and has no API
that could mutate WorldState or grants.
"""
from dataclasses import dataclass

from robot_factory_arena.contracts.outcomes import SUPPORTED_TOOLS
from robot_factory_arena.evidence.canonical import digest_json

FAULT_ID_RESPONSE_LOST = "response_lost_after_commit"
HOOK_AFTER_COMMIT_BEFORE_RESPONSE = "after_commit_before_response"
KIND_RESPONSE_LOST = "response_lost"

# Closed v0 sets: no before-commit fault exists in v0 (REQ-05).
FAULT_HOOKS = (HOOK_AFTER_COMMIT_BEFORE_RESPONSE,)
FAULT_KINDS = (KIND_RESPONSE_LOST,)


@dataclass(frozen=True)
class Fault:
    """The resolved, persisted declarative fault (exact REQ-05 shape)."""

    fault_id: str
    operation_key: str
    hook: str
    kind: str

    def __post_init__(self):
        if not isinstance(self.fault_id, str) or not self.fault_id:
            raise ValueError("fault_id must be a non-empty str")
        if not isinstance(self.operation_key, str) or not self.operation_key:
            raise ValueError("operation_key must be a non-empty str")
        if self.hook not in FAULT_HOOKS:
            raise ValueError(f"hook must be one of {FAULT_HOOKS}")
        if self.kind not in FAULT_KINDS:
            raise ValueError(f"kind must be one of {FAULT_KINDS}")

    def to_mapping(self):
        return {
            "fault_id": self.fault_id,
            "hook": self.hook,
            "kind": self.kind,
            "operation_key": self.operation_key,
        }


@dataclass(frozen=True)
class FaultTape:
    """An ordered, immutable resolved tape; ``identity`` is derived."""

    schema_version: int
    faults: tuple[Fault, ...]

    def __post_init__(self):
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise ValueError("schema_version must be 1")
        if not isinstance(self.faults, tuple) or not self.faults:
            raise ValueError("a resolved tape carries at least one Fault")
        for fault in self.faults:
            if not isinstance(fault, Fault):
                raise TypeError("faults must contain only Fault values")

    @property
    def identity(self) -> str:
        """Deterministic tape identity over the canonical resolved mapping."""
        return digest_json(self.to_mapping())

    def to_mapping(self):
        return {
            "schema_version": self.schema_version,
            "faults": [fault.to_mapping() for fault in self.faults],
        }


@dataclass(frozen=True)
class FaultDecision:
    """The outcome of one injector hook consultation.

    ``fired`` exactly when the decision carries its Fault; a quiet decision
    carries None so callers cannot mistake it for a fault result.
    """

    fired: bool
    fault: Fault | None

    def __post_init__(self):
        if not isinstance(self.fired, bool):
            raise TypeError("fired must be a bool")
        if self.fired:
            if not isinstance(self.fault, Fault):
                raise ValueError("a fired decision carries its Fault")
        elif self.fault is not None:
            raise ValueError("a non-fired decision carries no Fault")


@dataclass(frozen=True)
class FaultContext:
    """The read-only operation facts a hook consults (no state reference)."""

    operation_key: str
    tool: str

    def __post_init__(self):
        if not isinstance(self.operation_key, str) or not self.operation_key:
            raise ValueError("operation_key must be a non-empty str")
        if self.tool not in SUPPORTED_TOOLS:
            raise ValueError(f"tool must be one of {SUPPORTED_TOOLS}")
