"""Operation-only offline replay (T-17, REQ-06, REQ-07, REQ-09).

Replay consumes ONLY declared artifact data — the locked ``ReplayInput``
mapping recorded in ``replay-input.json`` and, optionally, the receipt — and
reconstructs the run by re-applying the recorded operations through the pure
transition authority. It regenerates the task from the recorded identity
(``generate_task(seed)``) and reproduces the final-state digest, the outcome
classification, and (from the receipt binding) the verified score. It never
asks for a decision: the module imports no ``policies``, no ``runtime``, and
no verifier code (AST-tested), and the ``policy`` parameter exists purely as
a tripwire that callers may pass (tests pass a raising object) — replay
stores it and never consults it.

Distinction from a rerun (REQ-07): a rerun executes the policy again under a
new execution id; replay consumes recorded committed/rejected/idempotent
submissions — including internal retries and fault evidence — without any
policy or provider. Every consistency contradiction (tampered seed, edited
events, wrong digests, a fault key without its committed op) raises
``ReplayInconsistentError``; receipt disagreement is REPORTED on the result
(``matches_receipt`` False), never upgraded to success (REQ-05).

Outcome classification from recorded evidence: every order complete ->
``completed``; incomplete with recorded steps reaching the budget ->
``safe_abort``; anything unprovable -> ``unresolved_outcome`` (ambiguity is
never success; ``duplicate_effect`` stays verifier-owned, REQ-06). Scores
are never recomputed here — the score is verifier-owned (REQ-06) and travels
from the receipt through ``ReplayResult`` for the report.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass

from robot_factory_arena.contracts.outcomes import (
    CLOSED_REASONS,
    OUTCOMES,
    SUPPORTED_TOOLS,
)
from robot_factory_arena.contracts.tool_call import validate_operation_key
from robot_factory_arena.domain.task import TASK_VERSION, generate_task
from robot_factory_arena.domain.transition import (
    DOMAIN_EVENT_TYPES,
    Operation,
    apply,
)
from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes,
    digest_json,
)

REPLAY_SCHEMA_VERSION = 1
BUDGET_MAXIMUM = 100

# Locked ReplayInput schema (tasks.md exact auxiliary schema lock).
REPLAY_INPUT_KEYS = (
    "budget", "fault_tape", "grants", "logical_run_id", "operations",
    "schema_version", "task_identity",
)
TASK_IDENTITY_KEYS = ("config_digest", "seed", "task_version")
OPERATION_KEYS = (
    "arguments", "events", "fault_ids", "operation_key", "policy_step",
    "reason", "retry", "state_after", "state_before", "status", "tool",
)
EVENT_KEYS = ("data", "event_type", "operation_key", "schema_version")
FAULT_KEYS = ("fault_id", "hook", "kind", "operation_key")

OPERATION_STATUSES = ("committed", "idempotent_replay", "rejected")

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SEED_TEXT_RE = re.compile(r"(0|[1-9][0-9]*)")

_RECEIPT_REQUIRED_KEYS = (
    "final_state_digest", "logical_run_id", "outcome", "verified_score",
)


class ReplayInputError(ValueError):
    """The replay input violates the locked ReplayInput schema."""


class ReplayInconsistentError(ValueError):
    """The recorded operations contradict the deterministic reconstruction."""


def _require_mapping(value, label) -> Mapping:
    if not isinstance(value, Mapping):
        raise ReplayInputError(f"{label} must be a mapping")
    return value


def _require_keys(mapping, expected, label) -> None:
    extra = sorted(set(mapping) - set(expected))
    missing = sorted(set(expected) - set(mapping))
    if extra or missing:
        raise ReplayInputError(f"{label} key drift; extra={extra} missing={missing}")


def _is_digest(value) -> bool:
    return (
        isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None
    )


def _require_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReplayInputError(f"{label} must be an int")
    return value


def _validate_operation(operation, logical_run_id, budget, index) -> None:
    label = f"operations[{index}]"
    _require_keys(operation, OPERATION_KEYS, label)
    step = _require_int(operation["policy_step"], f"{label}.policy_step")
    if not 1 <= step <= budget:
        raise ReplayInputError(f"{label}.policy_step must be 1..{budget}")
    key = operation["operation_key"]
    if key is not None and not validate_operation_key(key, logical_run_id):
        raise ReplayInputError(f"{label}.operation_key is not valid for this run")
    tool = operation["tool"]
    if tool is not None and tool not in SUPPORTED_TOOLS:
        raise ReplayInputError(f"{label}.tool is not a supported tool")
    if operation["arguments"] is not None and not isinstance(
        operation["arguments"], Mapping
    ):
        raise ReplayInputError(f"{label}.arguments must be a mapping or null")
    status = operation["status"]
    if status not in OPERATION_STATUSES:
        raise ReplayInputError(f"{label}.status must be one of {OPERATION_STATUSES}")
    reason = operation["reason"]
    if reason is not None and reason not in CLOSED_REASONS:
        raise ReplayInputError(f"{label}.reason is outside the closed taxonomy")
    for name in ("state_before", "state_after"):
        value = operation[name]
        if value is not None and not _is_digest(value):
            raise ReplayInputError(f"{label}.{name} must be a digest or null")
    if (operation["state_after"] is not None) != (status == "committed"):
        raise ReplayInputError(
            f"{label}.state_after is non-null exactly for committed operations"
        )
    if status == "committed" and (tool is None or operation["arguments"] is None):
        raise ReplayInputError(
            f"{label} is committed but carries no tool/arguments"
        )
    retry = operation["retry"]
    if not isinstance(retry, bool):
        raise ReplayInputError(f"{label}.retry must be a boolean")
    fault_ids = operation["fault_ids"]
    if not isinstance(fault_ids, list) or any(
        not isinstance(f, str) or not f for f in fault_ids
    ):
        raise ReplayInputError(f"{label}.fault_ids must be a list of strings")
    events = operation["events"]
    if not isinstance(events, list):
        raise ReplayInputError(f"{label}.events must be a list")
    if (status == "committed") != bool(events):
        raise ReplayInputError(
            f"{label}.events is non-empty exactly for committed operations"
        )
    for position, event in enumerate(events):
        event_label = f"{label}.events[{position}]"
        _require_keys(event, EVENT_KEYS, event_label)
        if event["schema_version"] != REPLAY_SCHEMA_VERSION:
            raise ReplayInputError(f"{event_label}.schema_version must be 1")
        if event["event_type"] not in DOMAIN_EVENT_TYPES:
            raise ReplayInputError(
                f"{event_label}.event_type must be one of {DOMAIN_EVENT_TYPES}"
            )
        if not validate_operation_key(event["operation_key"], logical_run_id):
            raise ReplayInputError(
                f"{event_label}.operation_key is not valid for this run"
            )
        if not isinstance(event["data"], Mapping):
            raise ReplayInputError(f"{event_label}.data must be a mapping")
        if event["operation_key"] != key:
            raise ReplayInputError(
                f"{event_label} belongs to a different operation key"
            )


def _validate_fault_tape(tape, logical_run_id) -> None:
    _require_keys(tape, ("faults", "schema_version"), "fault_tape")
    if tape["schema_version"] != REPLAY_SCHEMA_VERSION:
        raise ReplayInputError("fault_tape.schema_version must be 1")
    faults = tape["faults"]
    if not isinstance(faults, list) or not faults:
        raise ReplayInputError("fault_tape.faults must be a non-empty list")
    for position, fault in enumerate(faults):
        label = f"fault_tape.faults[{position}]"
        _require_keys(fault, FAULT_KEYS, label)
        for name in FAULT_KEYS:
            if not isinstance(fault[name], str) or not fault[name]:
                raise ReplayInputError(f"{label}.{name} must be a non-empty str")
        if not validate_operation_key(fault["operation_key"], logical_run_id):
            raise ReplayInputError(
                f"{label}.operation_key is not valid for this run"
            )


@dataclass(frozen=True)
class ReplayInput:
    """The parsed, validated locked ReplayInput artifact."""

    schema_version: int
    logical_run_id: str
    task_identity: dict
    grants: tuple[str, ...]
    budget: int
    fault_tape: dict | None
    operations: tuple[dict, ...]

    @classmethod
    def from_mapping(cls, mapping) -> "ReplayInput":
        mapping = dict(_require_mapping(mapping, "replay input"))
        try:
            # The canonical JSON gate: floats and non-JSON values are
            # rejected outright (REQ-07).
            canonical_json_bytes(mapping)
        except TypeError as error:
            raise ReplayInputError(
                f"replay input is not canonical-JSON representable: {error}"
            ) from error
        _require_keys(mapping, REPLAY_INPUT_KEYS, "replay input")
        if mapping["schema_version"] != REPLAY_SCHEMA_VERSION:
            raise ReplayInputError("schema_version must be 1")
        logical_run_id = mapping["logical_run_id"]
        if (
            not isinstance(logical_run_id, str)
            or _LOGICAL_RUN_ID_RE.fullmatch(logical_run_id) is None
        ):
            raise ReplayInputError(
                "logical_run_id must be lr_ plus 64 hex characters"
            )
        identity = dict(_require_mapping(mapping["task_identity"],
                                         "task_identity"))
        _require_keys(identity, TASK_IDENTITY_KEYS, "task_identity")
        if identity["task_version"] != TASK_VERSION:
            raise ReplayInputError(
                f"task_identity.task_version must be {TASK_VERSION!r}"
            )
        if (
            not isinstance(identity["seed"], str)
            or _SEED_TEXT_RE.fullmatch(identity["seed"]) is None
        ):
            raise ReplayInputError("task_identity.seed must be canonical decimal")
        if not _is_digest(identity["config_digest"]):
            raise ReplayInputError(
                "task_identity.config_digest must be a sha256 digest"
            )
        grants = mapping["grants"]
        if (
            not isinstance(grants, list)
            or not grants
            or any(capability not in SUPPORTED_TOOLS for capability in grants)
        ):
            raise ReplayInputError("grants must be a list of supported tools")
        if any(a >= b for a, b in zip(grants, grants[1:])):
            raise ReplayInputError("grants must be strictly ascending")
        budget = _require_int(mapping["budget"], "budget")
        if budget != BUDGET_MAXIMUM:
            raise ReplayInputError(f"budget is locked at {BUDGET_MAXIMUM}")
        fault_tape = mapping["fault_tape"]
        if fault_tape is not None:
            _validate_fault_tape(
                dict(_require_mapping(fault_tape, "fault_tape")),
                logical_run_id,
            )
        operations = mapping["operations"]
        if not isinstance(operations, list):
            raise ReplayInputError("operations must be a list")
        previous_step = None
        for index, operation in enumerate(operations):
            operation = dict(_require_mapping(operation, f"operations[{index}]"))
            _validate_operation(operation, logical_run_id, budget, index)
            operations[index] = operation
            step = operation["policy_step"]
            if previous_step is not None and step < previous_step:
                raise ReplayInputError(
                    "operations must be in submission order (policy steps "
                    "never decrease)"
                )
            previous_step = step
        return cls(
            schema_version=mapping["schema_version"],
            logical_run_id=logical_run_id,
            task_identity=identity,
            grants=tuple(grants),
            budget=budget,
            fault_tape=None if fault_tape is None else dict(fault_tape),
            operations=tuple(operations),
        )

    def to_mapping(self) -> dict:
        """The exact locked ReplayInput mapping (round-trip safe)."""
        return {
            "schema_version": self.schema_version,
            "logical_run_id": self.logical_run_id,
            "task_identity": dict(self.task_identity),
            "grants": list(self.grants),
            "budget": self.budget,
            "fault_tape": (
                None if self.fault_tape is None else dict(self.fault_tape)
            ),
            "operations": [dict(operation) for operation in self.operations],
        }


@dataclass(frozen=True)
class ReplayResult:
    """The deterministic replay outcome plus receipt agreement (REQ-09)."""

    schema_version: int
    logical_run_id: str
    replayed_operations: int
    final_state_digest: str
    final_step: int
    replay_outcome: str
    receipt_final_state_digest: str | None
    receipt_verified_score: int | None
    receipt_outcome: str | None
    digest_matches: bool | None
    outcome_matches: bool | None
    matches_receipt: bool

    def to_mapping(self) -> dict:
        """Canonical mapping for ``replay-result.json`` (explicit nulls)."""
        return {
            "schema_version": self.schema_version,
            "logical_run_id": self.logical_run_id,
            "replayed_operations": self.replayed_operations,
            "final_state_digest": self.final_state_digest,
            "final_step": self.final_step,
            "replay_outcome": self.replay_outcome,
            "receipt_final_state_digest": self.receipt_final_state_digest,
            "receipt_verified_score": self.receipt_verified_score,
            "receipt_outcome": self.receipt_outcome,
            "digest_matches": self.digest_matches,
            "outcome_matches": self.outcome_matches,
            "matches_receipt": self.matches_receipt,
        }


def _classify(state, operations, budget) -> str:
    """Completed / safe_abort / unresolved_outcome from recorded evidence."""
    complete = set(state.completed_orders) >= {
        order.order_id for order in state.orders
    }
    if complete:
        return "completed"
    if operations and max(
        operation["policy_step"] for operation in operations
    ) >= budget:
        return "safe_abort"
    return "unresolved_outcome"


def replay(replay_input, receipt=None, policy=None) -> ReplayResult:
    """Reconstruct the run from recorded operations (policy-free, offline).

    ``replay_input`` is a ReplayInput or a raw locked-schema mapping.
    ``receipt``, when given, is the run's receipt mapping; digest and
    outcome agreement are REPORTED (never thrown). ``policy`` is a caller
    suplied tripwire (tests pass a raising object): replay stores it and
    never consults it — a decision is never requested (REQ-07).
    """
    del policy  # never consulted; presence proves the API invites no policy
    parsed = (
        replay_input
        if isinstance(replay_input, ReplayInput)
        else ReplayInput.from_mapping(replay_input)
    )
    if parsed.task_identity["task_version"] != TASK_VERSION:
        raise ReplayInputError("unsupported task version")
    state = generate_task(parsed.task_identity["seed"]).initial_state

    committed_keys = {
        operation["operation_key"]
        for operation in parsed.operations
        if operation["status"] == "committed"
    }
    if parsed.fault_tape is not None:
        for fault in parsed.fault_tape["faults"]:
            if fault["operation_key"] not in committed_keys:
                raise ReplayInconsistentError(
                    "fault tape references an operation that never committed: "
                    f"{fault['operation_key']}"
                )

    for operation in parsed.operations:
        before = digest_json(state.to_mapping())
        recorded_before = operation["state_before"]
        if recorded_before is not None and recorded_before != before:
            raise ReplayInconsistentError(
                f"operation {operation['operation_key']!r} records a state "
                "before that disagrees with the reconstruction"
            )
        if operation["status"] == "committed":
            outcome = apply(
                state,
                Operation(
                    operation["tool"],
                    dict(operation["arguments"]),
                    operation["operation_key"],
                ),
            )
            if outcome.status != "committed":
                raise ReplayInconsistentError(
                    f"recorded committed operation replays as a rejection: "
                    f"{outcome.reason}"
                )
            if [event.to_mapping() for event in outcome.events] != list(
                operation["events"]
            ):
                raise ReplayInconsistentError(
                    f"operation {operation['operation_key']!r} emits different "
                    "events than recorded"
                )
            state = outcome.state
            if operation["state_after"] != digest_json(state.to_mapping()):
                raise ReplayInconsistentError(
                    f"operation {operation['operation_key']!r} records a "
                    "state after that disagrees with the reconstruction"
                )

    final_digest = digest_json(state.to_mapping())
    replay_outcome = _classify(state, parsed.operations, parsed.budget)

    if receipt is None:
        return ReplayResult(
            schema_version=REPLAY_SCHEMA_VERSION,
            logical_run_id=parsed.logical_run_id,
            replayed_operations=len(parsed.operations),
            final_state_digest=final_digest,
            final_step=state.step,
            replay_outcome=replay_outcome,
            receipt_final_state_digest=None,
            receipt_verified_score=None,
            receipt_outcome=None,
            digest_matches=None,
            outcome_matches=None,
            matches_receipt=False,
        )

    receipt = _require_mapping(receipt, "receipt")
    missing = [key for key in _RECEIPT_REQUIRED_KEYS if key not in receipt]
    if missing:
        raise ReplayInputError(f"receipt is missing required keys: {missing}")
    if not _is_digest(receipt["final_state_digest"]):
        raise ReplayInputError("receipt final_state_digest must be a digest")
    if receipt["outcome"] not in OUTCOMES:
        raise ReplayInputError("receipt outcome is outside the closed enum")
    score = receipt["verified_score"]
    if score is not None and (
        isinstance(score, bool) or not isinstance(score, int)
    ):
        raise ReplayInputError("receipt verified_score must be an int or null")

    digest_matches = (
        receipt["logical_run_id"] == parsed.logical_run_id
        and receipt["final_state_digest"] == final_digest
    )
    outcome_matches = receipt["outcome"] == replay_outcome
    return ReplayResult(
        schema_version=REPLAY_SCHEMA_VERSION,
        logical_run_id=parsed.logical_run_id,
        replayed_operations=len(parsed.operations),
        final_state_digest=final_digest,
        final_step=state.step,
        replay_outcome=replay_outcome,
        receipt_final_state_digest=receipt["final_state_digest"],
        receipt_verified_score=score,
        receipt_outcome=receipt["outcome"],
        digest_matches=digest_matches,
        outcome_matches=outcome_matches,
        matches_receipt=bool(digest_matches and outcome_matches),
    )
