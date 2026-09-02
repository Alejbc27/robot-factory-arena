"""Sole domain transition authority: pure apply() for move/pick/place (REQ-04).

``apply`` is pure and functional: it constructs a new frozen WorldState, never
mutates its input, and emits only the three v0 domain event types
(``robot_moved``, ``item_picked``, ``order_delivered``) in the exact REQ-03
event shape. Every move/pick/place precondition is enforced as a
deterministic rejection carrying a reason from the closed taxonomy. It knows
nothing of grants, ledgers, faults, runners, traces, or reports: ToolBay
performs those outer concerns before calling it (design §2).

Design amendment (documented in docs/sdd/apply-progress.md): the design
sketch lists Operation(tool, arguments), but REQ-03 requires every domain
event to carry the operation_key of the operation that produced it, so
Operation carries operation_key as a third field. This is the minimal
resolution of that gap and keeps apply() the single transition authority.

Deterministic rejection precedence (documented; closed taxonomy only):
- move:  argument shape -> destination_out_of_grid
- pick:  robot_not_at_shelf (unknown shelf or robot elsewhere) ->
         inventory_not_empty -> shelf_item_mismatch (empty shelf or
         different item) -> item_not_pending (no incomplete order requests
         the item)
- place: not_at_base -> inventory_empty -> order_already_completed ->
         order_item_mismatch (unknown order id or carried item mismatch)
"""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from robot_factory_arena.contracts.outcomes import (
    CLOSED_REASONS,
    SUPPORTED_TOOLS,
)
from robot_factory_arena.domain.model import (
    GRID_SIZE,
    RobotState,
    ShelfState,
    WorldState,
)

SCHEMA_VERSION = 1
COMMITTED = "committed"
REJECTED = "rejected"

# The complete set of v0 domain event types (REQ-03); closed.
DOMAIN_EVENT_TYPES = ("item_picked", "order_delivered", "robot_moved")

# REQ-01 axes: north (0,+1), east (+1,0), south (0,-1), west (-1,0).
_MOVE_DELTAS = {
    "north": (0, 1),
    "east": (1, 0),
    "south": (0, -1),
    "west": (-1, 0),
}


@dataclass(frozen=True)
class Operation:
    """One normalized domain operation handed to apply().

    The operation key travels with the operation so every emitted domain
    event can carry it (REQ-03); grammar validation stays in
    contracts.tool_call, which is the only producer of real keys.
    """

    tool: str
    arguments: Mapping
    operation_key: str

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool:
            raise TypeError("tool must be a non-empty str")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        if not isinstance(self.operation_key, str) or not self.operation_key:
            raise ValueError("operation_key must be a non-empty str")
        object.__setattr__(
            self, "arguments", MappingProxyType(dict(self.arguments))
        )


@dataclass(frozen=True)
class DomainEvent:
    """Exactly the REQ-03 event shape {schema_version, event_type,
    operation_key, data}."""

    schema_version: int
    event_type: str
    operation_key: str
    data: Mapping

    def __post_init__(self):
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise TypeError("schema_version must be an int")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if self.event_type not in DOMAIN_EVENT_TYPES:
            raise ValueError(
                f"event_type must be one of {DOMAIN_EVENT_TYPES}"
            )
        if not isinstance(self.operation_key, str) or not self.operation_key:
            raise ValueError("operation_key must be a non-empty str")
        if not isinstance(self.data, Mapping):
            raise TypeError("data must be a mapping")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    def to_mapping(self):
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "operation_key": self.operation_key,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class TransitionResult:
    """The pure outcome of one apply() call.

    A committed result carries the new state and one or more events with no
    reason; a rejected result carries the unchanged input state, no events,
    and one closed-taxonomy reason.
    """

    state: WorldState
    status: str
    events: tuple[DomainEvent, ...]
    reason: str | None

    def __post_init__(self):
        if not isinstance(self.state, WorldState):
            raise TypeError("state must be a WorldState")
        if self.status not in (COMMITTED, REJECTED):
            raise ValueError(
                f"status must be {COMMITTED!r} or {REJECTED!r}"
            )
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, DomainEvent) for event in self.events
        ):
            raise TypeError("events must be a tuple of DomainEvent")
        if self.status == COMMITTED:
            if self.reason is not None:
                raise ValueError("a committed result carries no reason")
            if not self.events:
                raise ValueError(
                    "a committed transition emits one or more domain events"
                )
            return
        if self.events:
            raise ValueError("a rejected result emits no events")
        if self.reason not in CLOSED_REASONS:
            raise ValueError(
                f"reason {self.reason!r} is outside the closed taxonomy"
            )


def _reject(state, reason):
    return TransitionResult(state, REJECTED, (), reason)


def _move_arguments_ok(arguments) -> bool:
    if not isinstance(arguments, Mapping) or set(arguments) != {"direction"}:
        return False
    direction = arguments["direction"]
    return isinstance(direction, str) and direction in _MOVE_DELTAS


def _pick_arguments_ok(arguments) -> bool:
    if not isinstance(arguments, Mapping):
        return False
    if set(arguments) != {"shelf_id", "item_id"}:
        return False
    return isinstance(arguments["shelf_id"], str) and isinstance(
        arguments["item_id"], str
    )


def _place_arguments_ok(arguments) -> bool:
    if not isinstance(arguments, Mapping):
        return False
    if set(arguments) != {"order_id"}:
        return False
    return isinstance(arguments["order_id"], str)


def _shelf_by_id(state, shelf_id):
    for shelf in state.shelves:
        if shelf.shelf_id == shelf_id:
            return shelf
    return None


def _order_by_id(state, order_id):
    for order in state.orders:
        if order.order_id == order_id:
            return order
    return None


def _in_grid(x, y) -> bool:
    return 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE


def _new_state(state, robot, shelves, completed_orders):
    return WorldState(
        schema_version=state.schema_version,
        width=state.width,
        height=state.height,
        robot=robot,
        shelves=shelves,
        orders=state.orders,
        completed_orders=completed_orders,
        step=state.step + 1,
    )


def _apply_move(state, operation):
    arguments = operation.arguments
    if not _move_arguments_ok(arguments):
        return _reject(state, "invalid_arguments")
    direction = arguments["direction"]
    from_x, from_y = state.robot.position
    delta_x, delta_y = _MOVE_DELTAS[direction]
    to_x, to_y = from_x + delta_x, from_y + delta_y
    if not _in_grid(to_x, to_y):
        return _reject(state, "destination_out_of_grid")
    robot = RobotState(
        (to_x, to_y), state.robot.inventory_item_id, state.robot.base_position
    )
    new_state = _new_state(
        state, robot, state.shelves, state.completed_orders
    )
    event = DomainEvent(
        SCHEMA_VERSION,
        "robot_moved",
        operation.operation_key,
        {
            "from": [from_x, from_y],
            "to": [to_x, to_y],
            "direction": direction,
        },
    )
    return TransitionResult(new_state, COMMITTED, (event,), None)


def _apply_pick(state, operation):
    arguments = operation.arguments
    if not _pick_arguments_ok(arguments):
        return _reject(state, "invalid_arguments")
    shelf_id = arguments["shelf_id"]
    item_id = arguments["item_id"]
    shelf = _shelf_by_id(state, shelf_id)
    if shelf is None or state.robot.position != shelf.position:
        return _reject(state, "robot_not_at_shelf")
    if state.robot.inventory_item_id is not None:
        return _reject(state, "inventory_not_empty")
    if shelf.item_id != item_id:
        return _reject(state, "shelf_item_mismatch")
    completed = set(state.completed_orders)
    if not any(
        order.item_id == item_id and order.order_id not in completed
        for order in state.orders
    ):
        return _reject(state, "item_not_pending")
    shelves = tuple(
        ShelfState(existing.shelf_id, existing.position, None)
        if existing.shelf_id == shelf_id
        else existing
        for existing in state.shelves
    )
    robot = RobotState(
        state.robot.position, item_id, state.robot.base_position
    )
    new_state = _new_state(state, robot, shelves, state.completed_orders)
    event = DomainEvent(
        SCHEMA_VERSION,
        "item_picked",
        operation.operation_key,
        {"shelf_id": shelf_id, "item_id": item_id},
    )
    return TransitionResult(new_state, COMMITTED, (event,), None)


def _apply_place(state, operation):
    arguments = operation.arguments
    if not _place_arguments_ok(arguments):
        return _reject(state, "invalid_arguments")
    order_id = arguments["order_id"]
    if state.robot.position != state.robot.base_position:
        return _reject(state, "not_at_base")
    item_id = state.robot.inventory_item_id
    if item_id is None:
        return _reject(state, "inventory_empty")
    order = _order_by_id(state, order_id)
    if order is not None and order_id in state.completed_orders:
        return _reject(state, "order_already_completed")
    if order is None or order.item_id != item_id:
        return _reject(state, "order_item_mismatch")
    completed_orders = tuple(sorted(set(state.completed_orders) | {order_id}))
    robot = RobotState(state.robot.position, None, state.robot.base_position)
    new_state = _new_state(state, robot, state.shelves, completed_orders)
    event = DomainEvent(
        SCHEMA_VERSION,
        "order_delivered",
        operation.operation_key,
        {"order_id": order_id, "item_id": item_id},
    )
    return TransitionResult(new_state, COMMITTED, (event,), None)


_APPLIERS = {
    "move": _apply_move,
    "pick": _apply_pick,
    "place": _apply_place,
}


def apply(state: WorldState, operation: Operation) -> TransitionResult:
    """Apply one operation to a frozen state, purely.

    Returns a committed result with the new frozen state and its domain
    events, or a rejected result with the unchanged input state and one
    closed-taxonomy reason. The input state is never mutated; nothing outside
    the domain (grants, ledger, faults, runner, evidence) is consulted.
    """
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    if not isinstance(operation, Operation):
        raise TypeError("operation must be an Operation")
    tool = operation.tool
    if tool not in SUPPORTED_TOOLS:
        return _reject(state, "unsupported_tool")
    return _APPLIERS[tool](state, operation)
