"""Runtime boundary tests: grants, budget kill switch, counters (REQ-02).

T-08 covers the grants and budget primitives; T-09 adds the idempotency
ledger and ToolBay gateway; T-10/T-13 (B3) add the runner lifecycle and the
response-loss retry integration.
"""
import dataclasses
import unittest
from types import SimpleNamespace

from robot_factory_arena.runtime.ledger import (
    Ledger,
    LedgerDecision,
    LedgerEntry,
    arguments_digest,
)
from robot_factory_arena.runtime.tool_bay import ToolBay, ToolResult

from robot_factory_arena.contracts.outcomes import BudgetView, Counters
from robot_factory_arena.contracts.tool_call import ToolCall
from robot_factory_arena.domain.model import (
    OrderState,
    RobotState,
    ShelfState,
    WorldState,
)
from robot_factory_arena.runtime.budget import Budget, BudgetExhaustedError
from robot_factory_arena.runtime.grants import (
    AuthorizationResult,
    Grant,
    GrantSet,
    authorize,
    grant_id_for,
    issue_grants,
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
