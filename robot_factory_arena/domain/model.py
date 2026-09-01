"""Immutable warehouse state values and exact REQ-03 serialization.

Owns only serializable warehouse values and their validation. It knows no
runner, capabilities, faults, reports, providers, Arena, or verifier (design
§2). All value objects are frozen dataclasses; collections crossing the
boundary are tuples in semantic order. Constructors validate rather than
normalize: unsorted input, mutable collections, out-of-grid coordinates, and
malformed IDs are rejected so no hidden mutation or implicit default can
enter the evidence chain (REQ-03, REQ-04, REQ-07).
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass

SCHEMA_VERSION = 1
GRID_SIZE = 10

_SHELF_ID_RE = re.compile(r"shelf-(0[0-9])")
_ITEM_ID_RE = re.compile(r"item-(0[0-9])")
_ORDER_ID_RE = re.compile(r"order-(0[0-9])")

_WORLD_KEYS = (
    "completed_orders", "height", "orders", "robot", "schema_version",
    "shelves", "step", "width",
)
_ROBOT_KEYS = ("base_position", "inventory_item_id", "position")
_SHELF_KEYS = ("item_id", "position", "shelf_id")
_ORDER_KEYS = ("item_id", "order_id", "quantity")


def _require_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an int, got {type(value).__name__}")
    return value


def _require_position_shape(value, label):
    """Positions are (x, y) int tuples; lists and other shapes are rejected."""
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError(f"{label} must be an (x, y) tuple of two ints")
    for index, coordinate in enumerate(value):
        _require_int(coordinate, f"{label}[{index}]")
    return value


def _require_in_grid(position, label):
    x, y = position
    if not (0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE):
        raise ValueError(
            f"{label} {position!r} is outside the {GRID_SIZE}x{GRID_SIZE} grid"
        )


def _require_id(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} {value!r} is not a valid v0 identifier")
    return value


def _require_exact_keys(mapping, expected, label):
    if not isinstance(mapping, Mapping):
        raise TypeError(f"{label} must be a mapping")
    extra = sorted(set(mapping) - set(expected))
    missing = sorted(set(expected) - set(mapping))
    if extra or missing:
        raise ValueError(
            f"{label} key drift; extra={extra} missing={missing}"
        )


def _require_strictly_sorted(values, label):
    if any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError(f"{label} must be strictly ascending: {values!r}")
    return values


@dataclass(frozen=True)
class RobotState:
    """Robot position, optional carried item, and the immutable base."""

    position: tuple[int, int]
    inventory_item_id: str | None
    base_position: tuple[int, int]

    def __post_init__(self):
        _require_position_shape(self.position, "position")
        _require_position_shape(self.base_position, "base_position")
        if self.inventory_item_id is not None:
            _require_id(self.inventory_item_id, _ITEM_ID_RE, "inventory_item_id")

    def to_mapping(self):
        return {
            "position": [self.position[0], self.position[1]],
            "inventory_item_id": self.inventory_item_id,
            "base_position": [self.base_position[0], self.base_position[1]],
        }

    @classmethod
    def from_mapping(cls, mapping):
        _require_exact_keys(mapping, _ROBOT_KEYS, "RobotState")
        return cls(
            position=tuple(mapping["position"]),
            inventory_item_id=mapping["inventory_item_id"],
            base_position=tuple(mapping["base_position"]),
        )


@dataclass(frozen=True)
class ShelfState:
    """One storage cell; item_id is null exactly when the shelf is empty."""

    shelf_id: str
    position: tuple[int, int]
    item_id: str | None

    def __post_init__(self):
        _require_id(self.shelf_id, _SHELF_ID_RE, "shelf_id")
        _require_position_shape(self.position, "position")
        if self.item_id is not None:
            _require_id(self.item_id, _ITEM_ID_RE, "item_id")

    def to_mapping(self):
        return {
            "shelf_id": self.shelf_id,
            "position": [self.position[0], self.position[1]],
            "item_id": self.item_id,
        }

    @classmethod
    def from_mapping(cls, mapping):
        _require_exact_keys(mapping, _SHELF_KEYS, "ShelfState")
        return cls(
            shelf_id=mapping["shelf_id"],
            position=tuple(mapping["position"]),
            item_id=mapping["item_id"],
        )


@dataclass(frozen=True)
class OrderState:
    """One delivery order; quantity is exactly 1 in v0."""

    order_id: str
    item_id: str
    quantity: int

    def __post_init__(self):
        _require_id(self.order_id, _ORDER_ID_RE, "order_id")
        _require_id(self.item_id, _ITEM_ID_RE, "item_id")
        _require_int(self.quantity, "quantity")
        if self.quantity != 1:
            raise ValueError("order quantity is exactly 1 in v0")

    def to_mapping(self):
        return {
            "order_id": self.order_id,
            "item_id": self.item_id,
            "quantity": self.quantity,
        }

    @classmethod
    def from_mapping(cls, mapping):
        _require_exact_keys(mapping, _ORDER_KEYS, "OrderState")
        return cls(
            order_id=mapping["order_id"],
            item_id=mapping["item_id"],
            quantity=mapping["quantity"],
        )


@dataclass(frozen=True)
class WorldState:
    """Exactly the REQ-03 top-level world shape; no hidden mutable state.

    ``shelves`` sorts by shelf_id, ``orders`` by order_id, and
    ``completed_orders`` lexicographically; ``step`` advances only on a
    committed move/pick/place — never on rejection, terminal check, or retry.
    """

    schema_version: int
    width: int
    height: int
    robot: RobotState
    shelves: tuple[ShelfState, ...]
    orders: tuple[OrderState, ...]
    completed_orders: tuple[str, ...]
    step: int

    def __post_init__(self):
        _require_int(self.schema_version, "schema_version")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        _require_int(self.width, "width")
        _require_int(self.height, "height")
        if self.width != GRID_SIZE or self.height != GRID_SIZE:
            raise ValueError("v0 world geometry is fixed at 10x10")
        if not isinstance(self.robot, RobotState):
            raise TypeError("robot must be a RobotState")
        _require_in_grid(self.robot.position, "robot.position")
        _require_in_grid(self.robot.base_position, "robot.base_position")
        if not isinstance(self.shelves, tuple):
            raise TypeError("shelves must be a tuple of ShelfState")
        if not isinstance(self.orders, tuple):
            raise TypeError("orders must be a tuple of OrderState")
        if not isinstance(self.completed_orders, tuple):
            raise TypeError("completed_orders must be a tuple of str")
        for shelf in self.shelves:
            if not isinstance(shelf, ShelfState):
                raise TypeError("shelves must contain only ShelfState values")
            _require_in_grid(shelf.position, f"{shelf.shelf_id}.position")
        for order in self.orders:
            if not isinstance(order, OrderState):
                raise TypeError("orders must contain only OrderState values")
        for order_id in self.completed_orders:
            if not isinstance(order_id, str):
                raise TypeError("completed_orders must contain only str")
        _require_strictly_sorted(
            [shelf.shelf_id for shelf in self.shelves], "shelves by shelf_id"
        )
        _require_strictly_sorted(
            [order.order_id for order in self.orders], "orders by order_id"
        )
        _require_strictly_sorted(
            list(self.completed_orders), "completed_orders"
        )
        positions = [shelf.position for shelf in self.shelves]
        if len(set(positions)) != len(positions):
            raise ValueError("shelf positions must be unique")
        order_ids = {order.order_id for order in self.orders}
        unknown = [o for o in self.completed_orders if o not in order_ids]
        if unknown:
            raise ValueError(f"completed_orders reference unknown orders: {unknown}")
        _require_int(self.step, "step")
        if self.step < 0:
            raise ValueError("step must be non-negative")

    def to_mapping(self):
        return {
            "schema_version": self.schema_version,
            "width": self.width,
            "height": self.height,
            "robot": self.robot.to_mapping(),
            "shelves": [shelf.to_mapping() for shelf in self.shelves],
            "orders": [order.to_mapping() for order in self.orders],
            "completed_orders": list(self.completed_orders),
            "step": self.step,
        }

    @classmethod
    def from_mapping(cls, mapping):
        _require_exact_keys(mapping, _WORLD_KEYS, "WorldState")
        return cls(
            schema_version=mapping["schema_version"],
            width=mapping["width"],
            height=mapping["height"],
            robot=RobotState.from_mapping(mapping["robot"]),
            shelves=tuple(
                ShelfState.from_mapping(shelf) for shelf in mapping["shelves"]
            ),
            orders=tuple(
                OrderState.from_mapping(order) for order in mapping["orders"]
            ),
            completed_orders=tuple(mapping["completed_orders"]),
            step=mapping["step"],
        )
