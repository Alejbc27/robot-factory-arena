"""Serialized policy view: exactly the REQ-03 observation keys (REQ-02, REQ-03).

The runner constructs an Observation from immutable state and counters
immediately before each policy call. The serialized form is sufficient to
decide a legal action and contains no execution id, timestamp, host data,
mutable object reference, or hidden side channel. Grants appear as the
explicit sorted capability list.
"""
import re
from dataclasses import dataclass

from robot_factory_arena.contracts.outcomes import (
    SUPPORTED_TOOLS,
    BudgetView,
    LastResult,
)
from robot_factory_arena.domain.model import WorldState

SCHEMA_VERSION = 1

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")


def _require_strictly_ascending(values, label):
    if any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError(f"{label} must be strictly ascending: {values!r}")
    return values


@dataclass(frozen=True)
class Observation:
    """The immutable inputs behind one policy decision."""

    world_state: WorldState
    logical_run_id: str
    grants: tuple[str, ...]
    policy_steps: BudgetView
    last_result: LastResult | None

    def __post_init__(self):
        if not isinstance(self.world_state, WorldState):
            raise TypeError("world_state must be a WorldState")
        if not isinstance(self.logical_run_id, str) or (
            _LOGICAL_RUN_ID_RE.fullmatch(self.logical_run_id) is None
        ):
            raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
        if not isinstance(self.grants, tuple) or not self.grants:
            raise TypeError("grants must be a non-empty tuple of capability names")
        for capability in self.grants:
            if capability not in SUPPORTED_TOOLS:
                raise ValueError(f"unsupported capability: {capability!r}")
        _require_strictly_ascending(self.grants, "grants")
        if not isinstance(self.policy_steps, BudgetView):
            raise TypeError("policy_steps must be a BudgetView")
        if self.last_result is not None and not isinstance(
            self.last_result, LastResult
        ):
            raise TypeError("last_result must be a LastResult or None")


def serialize_observation(observation: Observation) -> dict:
    """Exact REQ-03 keys; sorted grants; explicit null last_result."""
    last = observation.last_result
    return {
        "schema_version": SCHEMA_VERSION,
        "logical_run_id": observation.logical_run_id,
        "world_state": observation.world_state.to_mapping(),
        "grants": list(observation.grants),
        "policy_steps": {
            "used": observation.policy_steps.used,
            "remaining": observation.policy_steps.remaining,
            "maximum": observation.policy_steps.maximum,
        },
        "last_result": None
        if last is None
        else {
            "status": last.status,
            "operation_key": last.operation_key,
            "reason": last.reason,
        },
    }
