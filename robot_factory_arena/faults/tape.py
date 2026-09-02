"""Semantic fault-tape descriptors and two-stage resolution (REQ-03, REQ-05).

The SEMANTIC descriptor — exactly {schema_version, fault_id, hook, kind,
target{policy_step, intent}} — is what enters run identity (locked decision 4):
``logical_run_id`` is derived from it first; only then is the concrete
operation key ``op/<logical_run_id>/step-<decimal>/intent-<token>`` expanded
into the RESOLVED tape. The semantic spec deliberately carries no
``operation_key``; the accepted REQ-03/REQ-05 interpretation is documented in
the run-config schema fixture and in tests, and must not be silently changed.

v0 admits exactly one demonstrator tape: ``response_lost_after_commit`` with
hook ``after_commit_before_response`` and kind ``response_lost``, targeting a
delivery ``place`` intent. Resolution is a pure function of frozen constants
plus the run id: no RNG, no wall clock, no runtime state, and NO policy is
consulted to discover a target (locked decision 2). The faults package imports
no runtime, policy, domain-transition, or verifier code.
"""
import re
from dataclasses import dataclass

from robot_factory_arena.contracts.tool_call import (
    expand_operation_key,
    is_valid_logical_run_id,
)
from robot_factory_arena.faults.model import (
    FAULT_HOOKS,
    FAULT_ID_RESPONSE_LOST,
    FAULT_KINDS,
    Fault,
    FaultTape,
)

_INTENT_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_PLACE_INTENT_RE = re.compile(r"place-order-(0[0-9])")


@dataclass(frozen=True)
class FaultTarget:
    """Predeclared delivery target: 1-based decimal policy step + exact intent.

    ``policy_step`` is the reserved-attempt number the faulted call will carry
    in its operation key (1-based: the first attempt is step 1), and ``intent``
    is the exact deterministic intent token the policy emits.
    """

    policy_step: int
    intent: str

    def __post_init__(self):
        if isinstance(self.policy_step, bool) or not isinstance(
            self.policy_step, int
        ):
            raise TypeError("policy_step must be an int")
        if self.policy_step < 1:
            raise ValueError(
                "policy_step must be a positive decimal: attempts are 1-based"
            )
        if (
            not isinstance(self.intent, str)
            or _INTENT_TOKEN_RE.fullmatch(self.intent) is None
        ):
            raise ValueError("intent must be a 1..64 [A-Za-z0-9._-] token")

    def to_mapping(self):
        return {"intent": self.intent, "policy_step": self.policy_step}


@dataclass(frozen=True)
class FaultTapeSpec:
    """The semantic fault-tape descriptor; exactly the RunConfig nested shape.

    Unknown fault kinds/hooks/ids are rejected here (fail closed), and the
    demonstrator target must be a delivery-place intent: any other intent
    would be an ambiguous target constant, because only a committing place
    crosses the after-commit persistence boundary in v0.
    """

    schema_version: int
    fault_id: str
    hook: str
    kind: str
    target: FaultTarget

    def __post_init__(self):
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise ValueError("schema_version must be 1")
        if self.fault_id != FAULT_ID_RESPONSE_LOST:
            raise ValueError(
                f"v0 admits exactly one fault_id: {FAULT_ID_RESPONSE_LOST!r}"
            )
        if self.hook not in FAULT_HOOKS:
            raise ValueError(f"hook must be one of {FAULT_HOOKS}")
        if self.kind not in FAULT_KINDS:
            raise ValueError(f"kind must be one of {FAULT_KINDS}")
        if not isinstance(self.target, FaultTarget):
            raise TypeError("target must be a FaultTarget")
        if _PLACE_INTENT_RE.fullmatch(self.target.intent) is None:
            raise ValueError(
                "the v0 demonstrator target intent must be place-order-<NN>; "
                f"got {self.target.intent!r}"
            )

    def to_mapping(self):
        """Exact semantic descriptor mapping — NO concrete operation key."""
        return {
            "schema_version": self.schema_version,
            "fault_id": self.fault_id,
            "hook": self.hook,
            "kind": self.kind,
            "target": self.target.to_mapping(),
        }


def resolve_fault_tape(spec: FaultTapeSpec, logical_run_id: str) -> FaultTape:
    """Stage two: expand the concrete key and freeze the resolved tape.

    The caller derives ``logical_run_id`` from the semantic descriptor first
    (locked decision 4); this function then expands the predeclared target
    constants into the concrete operation key. Fully resolved before any
    execution; identity is deterministic and execution-independent.
    """
    if not isinstance(spec, FaultTapeSpec):
        raise TypeError("spec must be a FaultTapeSpec")
    if not is_valid_logical_run_id(logical_run_id):
        raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
    operation_key = expand_operation_key(
        logical_run_id, spec.target.policy_step, spec.target.intent
    )
    fault = Fault(spec.fault_id, operation_key, spec.hook, spec.kind)
    return FaultTape(1, (fault,))
