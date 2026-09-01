"""Structural and determinism invariants (REQ-07, REQ-08).

Hosts the T-01 discovery smoke test plus the canonical/serialization,
schema-lock, formula, and purity invariants filled in by T-02..T-06.
"""
import ast
import importlib
import inspect
import json
import pathlib
import unittest

from robot_factory_arena.contracts.outcomes import BudgetView, LastResult
from robot_factory_arena.contracts.policy import policy_manifest_digest
from robot_factory_arena.contracts.tool_call import (
    normalize_tool_call,
    validate_operation_key,
)
from robot_factory_arena.domain.model import (
    OrderState,
    RobotState,
    ShelfState,
    WorldState,
)
from robot_factory_arena.domain.observation import Observation, serialize_observation
from robot_factory_arena.domain.task import generate_task, serialize_task
from robot_factory_arena.policies.bounded import BoundedPolicy
from robot_factory_arena.policies.greedy import GreedyPolicy
from robot_factory_arena.evidence.canonical import (
    canonical_json,
    canonical_json_bytes,
    canonical_jsonl_line,
    digest_json,
    sha256_digest,
)

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"

REQUIRED_TEST_MODULES = (
    "test_golden_trace",
    "test_transitions",
    "test_invariants",
    "test_adversarial_verifier",
    "test_runtime_boundaries",
    "test_faults",
    "test_rerun_replay",
    "test_verifier_dependencies",
)

REQUIRED_PACKAGES = (
    "robot_factory_arena",
    "robot_factory_arena.domain",
    "robot_factory_arena.contracts",
    "robot_factory_arena.runtime",
    "robot_factory_arena.policies",
    "robot_factory_arena.faults",
    "robot_factory_arena.evidence",
    "robot_factory_arena.verification",
)


def key_drift(obj, expected_keys):
    """Return (extra, missing) sorted key lists versus an exact expected set.

    Schema-lock helper: rejects extra/missing/default fields against the
    exact auxiliary schemas frozen in tests/fixtures/.
    """
    actual = set(obj)
    expected = set(expected_keys)
    return sorted(actual - expected), sorted(expected - actual)


class TestScaffoldDiscovery(unittest.TestCase):
    """T-01: the stdlib package imports and unittest discovery is complete."""

    def test_all_required_packages_import(self):
        for name in REQUIRED_PACKAGES:
            with self.subTest(package=name):
                module = importlib.import_module(name)
                self.assertIsNotNone(module.__doc__)

    def test_discovery_finds_all_required_test_modules(self):
        # The locked command loads test modules as top-level modules with
        # tests/ as the discovery root, so import them by bare module name.
        loader = unittest.defaultTestLoader
        for name in REQUIRED_TEST_MODULES:
            with self.subTest(module=name):
                module = importlib.import_module(name)
                suite = loader.loadTestsFromModule(module)
                self.assertGreaterEqual(
                    suite.countTestCases(),
                    1,
                    f"discovery found no tests in {name}",
                )


# --- T-02: single canonical JSON and digest authority (REQ-03, REQ-07) --------


class TestCanonicalJsonAuthority(unittest.TestCase):
    """One serializer: sorted keys, compact, UTF-8, finite ints only, no floats."""

    def test_sorted_keys_and_compact_separators(self):
        self.assertEqual(canonical_json({"b": 1, "a": 2}), '{"a":2,"b":1}')
        nested = {"z": {"b": [1, {"y": 1, "x": 2}], "a": 3}}
        self.assertEqual(canonical_json(nested), '{"z":{"a":3,"b":[1,{"x":2,"y":1}]}}')

    def test_utf8_bytes_without_ascii_escapes(self):
        self.assertEqual(canonical_json({"name": "café"}), '{"name":"café"}')
        self.assertEqual(
            canonical_json_bytes({"name": "café"}),
            '{"name":"café"}'.encode("utf-8"),
        )

    def test_explicit_nulls_and_empty_containers(self):
        self.assertEqual(canonical_json({"a": None}), '{"a":null}')
        self.assertEqual(canonical_json({}), "{}")
        self.assertEqual(canonical_json({"a": []}), '{"a":[]}')

    def test_floats_rejected_even_when_finite(self):
        # REQ-07: no floating-point values in scoring or digest inputs; the
        # finite case is rejected exactly like nan/inf.
        for bad in (1.5, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(TypeError):
                    canonical_json({"x": bad})
        with self.assertRaises(TypeError):
            canonical_json({"a": [1, {"b": 2.0}]})

    def test_bools_ints_and_strings_allowed(self):
        value = {"flag": True, "n": 0, "big": 10 ** 30, "s": "warehouse-v0"}
        self.assertEqual(
            canonical_json(value),
            f'{{"big":{10 ** 30},"flag":true,"n":0,"s":"warehouse-v0"}}',
        )

    def test_non_string_keys_and_unsupported_types_rejected(self):
        with self.assertRaises(TypeError):
            canonical_json({1: "a"})
        with self.assertRaises(TypeError):
            canonical_json({"s": {1, 2}})
        with self.assertRaises(TypeError):
            canonical_json(object())

    def test_digest_format_and_known_vector(self):
        self.assertEqual(
            sha256_digest(b"abc"),
            "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        self.assertRegex(digest_json({"a": 1}), r"^sha256:[0-9a-f]{64}$")

    def test_equivalent_mappings_hash_identically(self):
        self.assertEqual(digest_json({"a": 1, "b": 2}), digest_json({"b": 2, "a": 1}))

    def test_jsonl_line_is_record_bytes_plus_single_lf(self):
        line = canonical_jsonl_line({"b": 1, "a": 2})
        self.assertEqual(line, b'{"a":2,"b":1}\n')
        self.assertEqual(line.count(b"\n"), 1)

    def test_no_path_or_execution_metadata_is_injected(self):
        value = {"logical_run_id": "lr_" + "ab" * 32}
        parsed = json.loads(canonical_json(value))
        self.assertEqual(sorted(parsed), ["logical_run_id"])


# --- T-04: state collection invariants (REQ-03, REQ-04) ------------------------

_TRANSITION_STATES = (
    "start", "at_shelf", "carrying", "at_base_carrying", "one_completed",
)


def _fixture_state(name):
    raw = (FIXTURE_DIR / "transition_states.json").read_text(encoding="utf-8")
    return json.loads(raw)["states"][name]


class TestStateCollectionInvariants(unittest.TestCase):
    """Semantic collections serialize sorted; reconstruction never mutates input."""

    def test_serialized_collections_are_sorted(self):
        for name in _TRANSITION_STATES:
            with self.subTest(state=name):
                mapping = _fixture_state(name)
                shelf_ids = [s["shelf_id"] for s in mapping["shelves"]]
                order_ids = [o["order_id"] for o in mapping["orders"]]
                completed = list(mapping["completed_orders"])
                self.assertEqual(shelf_ids, sorted(shelf_ids))
                self.assertEqual(order_ids, sorted(order_ids))
                self.assertEqual(completed, sorted(completed))

    def test_from_mapping_does_not_mutate_input(self):
        mapping = _fixture_state("one_completed")
        snapshot = json.loads(json.dumps(mapping))
        WorldState.from_mapping(mapping)
        self.assertEqual(mapping, snapshot)


# --- T-05: seeded task generation invariants (REQ-01, REQ-07, REQ-10) ----------


class TestSeedNormalizationInvariants(unittest.TestCase):
    """Seeds normalize to canonical decimal text; everything else rejects."""

    def test_int_and_digit_string_normalize_to_canonical_decimal_text(self):
        self.assertEqual(generate_task(17).seed, "17")
        self.assertEqual(generate_task("17").seed, "17")
        self.assertEqual(generate_task("017").seed, "17")
        self.assertEqual(generate_task(0).seed, "0")
        self.assertEqual(generate_task("0").seed, "0")

    def test_invalid_seeds_are_rejected(self):
        for bad in (-1, 1.5, True, "abc", "", "+17", " 17", "1_7", None, "0x11"):
            with self.subTest(seed=bad):
                with self.assertRaises((TypeError, ValueError)):
                    generate_task(bad)


class TestTaskFormulaInvariants(unittest.TestCase):
    """REQ-01 formulas, hand-checked for seeds 17 and 29 plus a sweep."""

    def test_seed_17_formulas(self):
        task = generate_task(17)
        # q = 17 mod 10 = 7; n = 5 + (17 mod 6) = 10.
        # P[7] = (5, 5), so shelf-00 sits on slot P[(0+7) mod 10].
        self.assertEqual(task.shelves[0].position, (5, 5))
        self.assertEqual(task.shelves[0].item_id, "item-04")
        self.assertEqual(task.shelves[3].position, (1, 2))
        self.assertEqual(task.shelves[3].item_id, "item-07")
        self.assertEqual(len(task.orders), 10)
        self.assertEqual(task.orders[0].item_id, "item-07")
        self.assertEqual(task.orders[9].item_id, "item-04")

    def test_seed_29_formulas(self):
        task = generate_task(29)
        # q = 29 mod 10 = 9; n = 5 + (29 mod 6) = 10.
        # P[9] = (9, 5), so shelf-00 sits on slot P[(0+9) mod 10].
        self.assertEqual(task.shelves[0].position, (9, 5))
        self.assertEqual(task.shelves[0].item_id, "item-08")
        self.assertEqual(task.orders[0].item_id, "item-09")
        self.assertEqual(len(task.orders), 10)

    def test_initial_state_is_robot_at_base_with_no_completion(self):
        task = generate_task(17)
        state = task.initial_state
        self.assertEqual(state.robot.position, (0, 0))
        self.assertIsNone(state.robot.inventory_item_id)
        self.assertEqual(state.robot.base_position, (0, 0))
        self.assertEqual(state.step, 0)
        self.assertEqual(state.completed_orders, ())

    def test_slot_list_is_exactly_the_fixed_p_and_fully_covered(self):
        p = [
            (1, 2), (3, 2), (5, 2), (7, 2), (9, 2),
            (1, 5), (3, 5), (5, 5), (7, 5), (9, 5),
        ]
        for seed in (17, 29, 0, 3, 7):
            with self.subTest(seed=seed):
                positions = sorted(
                    shelf.position for shelf in generate_task(seed).shelves
                )
                self.assertEqual(positions, sorted(p))

    def test_every_order_has_exactly_one_source_shelf(self):
        for seed in range(0, 36):
            task = generate_task(seed)
            holdings = {}
            for shelf in task.shelves:
                holdings.setdefault(shelf.item_id, []).append(shelf.shelf_id)
            for order in task.orders:
                with self.subTest(seed=seed, order=order.order_id):
                    self.assertEqual(len(holdings.get(order.item_id, [])), 1)

    def test_all_positions_in_grid(self):
        for seed in range(0, 36):
            task = generate_task(seed)
            for shelf in task.shelves:
                x, y = shelf.position
                self.assertTrue(0 <= x <= 9 and 0 <= y <= 9)

    def test_repeated_generation_is_byte_identical(self):
        first = canonical_json_bytes(serialize_task(generate_task(17)))
        second = canonical_json_bytes(serialize_task(generate_task(17)))
        self.assertEqual(first, second)


class TestTaskDefinitionConformance(unittest.TestCase):
    """Generated TaskDefinitions match the locked schema fixture exactly."""

    def test_serialization_keys_match_locked_schema(self):
        fixture = _load_fixture("task_definition_schema.json")
        mapping = serialize_task(generate_task(17))
        extra, missing = key_drift(mapping, fixture["keys"])
        self.assertEqual((extra, missing), ([], []))
        nested = fixture["nested"]
        extra, missing = key_drift(
            mapping["config_identity"], nested["config_identity"]
        )
        self.assertEqual((extra, missing), ([], []))
        extra, missing = key_drift(mapping["initial_state"], nested["initial_state"])
        self.assertEqual((extra, missing), ([], []))
        self.assertEqual(mapping["config_identity"], fixture["config_identity"])


# --- T-06: observation contract, ToolCall grammar, reference policies ----------

RUN_ID = "lr_" + "0123456789abcdef" * 4


def _policy_observation(position, inventory, shelves, orders, completed=(),
                        used=0, last_result=None):
    world = WorldState(
        schema_version=1,
        width=10,
        height=10,
        robot=RobotState(position, inventory, (0, 0)),
        shelves=shelves,
        orders=orders,
        completed_orders=completed,
        step=0,
    )
    observation = Observation(
        world_state=world,
        logical_run_id=RUN_ID,
        grants=("move", "pick", "place"),
        policy_steps=BudgetView(used, 100 - used, 100),
        last_result=last_result,
    )
    return serialize_observation(observation)


class TestObservationContract(unittest.TestCase):
    """REQ-03: exactly the required keys; no side channels; sorted grants."""

    def test_serialized_observation_has_exactly_the_req03_keys(self):
        mapping = _policy_observation(
            (0, 0), None,
            (ShelfState("shelf-00", (1, 1), "item-00"),),
            (OrderState("order-00", "item-00", 1),),
        )
        self.assertEqual(
            sorted(mapping),
            ["grants", "last_result", "logical_run_id", "policy_steps",
             "schema_version", "world_state"],
        )
        self.assertEqual(
            mapping["policy_steps"], {"maximum": 100, "remaining": 100, "used": 0}
        )
        self.assertIsNone(mapping["last_result"])
        self.assertEqual(mapping["grants"], ["move", "pick", "place"])
        self.assertEqual(mapping["logical_run_id"], RUN_ID)

    def test_last_result_serializes_status_key_reason(self):
        mapping = _policy_observation(
            (0, 0), None,
            (ShelfState("shelf-00", (1, 1), "item-00"),),
            (OrderState("order-00", "item-00", 1),),
            last_result=LastResult("rejected", "op/x", "missing_grant"),
        )
        self.assertEqual(
            mapping["last_result"],
            {"operation_key": "op/x", "reason": "missing_grant",
             "status": "rejected"},
        )

    def test_grants_must_be_sorted_supported_and_unique(self):
        def observation_with(grants):
            return Observation(
                world_state=generate_task(17).initial_state,
                logical_run_id=RUN_ID,
                grants=grants,
                policy_steps=BudgetView(0, 100, 100),
                last_result=None,
            )

        with self.assertRaises(ValueError):
            observation_with(("pick", "move", "place"))
        with self.assertRaises(ValueError):
            observation_with(("move", "move"))
        with self.assertRaises(ValueError):
            observation_with(("move", "assemble"))

    def test_logical_run_id_format_is_enforced(self):
        with self.assertRaises(ValueError):
            Observation(
                world_state=generate_task(17).initial_state,
                logical_run_id="run-1",
                grants=("move",),
                policy_steps=BudgetView(0, 100, 100),
                last_result=None,
            )

    def test_budget_view_must_be_consistent(self):
        with self.assertRaises(ValueError):
            BudgetView(10, 89, 100)
        with self.assertRaises(ValueError):
            BudgetView(-1, 101, 100)


class TestOperationKeyGrammar(unittest.TestCase):
    """REQ-03 grammar: op/<lr_64hex>/step-<canonical decimal>/intent-token."""

    def test_accepts_valid_key(self):
        self.assertTrue(
            validate_operation_key(
                f"op/{RUN_ID}/step-8/intent-place-order-03", RUN_ID
            )
        )

    def test_rejects_mismatched_run_id(self):
        other = "lr_" + "f" * 64
        self.assertFalse(
            validate_operation_key(f"op/{other}/step-1/intent-a", RUN_ID)
        )

    def test_rejects_leading_zero_step_and_bad_intents(self):
        for key in (
            f"op/{RUN_ID}/step-08/intent-a",
            f"op/{RUN_ID}/step-1/intent-",
            f"op/{RUN_ID}/step-1/intent-{'x' * 65}",
            f"op/{RUN_ID}/step-1/intent-has space",
            "op/not-a-run-id/step-1/intent-a",
        ):
            with self.subTest(key=key):
                self.assertFalse(validate_operation_key(key, RUN_ID))


class TestToolCallNormalization(unittest.TestCase):
    """Untrusted candidates normalize to exact shapes or closed-taxonomy reasons."""

    def _candidate(self, **overrides):
        candidate = {
            "schema_version": 1,
            "tool": "move",
            "arguments": {"direction": "north"},
            "operation_key": f"op/{RUN_ID}/step-1/intent-probe",
        }
        candidate.update(overrides)
        return candidate

    def test_normalizes_valid_calls_for_each_tool(self):
        cases = (
            ("move", {"direction": "north"}),
            ("pick", {"shelf_id": "shelf-00", "item_id": "item-00"}),
            ("place", {"order_id": "order-00"}),
        )
        for tool, arguments in cases:
            with self.subTest(tool=tool):
                result = normalize_tool_call(self._candidate(tool=tool, arguments=arguments))
                self.assertTrue(result.ok)
                self.assertIsNone(result.reason)
                self.assertIsNotNone(result.tool_call)
                self.assertEqual(result.tool_call.tool, tool)
                self.assertEqual(dict(result.tool_call.arguments), arguments)
                self.assertEqual(result.tool_call.operation_key, self._candidate()["operation_key"])

    def test_malformed_candidates(self):
        malformed = (
            "not-a-mapping",
            {"schema_version": 1, "tool": "move"},
            {"schema_version": 1, "tool": "move", "arguments": {"direction": "north"},
             "operation_key": f"op/{RUN_ID}/step-1/intent-a", "extra": True},
            self._candidate(schema_version=2),
        )
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                result = normalize_tool_call(candidate)
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "malformed_call")

    def test_unsupported_tool(self):
        result = normalize_tool_call(self._candidate(tool="assemble"))
        self.assertEqual(result.reason, "unsupported_tool")

    def test_invalid_operation_key(self):
        for key in ("op/nope", 42, None):
            with self.subTest(key=key):
                result = normalize_tool_call(self._candidate(operation_key=key))
                self.assertEqual(result.reason, "operation_key_invalid")

    def test_invalid_argument_shapes(self):
        bad_arguments = (
            {"direction": "inward"},
            {"direction": "north", "speed": 1},
            {},
            "north",
        )
        for arguments in bad_arguments:
            with self.subTest(arguments=arguments):
                result = normalize_tool_call(self._candidate(arguments=arguments))
                self.assertEqual(result.reason, "invalid_arguments")

    def test_pick_and_place_argument_validation(self):
        result = normalize_tool_call(self._candidate(
            tool="pick", arguments={"shelf_id": "shelf-0", "item_id": "item-00"}
        ))
        self.assertEqual(result.reason, "invalid_arguments")
        result = normalize_tool_call(self._candidate(
            tool="place", arguments={"order_id": "order-00", "extra": 1}
        ))
        self.assertEqual(result.reason, "invalid_arguments")

    def test_arguments_exposed_as_immutable_view(self):
        result = normalize_tool_call(self._candidate())
        self.assertIsNotNone(result.tool_call)
        with self.assertRaises(TypeError):
            result.tool_call.arguments["direction"] = "south"


class TestReferencePolicyPurity(unittest.TestCase):
    """Policies import only the contract boundary; identity is deterministic."""

    ALLOWED_IMPORTS = {
        "robot_factory_arena.contracts.tool_call",
        "robot_factory_arena.contracts.policy",
    }

    def _imports_of(self, module_name):
        module = importlib.import_module(module_name)
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_policy_modules_import_only_the_contract_boundary(self):
        for module_name in (
            "robot_factory_arena.policies.greedy",
            "robot_factory_arena.policies.bounded",
        ):
            with self.subTest(module=module_name):
                extra = self._imports_of(module_name) - self.ALLOWED_IMPORTS
                self.assertEqual(extra, set())

    def test_policy_identity_is_deterministic_and_distinct(self):
        greedy = GreedyPolicy()
        bounded = BoundedPolicy()
        self.assertEqual(greedy.policy_id, "greedy-v0")
        self.assertEqual(bounded.policy_id, "bounded-v0")
        self.assertEqual(greedy.policy_digest, policy_manifest_digest("greedy-v0"))
        self.assertEqual(bounded.policy_digest, policy_manifest_digest("bounded-v0"))
        self.assertNotEqual(greedy.policy_digest, bounded.policy_digest)


class TestGreedyPolicyDecisions(unittest.TestCase):
    """REQ-01 greedy-v0: lexicographic target, N/E/S/W tie order, pure."""

    def test_targets_lexicographically_first_pending_order(self):
        shelves = (
            ShelfState("shelf-00", (9, 9), "item-00"),
            ShelfState("shelf-01", (1, 1), "item-01"),
        )
        orders = (
            OrderState("order-00", "item-00", 1),
            OrderState("order-01", "item-01", 1),
        )
        call_ = GreedyPolicy().decide(
            _policy_observation((0, 0), None, shelves, orders)
        )
        self.assertEqual(call_.tool, "move")
        self.assertEqual(call_.arguments, {"direction": "north"})
        self.assertIn("goto-shelf-00", call_.operation_key)

    def test_move_tie_order_north_east_south_west(self):
        order = OrderState("order-00", "item-00", 1)
        cases = (
            ((5, 5), (6, 6), "north"),
            ((5, 5), (6, 4), "east"),
            ((5, 5), (4, 4), "south"),
            ((5, 5), (4, 5), "west"),
        )
        for position, shelf_position, expected in cases:
            with self.subTest(target=shelf_position):
                shelves = (ShelfState("shelf-00", shelf_position, "item-00"),)
                call_ = GreedyPolicy().decide(
                    _policy_observation(position, None, shelves, (order,))
                )
                self.assertEqual(call_.arguments, {"direction": expected})

    def test_at_shelf_picks_with_exact_arguments_and_intent(self):
        shelves = (ShelfState("shelf-00", (2, 2), "item-00"),)
        orders = (OrderState("order-00", "item-00", 1),)
        call_ = GreedyPolicy().decide(
            _policy_observation((2, 2), None, shelves, orders, used=3)
        )
        self.assertEqual(call_.tool, "pick")
        self.assertEqual(dict(call_.arguments), {"shelf_id": "shelf-00", "item_id": "item-00"})
        self.assertEqual(
            call_.operation_key, f"op/{RUN_ID}/step-4/intent-pick-shelf-00"
        )

    def test_carrying_navigates_to_base_then_places_first_matching_order(self):
        shelves = (ShelfState("shelf-00", (2, 2), None),)
        orders = (
            OrderState("order-00", "item-00", 1),
            OrderState("order-01", "item-01", 1),
        )
        moving = GreedyPolicy().decide(
            _policy_observation((2, 2), "item-00", shelves, orders)
        )
        self.assertEqual(moving.tool, "move")
        self.assertEqual(moving.arguments, {"direction": "south"})
        self.assertIn("intent-goto-base", moving.operation_key)

        placing = GreedyPolicy().decide(
            _policy_observation((0, 0), "item-00", shelves, orders)
        )
        self.assertEqual(placing.tool, "place")
        self.assertEqual(dict(placing.arguments), {"order_id": "order-00"})
        self.assertEqual(
            placing.operation_key, f"op/{RUN_ID}/step-1/intent-place-order-00"
        )

    def test_decide_is_deterministic_and_does_not_mutate_observation(self):
        mapping = _policy_observation(
            (0, 0), None,
            (ShelfState("shelf-00", (1, 1), "item-00"),),
            (OrderState("order-00", "item-00", 1),),
        )
        snapshot = json.loads(json.dumps(mapping))
        first = GreedyPolicy().decide(mapping)
        second = GreedyPolicy().decide(mapping)
        self.assertEqual(first, second)
        self.assertEqual(mapping, snapshot)

    def test_emitted_keys_match_the_operation_key_grammar(self):
        shelves = (ShelfState("shelf-00", (1, 1), "item-00"),)
        orders = (OrderState("order-00", "item-00", 1),)
        call_ = GreedyPolicy().decide(
            _policy_observation((0, 0), None, shelves, orders, used=41)
        )
        self.assertTrue(validate_operation_key(call_.operation_key, RUN_ID))
        self.assertIn("/step-42/", call_.operation_key)


class TestBoundedPolicyDecisions(unittest.TestCase):
    """REQ-01 bounded-v0: Manhattan cost minimum, tie by order id."""

    def test_selects_minimum_manhattan_cost_order(self):
        shelves = (
            ShelfState("shelf-00", (9, 9), "item-00"),
            ShelfState("shelf-01", (1, 1), "item-01"),
        )
        orders = (
            OrderState("order-00", "item-00", 1),
            OrderState("order-01", "item-01", 1),
        )
        call_ = BoundedPolicy().decide(
            _policy_observation((0, 0), None, shelves, orders)
        )
        # Cost order-00 = 18+18+2 = 38; order-01 = 2+2+2 = 6.
        self.assertIn("goto-shelf-01", call_.operation_key)
        self.assertEqual(call_.arguments, {"direction": "north"})

    def test_tie_breaks_by_order_id(self):
        shelves = (
            ShelfState("shelf-00", (1, 1), "item-00"),
            ShelfState("shelf-01", (2, 0), "item-01"),
        )
        orders = (
            OrderState("order-00", "item-00", 1),
            OrderState("order-01", "item-01", 1),
        )
        call_ = BoundedPolicy().decide(
            _policy_observation((0, 0), None, shelves, orders)
        )
        # Both cost 6; order-00 wins the tie.
        self.assertIn("goto-shelf-00", call_.operation_key)

    def test_carrying_places_at_base_like_greedy(self):
        shelves = (ShelfState("shelf-00", (2, 2), None),)
        orders = (
            OrderState("order-00", "item-00", 1),
            OrderState("order-01", "item-01", 1),
        )
        call_ = BoundedPolicy().decide(
            _policy_observation((0, 0), "item-01", shelves, orders)
        )
        self.assertEqual(call_.tool, "place")
        self.assertEqual(dict(call_.arguments), {"order_id": "order-01"})
        self.assertIn("intent-place-order-01", call_.operation_key)

    def test_decide_is_deterministic_and_does_not_mutate_observation(self):
        mapping = _policy_observation(
            (0, 0), None,
            (ShelfState("shelf-00", (1, 1), "item-00"),),
            (OrderState("order-00", "item-00", 1),),
        )
        snapshot = json.loads(json.dumps(mapping))
        first = BoundedPolicy().decide(mapping)
        second = BoundedPolicy().decide(mapping)
        self.assertEqual(first, second)
        self.assertEqual(mapping, snapshot)


# --- T-03: auxiliary artifact schemas are locked before any writer (REQ-03/06/07/09) ---

TASK_DEFINITION_KEYS = (
    "config_identity", "height", "initial_state", "orders", "schema_version",
    "seed", "shelves", "slot_positions", "task_version", "width",
)
WORLD_STATE_KEYS = (
    "completed_orders", "height", "orders", "robot", "schema_version",
    "shelves", "step", "width",
)
RUN_CONFIG_KEYS = (
    "budget", "fault_tape_spec", "grants", "policy_digest", "policy_id",
    "schema_version", "seed", "task_version",
)
FAULT_TAPE_SPEC_KEYS = ("fault_id", "hook", "kind", "schema_version", "target")
FAULT_TARGET_KEYS = ("intent", "policy_step")
OPERATION_LOG_KEYS = ("logical_run_id", "operations", "schema_version")
OPERATION_KEYS = (
    "arguments", "events", "fault_ids", "operation_key", "policy_step",
    "reason", "retry", "state_after", "state_before", "status", "tool",
)
OPERATION_NULLABLE = (
    "operations[].arguments", "operations[].operation_key",
    "operations[].reason", "operations[].state_after",
    "operations[].state_before", "operations[].tool",
)
STATUS_ENUM = ("committed", "idempotent_replay", "rejected")
REPLAY_INPUT_KEYS = (
    "budget", "fault_tape", "grants", "logical_run_id", "operations",
    "schema_version", "task_identity",
)
TASK_IDENTITY_KEYS = ("config_digest", "seed", "task_version")
VIOLATION_KEYS = ("code", "detail", "location")
CLOSED_REASON_TAXONOMY = (
    "artifact_mismatch", "budget_exhausted", "commit_boundary_unproven",
    "destination_out_of_grid", "grant_scope_mismatch", "invalid_arguments",
    "inventory_empty", "inventory_not_empty", "item_not_pending",
    "malformed_call", "missing_grant", "not_at_base",
    "operation_key_conflict", "operation_key_invalid",
    "order_already_completed", "order_item_mismatch", "response_lost",
    "robot_not_at_shelf", "score_mismatch", "shelf_item_mismatch",
    "trace_chain_invalid", "unsupported_tool",
)


def _load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class TestAuxiliarySchemaLock(unittest.TestCase):
    """Exact key lists from the tasks.md schema lock; B2+ writers conform verbatim."""

    def test_task_definition_lock(self):
        fixture = _load_fixture("task_definition_schema.json")
        self.assertEqual(fixture["keys"], list(TASK_DEFINITION_KEYS))
        self.assertEqual(
            fixture["nested"]["config_identity"],
            ["generator", "generator_version", "slot_layout"],
        )
        self.assertEqual(fixture["nested"]["initial_state"], list(WORLD_STATE_KEYS))
        self.assertEqual(
            fixture["config_identity"],
            {
                "generator": "formula-v0",
                "generator_version": "1",
                "slot_layout": "p10-two-rows",
            },
        )
        self.assertEqual(fixture["enums"]["task_version"], ["warehouse-v0"])
        self.assertEqual(fixture["constants"]["width"], 10)
        self.assertEqual(fixture["constants"]["height"], 10)
        self.assertIn("initial_state.completed_orders", fixture["sorted_arrays"])

    def test_run_config_lock(self):
        fixture = _load_fixture("run_config_schema.json")
        self.assertEqual(fixture["keys"], list(RUN_CONFIG_KEYS))
        self.assertEqual(
            fixture["nested"]["fault_tape_spec"], list(FAULT_TAPE_SPEC_KEYS)
        )
        self.assertEqual(
            fixture["nested"]["fault_tape_spec.target"], list(FAULT_TARGET_KEYS)
        )
        self.assertIn("fault_tape_spec", fixture["nullables"])
        # Accepted SPEC-CONFLICT interpretation (locked decision 4): the
        # semantic fault descriptor deliberately carries no concrete
        # operation_key. logical_run_id is derived from the semantic
        # descriptor first; only then is the concrete key
        # op/<logical_run_id>/step-<decimal>/intent-<token> expanded into the
        # resolved tape. The pre-resolution spec and resolved tape differ by
        # exactly this expansion; this must not be silently changed.
        self.assertNotIn("operation_key", fixture["nested"]["fault_tape_spec"])
        self.assertEqual(
            fixture["excluded_fields"]["fault_tape_spec"], ["operation_key"]
        )
        self.assertTrue(fixture["spec_conflict_interpretation"].strip())
        self.assertEqual(fixture["constants"]["budget"], 100)

    def test_operation_log_lock(self):
        fixture = _load_fixture("operation_log_schema.json")
        self.assertEqual(fixture["keys"], list(OPERATION_LOG_KEYS))
        self.assertEqual(fixture["nested"]["operations[]"], list(OPERATION_KEYS))
        self.assertEqual(
            fixture["enums"]["operations[].status"], list(STATUS_ENUM)
        )
        for field in OPERATION_NULLABLE:
            self.assertIn(field, fixture["nullables"])
        extra, missing = key_drift(fixture["valid_operation"], OPERATION_KEYS)
        self.assertEqual((extra, missing), ([], []))
        extra, missing = key_drift(fixture["rejected_operation"], OPERATION_KEYS)
        self.assertEqual((extra, missing), ([], []))
        self.assertIsNone(fixture["rejected_operation"]["state_after"])
        self.assertEqual(fixture["rejected_operation"]["status"], "rejected")
        extra, missing = key_drift(fixture["malformed_operation"], OPERATION_KEYS)
        self.assertTrue(extra or missing, "malformed example must violate the shape")

    def test_replay_input_lock(self):
        fixture = _load_fixture("replay_input_schema.json")
        self.assertEqual(fixture["keys"], list(REPLAY_INPUT_KEYS))
        self.assertEqual(
            fixture["nested"]["task_identity"], list(TASK_IDENTITY_KEYS)
        )
        self.assertEqual(fixture["nested"]["operations[]"], list(OPERATION_KEYS))
        self.assertIn("fault_tape", fixture["nullables"])
        self.assertIn("policy", fixture["prohibited_references"])
        self.assertIn("provider", fixture["prohibited_references"])

    def test_verifier_violation_lock(self):
        fixture = _load_fixture("verifier_violation_schema.json")
        self.assertEqual(fixture["keys"], list(VIOLATION_KEYS))
        self.assertEqual(fixture["enums"]["code"], list(CLOSED_REASON_TAXONOMY))
        extra, missing = key_drift(fixture["example"], VIOLATION_KEYS)
        self.assertEqual((extra, missing), ([], []))
        self.assertIn("(code, location, detail)", fixture["sorted_arrays"][0])
