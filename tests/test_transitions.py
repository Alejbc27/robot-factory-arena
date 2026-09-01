"""Transition-unit tests (REQ-03, REQ-04).

T-04 covers the frozen state values, validation, and exact serialization;
T-07 adds the pure apply() transition units and T-09 the ToolBay
place-delivers-exactly-once check to this module.
"""
import ast
import copy
import dataclasses
import importlib
import inspect
import json
import pathlib
import unittest
from robot_factory_arena.contracts.outcomes import CLOSED_REASONS
from robot_factory_arena.contracts.tool_call import ToolCall, validate_operation_key
from robot_factory_arena.domain.model import (
    OrderState,
    RobotState,
    ShelfState,
    WorldState,
)
from robot_factory_arena.domain.transition import Operation, apply

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


# --- T-07: the pure apply() transition authority (REQ-01, REQ-03, REQ-04) ------

RUN_ID = "lr_" + "0123456789abcdef" * 4


def _key(step, intent):
    return f"op/{RUN_ID}/step-{step}/intent-{intent}"


def _op(tool, arguments, step, intent):
    return Operation(tool, arguments, _key(step, intent))


def _fixture_world(name):
    return WorldState.from_mapping(_fixture_states()[name])


class TestApplyLegalMoves(unittest.TestCase):
    """REQ-04: legal moves update robot position and exactly one step."""

    def setUp(self):
        self.start = _fixture_world("start")

    def test_each_direction_moves_one_cell_with_robot_moved_event(self):
        # Mid-grid start so all four destinations stay inside the 10x10 grid.
        center = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((5, 5), None, (0, 0)),
            shelves=self.start.shelves,
            orders=self.start.orders,
            completed_orders=(),
            step=4,
        )
        cases = (
            ("north", (5, 6)),
            ("east", (6, 5)),
            ("south", (5, 4)),
            ("west", (4, 5)),
        )
        for direction, expected_position in cases:
            with self.subTest(direction=direction):
                result = apply(
                    center, _op("move", {"direction": direction}, 5, "probe")
                )
                self.assertEqual(result.status, "committed")
                self.assertIsNone(result.reason)
                self.assertEqual(result.state.robot.position, expected_position)
                self.assertEqual(result.state.step, 5)
                self.assertEqual(len(result.events), 1)
                event = result.events[0]
                self.assertEqual(event.event_type, "robot_moved")
                self.assertEqual(event.operation_key, _key(5, "probe"))
                self.assertEqual(
                    event.data,
                    {
                        "from": [5, 5],
                        "to": list(expected_position),
                        "direction": direction,
                    },
                )

    def test_moves_off_the_origin_edges_are_rejected(self):
        # From (0,0) both south and west leave the grid.
        for direction in ("south", "west"):
            with self.subTest(direction=direction):
                result = apply(
                    self.start, _op("move", {"direction": direction}, 1, "p")
                )
                self.assertEqual(result.status, "rejected")
                self.assertEqual(result.reason, "destination_out_of_grid")
                self.assertEqual(result.events, ())
                self.assertEqual(result.state.step, 0)

    def test_move_off_grid_is_rejected_without_effect(self):
        # North from y=9 leaves the 10x10 grid.
        top = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((0, 9), None, (0, 0)),
            shelves=self.start.shelves,
            orders=self.start.orders,
            completed_orders=(),
            step=0,
        )
        result = apply(top, _op("move", {"direction": "north"}, 1, "probe"))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "destination_out_of_grid")
        self.assertEqual(result.events, ())
        self.assertEqual(result.state, top)
        self.assertEqual(result.state.step, 0)


class TestApplyLegalPick(unittest.TestCase):
    """REQ-04: pick moves the shelf item into empty robot inventory."""

    def test_pick_at_shelf_commits(self):
        at_shelf = _fixture_world("at_shelf")
        result = apply(
            at_shelf,
            _op(
                "pick",
                {"shelf_id": "shelf-00", "item_id": "item-05"},
                4,
                "pick-shelf-00",
            ),
        )
        self.assertEqual(result.status, "committed")
        self.assertEqual(result.state.step, 4)
        self.assertEqual(result.state.robot.inventory_item_id, "item-05")
        self.assertIsNone(result.state.shelves[0].item_id)
        self.assertIsNot(result.state.shelves[0], at_shelf.shelves[0])
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual(event.event_type, "item_picked")
        self.assertEqual(event.operation_key, _key(4, "pick-shelf-00"))
        self.assertEqual(
            event.data, {"shelf_id": "shelf-00", "item_id": "item-05"}
        )


class TestApplyPickRejections(unittest.TestCase):
    """Every pick precondition has one deterministic closed-taxonomy reason."""

    def setUp(self):
        self.at_shelf = _fixture_world("at_shelf")

    def test_robot_not_at_shelf(self):
        result = apply(
            self.at_shelf,
            _op("pick", {"shelf_id": "shelf-01", "item_id": "item-08"}, 4, "p"),
        )
        self.assertEqual(result.reason, "robot_not_at_shelf")

    def test_unknown_shelf_is_robot_not_at_shelf(self):
        result = apply(
            self.at_shelf,
            _op("pick", {"shelf_id": "shelf-09", "item_id": "item-05"}, 4, "p"),
        )
        self.assertEqual(result.reason, "robot_not_at_shelf")

    def test_inventory_not_empty(self):
        result = apply(
            _fixture_world("carrying"),
            _op("pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 5, "p"),
        )
        self.assertEqual(result.reason, "inventory_not_empty")

    def test_shelf_item_mismatch_when_shelf_is_empty(self):
        # Robot stands on an emptied shelf-00 with empty hands.
        empty_shelf = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((1, 2), None, (0, 0)),
            shelves=(
                ShelfState("shelf-00", (1, 2), None),
                ShelfState("shelf-01", (3, 2), "item-08"),
            ),
            orders=self.at_shelf.orders,
            completed_orders=(),
            step=5,
        )
        result = apply(
            empty_shelf,
            _op("pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 6, "p"),
        )
        self.assertEqual(result.reason, "shelf_item_mismatch")

    def test_shelf_item_mismatch_when_item_differs(self):
        # Robot stands on shelf-00 with empty hands; shelf-00 holds item-05.
        result = apply(
            self.at_shelf,
            _op("pick", {"shelf_id": "shelf-00", "item_id": "item-08"}, 4, "p"),
        )
        self.assertEqual(result.reason, "shelf_item_mismatch")

    def test_item_not_pending_when_all_requests_completed(self):
        # Build a world whose only order for item-05 is already completed.
        state = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((1, 2), None, (0, 0)),
            shelves=(ShelfState("shelf-00", (1, 2), "item-05"),),
            orders=(OrderState("order-00", "item-05", 1),),
            completed_orders=("order-00",),
            step=9,
        )
        result = apply(
            state,
            _op("pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 10, "p"),
        )
        self.assertEqual(result.reason, "item_not_pending")


class TestApplyLegalPlace(unittest.TestCase):
    """REQ-04: place clears inventory and completes the order exactly once."""

    def test_place_at_base_commits(self):
        at_base = _fixture_world("at_base_carrying")
        result = apply(
            at_base, _op("place", {"order_id": "order-00"}, 8, "place-order-00")
        )
        self.assertEqual(result.status, "committed")
        self.assertEqual(result.state.step, 8)
        self.assertIsNone(result.state.robot.inventory_item_id)
        self.assertEqual(result.state.completed_orders, ("order-00",))
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual(event.event_type, "order_delivered")
        self.assertEqual(event.operation_key, _key(8, "place-order-00"))
        self.assertEqual(
            event.data, {"order_id": "order-00", "item_id": "item-05"}
        )

    def test_completion_stays_sorted_when_higher_id_completes_first(self):
        at_base = _fixture_world("at_base_carrying")
        # Complete the lexicographically later order-01 first (item-08).
        state = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((0, 0), "item-08", (0, 0)),
            shelves=at_base.shelves,
            orders=at_base.orders,
            completed_orders=(),
            step=7,
        )
        first = apply(state, _op("place", {"order_id": "order-01"}, 8, "p1"))
        self.assertEqual(first.status, "committed")
        self.assertEqual(first.state.completed_orders, ("order-01",))
        # Re-arm the robot with item-05 (order-00's item) without replaying the
        # full routing; the unit under test is sorted completion, not routing.
        rearmed = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((0, 0), "item-05", (0, 0)),
            shelves=first.state.shelves,
            orders=first.state.orders,
            completed_orders=first.state.completed_orders,
            step=first.state.step,
        )
        second = apply(rearmed, _op("place", {"order_id": "order-00"}, 9, "p2"))
        self.assertEqual(second.status, "committed")
        self.assertEqual(second.state.completed_orders, ("order-00", "order-01"))


class TestApplyPlaceRejections(unittest.TestCase):
    """Every place precondition has one deterministic closed-taxonomy reason."""

    def test_not_at_base(self):
        carrying = _fixture_world("carrying")
        result = apply(carrying, _op("place", {"order_id": "order-00"}, 5, "p"))
        self.assertEqual(result.reason, "not_at_base")

    def test_inventory_empty(self):
        start = _fixture_world("start")
        result = apply(start, _op("place", {"order_id": "order-00"}, 1, "p"))
        self.assertEqual(result.reason, "inventory_empty")

    def test_order_item_mismatch_when_carrying_wrong_item(self):
        at_base = _fixture_world("at_base_carrying")
        state = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((0, 0), "item-08", (0, 0)),
            shelves=at_base.shelves,
            orders=at_base.orders,
            completed_orders=(),
            step=7,
        )
        result = apply(state, _op("place", {"order_id": "order-00"}, 8, "p"))
        self.assertEqual(result.reason, "order_item_mismatch")

    def test_unknown_order_is_order_item_mismatch(self):
        at_base = _fixture_world("at_base_carrying")
        result = apply(at_base, _op("place", {"order_id": "order-09"}, 8, "p"))
        self.assertEqual(result.reason, "order_item_mismatch")

    def test_order_already_completed(self):
        one_completed = _fixture_world("one_completed")
        state = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((0, 0), "item-08", (0, 0)),
            shelves=one_completed.shelves,
            orders=one_completed.orders,
            completed_orders=("order-00",),
            step=8,
        )
        result = apply(state, _op("place", {"order_id": "order-00"}, 9, "p"))
        self.assertEqual(result.reason, "order_already_completed")


class TestApplyEventShapeAndPurity(unittest.TestCase):
    """REQ-03 event shape, one step per commit, no effect on rejection."""

    def test_events_have_exactly_the_req03_keys(self):
        at_shelf = _fixture_world("at_shelf")
        result = apply(
            at_shelf,
            _op("pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 4, "p"),
        )
        self.assertEqual(
            result.events[0].to_mapping(),
            {
                "schema_version": 1,
                "event_type": "item_picked",
                "operation_key": _key(4, "p"),
                "data": {"shelf_id": "shelf-00", "item_id": "item-05"},
            },
        )

    def test_rejections_produce_no_step_and_no_events(self):
        # Off-grid move from (0,0): west leaves the grid.
        result = apply(
            _fixture_world("start"), _op("move", {"direction": "west"}, 1, "p")
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.events, ())
        self.assertEqual(result.state.step, 0)

    def test_apply_never_mutates_its_inputs(self):
        at_shelf = _fixture_world("at_shelf")
        snapshot = copy.deepcopy(at_shelf.to_mapping())
        arguments = {"shelf_id": "shelf-00", "item_id": "item-05"}
        arguments_snapshot = dict(arguments)
        committed = apply(at_shelf, _op("pick", arguments, 4, "p"))
        rejected = apply(
            at_shelf,
            _op("pick", {"shelf_id": "shelf-01", "item_id": "item-08"}, 4, "p"),
        )
        self.assertEqual(at_shelf.to_mapping(), snapshot)
        self.assertEqual(arguments, arguments_snapshot)
        self.assertIsNot(committed.state, at_shelf)
        # Rejections return the unchanged frozen input state (safe aliasing).
        self.assertEqual(rejected.state, at_shelf)

    def test_apply_is_pure_under_repetition(self):
        at_shelf = _fixture_world("at_shelf")
        operation = _op(
            "pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 4, "p"
        )
        first = apply(at_shelf, operation)
        second = apply(at_shelf, operation)
        self.assertEqual(first, second)
        self.assertEqual(first.state, second.state)

    def test_invalid_arguments_reject_as_invalid_arguments(self):
        at_shelf = _fixture_world("at_shelf")
        for tool, arguments in (
            ("move", {"direction": "inward"}),
            ("move", {}),
            ("move", {"direction": ["north"]}),
            ("pick", {"shelf_id": "shelf-00"}),
            ("place", {"order_id": "order-00", "extra": 1}),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                result = apply(at_shelf, _op(tool, arguments, 4, "p"))
                self.assertEqual(result.status, "rejected")
                self.assertEqual(result.reason, "invalid_arguments")

    def test_non_mapping_arguments_never_reach_apply(self):
        # Operation's type contract rejects non-mapping arguments up front;
        # apply only ever receives well-typed Operation values.
        with self.assertRaises(TypeError):
            _op("place", "order-00", 4, "p")

    def test_unsupported_tool_rejects_without_effect(self):
        at_shelf = _fixture_world("at_shelf")
        result = apply(at_shelf, _op("assemble", {}, 4, "p"))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "unsupported_tool")

    def test_transition_precedence_is_deterministic(self):
        # pick precedence: robot-at-shelf before inventory before shelf-item.
        carrying = _fixture_world("carrying")
        result = apply(
            carrying,
            _op("pick", {"shelf_id": "shelf-01", "item_id": "item-05"}, 5, "p"),
        )
        self.assertEqual(result.reason, "robot_not_at_shelf")
        # place precedence: at-base before inventory-empty. A robot away from
        # the base with empty hands rejects as not_at_base.
        away = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((5, 5), None, (0, 0)),
            shelves=carrying.shelves,
            orders=carrying.orders,
            completed_orders=(),
            step=2,
        )
        result = apply(away, _op("place", {"order_id": "order-00"}, 3, "p"))
        self.assertEqual(result.reason, "not_at_base")


class TestApplyStoryReproducesFixtureStates(unittest.TestCase):
    """The fixture story is exactly reproducible through pure apply()."""

    def test_story_moves_pick_place_reproduce_fixture_states(self):
        state = _fixture_world("start")
        script = (
            (("move", {"direction": "north"}, 1, "goto-shelf-00"), None),
            (("move", {"direction": "north"}, 2, "goto-shelf-00"), None),
            (("move", {"direction": "east"}, 3, "goto-shelf-00"), "at_shelf"),
            (
                ("pick", {"shelf_id": "shelf-00", "item_id": "item-05"},
                 4, "pick-shelf-00"),
                "carrying",
            ),
            (("move", {"direction": "south"}, 5, "goto-base"), None),
            (("move", {"direction": "south"}, 6, "goto-base"), None),
            (("move", {"direction": "west"}, 7, "goto-base"), "at_base_carrying"),
            (("place", {"order_id": "order-00"}, 8, "place-order-00"),
             "one_completed"),
        )
        for operation_fields, expected_snapshot in script:
            result = apply(state, _op(*operation_fields))
            self.assertEqual(result.status, "committed", operation_fields)
            state = result.state
            if expected_snapshot is not None:
                self.assertEqual(
                    state.to_mapping(), _fixture_states()[expected_snapshot]
                )


class TestTransitionAuthorityIndependence(unittest.TestCase):
    """apply() knows nothing of grants, ledger, faults, runner, or reports."""

    ALLOWED_IMPORTS = {
        "robot_factory_arena.contracts.outcomes",
        "robot_factory_arena.domain.model",
    }

    def test_transition_module_imports_only_domain_and_outcomes(self):
        module = importlib.import_module("robot_factory_arena.domain.transition")
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        # Only robot_factory_arena imports are constrained; the stdlib surface
        # is free. No grants/ledger/faults/runner/report/evidence import may
        # appear in the transition authority.
        project_imports = {
            name for name in imported if name.startswith("robot_factory_arena")
        }
        self.assertEqual(project_imports - self.ALLOWED_IMPORTS, set())

    def test_emitted_event_types_are_exactly_the_domain_three(self):
        module = importlib.import_module("robot_factory_arena.domain.transition")
        source = inspect.getsource(module)
        for event_type in ("robot_moved", "item_picked", "order_delivered"):
            self.assertIn(event_type, source)
        for foreign in ("run_started", "tool_committed", "budget_exhausted"):
            self.assertNotIn(foreign, source)


class TestEmittedKeysMatchGrammar(unittest.TestCase):
    """Events carry the exact operation key handed to apply()."""

    def test_operation_keys_inside_events_validate(self):
        at_shelf = _fixture_world("at_shelf")
        key = _key(4, "pick-shelf-00")
        self.assertTrue(validate_operation_key(key, RUN_ID))
        result = apply(
            at_shelf,
            _op(
                "pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 4,
                "pick-shelf-00",
            ),
        )
        self.assertEqual(result.events[0].operation_key, key)


# --- T-09: ToolBay place-delivers-exactly-once over the committed fixture ------


class TestToolBayPlaceDeliversOnce(unittest.TestCase):
    """T-09 done-check: the committed place fixture delivers exactly once."""

    def test_committed_place_fixture_delivers_exactly_once(self):
        from robot_factory_arena.runtime.grants import issue_grants
        from robot_factory_arena.runtime.ledger import Ledger
        from robot_factory_arena.runtime.tool_bay import ToolBay

        bay = ToolBay(Ledger())
        grants = issue_grants(RUN_ID, ("move", "pick", "place"))
        state = _fixture_world("at_base_carrying")
        call = ToolCall(
            1, "place", {"order_id": "order-00"}, _key(8, "place-order-00")
        )
        first = bay.submit(state, call, grants)
        self.assertEqual(first.status, "committed")
        self.assertTrue(first.committed)
        self.assertEqual(len(first.events), 1)
        delivered = [
            event for event in first.events if event.event_type == "order_delivered"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(first.state.completed_orders, ("order-00",))

        replay = bay.submit(first.state, call, grants)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertFalse(replay.committed)
        self.assertEqual(replay.events, ())
        self.assertEqual(replay.state.completed_orders, ("order-00",))
        self.assertEqual(replay.state.to_mapping(), first.state.to_mapping())

    def test_no_toolbay_rejection_reason_outside_closed_taxonomy(self):
        # The gateway may only reject through the closed taxonomy.
        self.assertIn("missing_grant", CLOSED_REASONS)
        self.assertIn("operation_key_conflict", CLOSED_REASONS)
