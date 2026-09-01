"""Runtime boundary tests: grants, budget kill switch, counters (REQ-02).

T-08 covers the grants and budget primitives; T-09 adds the idempotency
ledger and ToolBay gateway; T-10/T-13 (B3) add the runner lifecycle and the
response-loss retry integration.
"""
import dataclasses
import unittest
from types import SimpleNamespace

from robot_factory_arena.contracts.outcomes import BudgetView, Counters
from robot_factory_arena.contracts.policy import policy_manifest_digest
from robot_factory_arena.contracts.tool_call import ToolCall
from robot_factory_arena.domain.model import (
    OrderState,
    RobotState,
    ShelfState,
    WorldState,
)
from robot_factory_arena.domain.task import SLOT_POSITIONS, TaskDefinition
from robot_factory_arena.runtime.budget import Budget, BudgetExhaustedError
from robot_factory_arena.runtime.grants import (
    AuthorizationResult,
    Grant,
    GrantSet,
    authorize,
    grant_id_for,
    issue_grants,
)
from robot_factory_arena.runtime.ledger import (
    Ledger,
    LedgerDecision,
    LedgerEntry,
    arguments_digest,
)
from robot_factory_arena.runtime.runner import (
    EvidenceCallbacks,
    RunConfig,
    Runner,
    compute_logical_run_id,
)
from robot_factory_arena.runtime.tool_bay import (
    ResponseLostError,
    ToolBay,
    ToolResult,
)

RUN_ID = "lr_" + "0123456789abcdef" * 4
OTHER_RUN_ID = "lr_" + "fedcba9876543210" * 4


def _call(tool, arguments, step=1, intent="probe", run_id=RUN_ID):
    return ToolCall(1, tool, arguments, f"op/{run_id}/step-{step}/intent-{intent}")


# --- T-08: run-scoped grants and authorization (REQ-03, REQ-04) ----------------


class TestGrantIssuance(unittest.TestCase):
    """Grant ids are deterministic; the issued set is explicit and sorted."""

    def test_grant_ids_are_deterministic_from_run_and_capability(self):
        first = grant_id_for(RUN_ID, "move")
        self.assertEqual(first, grant_id_for(RUN_ID, "move"))
        self.assertNotEqual(first, grant_id_for(RUN_ID, "pick"))
        self.assertNotEqual(first, grant_id_for(OTHER_RUN_ID, "move"))
        self.assertRegex(first, r"^grant-[0-9a-f]{16}$")

    def test_issue_grants_sorts_and_dedupes_capabilities(self):
        grant_set = issue_grants(RUN_ID, ("place", "move", "pick", "move"))
        self.assertEqual(
            [grant.capability for grant in grant_set.grants],
            ["move", "pick", "place"],
        )
        for grant in grant_set.grants:
            self.assertEqual(grant.scope, "run")
            self.assertEqual(
                grant.grant_id, grant_id_for(RUN_ID, grant.capability)
            )

    def test_issue_grants_reject_unsupported_or_empty_capabilities(self):
        with self.assertRaises(ValueError):
            issue_grants(RUN_ID, ("move", "assemble"))
        with self.assertRaises(ValueError):
            issue_grants(RUN_ID, ())
        with self.assertRaises(ValueError):
            issue_grants("run-1", ("move",))

    def test_grant_scope_is_literal_run_in_v0(self):
        with self.assertRaises(ValueError):
            Grant("grant-x", "move", "session")
        with self.assertRaises(ValueError):
            Grant("grant-x", "assemble", "run")

    def test_grant_set_is_frozen_and_strictly_sorted(self):
        grant_set = issue_grants(RUN_ID, ("move", "pick"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            grant_set.grants = ()
        # Construction rejects capabilities that are not strictly ascending.
        with self.assertRaises(ValueError):
            GrantSet(
                (
                    Grant(grant_id_for(RUN_ID, "pick"), "pick", "run"),
                    Grant(grant_id_for(RUN_ID, "move"), "move", "run"),
                )
            )


class TestAuthorization(unittest.TestCase):
    """Authorization precedes any transition; failures are closed-taxonomy."""

    def setUp(self):
        self.grants = issue_grants(RUN_ID, ("move", "pick", "place"))

    def test_authorized_for_each_granted_tool(self):
        for tool, arguments in (
            ("move", {"direction": "north"}),
            ("pick", {"shelf_id": "shelf-00", "item_id": "item-00"}),
            ("place", {"order_id": "order-00"}),
        ):
            with self.subTest(tool=tool):
                result = authorize(_call(tool, arguments), self.grants, RUN_ID)
                self.assertTrue(result.ok)
                self.assertIsNone(result.reason)

    def test_capability_mismatch_is_missing_grant(self):
        move_only = issue_grants(RUN_ID, ("move",))
        result = authorize(
            _call("place", {"order_id": "order-00"}), move_only, RUN_ID
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "missing_grant")

    def test_grant_from_another_run_is_scope_mismatch(self):
        foreign = issue_grants(OTHER_RUN_ID, ("move", "pick", "place"))
        result = authorize(
            _call("move", {"direction": "north"}), foreign, RUN_ID
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "grant_scope_mismatch")

    def test_unsupported_tool_is_unauthorized(self):
        # Duck-typed probe: normalize_tool_call rejects unsupported tools
        # upstream, so authorize() defends with the closed taxonomy directly.
        result = authorize(
            SimpleNamespace(tool="assemble"), self.grants, RUN_ID
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "unsupported_tool")

    def test_authorization_result_is_frozen_and_consistent(self):
        ok = authorize(
            _call("move", {"direction": "north"}), self.grants, RUN_ID
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ok.ok = False
        with self.assertRaises(ValueError):
            AuthorizationResult(True, "missing_grant")
        with self.assertRaises(ValueError):
            AuthorizationResult(False, None)


# --- T-08: the runner-owned hard budget and six counters (REQ-02) --------------


class TestBudgetPrimitive(unittest.TestCase):
    """Reserve-before-parse, 99->100 boundary, no 101st reservation."""

    def test_default_maximum_is_100(self):
        budget = Budget()
        self.assertEqual(budget.view(), BudgetView(0, 100, 100))

    def test_reserve_consumes_exactly_one_step_before_any_validation(self):
        budget = Budget()
        view = budget.reserve_policy_attempt()
        self.assertEqual(view, BudgetView(1, 99, 100))
        # Even a contract violation after reservation never refunds the step.
        budget.record_violation()
        self.assertEqual(budget.view(), BudgetView(1, 99, 100))

    def test_boundary_from_99_to_100(self):
        budget = Budget()
        for _ in range(99):
            budget.reserve_policy_attempt()
        self.assertEqual(budget.view(), BudgetView(99, 1, 100))
        budget.require_available()  # must not raise at 99
        view = budget.reserve_policy_attempt()
        self.assertEqual(view, BudgetView(100, 0, 100))
        with self.assertRaises(BudgetExhaustedError):
            budget.require_available()
        with self.assertRaises(BudgetExhaustedError):
            budget.reserve_policy_attempt()

    def test_no_101st_reservation(self):
        budget = Budget()
        for _ in range(100):
            budget.reserve_policy_attempt()
        with self.assertRaises(BudgetExhaustedError):
            budget.reserve_policy_attempt()
        self.assertEqual(budget.view(), BudgetView(100, 0, 100))

    def test_counters_are_six_separate_exact_integers(self):
        budget = Budget()
        budget.reserve_policy_attempt()
        budget.record_tool_attempt()
        budget.record_tool_attempt()
        budget.record_commit()
        budget.record_retry()
        budget.record_violation()
        budget.record_violation()
        budget.record_terminal_check()
        budget.record_terminal_check()
        budget.record_terminal_check()
        self.assertEqual(
            budget.counters(),
            Counters(
                policy_steps=1,
                tool_attempts=2,
                committed_effects=1,
                internal_retries=1,
                contract_violations=2,
                terminal_checks=3,
            ),
        )

    def test_terminal_check_is_free(self):
        budget = Budget()
        budget.reserve_policy_attempt()
        budget.record_terminal_check()
        self.assertEqual(budget.view(), BudgetView(1, 99, 100))
        self.assertEqual(budget.counters().tool_attempts, 0)
        self.assertEqual(budget.counters().internal_retries, 0)
        self.assertEqual(budget.counters().committed_effects, 0)

    def test_internal_retry_separates_tool_attempts_from_policy_steps(self):
        # REQ-02 scenario: a retry at policy step 8 must not consume step 9.
        budget = Budget()
        for _ in range(8):
            budget.reserve_policy_attempt()
        budget.record_tool_attempt()
        budget.record_retry()
        budget.record_tool_attempt()
        self.assertEqual(budget.view(), BudgetView(8, 92, 100))
        self.assertEqual(
            budget.counters(),
            Counters(8, 2, 0, 1, 0, 0),
        )

    def test_snapshots_are_frozen_value_objects(self):
        budget = Budget()
        view = budget.view()
        counters = budget.counters()
        self.assertIsInstance(view, BudgetView)
        self.assertIsInstance(counters, Counters)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            view.used = 5
        with self.assertRaises(dataclasses.FrozenInstanceError):
            counters.policy_steps = 5

    def test_budget_validates_its_maximum(self):
        with self.assertRaises(TypeError):
            Budget(True)
        with self.assertRaises(TypeError):
            Budget("100")
        with self.assertRaises(ValueError):
            Budget(0)
        self.assertEqual(Budget(100).view().maximum, 100)


# --- T-09: idempotency ledger and the ToolBay gateway (REQ-03, REQ-04, REQ-06) --

_STANDARD_SHELVES = (
    ShelfState("shelf-00", (1, 2), "item-05"),
    ShelfState("shelf-01", (3, 2), "item-08"),
)
_STANDARD_ORDERS = (
    OrderState("order-00", "item-05", 1),
    OrderState("order-01", "item-08", 1),
)


def _world(position=(0, 0), inventory=None, step=0, shelves=_STANDARD_SHELVES,
           orders=_STANDARD_ORDERS, completed=()):
    return WorldState(
        schema_version=1,
        width=10,
        height=10,
        robot=RobotState(position, inventory, (0, 0)),
        shelves=shelves,
        orders=orders,
        completed_orders=completed,
        step=step,
    )


def _full_grants(run_id=RUN_ID):
    return issue_grants(run_id, ("move", "pick", "place"))


class TestLedgerDecisions(unittest.TestCase):
    """First result wins; same payload replays; different payload conflicts."""

    def test_first_lookup_is_a_new_key(self):
        decision = Ledger().lookup(
            f"op/{RUN_ID}/step-1/intent-a", "move", {"direction": "north"}
        )
        self.assertEqual(decision.kind, "new_key")
        self.assertIsNone(decision.stored)
        self.assertIsNone(decision.reason)

    def test_store_first_then_same_payload_replays(self):
        ledger = Ledger()
        key = f"op/{RUN_ID}/step-4/intent-pick-shelf-00"
        arguments = {"shelf_id": "shelf-00", "item_id": "item-05"}
        state = _world()
        committed = ToolResult(
            "committed", state, (), key, None, True
        )
        entry = LedgerEntry(
            key, "pick", arguments_digest(arguments), "committed", committed,
            "sha256:" + "0" * 64,
        )
        ledger.store_first(entry)
        replay = ledger.lookup(key, "pick", arguments)
        self.assertEqual(replay.kind, "idempotent_replay")
        self.assertEqual(replay.stored, entry)
        self.assertIsNone(replay.reason)

    def test_same_key_different_payload_conflicts(self):
        ledger = Ledger()
        key = f"op/{RUN_ID}/step-4/intent-pick-shelf-00"
        first = {"shelf_id": "shelf-00", "item_id": "item-05"}
        ledger.store_first(LedgerEntry(
            key, "pick", arguments_digest(first), "rejected",
            ToolResult("rejected", _world(), (), key, "robot_not_at_shelf", False),
            None,
        ))
        conflict = ledger.lookup(
            key, "pick", {"shelf_id": "shelf-00", "item_id": "item-08"}
        )
        self.assertEqual(conflict.kind, "operation_key_conflict")
        self.assertEqual(conflict.reason, "operation_key_conflict")
        # The tool is part of the payload identity: same args, other tool.
        conflict = ledger.lookup(
            key, "move", {"shelf_id": "shelf-00", "item_id": "item-05"}
        )
        self.assertEqual(conflict.kind, "operation_key_conflict")

    def test_equivalent_argument_insertion_order_replays(self):
        ledger = Ledger()
        key = f"op/{RUN_ID}/step-4/intent-pick-shelf-00"
        first = {"shelf_id": "shelf-00", "item_id": "item-05"}
        reordered = {"item_id": "item-05", "shelf_id": "shelf-00"}
        ledger.store_first(LedgerEntry(
            key, "pick", arguments_digest(first), "rejected",
            ToolResult("rejected", _world(), (), key, "robot_not_at_shelf", False),
            None,
        ))
        decision = ledger.lookup(key, "pick", reordered)
        self.assertEqual(decision.kind, "idempotent_replay")

    def test_first_store_wins_never_overwritten(self):
        ledger = Ledger()
        key = f"op/{RUN_ID}/step-1/intent-a"
        arguments = {"direction": "north"}
        first = LedgerEntry(
            key, "move", arguments_digest(arguments), "rejected",
            ToolResult("rejected", _world(), (), key, "not_at_base", False),
            None,
        )
        second = LedgerEntry(
            key, "move", arguments_digest(arguments), "committed",
            ToolResult("committed", _world(), (), key, None, True),
            "sha256:" + "0" * 64,
        )
        ledger.store_first(first)
        ledger.store_first(second)
        decision = ledger.lookup(key, "move", arguments)
        self.assertEqual(decision.stored, first)

    def test_ledger_entry_validates_shape(self):
        state = _world()
        with self.assertRaises(ValueError):
            # idempotent_replay is produced by lookups, never stored.
            LedgerEntry(
                f"op/{RUN_ID}/step-1/intent-a", "move",
                arguments_digest({"direction": "north"}), "idempotent_replay",
                ToolResult("rejected", state, (),
                           f"op/{RUN_ID}/step-1/intent-a", "not_at_base", False),
                None,
            )
        with self.assertRaises(ValueError):
            LedgerEntry(
                f"op/{RUN_ID}/step-1/intent-a", "assemble",
                arguments_digest({}), "rejected",
                ToolResult("rejected", state, (),
                           f"op/{RUN_ID}/step-1/intent-a", "not_at_base", False),
                None,
            )
        with self.assertRaises(ValueError):
            # A committed entry carries its snapshot digest; rejected carries none.
            LedgerEntry(
                f"op/{RUN_ID}/step-1/intent-a", "move",
                arguments_digest({"direction": "north"}), "committed",
                ToolResult("committed", state, (),
                           f"op/{RUN_ID}/step-1/intent-a", None, True),
                None,
            )


class TestToolBayGateway(unittest.TestCase):
    """Grants before apply; ledger before apply; no duplicate effects."""

    def setUp(self):
        self.bay = ToolBay(Ledger())
        self.grants = _full_grants()

    def test_missing_grant_rejects_before_apply_and_is_stored(self):
        state = _world(position=(0, 0), inventory="item-05")
        move_only = issue_grants(RUN_ID, ("move",))
        call = _call("place", {"order_id": "order-00"}, 8, "place-order-00")
        result = self.bay.submit(state, call, move_only)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "missing_grant")
        self.assertFalse(result.committed)
        self.assertEqual(result.events, ())
        self.assertEqual(result.state, state)
        # The first rejected result persists: the same submission replays.
        replay = self.bay.submit(state, call, move_only)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertEqual(replay.reason, "missing_grant")

    def test_invalid_arguments_reject_before_apply_and_are_stored(self):
        state = _world(position=(0, 0), inventory="item-05")
        call = ToolCall(
            1, "place", {"extra": 1},
            f"op/{RUN_ID}/step-8/intent-place-order-00",
        )
        result = self.bay.submit(state, call, self.grants)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "invalid_arguments")
        replay = self.bay.submit(state, call, self.grants)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertEqual(replay.reason, "invalid_arguments")

    def test_commit_is_stored_and_replayed_without_reapplying(self):
        state = _world(position=(1, 2), step=3)
        call = _call(
            "pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 4,
            "pick-shelf-00",
        )
        first = self.bay.submit(state, call, self.grants)
        self.assertEqual(first.status, "committed")
        self.assertTrue(first.committed)
        self.assertEqual(len(first.events), 1)
        self.assertEqual(first.state.robot.inventory_item_id, "item-05")
        # Replayed from a DIFFERENT world where apply() would reject: proves
        # the transition authority is never invoked on replay.
        moved = _world(position=(5, 5), step=9)
        replay = self.bay.submit(moved, call, self.grants)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertFalse(replay.committed)
        self.assertEqual(replay.events, ())
        self.assertEqual(replay.state, moved)
        self.assertIsNone(replay.reason)

    def test_same_key_different_payload_conflicts_without_effect(self):
        state = _world(position=(1, 2), step=3)
        key = f"op/{RUN_ID}/step-4/intent-pick-shelf-00"
        first = self.bay.submit(
            state,
            ToolCall(1, "pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, key),
            self.grants,
        )
        self.assertEqual(first.status, "committed")
        conflicting = ToolCall(
            1, "pick", {"shelf_id": "shelf-00", "item_id": "item-08"}, key
        )
        result = self.bay.submit(state, conflicting, self.grants)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "operation_key_conflict")
        self.assertEqual(result.events, ())
        self.assertEqual(result.state, state)
        # The original entry is intact: the original payload still replays.
        original = ToolCall(
            1, "pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, key
        )
        replay = self.bay.submit(state, original, self.grants)
        self.assertEqual(replay.status, "idempotent_replay")

    def test_domain_rejection_is_stored_once_and_replayed_with_reason(self):
        state = _world(position=(5, 5), step=1)
        call = _call(
            "pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 2,
            "pick-shelf-00",
        )
        first = self.bay.submit(state, call, self.grants)
        self.assertEqual(first.status, "rejected")
        self.assertEqual(first.reason, "robot_not_at_shelf")
        replay = self.bay.submit(state, call, self.grants)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertEqual(replay.reason, "robot_not_at_shelf")

    def test_retry_flag_replays_identically(self):
        state = _world(position=(0, 0), inventory="item-05", step=7)
        call = _call("place", {"order_id": "order-00"}, 8, "place-order-00")
        first = self.bay.submit(state, call, self.grants)
        self.assertEqual(first.status, "committed")
        retry = self.bay.submit(first.state, call, self.grants, retry=True)
        self.assertEqual(retry.status, "idempotent_replay")
        self.assertEqual(retry.events, ())
        self.assertEqual(retry.state, first.state)

    def test_submit_validates_its_inputs(self):
        state = _world()
        call = _call("move", {"direction": "north"})
        with self.assertRaises(TypeError):
            self.bay.submit(None, call, self.grants)
        with self.assertRaises(TypeError):
            self.bay.submit(state, {"tool": "move"}, self.grants)
        with self.assertRaises(TypeError):
            self.bay.submit(state, call, None)
        with self.assertRaises(TypeError):
            self.bay.submit(state, call, self.grants, retry="yes")

    def test_gateway_exposes_no_mutable_authority(self):
        self.assertFalse(hasattr(self.bay, "ledger"))
        self.assertFalse(hasattr(self.bay, "apply"))
        self.assertFalse(hasattr(self.bay, "lookup"))
        result = ToolResult(
            "rejected", _world(), (), f"op/{RUN_ID}/step-1/intent-a",
            "not_at_base", False,
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.committed = True
        with self.assertRaises(ValueError):
            # committed flag must match the status exactly.
            ToolResult(
                "committed", _world(), (), f"op/{RUN_ID}/step-1/intent-a",
                None, False,
            )
        with self.assertRaises(ValueError):
            # A rejected result cannot carry an out-of-taxonomy reason.
            ToolResult(
                "rejected", _world(), (), f"op/{RUN_ID}/step-1/intent-a",
                "because_i_said_so", False,
            )

    def test_ledger_decision_is_frozen(self):
        decision = LedgerDecision("new_key", None, None)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.kind = "idempotent_replay"


# --- T-10: RunConfig and the runner lifecycle (REQ-02, REQ-03, REQ-07) ---------


def _carrying_one_order_task(seed_text="17"):
    """Minimal hand-built task: robot at base already carrying the one order's item."""
    shelves = (ShelfState("shelf-00", (1, 2), None),)
    orders = (OrderState("order-00", "item-05", 1),)
    initial = WorldState(
        schema_version=1,
        width=10,
        height=10,
        robot=RobotState((0, 0), "item-05", (0, 0)),
        shelves=shelves,
        orders=orders,
        completed_orders=(),
        step=0,
    )
    return TaskDefinition(
        task_version="warehouse-v0",
        schema_version=1,
        seed=seed_text,
        width=10,
        height=10,
        slot_positions=SLOT_POSITIONS,
        shelves=shelves,
        orders=orders,
        initial_state=initial,
        config_identity={
            "generator": "formula-v0",
            "generator_version": "1",
            "slot_layout": "p10-two-rows",
        },
    )


def _run_config(task, grants=("move", "pick", "place"), policy_id="scripted-v0"):
    return RunConfig(
        schema_version=1,
        task_version=task.task_version,
        seed=task.seed,
        policy_id=policy_id,
        policy_digest=policy_manifest_digest(policy_id),
        budget=100,
        grants=tuple(grants),
        fault_tape_spec=None,
    )


def _place_candidate(run_id, step, order_id="order-00"):
    return {
        "schema_version": 1,
        "tool": "place",
        "arguments": {"order_id": order_id},
        "operation_key": f"op/{run_id}/step-{step}/intent-place-{order_id}",
    }


MALFORMED_CANDIDATE = {"nonsense": True}


class ScriptedPolicy:
    """Deterministic fake policy: canned candidates, last one repeats forever."""

    policy_id = "scripted-v0"

    def __init__(self, candidates):
        policy_manifest_digest(self.policy_id)  # digest shape used below
        self._candidates = list(candidates)
        self.observations = []
        self.calls = 0

    @property
    def policy_digest(self):
        return policy_manifest_digest(self.policy_id)

    def decide(self, observation):
        self.calls += 1
        self.observations.append(observation)
        index = min(self.calls, len(self._candidates)) - 1
        return self._candidates[index]


class TestRunConfig(unittest.TestCase):
    """T-10: the locked RunConfig schema is enforced, not inferred."""

    def _invalid(self, **overrides):
        task = _carrying_one_order_task()
        values = dict(
            schema_version=1,
            task_version=task.task_version,
            seed=task.seed,
            policy_id="greedy-v0",
            policy_digest=policy_manifest_digest("greedy-v0"),
            budget=100,
            grants=("move", "pick", "place"),
            fault_tape_spec=None,
        )
        values.update(overrides)
        return RunConfig(**values)

    def test_exact_serialization_shape_with_null_spec(self):
        config = self._invalid()
        mapping = config.to_mapping()
        self.assertEqual(
            sorted(mapping),
            [
                "budget",
                "fault_tape_spec",
                "grants",
                "policy_digest",
                "policy_id",
                "schema_version",
                "seed",
                "task_version",
            ],
        )
        self.assertIsNone(mapping["fault_tape_spec"])
        self.assertEqual(mapping["budget"], 100)
        self.assertEqual(mapping["grants"], ["move", "pick", "place"])
        self.assertEqual(mapping["schema_version"], 1)

    def test_run_config_rejects_drift(self):
        with self.assertRaises(ValueError):
            self._invalid(budget=99)
        with self.assertRaises(ValueError):
            self._invalid(budget="100")
        with self.assertRaises(ValueError):
            self._invalid(schema_version=2)
        with self.assertRaises(ValueError):
            self._invalid(seed="017")  # not canonical decimal text
        with self.assertRaises(ValueError):
            self._invalid(policy_id="")
        with self.assertRaises(ValueError):
            self._invalid(policy_digest="deadbeef")
        with self.assertRaises(ValueError):
            self._invalid(task_version="warehouse-v1")
        with self.assertRaises(ValueError):
            self._invalid(grants=("place", "move"))  # not strictly ascending
        with self.assertRaises(ValueError):
            self._invalid(grants=("move", "assemble"))
        with self.assertRaises(ValueError):
            self._invalid(grants=())
        with self.assertRaises(TypeError):
            self._invalid(fault_tape_spec={"fault_id": "x"})

    def test_runner_validates_its_inputs(self):
        task = _carrying_one_order_task()
        config = _run_config(task)
        policy = ScriptedPolicy([])
        runner = Runner()
        with self.assertRaises(TypeError):
            runner.run(None, policy, config, "exec-1")
        with self.assertRaises(TypeError):
            runner.run(task, object(), config, "exec-1")
        with self.assertRaises(TypeError):
            runner.run(task, policy, None, "exec-1")
        with self.assertRaises(TypeError):
            runner.run(task, "not-a-policy", config, "exec-1")
        with self.assertRaises(ValueError):
            runner.run(task, policy, config, "")
        with self.assertRaises(ValueError):
            runner.run(task, policy, _run_config(_carrying_one_order_task("29")), "exec-1")
        with self.assertRaises(TypeError):
            Runner(evidence="sink")


class TestRunnerLifecycle(unittest.TestCase):
    """T-10: terminal checks, reserve-before-parse, kill switch, counters."""

    def _run(self, candidates, task=None, grants=("move", "pick", "place")):
        task = task or _carrying_one_order_task()
        config = _run_config(task, grants=grants)
        run_id = compute_logical_run_id(task, config)
        policy = ScriptedPolicy(candidates)
        result = Runner().run(task, policy, config, "execution-b3")
        return result, policy, run_id, task

    def test_initial_terminal_check_completes_without_policy_attempt(self):
        task = _carrying_one_order_task()
        already = TaskDefinition(
            task_version=task.task_version,
            schema_version=1,
            seed=task.seed,
            width=10,
            height=10,
            slot_positions=SLOT_POSITIONS,
            shelves=task.shelves,
            orders=task.orders,
            initial_state=WorldState(
                schema_version=1,
                width=10,
                height=10,
                robot=RobotState((0, 0), None, (0, 0)),
                shelves=task.shelves,
                orders=task.orders,
                completed_orders=("order-00",),
                step=3,
            ),
            config_identity=dict(task.config_identity),
        )
        config = _run_config(already)
        policy = ScriptedPolicy([])
        result = Runner().run(already, policy, config, "exec-done")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.counters, Counters(0, 0, 0, 0, 0, 1))
        self.assertEqual(policy.calls, 0)
        self.assertEqual(result.final_state, already.initial_state)

    def test_place_completes_with_exact_counters(self):
        task = _carrying_one_order_task()
        config = _run_config(task)
        run_id = compute_logical_run_id(task, config)
        policy = ScriptedPolicy([_place_candidate(run_id, 1)])
        result = Runner().run(task, policy, config, "execution-b3")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.counters, Counters(1, 1, 1, 0, 0, 2))
        self.assertEqual(result.final_state.completed_orders, ("order-00",))
        self.assertEqual(result.final_state.robot.inventory_item_id, None)
        self.assertEqual(result.final_state.step, 1)
        self.assertEqual(policy.calls, 1)
        self.assertEqual(result.logical_run_id, run_id)

    def test_malformed_candidate_consumes_step_and_violation_then_completes(self):
        task = _carrying_one_order_task()
        config = _run_config(task)
        run_id = compute_logical_run_id(task, config)
        policy = ScriptedPolicy([MALFORMED_CANDIDATE, _place_candidate(run_id, 2)])
        result = Runner().run(task, policy, config, "execution-b3")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.counters, Counters(2, 1, 1, 0, 1, 3))
        # The second observation reports the parse rejection with no key.
        self.assertEqual(
            policy.observations[1]["last_result"],
            {"status": "rejected", "operation_key": "", "reason": "malformed_call"},
        )
        self.assertEqual(
            policy.observations[1]["policy_steps"],
            {"used": 1, "remaining": 99, "maximum": 100},
        )

    def test_unauthorized_calls_cannot_bypass_the_budget(self):
        # REQ-02 scenario: a grantless place is rejected and traced, consumes
        # its reserved step, and the run terminates at 100 without a 101st
        # policy action.
        result, policy, _, _ = self._run(
            [_place_candidate("lr_" + "0" * 64, 1)], grants=("move",)
        )
        self.assertEqual(result.outcome, "safe_abort")
        self.assertEqual(result.counters, Counters(100, 100, 0, 0, 100, 101))
        self.assertEqual(policy.calls, 100)
        self.assertEqual(len(policy.observations), 100)

    def test_domain_rejections_consume_steps_without_contract_violations(self):
        # Legal tool call, world refuses (inventory_empty): protocol hygiene
        # stays clean; the budget still kills the run.
        task = _carrying_one_order_task()
        shelves = (ShelfState("shelf-00", (1, 2), None),)
        empty_handed = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((0, 0), None, (0, 0)),
            shelves=shelves,
            orders=task.orders,
            completed_orders=(),
            step=0,
        )
        naked_task = TaskDefinition(
            task_version="warehouse-v0",
            schema_version=1,
            seed="17",
            width=10,
            height=10,
            slot_positions=SLOT_POSITIONS,
            shelves=shelves,
            orders=task.orders,
            initial_state=empty_handed,
            config_identity=dict(task.config_identity),
        )
        config = _run_config(naked_task)
        run_id = compute_logical_run_id(naked_task, config)
        # Fresh key per step: every attempt is a NEW first submission, so the
        # run isolates domain rejections (no hygiene/replay noise).
        candidates = [_place_candidate(run_id, step) for step in range(1, 101)]
        result, policy, _, _ = self._run(candidates, task=naked_task)
        self.assertEqual(result.outcome, "safe_abort")
        self.assertEqual(result.counters, Counters(100, 100, 0, 0, 0, 101))
        self.assertEqual(
            policy.observations[0]["last_result"],
            None,
        )
        self.assertEqual(
            policy.observations[1]["last_result"]["reason"], "inventory_empty"
        )

    def test_terminal_completion_on_step_100_takes_precedence(self):
        task = _carrying_one_order_task()
        config = _run_config(task)
        run_id = compute_logical_run_id(task, config)
        candidates = [MALFORMED_CANDIDATE] * 99 + [_place_candidate(run_id, 100)]
        policy = ScriptedPolicy(candidates)
        result = Runner().run(task, policy, config, "execution-b3")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.counters, Counters(100, 1, 1, 0, 99, 101))
        self.assertEqual(policy.calls, 100)

    def test_policy_initiated_key_replay_counts_as_hygiene_violation(self):
        # Robot starts at (1,0): move west (commits), re-submit the SAME key
        # (ledger replay -> hygiene violation), then place from base.
        task = _carrying_one_order_task()
        shelves = (ShelfState("shelf-00", (1, 2), None),)
        at_edge = WorldState(
            schema_version=1,
            width=10,
            height=10,
            robot=RobotState((1, 0), "item-05", (0, 0)),
            shelves=shelves,
            orders=task.orders,
            completed_orders=(),
            step=0,
        )
        edge_task = TaskDefinition(
            task_version="warehouse-v0",
            schema_version=1,
            seed="17",
            width=10,
            height=10,
            slot_positions=SLOT_POSITIONS,
            shelves=shelves,
            orders=task.orders,
            initial_state=at_edge,
            config_identity=dict(task.config_identity),
        )
        config = _run_config(edge_task)
        run_id = compute_logical_run_id(edge_task, config)
        reused_key = f"op/{run_id}/step-1/intent-goto-base"
        policy = ScriptedPolicy(
            [
                {
                    "schema_version": 1,
                    "tool": "move",
                    "arguments": {"direction": "west"},
                    "operation_key": reused_key,
                },
                {
                    "schema_version": 1,
                    "tool": "move",
                    "arguments": {"direction": "west"},
                    "operation_key": reused_key,
                },
                _place_candidate(run_id, 3),
            ]
        )
        result = Runner().run(edge_task, policy, config, "execution-b3")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.counters, Counters(3, 3, 2, 0, 1, 4))
        self.assertEqual(result.final_state.step, 2)
        self.assertEqual(
            result.final_state.robot.position, (0, 0)
        )
        # The replay observation carries the idempotent status and no reason
        # (the first result committed).
        self.assertEqual(
            policy.observations[2]["last_result"],
            {"status": "idempotent_replay", "operation_key": reused_key,
             "reason": None},
        )

    def test_policy_receives_only_serialized_observations(self):
        task = _carrying_one_order_task()
        config = _run_config(task)
        run_id = compute_logical_run_id(task, config)
        policy = ScriptedPolicy([_place_candidate(run_id, 1)])
        Runner().run(task, policy, config, "execution-b3")
        observation = policy.observations[0]
        self.assertEqual(
            sorted(observation),
            [
                "grants",
                "last_result",
                "logical_run_id",
                "policy_steps",
                "schema_version",
                "world_state",
            ],
        )
        self.assertIsInstance(observation["world_state"], dict)
        self.assertEqual(observation["logical_run_id"], run_id)
        self.assertEqual(observation["grants"], ["move", "pick", "place"])
        # Mutating the delivered mapping cannot corrupt the next observation:
        observation["grants"].append("assemble")
        observation["world_state"]["step"] = 999
        second = ScriptedPolicy([_place_candidate(run_id, 1)])
        again = Runner().run(task, second, config, "execution-b3")
        self.assertEqual(again.outcome, "completed")
        self.assertEqual(second.observations[0]["grants"], ["move", "pick", "place"])
        self.assertEqual(second.observations[0]["world_state"]["step"], 0)

    def test_evidence_stream_is_ordered_and_complete(self):
        task = _carrying_one_order_task()
        config = _run_config(task)
        run_id = compute_logical_run_id(task, config)
        events = []

        def record(name):
            def sink(*payload):
                events.append((name,) + payload)

            return sink

        evidence = EvidenceCallbacks(
            on_run_started=record("run_started"),
            on_observation=lambda step, obs: events.append(("observation", step)),
            on_policy_decision=lambda step, cand: events.append(("decision", step)),
            on_parse_rejected=lambda step, reason, key: events.append(
                ("parse_rejected", step, reason, key)
            ),
            on_tool_result=lambda step, call, res: events.append(
                ("tool_result", step, res.status)
            ),
            on_run_finished=lambda outcome, counters, state: events.append(
                ("run_finished", outcome)
            ),
        )
        policy = ScriptedPolicy([MALFORMED_CANDIDATE, _place_candidate(run_id, 2)])
        result = Runner(evidence=evidence).run(task, policy, config, "execution-b3")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(
            events,
            [
                ("run_started", result.logical_run_id, config),
                ("observation", 1),
                ("decision", 1),
                ("parse_rejected", 1, "malformed_call", ""),
                ("observation", 2),
                ("decision", 2),
                ("tool_result", 2, "committed"),
                ("run_finished", "completed"),
            ],
        )

    def test_logical_identity_excludes_execution_id(self):
        task = _carrying_one_order_task()
        config = _run_config(task)
        policy = ScriptedPolicy([_place_candidate(compute_logical_run_id(task, config), 1)])
        first = Runner().run(task, policy, config, "execution-one")
        second = Runner().run(task, policy, config, "execution-two")
        self.assertEqual(first.logical_run_id, second.logical_run_id)
        self.assertEqual(first.final_state, second.final_state)
        self.assertEqual(first.counters, second.counters)


# --- T-13: the ToolBay commit boundary and the response-loss signal (REQ-05) ---


class TestToolBayCommitBoundary(unittest.TestCase):
    """The boundary fires after store_first and before the response returns."""

    def setUp(self):
        self.ledger = Ledger()
        self.bay = ToolBay(self.ledger)
        self.grants = _full_grants()

    def _committed_place(self):
        state = _world(position=(0, 0), inventory="item-05")
        call = _call("place", {"order_id": "order-00"}, 8, "place-order-00")
        return state, call

    def test_boundary_fires_after_store_first_before_return(self):
        state, call = self._committed_place()
        observed = []

        def boundary(received_call, result):
            observed.append(
                (
                    received_call is call,
                    result.status,
                    call.operation_key in self.ledger,
                )
            )
            return None

        result = self.bay.submit(state, call, self.grants,
                                 on_commit_boundary=boundary)
        self.assertEqual(result.status, "committed")
        # Ledger-first ordering: the durable first result exists when the
        # persistence boundary fires, and the committed result is returned.
        self.assertEqual(observed, [(True, "committed", True)])

    def test_boundary_receives_the_committed_result_with_events(self):
        state, call = self._committed_place()
        seen = {}
        self.bay.submit(
            state, call, self.grants,
            on_commit_boundary=lambda c, r: seen.update(call=c, result=r),
        )
        self.assertIs(seen["call"], call)
        self.assertEqual(seen["result"].status, "committed")
        self.assertEqual(len(seen["result"].events), 1)
        self.assertEqual(seen["result"].operation_key, call.operation_key)

    def test_boundary_not_invoked_for_rejections_or_replays(self):
        state = _world(position=(5, 5))
        call = _call(
            "pick", {"shelf_id": "shelf-00", "item_id": "item-05"}, 2,
            "pick-shelf-00",
        )
        statuses = []
        first = self.bay.submit(state, call, self.grants,
                                on_commit_boundary=lambda c, r: statuses.append(r.status))
        self.assertEqual(first.status, "rejected")
        replay = self.bay.submit(state, call, self.grants, retry=True,
                                 on_commit_boundary=lambda c, r: statuses.append(r.status))
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertEqual(statuses, [])

    def test_response_lost_signal_raises_and_keeps_the_ledger_entry(self):
        state, call = self._committed_place()
        with self.assertRaises(ResponseLostError) as caught:
            self.bay.submit(
                state, call, self.grants,
                on_commit_boundary=lambda c, r: "response_lost",
            )
        self.assertEqual(caught.exception.operation_key, call.operation_key)
        self.assertEqual(caught.exception.reason, "response_lost")
        # The committed response was never returned; the durable first
        # result remains replayable through an internal retry.
        retry = self.bay.submit(state, call, self.grants, retry=True)
        self.assertEqual(retry.status, "idempotent_replay")
        self.assertEqual(len(self.ledger), 1)

    def test_boundary_must_be_callable_and_return_known_signals(self):
        state, call = self._committed_place()
        with self.assertRaises(TypeError):
            self.bay.submit(state, call, self.grants,
                            on_commit_boundary="persist")
        with self.assertRaises(TypeError):
            self.bay.submit(
                state, call, self.grants,
                on_commit_boundary=lambda c, r: "duplicate_effect",
            )
