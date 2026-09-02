"""The only runtime gateway to the transition authority (REQ-04).

Check order: envelope -> ledger (replay/conflict) -> grants -> normalized
arguments -> apply. The ledger lookup precedes authorization deliberately:
REQ-04 requires that repeating a stored operation key with the same
normalized tool and arguments MUST return the stored result, and REQ-04's
grant requirement is "grant checks before APPLYING a transition" — which is
preserved (no apply() is ever reached without passing the grant check, and
replays/conflicts never apply anything). Interpretation note for the design
flow (§3 steps 6–7 list authorization before the ledger query on the fresh
dispatch path): the binding spec's unconditional replay clause takes
precedence for already-stored keys; first-time dispatch still checks grants
and arguments before apply, and first rejected results with a valid key
enter the ledger exactly like first commits. Parse-level rejections
(malformed/unsupported/invalid-key) never reach ToolBay because no ToolCall
can be constructed for them — the runner owns those (B3).

State, ledger, and the transition authority are never exposed to policy
code. The ``retry`` flag marks an internal retry submission; in B2 it walks
the identical ledger/grant path (a retry never bypasses the gateway), and
B3's response-loss integration adds the persistence-boundary callbacks
around the commit return while consuming this flag for fault bookkeeping.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass

from robot_factory_arena.contracts.outcomes import CLOSED_REASONS, RESULT_STATUSES
from robot_factory_arena.contracts.tool_call import ToolCall, normalize_tool_call
from robot_factory_arena.domain.model import WorldState
from robot_factory_arena.domain.transition import DomainEvent, Operation, apply
from robot_factory_arena.evidence.canonical import digest_json
from robot_factory_arena.runtime.grants import GrantSet, authorize
from robot_factory_arena.runtime.ledger import (
    IDEMPOTENT_REPLAY,
    OPERATION_KEY_CONFLICT,
    Ledger,
    LedgerEntry,
    arguments_digest,
)

COMMITTED = "committed"
REJECTED = "rejected"
IDEMPOTENT_REPLAY_STATUS = "idempotent_replay"

# The one ToolBay commit-boundary loss signal (closed-taxonomy reason).
RESPONSE_LOST = "response_lost"

# Same grammar as contracts.tool_call; used only to extract the run id.
_OPERATION_KEY_RE = re.compile(
    r"op/(lr_[0-9a-f]{64})/step-(0|[1-9][0-9]*)/intent-([A-Za-z0-9._-]{1,64})"
)


class ResponseLostError(RuntimeError):
    """The response was lost after the commit crossed its boundary (REQ-05).

    Raised by ``submit`` when the ``on_commit_boundary`` handler returns the
    ``RESPONSE_LOST`` signal: the after_commit_before_response hook fired
    after the persistence boundary, so the committed ToolResult is never
    returned to the caller. The durable first result remains in the ledger —
    an internal retry with the exact call and key replays it as
    ``idempotent_replay`` without a second effect.
    """

    def __init__(self, operation_key: str, reason: str = RESPONSE_LOST):
        self.operation_key = operation_key
        self.reason = reason
        super().__init__(f"{reason}: {operation_key}")


@dataclass(frozen=True)
class ToolResult:
    """The gateway result handed back to the runner.

    ``committed`` is True exactly when the status is ``committed``. Replays
    carry the caller's CURRENT state unchanged (no effect) and, for replays
    of rejections, the original closed-taxonomy reason for audit visibility.
    """

    status: str
    state: WorldState
    events: tuple[DomainEvent, ...]
    operation_key: str
    reason: str | None
    committed: bool

    def __post_init__(self):
        if self.status not in RESULT_STATUSES:
            raise ValueError(f"status must be one of {RESULT_STATUSES}")
        if not isinstance(self.state, WorldState):
            raise TypeError("state must be a WorldState")
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, DomainEvent) for event in self.events
        ):
            raise TypeError("events must be a tuple of DomainEvent")
        if not isinstance(self.operation_key, str) or not self.operation_key:
            raise ValueError("operation_key must be a non-empty str")
        if not isinstance(self.committed, bool):
            raise TypeError("committed must be a bool")
        if self.committed != (self.status == COMMITTED):
            raise ValueError("committed is True exactly for committed results")
        if self.reason is not None:
            if self.reason not in CLOSED_REASONS:
                raise ValueError(
                    f"reason {self.reason!r} is outside the closed taxonomy"
                )
            if self.status == COMMITTED:
                raise ValueError("a committed result carries no reason")
        elif self.status == REJECTED:
            raise ValueError("a rejected result carries a taxonomy reason")


class ToolBay:
    """Runtime gateway: grants -> arguments -> ledger -> apply; nothing exposed."""

    def __init__(self, ledger: Ledger | None = None):
        if ledger is not None and not isinstance(ledger, Ledger):
            raise TypeError("ledger must be a Ledger or None")
        self._ledger = ledger if ledger is not None else Ledger()

    def submit(
        self,
        state: WorldState,
        call: ToolCall,
        grant_set: GrantSet,
        retry: bool = False,
        on_commit_boundary: Callable[[ToolCall, "ToolResult"], str | None] | None = None,
    ) -> ToolResult:
        """Dispatch one normalized call; every outcome is deterministic.

        ``on_commit_boundary`` (T-13): an optional callable invoked exactly
        once for a FRESH committed result, after ``store_first`` and before
        the response is returned — the persistence boundary. It receives
        ``(call, tool_result)`` and may return None or ``RESPONSE_LOST``;
        the loss signal raises ``ResponseLostError`` so the committed
        response is never observed by the caller. Rejections, replays, and
        conflicts never invoke the boundary.
        """
        if not isinstance(state, WorldState):
            raise TypeError("state must be a WorldState")
        if not isinstance(call, ToolCall):
            raise TypeError("submit accepts only normalized ToolCall values")
        if not isinstance(grant_set, GrantSet):
            raise TypeError("grant_set must be a GrantSet")
        if not isinstance(retry, bool):
            raise TypeError("retry must be a bool")
        if on_commit_boundary is not None and not callable(
            on_commit_boundary
        ):
            raise TypeError("on_commit_boundary must be callable or None")
        key = call.operation_key
        key_match = _OPERATION_KEY_RE.fullmatch(key)
        if key_match is None:  # fail-closed; unreachable for constructed calls
            raise TypeError("operation key does not match the v0 grammar")
        logical_run_id = key_match.group(1)

        # 1) Ledger first: a stored first result replays verbatim and a used
        #    key with a different payload conflicts, regardless of grants
        #    (REQ-04's unconditional replay clause; see module docstring).
        decision = self._ledger.lookup(key, call.tool, call.arguments)
        if decision.kind == OPERATION_KEY_CONFLICT:
            return ToolResult(
                REJECTED, state, (), key, OPERATION_KEY_CONFLICT, False
            )
        if decision.kind == IDEMPOTENT_REPLAY:
            stored = decision.stored
            if stored is None:  # fail-closed; replay decisions always store
                raise TypeError("replay decision without a stored entry")
            return ToolResult(
                IDEMPOTENT_REPLAY_STATUS,
                state,
                (),
                key,
                stored.result.reason,
                False,
            )

        # 2) Grants before anything reaches the world (REQ-04).
        authorization = authorize(call, grant_set, logical_run_id)
        if not authorization.ok:
            return self._reject_and_store(state, call, authorization.reason)

        # 3) Normalized per-tool argument shapes (defensive; never trusted
        #    from the caller alone).
        parse = normalize_tool_call(call)
        if not parse.ok:
            return self._reject_and_store(state, call, parse.reason)

        # 4) The pure transition authority.
        outcome = apply(state, Operation(call.tool, dict(call.arguments), key))
        if outcome.status == COMMITTED:
            snapshot_digest = digest_json(outcome.state.to_mapping())
            result = ToolResult(
                COMMITTED, outcome.state, outcome.events, key, None, True
            )
            self._ledger.store_first(
                LedgerEntry(
                    key,
                    call.tool,
                    arguments_digest(call.arguments),
                    COMMITTED,
                    result,
                    snapshot_digest,
                )
            )
            # Persistence boundary: fires after the durable first result
            # exists and before the response is observed (REQ-05).
            if on_commit_boundary is not None:
                signal = on_commit_boundary(call, result)
                if signal is not None:
                    if signal != RESPONSE_LOST:
                        raise TypeError(
                            "on_commit_boundary may only return None or "
                            "'response_lost'"
                        )
                    raise ResponseLostError(key)
            return result
        return self._reject_and_store(state, call, outcome.reason)

    def _reject_and_store(self, state, call, reason) -> ToolResult:
        """First rejected result with a valid key enters the ledger."""
        result = ToolResult(REJECTED, state, (), call.operation_key, reason, False)
        self._ledger.store_first(
            LedgerEntry(
                call.operation_key,
                call.tool,
                arguments_digest(call.arguments),
                REJECTED,
                result,
                None,
            )
        )
        return result
