"""Independent structural invariants and the minimal warehouse model (T-18).

The verifier's private reconstruction layer. It re-declares every artifact
schema it checks (it never imports an evidence writer) and re-implements the
REQ-04 transition semantics over plain canonical-JSON mappings, so legality is
validated independently of ``domain.transition``.

Import allowlist (AST-tested, tests/test_verifier_dependencies.py): the
stdlib, ``contracts.outcomes`` (shared frozen value objects),
``evidence.canonical`` (the one shared serialization authority — artifact byte
identity must be interpreted identically), ``domain.task`` (the golden-frozen
REQ-01 seed formulas: verifier and runtime must interpret a seed identically),
and ``verification.*`` siblings. Forbidden: ``runtime.*``, ``policies.*``,
``domain.transition``, runtime scoring, and every evidence writer module —
the trace-chain rules are re-derived here, not imported.
"""

import copy
import re
from collections.abc import Mapping

from robot_factory_arena.domain.task import TASK_VERSION
from robot_factory_arena.evidence.canonical import (
    canonical_jsonl_line,
    sha256_digest,
)

SCHEMA_VERSION = 1
BUDGET_MAXIMUM = 100

__all__ = [
    "BOUNDARY_KEYS",
    "BUDGET_MAXIMUM",
    "CONFIG_KEYS",
    "DOMAIN_TRACE_EVENTS",
    "EVENT_KEYS",
    "FAULT_HOOK",
    "FAULT_ID",
    "FAULT_KEYS",
    "FAULT_KIND",
    "FAULT_TAPE_KEYS",
    "LOG_KEYS",
    "OPERATION_KEYS",
    "PROTOCOL_REASONS",
    "REPLAY_KEYS",
    "RUNTIME_TRACE_EVENTS",
    "SCHEMA_VERSION",
    "STATE_KEYS",
    "TARGET_KEYS",
    "TASK_IDENTITY_KEYS",
    "TASK_KEYS",
    "TASK_VERSION",
    "TRACE_EVENT_TYPES",
    "TRACE_RECORD_FIELDS",
    "canonical_trace_digest",
    "independently_apply",
    "is_digest",
    "is_valid_logical_run_id",
    "validate_operation_key",
]

# -- locked auxiliary shapes (re-declared; never imported from writers) ------

TASK_KEYS = (
    "config_identity",
    "height",
    "initial_state",
    "orders",
    "schema_version",
    "seed",
    "shelves",
    "slot_positions",
    "task_version",
    "width",
)
CONFIG_KEYS = (
    "budget",
    "fault_tape_spec",
    "grants",
    "policy_digest",
    "policy_id",
    "schema_version",
    "seed",
    "task_version",
)
TARGET_KEYS = ("intent", "policy_step")
FAULT_SPEC_KEYS = ("fault_id", "hook", "kind", "schema_version", "target")
LOG_KEYS = ("logical_run_id", "operations", "schema_version")
OPERATION_KEYS = (
    "arguments",
    "events",
    "fault_ids",
    "operation_key",
    "policy_step",
    "reason",
    "retry",
    "state_after",
    "state_before",
    "status",
    "tool",
)
EVENT_KEYS = ("data", "event_type", "operation_key", "schema_version")
BOUNDARY_KEYS = (
    "arguments",
    "commit_marker",
    "events",
    "operation_key",
    "operation_record",
    "policy_step",
    "schema_version",
    "state_snapshot",
    "tool",
)
COMMIT_MARKER_KEYS = ("commit_proven", "snapshot_digest")
REPLAY_KEYS = (
    "budget",
    "fault_tape",
    "grants",
    "logical_run_id",
    "operations",
    "schema_version",
    "task_identity",
)
TASK_IDENTITY_KEYS = ("config_digest", "seed", "task_version")
FAULT_TAPE_KEYS = ("faults", "schema_version")
FAULT_KEYS = ("fault_id", "hook", "kind", "operation_key")

# Exactly the 13 REQ-03 trace fields and the closed trace event vocabulary.
TRACE_RECORD_FIELDS = (
    "schema_version",
    "seq",
    "logical_run_id",
    "policy_step",
    "event",
    "operation_key",
    "tool",
    "arguments",
    "state_before",
    "state_after",
    "payload",
    "prev_event_hash",
    "event_hash",
)
RUNTIME_TRACE_EVENTS = (
    "budget_exhausted",
    "fault_injected",
    "observation_emitted",
    "policy_decision",
    "retry",
    "run_finished",
    "run_started",
    "tool_committed",
    "tool_rejected",
)
DOMAIN_TRACE_EVENTS = ("item_picked", "order_delivered", "robot_moved")
TRACE_EVENT_TYPES = RUNTIME_TRACE_EVENTS + DOMAIN_TRACE_EVENTS
DOMAIN_EVENT_TYPES = DOMAIN_TRACE_EVENTS

OPERATION_STATUSES = ("committed", "idempotent_replay", "rejected")
SUPPORTED_TOOLS = ("move", "pick", "place")

# The one v0 demonstrator fault (REQ-05), closed.
FAULT_ID = "response_lost_after_commit"
FAULT_HOOK = "after_commit_before_response"
FAULT_KIND = "response_lost"

# Protocol (contract) violation reasons whose rejections increment
# ``contract_violations`` (REQ-02); domain-legality rejections do not.
PROTOCOL_REASONS = frozenset(
    {
        "malformed_call",
        "unsupported_tool",
        "operation_key_invalid",
        "missing_grant",
        "grant_scope_mismatch",
        "operation_key_conflict",
        "invalid_arguments",
    }
)

# Canonical state mapping shapes (REQ-03 WorldState serialization).
STATE_KEYS = (
    "completed_orders",
    "height",
    "orders",
    "robot",
    "schema_version",
    "shelves",
    "step",
    "width",
)

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SEED_TEXT_RE = re.compile(r"(0|[1-9][0-9]*)")
_OPERATION_KEY_RE = re.compile(
    r"op/(lr_[0-9a-f]{64})/step-(0|[1-9][0-9]*)/intent-([A-Za-z0-9._-]{1,64})"
)

# REQ-01 axes: north (0,+1), east (+1,0), south (0,-1), west (-1,0).
_MOVE_DELTAS = {
    "north": (0, 1),
    "east": (1, 0),
    "south": (0, -1),
    "west": (-1, 0),
}


def is_valid_logical_run_id(value) -> bool:
    """True iff value is ``lr_`` plus 64 lowercase hex characters."""
    return isinstance(value, str) and _LOGICAL_RUN_ID_RE.fullmatch(value) is not None


def is_digest(value) -> bool:
    """True iff value is ``sha256:`` plus 64 lowercase hex characters."""
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def is_canonical_seed(value) -> bool:
    """True iff value is canonical non-negative decimal text (REQ-01)."""
    return isinstance(value, str) and _SEED_TEXT_RE.fullmatch(value) is not None


def validate_operation_key(key, logical_run_id) -> bool:
    """True iff key matches the REQ-03 grammar and embeds exactly this run."""
    if not is_valid_logical_run_id(logical_run_id):
        return False
    if not isinstance(key, str):
        return False
    match = _OPERATION_KEY_RE.fullmatch(key)
    return match is not None and match.group(1) == logical_run_id


def canonical_trace_digest(records) -> str:
    """``trace_digest`` over the canonical LF-terminated record bytes."""
    return sha256_digest(b"".join(canonical_jsonl_line(record) for record in records))


# -- independent minimal warehouse model --------------------------------------


def _arguments_shape(arguments, expected_keys) -> bool:
    if not isinstance(arguments, Mapping) or set(arguments) != expected_keys:
        return False
    return all(isinstance(arguments[name], str) for name in expected_keys)


def independently_apply(state, tool, arguments):
    """Pure REQ-04 semantics over canonical state mappings (independent).

    Returns ``(new_state, events, reason)``. ``reason`` is None exactly when
    the transition commits; a committed result carries one or more events in
    the REQ-03 shape minus ``operation_key`` (the caller stitches the
    producing operation's key in before comparing with recorded evidence).
    The input state mapping is never mutated. Rejection precedence mirrors
    the closed taxonomy: move -> invalid_arguments/destination_out_of_grid;
    pick -> robot_not_at_shelf/inventory_not_empty/shelf_item_mismatch/
    item_not_pending; place -> not_at_base/inventory_empty/
    order_already_completed/order_item_mismatch.
    """
    if tool == "move":
        if (
            not isinstance(arguments, Mapping)
            or set(arguments) != {"direction"}
            or arguments["direction"] not in _MOVE_DELTAS
        ):
            return state, (), "invalid_arguments"
        direction = arguments["direction"]
        from_x, from_y = state["robot"]["position"]
        delta_x, delta_y = _MOVE_DELTAS[direction]
        to_x, to_y = from_x + delta_x, from_y + delta_y
        if not (0 <= to_x < state["width"] and 0 <= to_y < state["height"]):
            return state, (), "destination_out_of_grid"
        new_state = copy.deepcopy(state)
        new_state["robot"] = dict(new_state["robot"], position=[to_x, to_y])
        new_state["step"] = state["step"] + 1
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": "robot_moved",
            "data": {
                "from": [from_x, from_y],
                "to": [to_x, to_y],
                "direction": direction,
            },
        }
        return new_state, (event,), None

    if tool == "pick":
        if not _arguments_shape(arguments, {"shelf_id", "item_id"}):
            return state, (), "invalid_arguments"
        shelf_id = arguments["shelf_id"]
        item_id = arguments["item_id"]
        shelf = None
        for candidate in state["shelves"]:
            if candidate["shelf_id"] == shelf_id:
                shelf = candidate
                break
        if shelf is None or state["robot"]["position"] != shelf["position"]:
            return state, (), "robot_not_at_shelf"
        if state["robot"]["inventory_item_id"] is not None:
            return state, (), "inventory_not_empty"
        if shelf["item_id"] != item_id:
            return state, (), "shelf_item_mismatch"
        completed = set(state["completed_orders"])
        if not any(
            order["item_id"] == item_id and order["order_id"] not in completed
            for order in state["orders"]
        ):
            return state, (), "item_not_pending"
        new_state = copy.deepcopy(state)
        new_state["shelves"] = [
            dict(shelf_entry, item_id=None)
            if shelf_entry["shelf_id"] == shelf_id
            else shelf_entry
            for shelf_entry in new_state["shelves"]
        ]
        new_state["robot"] = dict(new_state["robot"], inventory_item_id=item_id)
        new_state["step"] = state["step"] + 1
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": "item_picked",
            "data": {"shelf_id": shelf_id, "item_id": item_id},
        }
        return new_state, (event,), None

    if tool == "place":
        if not _arguments_shape(arguments, {"order_id"}):
            return state, (), "invalid_arguments"
        order_id = arguments["order_id"]
        if state["robot"]["position"] != state["robot"]["base_position"]:
            return state, (), "not_at_base"
        item_id = state["robot"]["inventory_item_id"]
        if item_id is None:
            return state, (), "inventory_empty"
        completed = state["completed_orders"]
        order = None
        for candidate in state["orders"]:
            if candidate["order_id"] == order_id:
                order = candidate
                break
        if order is not None and order_id in completed:
            return state, (), "order_already_completed"
        if order is None or order["item_id"] != item_id:
            return state, (), "order_item_mismatch"
        new_state = copy.deepcopy(state)
        new_state["completed_orders"] = sorted(set(completed) | {order_id})
        new_state["robot"] = dict(new_state["robot"], inventory_item_id=None)
        new_state["step"] = state["step"] + 1
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": "order_delivered",
            "data": {"order_id": order_id, "item_id": item_id},
        }
        return new_state, (event,), None

    return state, (), "unsupported_tool"


def stitched_event(event, operation_key):
    """The recorded REQ-03 event shape for one engine event."""
    return {
        "schema_version": event["schema_version"],
        "event_type": event["event_type"],
        "operation_key": operation_key,
        "data": dict(event["data"]),
    }
