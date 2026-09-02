"""Seeded, formula-only task generation (REQ-01, REQ-03, REQ-07, REQ-10).

The generator uses only the explicit REQ-01 formulas — the fixed slot list
``P``, ``q = s mod 10``, ``n = 5 + (s mod 6)``, shelf positions
``P[(i + q) mod 10]``, shelf items ``item-((i + 2q) mod 10)``, and order
items ``item-((3k + q) mod 10)``. There is no randomness, no floating point,
no global state, and no runtime dependency; repeated generation of the same
seed is byte-identical. Seed 17 is the public hand-checkable fixture and
seed 29 is the plumbing-only evaluation fixture (REQ-10 honesty boundary).
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from robot_factory_arena.domain.model import (
    GRID_SIZE,
    SCHEMA_VERSION,
    OrderState,
    RobotState,
    ShelfState,
    WorldState,
)

TASK_VERSION = "warehouse-v0"

# Fixed slot list P from REQ-01.3, in order, never re-sorted.
SLOT_POSITIONS = (
    (1, 2), (3, 2), (5, 2), (7, 2), (9, 2),
    (1, 5), (3, 5), (5, 5), (7, 5), (9, 5),
)

# Frozen config identity; these values enter the config digest and logical
# run identity, so they must never change within v0.
GENERATOR = "formula-v0"
GENERATOR_VERSION = "1"
SLOT_LAYOUT = "p10-two-rows"

_SEED_TEXT_RE = re.compile(r"(0|[1-9][0-9]*)")
_SEED_INPUT_RE = re.compile(r"[0-9]+")
_EXPECTED_CONFIG_IDENTITY = {
    "generator": GENERATOR,
    "generator_version": GENERATOR_VERSION,
    "slot_layout": SLOT_LAYOUT,
}


def normalize_seed(seed) -> str:
    """Canonical decimal text from an int or digit-only string.

    Leading zeros normalize away ("017" -> "17"). Negatives, floats, bools,
    and any non-decimal input are rejected: REQ-01 admits only non-negative
    decimal integer seeds.
    """
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise TypeError("seed must be a non-negative int or decimal string")
    if isinstance(seed, int):
        if seed < 0:
            raise ValueError("seed must be non-negative")
        return str(seed)
    if _SEED_INPUT_RE.fullmatch(seed):
        return str(int(seed))
    raise ValueError(f"seed {seed!r} is not a decimal string")


@dataclass(frozen=True)
class TaskDefinition:
    """Locked TaskDefinition shape (tasks.md exact auxiliary schema lock)."""

    task_version: str
    schema_version: int
    seed: str
    width: int
    height: int
    slot_positions: tuple[tuple[int, int], ...]
    shelves: tuple[ShelfState, ...]
    orders: tuple[OrderState, ...]
    initial_state: WorldState
    config_identity: Mapping

    def __post_init__(self):
        if self.task_version != TASK_VERSION:
            raise ValueError(f"task_version must be {TASK_VERSION!r}")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if not isinstance(self.seed, str) or not _SEED_TEXT_RE.fullmatch(self.seed):
            raise ValueError("seed must be canonical decimal text")
        if self.width != GRID_SIZE or self.height != GRID_SIZE:
            raise ValueError("v0 task geometry is fixed at 10x10")
        if self.slot_positions != SLOT_POSITIONS:
            raise ValueError("slot_positions must be the fixed REQ-01 list P")
        if dict(self.config_identity) != _EXPECTED_CONFIG_IDENTITY:
            raise ValueError(
                "config_identity is locked to the v0 generator identity"
            )
        # Freeze the mapping view so the value object exposes no mutable data.
        object.__setattr__(
            self, "config_identity", MappingProxyType(dict(self.config_identity))
        )

    def to_mapping(self):
        return {
            "schema_version": self.schema_version,
            "task_version": self.task_version,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "slot_positions": [
                [x, y] for x, y in self.slot_positions
            ],
            "shelves": [shelf.to_mapping() for shelf in self.shelves],
            "orders": [order.to_mapping() for order in self.orders],
            "initial_state": self.initial_state.to_mapping(),
            "config_identity": dict(self.config_identity),
        }


def generate_task(seed) -> TaskDefinition:
    """Generate the deterministic warehouse task for ``seed`` (REQ-01)."""
    seed_text = normalize_seed(seed)
    s = int(seed_text)
    q = s % 10
    n = 5 + (s % 6)

    # Shelf ids shelf-00..shelf-09 are already strictly ascending, as the
    # WorldState constructor requires.
    shelves = tuple(
        ShelfState(
            f"shelf-{i:02d}",
            SLOT_POSITIONS[(i + q) % 10],
            f"item-{(i + 2 * q) % 10:02d}",
        )
        for i in range(10)
    )
    orders = tuple(
        OrderState(f"order-{k:02d}", f"item-{(3 * k + q) % 10:02d}", 1)
        for k in range(n)
    )
    initial_state = WorldState(
        schema_version=SCHEMA_VERSION,
        width=GRID_SIZE,
        height=GRID_SIZE,
        robot=RobotState((0, 0), None, (0, 0)),
        shelves=shelves,
        orders=orders,
        completed_orders=(),
        step=0,
    )
    return TaskDefinition(
        task_version=TASK_VERSION,
        schema_version=SCHEMA_VERSION,
        seed=seed_text,
        width=GRID_SIZE,
        height=GRID_SIZE,
        slot_positions=SLOT_POSITIONS,
        shelves=shelves,
        orders=orders,
        initial_state=initial_state,
        config_identity=dict(_EXPECTED_CONFIG_IDENTITY),
    )


def serialize_task(task: TaskDefinition) -> dict:
    """Exact locked TaskDefinition serialization (plain mapping form)."""
    return task.to_mapping()
