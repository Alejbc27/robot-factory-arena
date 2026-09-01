"""Transition-unit tests (REQ-03, REQ-04).

T-04 covers the frozen state values, validation, and exact serialization;
B2 (T-07) adds the pure apply() transition units to this module.
"""
import dataclasses
import json
import pathlib
import unittest

from robot_factory_arena.domain.model import (
    OrderState,
    RobotState,
    ShelfState,
    WorldState,
)

from robot_factory_arena.evidence.canonical import canonical_json_bytes

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


def _fixture_states():
    raw = (FIXTURE_DIR / "transition_states.json").read_text(encoding="utf-8")
    return json.loads(raw)["states"]


def _sample_world(**overrides):
    shelves = (
        ShelfState("shelf-00", (1, 2), "item-05"),
        ShelfState("shelf-01", (3, 2), "item-08"),
    )
    orders = (
        OrderState("order-00", "item-05", 1),
        OrderState("order-01", "item-08", 1),
    )
    kwargs = dict(
        schema_version=1,
        width=10,
        height=10,
        robot=RobotState((0, 0), None, (0, 0)),
        shelves=shelves,
        orders=orders,
        completed_orders=(),
        step=0,
    )
    kwargs.update(overrides)
    return WorldState(**kwargs)


class TestFrozenStateValues(unittest.TestCase):
    """REQ-03: immutable values; constructors validate, never normalize."""

    def test_value_objects_are_frozen(self):
        robot = RobotState((0, 0), None, (0, 0))
        shelf = ShelfState("shelf-00", (1, 2), "item-05")
        order = OrderState("order-00", "item-05", 1)
        world = _sample_world()
        for target, attr, value in (
            (robot, "position", (1, 1)),
            (shelf, "item_id", None),
            (order, "quantity", 2),
            (world, "step", 5),
        ):
            with self.subTest(target=type(target).__name__):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(target, attr, value)

    def test_constructor_rejects_mutable_collections(self):
        with self.assertRaises(TypeError):
            _sample_world(shelves=[ShelfState("shelf-00", (1, 2), "item-05")])
        with self.assertRaises(TypeError):
            _sample_world(orders=[OrderState("order-00", "item-05", 1)])
        with self.assertRaises(TypeError):
            _sample_world(completed_orders=["order-00"])

    def test_dimension_validation(self):
        with self.assertRaises(ValueError):
            _sample_world(width=9)
        with self.assertRaises(ValueError):
            _sample_world(height=11)
        with self.assertRaises(TypeError):
            _sample_world(width="10")
        with self.assertRaises(TypeError):
            _sample_world(width=True)

    def test_coordinate_validation(self):
        with self.assertRaises(ValueError):
            _sample_world(robot=RobotState((10, 0), None, (0, 0)))
        with self.assertRaises(ValueError):
            _sample_world(robot=RobotState((0, -1), None, (0, 0)))
        with self.assertRaises(ValueError):
            _sample_world(robot=RobotState((0, 0), None, (0, 10)))
        with self.assertRaises(TypeError):
            RobotState([0, 0], None, (0, 0))

    def test_id_validation(self):
        with self.assertRaises(ValueError):
            ShelfState("shelf-10", (1, 2), "item-05")
        with self.assertRaises(ValueError):
            ShelfState("shelf-00", (1, 2), "item-5")
        with self.assertRaises(ValueError):
            OrderState("order-0", "item-05", 1)
        with self.assertRaises(ValueError):
            OrderState("order-00", "widget", 1)

    def test_order_quantity_is_one_in_v0(self):
        with self.assertRaises(ValueError):
            OrderState("order-00", "item-05", 2)
        with self.assertRaises(TypeError):
            OrderState("order-00", "item-05", True)

    def test_semantic_tuple_order_is_enforced(self):
        unsorted_shelves = (
            ShelfState("shelf-01", (3, 2), "item-08"),
            ShelfState("shelf-00", (1, 2), "item-05"),
        )
        with self.assertRaises(ValueError):
            _sample_world(shelves=unsorted_shelves)
        unsorted_orders = (
            OrderState("order-01", "item-08", 1),
            OrderState("order-00", "item-05", 1),
        )
        with self.assertRaises(ValueError):
            _sample_world(orders=unsorted_orders)
        with self.assertRaises(ValueError):
            _sample_world(completed_orders=("order-01", "order-00"))

    def test_uniqueness_and_membership_validation(self):
        duplicate_position = (
            ShelfState("shelf-00", (1, 2), "item-05"),
            ShelfState("shelf-01", (1, 2), "item-08"),
        )
        with self.assertRaises(ValueError):
            _sample_world(shelves=duplicate_position)
        with self.assertRaises(ValueError):
            _sample_world(completed_orders=("order-02",))

    def test_schema_version_and_step_validation(self):
        with self.assertRaises(ValueError):
            _sample_world(schema_version=2)
        with self.assertRaises(ValueError):
            _sample_world(step=-1)
        with self.assertRaises(TypeError):
            _sample_world(step=True)
        with self.assertRaises(TypeError):
            _sample_world(robot={"position": [0, 0]})


class TestStateSerialization(unittest.TestCase):
    """REQ-03: exact serialized shapes; tuples to arrays; absent values to null."""

    def test_world_state_serializes_exact_eight_keys(self):
        mapping = _sample_world().to_mapping()
        self.assertEqual(
            sorted(mapping),
            [
                "completed_orders", "height", "orders", "robot",
                "schema_version", "shelves", "step", "width",
            ],
        )

    def test_tuples_serialize_as_arrays_and_absent_values_as_null(self):
        mapping = _sample_world().to_mapping()
        self.assertIsNone(mapping["robot"]["inventory_item_id"])
        self.assertEqual(mapping["robot"]["position"], [0, 0])
        self.assertEqual(mapping["shelves"][0]["position"], [1, 2])
        self.assertEqual(mapping["completed_orders"], [])

    def test_canonical_state_bytes_are_stable(self):
        expected = (
            '{"completed_orders":[],"height":10,"orders":['
            '{"item_id":"item-05","order_id":"order-00","quantity":1},'
            '{"item_id":"item-08","order_id":"order-01","quantity":1}],'
            '"robot":{"base_position":[0,0],"inventory_item_id":null,'
            '"position":[0,0]},"schema_version":1,'
            '"shelves":[{"item_id":"item-05","position":[1,2],'
            '"shelf_id":"shelf-00"},{"item_id":"item-08","position":[3,2],'
            '"shelf_id":"shelf-01"}],"step":0,"width":10}'
        )
        self.assertEqual(
            canonical_json_bytes(_sample_world().to_mapping()),
            expected.encode("utf-8"),
        )

    def test_from_mapping_rejects_key_drift(self):
        mapping = _sample_world().to_mapping()
        with self.assertRaises(ValueError):
            WorldState.from_mapping(dict(mapping, unexpected=1))
        missing = {k: v for k, v in mapping.items() if k != "step"}
        with self.assertRaises(ValueError):
            WorldState.from_mapping(missing)

    def test_from_mapping_round_trip_preserves_state(self):
        state = _sample_world()
        self.assertEqual(WorldState.from_mapping(state.to_mapping()), state)


class TestFixtureTransitionStates(unittest.TestCase):
    """T-04: hand-checked fixture states validate and tell one coherent story."""

    def test_all_fixture_states_pass_validation(self):
        for name, mapping in _fixture_states().items():
            with self.subTest(state=name):
                state = WorldState.from_mapping(mapping)
                self.assertEqual(state.to_mapping(), mapping)

    def test_fixture_story_steps_are_consistent(self):
        # Three moves to the shelf, pick, three moves back, place: the story
        # behind step counts 0 -> 3 -> 4 -> 7 -> 8.
        states = _fixture_states()
        self.assertEqual(states["start"]["step"], 0)
        self.assertEqual(states["at_shelf"]["step"], 3)
        self.assertEqual(states["carrying"]["step"], 4)
        self.assertEqual(states["at_base_carrying"]["step"], 7)
        self.assertEqual(states["one_completed"]["step"], 8)
