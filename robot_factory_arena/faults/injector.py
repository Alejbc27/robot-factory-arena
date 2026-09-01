"""The response-only fault injector (REQ-05).

The injector matches by semantic hook plus CONCRETE operation key against the
resolved tape and consumes each matched fault exactly once (one-shot). It has
no RNG and no wall-clock scheduling, and its API cannot mutate WorldState or
grants: hooks receive a read-only FaultContext (operation key + tool) and
return a FaultDecision. ``before_commit`` never fires in v0 — the only v0
fault kind is ``response_lost`` on ``after_commit_before_response``, which by
construction is consulted only AFTER the persistence boundary (the runner's
commit-boundary handler), never before a commit.
"""
from robot_factory_arena.faults.model import (
    HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
    FaultContext,
    FaultDecision,
    FaultTape,
)


class FaultInjector:
    """Deterministic one-shot hook matcher over a resolved FaultTape."""

    def __init__(self, tape: FaultTape):
        if not isinstance(tape, FaultTape):
            raise TypeError("tape must be a FaultTape")
        self._tape = tape
        self._consumed: set[str] = set()

    @property
    def tape(self) -> FaultTape:
        """The immutable resolved tape (safe to expose; never mutated)."""
        return self._tape

    def before_commit(self, context: FaultContext) -> FaultDecision:
        """Consulted before dispatch; v0 admits no before-commit fault."""
        self._require_context(context)
        return FaultDecision(False, None)

    def after_commit_before_response(self, context: FaultContext) -> FaultDecision:
        """Consulted after the persistence boundary, before the response.

        Fires exactly once for the fault whose concrete operation key equals
        the context key; later consultations of the same operation are quiet.
        """
        self._require_context(context)
        for fault in self._tape.faults:
            if (
                fault.hook == HOOK_AFTER_COMMIT_BEFORE_RESPONSE
                and fault.operation_key == context.operation_key
                and fault.operation_key not in self._consumed
            ):
                self._consumed.add(fault.operation_key)
                return FaultDecision(True, fault)
        return FaultDecision(False, None)

    @staticmethod
    def _require_context(context) -> None:
        if not isinstance(context, FaultContext):
            raise TypeError("hooks accept only FaultContext values")
