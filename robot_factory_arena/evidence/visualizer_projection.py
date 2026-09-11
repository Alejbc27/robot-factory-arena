"""Strict, deterministic, projection-only VisualizerProjection v1.

This module deliberately has no filesystem, renderer, verifier, or replay entry
point.  ``build_projection`` accepts only the already-approved release handoff
used by the later release unit; it refuses incomplete or ambiguous evidence.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from robot_factory_arena.contracts.outcomes import CLOSED_REASONS, DIRECTIONS

from .canonical import canonical_json_bytes, sha256_digest

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_LOGICAL_RUN = re.compile(r"lr_[0-9a-f]{64}\Z")
_COUNTERS = (
    "policy_steps",
    "tool_attempts",
    "committed_effects",
    "internal_retries",
    "contract_violations",
    "terminal_checks",
)
_TOP = (
    "projection_type",
    "schema_version",
    "logical_run",
    "initial_world",
    "frames",
    "counters",
    "terminal_result",
    "semantic_digests",
)
_LOGICAL = (
    "logical_run_id",
    "task_version",
    "seed",
    "generator",
    "generator_version",
    "slot_layout",
    "policy_id",
    "policy_digest",
    "provider",
    "model",
    "strategy_context_digest",
    "tool_schema_digest",
    "prompt_digest",
    "grants",
    "budget",
    "fault_tape",
)
_DIGESTS = (
    "config_digest",
    "task_digest",
    "policy_digest",
    "fault_tape_digest",
    "trace_digest",
    "initial_state_digest",
    "final_state_digest",
    "projection_digest",
    "strategy_context_digest",
    "tool_schema_digest",
    "prompt_digest",
)
_FRAME = (
    "policy_step",
    "observation",
    "tool_call",
    "candidate_shape",
    "classification",
    "reason",
    "attempts",
    "subevents",
    "committed_domain_events",
    "state_before",
    "state_after",
    "state_delta",
    "counters",
)
_TOOL_CALL = ("schema_version", "tool", "arguments", "operation_key")
_ARGUMENT_KEYS = {
    "move": ("direction",),
    "pick": ("shelf_id", "item_id"),
    "place": ("order_id",),
}
_ARGUMENT_GRAMMARS = {
    "shelf_id": re.compile(r"shelf-0[0-9]\Z"),
    "item_id": re.compile(r"item-0[0-9]\Z"),
    "order_id": re.compile(r"order-0[0-9]\Z"),
}
_REQUIRED_DIGESTS = (
    "config_digest",
    "task_digest",
    "policy_digest",
    "trace_digest",
    "initial_state_digest",
    "final_state_digest",
    "projection_digest",
)


def _fail(message: str) -> NoReturn:
    raise ValueError("invalid VisualizerProjection: " + message)


def _exact(value: Any, keys: tuple[str, ...], label: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail(label + " key drift")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(label + " must be an integer")
    return value


def _string(value: Any, label: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        _fail(label + " must be a string")
    return value


def _digest(value: Any, label: str, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _fail(label + " must be a sha256 digest")


def _reason(value: Any, label: str, *, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or value not in CLOSED_REASONS:
        _fail(label + " is outside the closed taxonomy")


def _canonical(value: Any) -> Any:
    """Copy JSON values while rejecting floats, arbitrary objects and key drift."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, float):
        _fail("floating-point values are forbidden")
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            _fail("object keys must be strings")
        return {key: _canonical(item) for key, item in value.items()}
    _fail("non-JSON value")


def _world(value: Any, label: str) -> dict:
    # Domain's own parser is the authoritative v0 shape guard.
    from robot_factory_arena.domain.model import WorldState

    if not isinstance(value, Mapping):
        _fail(label + " must be a WorldState mapping")
    try:
        parsed = WorldState.from_mapping(value)
    except (TypeError, ValueError) as error:
        _fail(label + ": " + str(error))
    return parsed.to_mapping()


def _safe_argument(name: str, value: Any) -> bool:
    """True only for the exact v0 scalar grammar of one allowlisted argument."""
    if name == "direction":
        return value in DIRECTIONS
    grammar = _ARGUMENT_GRAMMARS.get(name)
    return (
        isinstance(value, str)
        and grammar is not None
        and grammar.fullmatch(value) is not None
    )


def _tool_call(value: Any, label: str) -> dict:
    value = _exact(value, _TOOL_CALL, label)
    schema, tool, arguments, key = (value[name] for name in _TOOL_CALL)
    if tool is None:
        # A parse-level record keeps only explicitly null unsafe fields.
        if schema is not None or arguments is not None:
            _fail(label + " must null absent fields when the tool is absent")
    elif tool not in _ARGUMENT_KEYS or isinstance(schema, bool) or schema != 1:
        _fail(label + ".schema_version")
    if key is not None:
        _string(key, label + ".operation_key", nonempty=True)
    if arguments is not None:
        if not isinstance(tool, str) or not isinstance(arguments, Mapping):
            _fail(label + ".arguments")
        if set(arguments) != set(_ARGUMENT_KEYS[tool]) or not all(
            _safe_argument(name, item) for name, item in arguments.items()
        ):
            _fail(label + ".arguments are not safe v0 arguments")
    return _canonical(value)


def _event_data(event_type: Any, data: Mapping, width: int, height: int) -> None:
    """Fail closed unless event data is the exact observation-safe v0 shape."""
    if event_type == "robot_moved":
        if (
            set(data) != {"from", "to", "direction"}
            or data["direction"] not in DIRECTIONS
        ):
            _fail("domain event.data")
        for name in ("from", "to"):
            point = data[name]
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    isinstance(item, bool) or not isinstance(item, int)
                    for item in point
                )
            ):
                _fail("domain event.data coordinates")
            if not (0 <= point[0] < width and 0 <= point[1] < height):
                _fail("domain event.data coordinates leave the grid")
    elif event_type == "item_picked":
        if set(data) != {"shelf_id", "item_id"} or not all(
            _safe_argument(name, data[name]) for name in ("shelf_id", "item_id")
        ):
            _fail("domain event.data")
    elif event_type == "order_delivered":
        if set(data) != {"order_id", "item_id"} or not all(
            _safe_argument(name, data[name]) for name in ("order_id", "item_id")
        ):
            _fail("domain event.data")
    else:
        _fail("domain event.data")


def _counters(value: Any, label: str) -> dict:
    value = _exact(value, _COUNTERS, label)
    for name in _COUNTERS:
        _integer(value[name], label + "." + name)
    return dict(value)


@dataclass(frozen=True)
class VisualizerLogicalMetadata:
    provider: str | None
    model: str | None
    strategy_context_digest: str | None
    tool_schema_digest: str | None
    prompt_digest: str | None

    def __post_init__(self) -> None:
        for name in ("provider", "model"):
            value = getattr(self, name)
            if value is not None:
                _string(value, name, nonempty=True)
        for name in ("strategy_context_digest", "tool_schema_digest", "prompt_digest"):
            _digest(getattr(self, name), name, nullable=True)


@dataclass(frozen=True)
class VisualizerProjection:
    """An immutable, already schema-validated projection value."""

    _mapping: dict

    def to_mapping(self) -> dict:
        return copy.deepcopy(self._mapping)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_projection_bytes(self)

    @property
    def projection_digest(self) -> str:
        return self._mapping["semantic_digests"]["projection_digest"]


def _operation_key(
    value: Any, logical_run_id: str, label: str, nullable: bool = False
) -> None:
    from robot_factory_arena.contracts.tool_call import validate_operation_key

    if value is None and nullable:
        return
    if not isinstance(value, str) or not validate_operation_key(value, logical_run_id):
        _fail(label + " must be a valid logical operation key")


def _validate_frame(value: Any, index: int, logical_run_id: str) -> dict:
    value = _exact(value, _FRAME, "frame")
    _integer(value["policy_step"], "frame.policy_step", 1)
    observation = _exact(
        value["observation"],
        (
            "policy_steps_before",
            "policy_steps_remaining_before",
            "policy_steps_maximum",
            "last_result",
        ),
        "observation",
    )
    for name in observation:
        if name != "last_result":
            _integer(observation[name], "observation." + name)
    if observation["last_result"] is not None:
        last = _exact(
            observation["last_result"],
            ("status", "operation_key", "reason"),
            "last_result",
        )
        if last["status"] not in ("committed", "idempotent_replay", "rejected"):
            _fail("last_result.status")
        # The runner represents a parse-level rejection without a usable
        # key as the explicit empty string in its safe observation summary.
        # Preserve that closed upstream sentinel; every other key remains
        # bound to this logical run (an absent key is never silently ok).
        if last["operation_key"] != "":
            _operation_key(
                last["operation_key"], logical_run_id, "last_result.operation_key"
            )
        _reason(last["reason"], "last_result.reason")
    _tool_call(value["tool_call"], "tool_call")
    _operation_key(
        value["tool_call"]["operation_key"],
        logical_run_id,
        "tool_call.operation_key",
        True,
    )
    if value["candidate_shape"] not in ("mapping", "tool_call", "non_mapping"):
        _fail("candidate_shape")
    if value["classification"] not in (
        "committed",
        "rejected",
        "malformed",
        "unsupported",
        "invalid",
        "unauthorized",
        "idempotent_replay",
    ):
        _fail("classification")
    _reason(value["reason"], "reason")
    if (
        not isinstance(value["attempts"], list)
        or not isinstance(value["subevents"], list)
        or not isinstance(value["committed_domain_events"], list)
    ):
        _fail("frame collections")
    for attempt_index, attempt in enumerate(value["attempts"], 1):
        attempt = _exact(
            attempt,
            (
                "attempt_index",
                "kind",
                "tool_call",
                "status",
                "reason",
                "response",
                "fault_ids",
            ),
            "attempt",
        )
        if (
            attempt["attempt_index"] != attempt_index
            or attempt["kind"] not in ("primary", "internal_retry")
            or attempt["status"] not in ("committed", "idempotent_replay", "rejected")
            or attempt["response"] not in ("received", "lost")
            or not isinstance(attempt["fault_ids"], list)
        ):
            _fail("attempt ordering or enum")
        _tool_call(attempt["tool_call"], "attempt.tool_call")
        _operation_key(
            attempt["tool_call"]["operation_key"],
            logical_run_id,
            "attempt.tool_call.operation_key",
            nullable=True,
        )
        _reason(attempt["reason"], "attempt.reason")
        if not all(
            isinstance(fault_id, str) and fault_id for fault_id in attempt["fault_ids"]
        ):
            _fail("attempt.fault_ids")
    for sequence, subevent in enumerate(value["subevents"], 1):
        subevent = _exact(
            subevent,
            (
                "sequence",
                "kind",
                "operation_key",
                "fault_id",
                "hook",
                "fault_kind",
                "attempt_index",
                "status",
                "reason",
            ),
            "subevent",
        )
        if subevent["sequence"] != sequence or subevent["kind"] not in (
            "fault_injected",
            "internal_retry",
        ):
            _fail("subevent ordering or kind")
        _operation_key(
            subevent["operation_key"],
            logical_run_id,
            "subevent.operation_key",
            nullable=True,
        )
        _integer(subevent["attempt_index"], "subevent.attempt_index", 1)
        if subevent["status"] not in (
            "lost",
            "committed",
            "idempotent_replay",
            "rejected",
        ):
            _fail("subevent.status")
        _reason(subevent["reason"], "subevent.reason")
        if subevent["kind"] == "fault_injected":
            if not all(
                isinstance(subevent[name], str) and subevent[name]
                for name in ("fault_id", "hook", "fault_kind")
            ):
                _fail("fault subevent metadata")
        elif any(
            subevent[name] is not None for name in ("fault_id", "hook", "fault_kind")
        ):
            _fail("retry subevent metadata")
    before, after = (
        _world(value["state_before"], "state_before"),
        _world(value["state_after"], "state_after"),
    )
    for event in value["committed_domain_events"]:
        event = _exact(
            event,
            ("schema_version", "event_type", "operation_key", "data"),
            "domain event",
        )
        if (
            isinstance(event["schema_version"], bool)
            or event["schema_version"] != 1
            or event["event_type"]
            not in ("item_picked", "order_delivered", "robot_moved")
        ):
            _fail("domain event enum")
        _operation_key(
            event["operation_key"], logical_run_id, "domain event.operation_key"
        )
        if not isinstance(event["data"], Mapping):
            _fail("domain event.data")
        _event_data(
            event["event_type"], event["data"], before["width"], before["height"]
        )
    if value["classification"] != "committed" and value["committed_domain_events"]:
        _fail("non-committed frame carries committed domain events")
    delta = _exact(
        value["state_delta"],
        (
            "changed",
            "robot",
            "shelves",
            "completed_orders_added",
            "step_before",
            "step_after",
        ),
        "state_delta",
    )
    if (
        not isinstance(delta["changed"], bool)
        or not isinstance(delta["shelves"], list)
        or not isinstance(delta["completed_orders_added"], list)
    ):
        _fail("state_delta")
    _exact(
        delta["robot"],
        ("position_before", "position_after", "inventory_before", "inventory_after"),
        "state_delta.robot",
    )
    _integer(delta["step_before"], "state_delta.step_before")
    _integer(delta["step_after"], "state_delta.step_after")
    expected_delta = _delta(before, after, value["classification"] == "committed")
    if dict(delta) != expected_delta:
        _fail("state_delta does not match snapshots")
    if any(
        not isinstance(order, str) or not order
        for order in delta["completed_orders_added"]
    ):
        _fail("state_delta.completed_orders_added")
    for shelf in delta["shelves"]:
        shelf = _exact(
            shelf, ("shelf_id", "item_id_before", "item_id_after"), "state_delta.shelf"
        )
        for name in ("shelf_id", "item_id_before", "item_id_after"):
            if shelf[name] is not None:
                _string(shelf[name], "state_delta.shelf." + name, nonempty=True)
    if value["classification"] != "committed" and (
        before != after
        or delta["changed"]
        or delta["shelves"]
        or delta["completed_orders_added"]
        or delta["step_before"] != delta["step_after"]
    ):
        _fail("non-committed frame changed state")
    counters = _exact(value["counters"], ("before", "after"), "frame counters")
    _counters(counters["before"], "counters.before")
    _counters(counters["after"], "counters.after")
    return _canonical(value)


def validate_projection(mapping: Mapping) -> VisualizerProjection:
    """Fail closed unless ``mapping`` is exactly the v1 safe JSON contract."""
    value = _exact(mapping, _TOP, "projection")
    if (
        value["projection_type"] != "VisualizerProjection"
        or value["schema_version"] != 1
    ):
        _fail("projection type/version")
    logical = _exact(value["logical_run"], _LOGICAL, "logical_run")
    if (
        not isinstance(logical["logical_run_id"], str)
        or _LOGICAL_RUN.fullmatch(logical["logical_run_id"]) is None
    ):
        _fail("logical_run_id")
    for name in (
        "task_version",
        "seed",
        "generator",
        "generator_version",
        "slot_layout",
        "policy_id",
    ):
        _string(logical[name], name, nonempty=True)
    if not logical["seed"].isdigit() or (
        len(logical["seed"]) > 1 and logical["seed"].startswith("0")
    ):
        _fail("seed must be canonical decimal")
    _digest(logical["policy_digest"], "policy_digest")
    for name in ("provider", "model"):
        if logical[name] is not None:
            _string(logical[name], name, nonempty=True)
    for name in ("strategy_context_digest", "tool_schema_digest", "prompt_digest"):
        _digest(logical[name], name, True)
    if (
        not isinstance(logical["grants"], list)
        or logical["grants"] != sorted(logical["grants"])
        or any(item not in ("move", "pick", "place") for item in logical["grants"])
    ):
        _fail("grants")
    budget = _exact(logical["budget"], ("maximum",), "budget")
    _integer(budget["maximum"], "budget.maximum")
    if logical["fault_tape"] is not None:
        fault = _exact(
            logical["fault_tape"], ("fault_id", "hook", "kind"), "fault_tape"
        )
        for item in fault.values():
            _string(item, "fault_tape", nonempty=True)
    initial = _world(value["initial_world"], "initial_world")
    if not isinstance(value["frames"], list):
        _fail("frames")
    frames = [
        _validate_frame(frame, index, logical["logical_run_id"])
        for index, frame in enumerate(value["frames"], 1)
    ]
    if [frame["policy_step"] for frame in frames] != list(range(1, len(frames) + 1)):
        _fail("policy steps must be contiguous")
    chain = initial
    for frame in frames:
        if frame["state_before"] != chain:
            _fail("frame states must chain from the initial world")
        chain = frame["state_after"]
    counters = _counters(value["counters"], "counters")
    if frames and counters != frames[-1]["counters"]["after"]:
        _fail("final counter prefix differs")
    terminal = _exact(
        value["terminal_result"],
        (
            "outcome",
            "outcome_authority",
            "verified_score",
            "completed_count",
            "total_count",
            "final_state",
            "final_state_digest",
            "verifier_status",
            "verifier_version",
        ),
        "terminal_result",
    )
    if (
        terminal["outcome"]
        not in ("completed", "duplicate_effect", "safe_abort", "unresolved_outcome")
        or terminal["outcome_authority"] != "independent_verifier"
        or terminal["verifier_status"] != "valid"
    ):
        _fail("terminal authority")
    for name in ("verified_score", "completed_count", "total_count"):
        _integer(terminal[name], "terminal." + name)
    final_state = _world(terminal["final_state"], "final_state")
    if final_state != chain:
        _fail("final state must equal the last reconstructed frame state")
    _digest(terminal["final_state_digest"], "final_state_digest")
    _string(terminal["verifier_version"], "verifier_version", nonempty=True)
    digests = _exact(value["semantic_digests"], _DIGESTS, "semantic_digests")
    for name in _DIGESTS:
        _digest(digests[name], name, name not in _REQUIRED_DIGESTS)
    # A correctly rebound projection_digest must never launder forged
    # digest claims: state digests are recomputed against the canonical
    # worlds they bind, the two policy digest aliases must agree, and
    # terminal_result's duplicate final-state claim must agree with the
    # semantic digest that is itself bound to canonical final bytes.
    if digests["initial_state_digest"] != sha256_digest(canonical_json_bytes(initial)):
        _fail("semantic initial_state_digest does not bind the initial world")
    if digests["final_state_digest"] != sha256_digest(
        canonical_json_bytes(final_state)
    ):
        _fail("semantic final_state_digest does not bind the final state")
    if terminal["final_state_digest"] != digests["final_state_digest"]:
        _fail("terminal final_state_digest does not bind the final state")
    if digests["policy_digest"] != logical["policy_digest"]:
        _fail("policy digest aliases disagree")
    for name in ("strategy_context_digest", "tool_schema_digest", "prompt_digest"):
        if digests[name] != logical[name]:
            _fail(name + " digest aliases disagree")
    # Config, task, trace, and fault-tape digests are source-bound claims:
    # build_projection binds them to verified artifacts; this standalone
    # validator only preserves their grammar and explicit nullability.
    digest_input = _canonical(value)
    del digest_input["semantic_digests"]["projection_digest"]
    if digests["projection_digest"] != sha256_digest(
        canonical_json_bytes(digest_input)
    ):
        _fail("projection_digest does not bind canonical projection content")
    return VisualizerProjection(_canonical(value))


def canonical_projection_bytes(projection: VisualizerProjection) -> bytes:
    if not isinstance(projection, VisualizerProjection):
        raise TypeError("canonical_projection_bytes requires VisualizerProjection")
    return canonical_json_bytes(projection.to_mapping())


def _safe_call(record: Mapping, logical_run_id: str) -> dict:
    """Return only a validated v0 call; unsafe source fields become ``null``."""
    from robot_factory_arena.contracts.tool_call import validate_operation_key

    tool, arguments, key = (
        record.get(name) for name in ("tool", "arguments", "operation_key")
    )
    safe_tool = tool if tool in _ARGUMENT_KEYS else None
    result = {
        "schema_version": 1 if safe_tool is not None else None,
        "tool": safe_tool,
        "arguments": None,
        "operation_key": (
            key
            if isinstance(key, str) and validate_operation_key(key, logical_run_id)
            else None
        ),
    }
    if (
        isinstance(safe_tool, str)
        and isinstance(arguments, Mapping)
        and set(arguments) == set(_ARGUMENT_KEYS[safe_tool])
        and all(_safe_argument(name, item) for name, item in arguments.items())
    ):
        result["arguments"] = dict(arguments)
    return result


def _classification(status: Any, reason: Any) -> str:
    if status == "committed":
        return "committed"
    if status == "idempotent_replay":
        return "idempotent_replay"
    if reason == "malformed_call":
        return "malformed"
    if reason == "unsupported_tool":
        return "unsupported"
    if reason in ("invalid_arguments", "operation_key_invalid"):
        return "invalid"
    if reason in ("missing_grant", "grant_scope_mismatch"):
        return "unauthorized"
    return "rejected"


def _delta(before: Mapping, after: Mapping, changed: bool) -> dict:
    robot_before, robot_after = before["robot"], after["robot"]
    shelves_before = {s["shelf_id"]: s["item_id"] for s in before["shelves"]}
    shelves = [
        {
            "shelf_id": shelf["shelf_id"],
            "item_id_before": shelves_before[shelf["shelf_id"]],
            "item_id_after": shelf["item_id"],
        }
        for shelf in after["shelves"]
        if shelves_before[shelf["shelf_id"]] != shelf["item_id"]
    ]
    completed = [
        order
        for order in after["completed_orders"]
        if order not in before["completed_orders"]
    ]
    return {
        "changed": changed,
        "robot": {
            "position_before": robot_before["position"],
            "position_after": robot_after["position"],
            "inventory_before": robot_before["inventory_item_id"],
            "inventory_after": robot_after["inventory_item_id"],
        },
        "shelves": shelves,
        "completed_orders_added": completed,
        "step_before": before["step"],
        "step_after": after["step"],
    }


def _counter_increment(prefix: dict, record: Mapping, retry: bool) -> None:
    if record.get("tool") is not None:
        prefix["tool_attempts"] += 1
    if retry:
        prefix["internal_retries"] += 1
        return
    if record.get("status") == "committed":
        prefix["committed_effects"] += 1
    elif (
        record.get("reason")
        in (
            "malformed_call",
            "unsupported_tool",
            "invalid_arguments",
            "operation_key_invalid",
            "missing_grant",
            "grant_scope_mismatch",
        )
        or record.get("status") == "idempotent_replay"
    ):
        prefix["contract_violations"] += 1


def _source_projection(release: Any) -> dict:
    artifacts: Any = getattr(release, "artifacts", None)
    receipt: Any = getattr(release, "receipt", None)
    verification: Any = getattr(release, "verification", None)
    if (
        not getattr(release, "verified", False)
        or not isinstance(receipt, Mapping)
        or not getattr(verification, "valid", False)
    ):
        raise ValueError("projection requires an approved verified release")
    replay_result = getattr(release, "replay_result", None)
    if not getattr(replay_result, "matches_receipt", False):
        raise ValueError("projection requires a receipt-matching replay handoff")
    if artifacts is None or not all(
        isinstance(getattr(artifacts, name, None), Mapping)
        for name in ("task", "config", "operation_log", "final_state")
    ):
        raise ValueError("release lacks validated source artifacts")
    task, config, log = artifacts.task, artifacts.config, artifacts.operation_log
    operations = log.get("operations")
    if not isinstance(operations, list):
        raise ValueError("operation log is missing")
    if any(
        isinstance(entry, Mapping) and not isinstance(entry.get("retry"), bool)
        for entry in operations
    ):
        raise ValueError("ambiguous operation records")
    primary = [
        entry
        for entry in operations
        if isinstance(entry, Mapping) and not entry.get("retry")
    ]
    if len(primary) != len(operations) - sum(
        1 for entry in operations if isinstance(entry, Mapping) and entry.get("retry")
    ):
        raise ValueError("ambiguous operation records")
    if [entry.get("policy_step") for entry in primary] != list(
        range(1, len(primary) + 1)
    ):
        raise ValueError("primary policy steps are not contiguous")
    initial = _world(task.get("initial_state"), "task.initial_state")
    final = _world(artifacts.final_state, "final_state")
    if getattr(artifacts, "receipt", receipt) != receipt:
        raise ValueError("release receipt disagrees with validated artifacts")
    config_digest = _bound_digest(config, receipt.get("config_digest"), "config")
    fault_digest = _bound_digest(
        getattr(artifacts, "fault_tape", None),
        receipt.get("fault_tape_digest"),
        "fault tape",
    )
    trace_digest = sha256_digest(
        b"".join(
            canonical_json_bytes(record) + b"\n" for record in artifacts.trace_records
        )
    )
    if receipt.get("trace_digest") != trace_digest:
        raise ValueError("trace digest claim does not match its canonical source")
    final_digest = _bound_digest(
        final, receipt.get("final_state_digest"), "final state"
    )
    _bound_digest(log, receipt.get("operation_log_digest"), "operation log")
    boundaries = getattr(artifacts, "boundaries", {}) or {}
    if not isinstance(boundaries, Mapping):
        raise ValueError("boundaries are ambiguous")
    metadata = getattr(
        release,
        "logical_metadata",
        VisualizerLogicalMetadata(None, None, None, None, None),
    )
    if not isinstance(metadata, VisualizerLogicalMetadata):
        raise ValueError("logical metadata is invalid")
    logical_id = receipt.get("logical_run_id")
    if not isinstance(logical_id, str) or _LOGICAL_RUN.fullmatch(logical_id) is None:
        raise ValueError("receipt logical identity is invalid")
    budget = config.get("budget")
    if not isinstance(budget, int) or budget < len(primary):
        raise ValueError("budget source is invalid")
    # The recorder persists the semantic (unresolved) tape in config; its
    # resolved operation key is deliberately not projection metadata.
    tape = config.get("fault_tape_spec")
    fault = None
    if tape is not None:
        if not isinstance(tape, Mapping):
            raise ValueError("fault tape source is ambiguous")
        fault = {key: tape.get(key) for key in ("fault_id", "hook", "kind")}
        if any(not isinstance(item, str) or not item for item in fault.values()):
            raise ValueError("fault tape is invalid")
    prefix = dict.fromkeys(_COUNTERS, 0)
    prefix["terminal_checks"] = 1
    current, frames, previous_result = initial, [], None
    traces = getattr(artifacts, "trace_records", ())
    if not isinstance(traces, (tuple, list)) or any(
        not isinstance(trace, Mapping) for trace in traces
    ):
        raise ValueError("trace source is ambiguous")
    seqs = [trace.get("seq") for trace in traces]
    if (
        not seqs
        or not all(isinstance(seq, int) and not isinstance(seq, bool) for seq in seqs)
        or seqs != list(range(seqs[0], seqs[0] + len(seqs)))
    ):
        raise ValueError("trace ordering is ambiguous")

    primary_steps = {entry["policy_step"] for entry in primary}
    if any(
        entry.get("retry") and entry.get("policy_step") not in primary_steps
        for entry in operations
    ):
        raise ValueError("retry does not belong to a primary step")
    for step, record in enumerate(primary, 1):
        step_traces = [trace for trace in traces if trace.get("policy_step") == step]
        if sum(trace.get("event") == "policy_decision" for trace in step_traces) != 1:
            raise ValueError("policy decision trace is missing or ambiguous")
        retries = [
            entry
            for entry in operations
            if isinstance(entry, Mapping)
            and entry.get("retry")
            and entry.get("policy_step") == step
        ]
        if len(retries) > 1:
            raise ValueError("multiple retries are ambiguous")
        before = dict(prefix)
        prefix["policy_steps"] += 1
        call = _safe_call(record, logical_id)
        classification = _classification(record.get("status"), record.get("reason"))
        committed = classification == "committed"
        after = current
        if committed:
            boundary = boundaries.get(step)
            if (
                not isinstance(boundary, Mapping)
                or boundary.get("operation_record") != record
            ):
                raise ValueError("committed source boundary is missing or mismatched")
            after = _world(boundary.get("state_snapshot"), "boundary state")
        _counter_increment(prefix, record, False)
        attempts = [
            {
                "attempt_index": 1,
                "kind": "primary",
                "tool_call": call,
                "status": record.get("status")
                if record.get("status") in ("committed", "idempotent_replay")
                else "rejected",
                "reason": record.get("reason"),
                "response": "lost" if retries else "received",
                "fault_ids": list(retries[0].get("fault_ids", [])) if retries else [],
            }
        ]
        subevents = []
        if retries:
            retry = retries[0]
            fault_records = [
                trace for trace in step_traces if trace.get("event") == "fault_injected"
            ]
            retry_records = [
                trace for trace in step_traces if trace.get("event") == "retry"
            ]
            if (
                not retry.get("fault_ids")
                or not committed
                or len(fault_records) != 1
                or len(retry_records) != 1
                or fault_records[0].get("operation_key") != call["operation_key"]
                or retry_records[0].get("operation_key") != call["operation_key"]
                or fault_records[0].get("payload", {}).get("fault_id")
                != retry["fault_ids"][0]
                or retry_records[0].get("payload", {}).get("status")
                != retry.get("status")
            ):
                raise ValueError("retry/fault trace pairing is ambiguous")
            _counter_increment(prefix, retry, True)
            prefix["terminal_checks"] += 1
            fault_id = retry["fault_ids"][0]
            if fault is None or fault_id != fault["fault_id"]:
                raise ValueError("retry fault identity mismatches tape")
            subevents = [
                {
                    "sequence": 1,
                    "kind": "fault_injected",
                    "operation_key": call["operation_key"],
                    "fault_id": fault_id,
                    "hook": fault["hook"],
                    "fault_kind": fault["kind"],
                    "attempt_index": 1,
                    "status": "lost",
                    "reason": "response_lost",
                },
                {
                    "sequence": 2,
                    "kind": "internal_retry",
                    "operation_key": call["operation_key"],
                    "fault_id": None,
                    "hook": None,
                    "fault_kind": None,
                    "attempt_index": 2,
                    "status": retry.get("status"),
                    "reason": retry.get("reason"),
                },
            ]
            attempts.append(
                {
                    "attempt_index": 2,
                    "kind": "internal_retry",
                    "tool_call": _safe_call(retry, logical_id),
                    "status": retry.get("status"),
                    "reason": retry.get("reason"),
                    "response": "received",
                    "fault_ids": list(retry["fault_ids"]),
                }
            )
        prefix["terminal_checks"] += 1
        events = list(record.get("events", [])) if committed else []
        frame = {
            "policy_step": step,
            "observation": {
                "policy_steps_before": step - 1,
                "policy_steps_remaining_before": budget - (step - 1),
                "policy_steps_maximum": budget,
                "last_result": previous_result,
            },
            "tool_call": call,
            "candidate_shape": "tool_call"
            if call["tool"]
            else (
                "non_mapping"
                if any(
                    isinstance(t, Mapping)
                    and t.get("policy_step") == step
                    and isinstance(t.get("payload"), Mapping)
                    and "candidate_type" in t["payload"]
                    for t in traces
                )
                else "mapping"
            ),
            "classification": classification,
            "reason": record.get("reason"),
            "attempts": attempts,
            "subevents": subevents,
            "committed_domain_events": events,
            "state_before": current,
            "state_after": after,
            "state_delta": _delta(current, after, committed),
            "counters": {"before": before, "after": dict(prefix)},
        }
        frames.append(frame)
        current = after
        final_attempt = attempts[-1]
        final_call = final_attempt["tool_call"]
        if not isinstance(final_call, Mapping):
            raise ValueError("attempt tool call is ambiguous")
        previous_result = {
            "status": final_attempt["status"],
            "operation_key": final_call.get("operation_key") or "",
            "reason": final_attempt["reason"],
        }
    if current != final or dict(prefix) != receipt.get("counters"):
        raise ValueError("source final state or counters disagree")
    digests = {
        "config_digest": config_digest,
        "task_digest": None,
        "policy_digest": config.get("policy_digest"),
        "fault_tape_digest": fault_digest,
        "trace_digest": trace_digest,
        "initial_state_digest": None,
        "final_state_digest": final_digest,
        "projection_digest": None,
        "strategy_context_digest": metadata.strategy_context_digest,
        "tool_schema_digest": metadata.tool_schema_digest,
        "prompt_digest": metadata.prompt_digest,
    }
    digests["task_digest"] = sha256_digest(canonical_json_bytes(task))
    digests["initial_state_digest"] = sha256_digest(canonical_json_bytes(initial))
    # The digest input is the canonical projection content with the
    # projection_digest key omitted, never a null placeholder.
    del digests["projection_digest"]
    mapping = {
        "projection_type": "VisualizerProjection",
        "schema_version": 1,
        "logical_run": {
            "logical_run_id": logical_id,
            "task_version": task.get("task_version"),
            "seed": str(task.get("seed")),
            "generator": task.get("config_identity", {}).get("generator"),
            "generator_version": task.get("config_identity", {}).get(
                "generator_version"
            ),
            "slot_layout": task.get("config_identity", {}).get("slot_layout"),
            "policy_id": config.get("policy_id"),
            "policy_digest": config.get("policy_digest"),
            "provider": metadata.provider,
            "model": metadata.model,
            "strategy_context_digest": metadata.strategy_context_digest,
            "tool_schema_digest": metadata.tool_schema_digest,
            "prompt_digest": metadata.prompt_digest,
            "grants": list(config.get("grants", [])),
            "budget": {"maximum": budget},
            "fault_tape": fault,
        },
        "initial_world": initial,
        "frames": frames,
        "counters": dict(prefix),
        "terminal_result": {
            "outcome": verification.outcome,
            "outcome_authority": "independent_verifier",
            "verified_score": verification.verified_score,
            "completed_count": verification.completed_count,
            "total_count": verification.total_count,
            "final_state": final,
            "final_state_digest": receipt.get("final_state_digest"),
            "verifier_status": "valid",
            "verifier_version": verification.verifier_version,
        },
        "semantic_digests": digests,
    }
    digests["projection_digest"] = sha256_digest(
        canonical_json_bytes(copy.deepcopy(mapping))
    )
    return mapping


def _bound_digest(value: Any, claim: Any, label: str) -> str | None:
    """Bind a receipt digest claim to its canonical source, never just copy it."""
    actual = None if value is None else sha256_digest(canonical_json_bytes(value))
    if claim != actual:
        raise ValueError(label + " digest claim does not match its canonical source")
    return actual


def build_projection(release: Any) -> VisualizerProjection:
    """Reconstruct a projection from the verified, validated release sources."""
    if isinstance(getattr(release, "projection_mapping", None), Mapping):
        if not getattr(release, "verified", False):
            raise ValueError("projection requires an approved verified release")
        return validate_projection(release.projection_mapping)
    return validate_projection(_source_projection(release))
