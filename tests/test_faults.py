"""Fault-tape tests: frozen delivery targets, resolution, response-loss retry.

T-11 freezes the first delivery ``place`` target per (seed, policy) and proves
it by simulation from serialized observations only. T-12 covers the declarative
fault model, semantic resolver, and injector. T-13 integrates the response-loss
retry into the runner (REQ-05).
"""
import json
import pathlib
import unittest

from robot_factory_arena.contracts.outcomes import BudgetView
from robot_factory_arena.contracts.tool_call import normalize_tool_call
from robot_factory_arena.domain.observation import Observation, serialize_observation
from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.domain.transition import Operation, apply
from robot_factory_arena.evidence.canonical import canonical_json_bytes
from robot_factory_arena.policies.bounded import BoundedPolicy
from robot_factory_arena.policies.greedy import GreedyPolicy
from robot_factory_arena.runtime.runner import (
    EvidenceCallbacks,
    RunConfig,
    Runner,
    compute_logical_run_id,
)

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"

# Identity is irrelevant to policy decisions; one stable run id keeps the
# simulation keys grammar-valid without touching run identity derivation.
SIMULATION_RUN_ID = "lr_" + "0" * 64

TARGET_KEYS = ("intent", "policy_id", "policy_step", "seed")
FIXTURE_KEYS = ("derivation", "schema_version", "targets")
EXPECTED_COMBINATIONS = {
    ("17", "greedy-v0"),
    ("17", "bounded-v0"),
    ("29", "greedy-v0"),
    ("29", "bounded-v0"),
}


def _load_fixture():
    return json.loads(
        (FIXTURE_DIR / "fault_targets.json").read_text(encoding="utf-8")
    )


def _simulate_first_delivery(seed, policy):
    """Feed ONLY serialized observations to the policy; advance via apply().

    Mirrors the runner loop exactly (observation -> decide -> reserve step ->
    normalize -> apply) and returns (policy_step, intent, order_id) of the
    FIRST committing delivery place. Fails loudly on any rejection: the
    reference policies must complete each generated task legally.
    """
    task = generate_task(seed)
    state = task.initial_state
    used = 0
    while set(state.completed_orders) != {o.order_id for o in state.orders}:
        observation = Observation(
            world_state=state,
            logical_run_id=SIMULATION_RUN_ID,
            grants=("move", "pick", "place"),
            policy_steps=BudgetView(used, 100 - used, 100),
            last_result=None,
        )
        candidate = policy.decide(serialize_observation(observation))
        used += 1  # reserve-before-validation, exactly like the runner
        parse = normalize_tool_call(candidate)
        call = parse.tool_call
        if not parse.ok or call is None:  # fail-closed; policies never misparse
            raise AssertionError((seed, policy.policy_id, used, parse.reason))
        result = apply(
            state, Operation(call.tool, dict(call.arguments), call.operation_key)
        )
        assert result.status == "committed", (
            seed,
            policy.policy_id,
            used,
            result.reason,
        )
        state = result.state
        delivered = [
            event
            for event in result.events
            if event.event_type == "order_delivered"
        ]
        if delivered:
            intent = call.operation_key.rsplit("intent-", 1)[1]
            return used, call.tool, intent, call.arguments["order_id"]
    raise AssertionError("task completed without any delivery")


# --- T-11: frozen response-loss place targets (REQ-01, REQ-05, REQ-07) ---------


class TestFaultTargetFixture(unittest.TestCase):
    """The committed fixture carries computed constants, not placeholders."""

    def test_fixture_is_canonical_json_with_exact_keys(self):
        raw = (FIXTURE_DIR / "fault_targets.json").read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
        self.assertEqual(raw, canonical_json_bytes(parsed))
        self.assertEqual(sorted(parsed), list(FIXTURE_KEYS))
        self.assertEqual(parsed["schema_version"], 1)
        self.assertTrue(parsed["derivation"].strip())

    def test_fixture_covers_seed_17_and_every_seed_29_variant(self):
        targets = _load_fixture()["targets"]
        combinations = {(t["seed"], t["policy_id"]) for t in targets}
        self.assertEqual(combinations, EXPECTED_COMBINATIONS)
        self.assertEqual(len(targets), 4)

    def test_every_target_has_exact_shape_and_place_intent(self):
        targets = _load_fixture()["targets"]
        previous = None
        for target in targets:
            with self.subTest(target=target):
                extra = sorted(set(target) - set(TARGET_KEYS))
                missing = sorted(set(TARGET_KEYS) - set(target))
                self.assertEqual((extra, missing), ([], []))
                self.assertIsInstance(target["policy_step"], int)
                self.assertGreaterEqual(target["policy_step"], 1)
                self.assertLessEqual(target["policy_step"], 100)
                self.assertRegex(target["intent"], r"^place-order-(0[0-9])$")
                order = (target["seed"], target["policy_id"])
                if previous is not None:
                    self.assertGreater(order, previous)
                previous = order

    def test_documented_derivation_method(self):
        # Locked decision 2: derivation by hand or temporary script, deleted
        # unless part of the declared layout. The fixture documents which.
        derivation = _load_fixture()["derivation"]
        self.assertIn("temporary-script", derivation)
        self.assertIn("deleted", derivation)
        self.assertFalse(
            (FIXTURE_DIR.parent.parent / "derive_fault_targets.py").exists()
        )


class TestFirstDeliverySimulation(unittest.TestCase):
    """Re-simulation from serialized observations reproduces frozen targets."""

    def test_frozen_targets_match_deterministic_resimulation(self):
        for target in _load_fixture()["targets"]:
            with self.subTest(seed=target["seed"], policy=target["policy_id"]):
                policy = (
                    GreedyPolicy()
                    if target["policy_id"] == "greedy-v0"
                    else BoundedPolicy()
                )
                step, tool, intent, order_id = _simulate_first_delivery(
                    int(target["seed"]), policy
                )
                self.assertEqual(tool, "place")
                self.assertEqual(step, target["policy_step"])
                self.assertEqual(intent, target["intent"])
                self.assertEqual(order_id, target["intent"].removeprefix("place-"))

    def test_resimulation_is_deterministic(self):
        first = _simulate_first_delivery(17, GreedyPolicy())
        second = _simulate_first_delivery(17, GreedyPolicy())
        self.assertEqual(first, second)


class TestFaultTargetThroughRunner(unittest.TestCase):
    """The real runner lifecycle reaches the frozen delivery target."""

    def test_runner_emits_the_frozen_delivery_first(self):
        for target in _load_fixture()["targets"]:
            with self.subTest(seed=target["seed"], policy=target["policy_id"]):
                policy_obj = (
                    GreedyPolicy()
                    if target["policy_id"] == "greedy-v0"
                    else BoundedPolicy()
                )
                task = generate_task(int(target["seed"]))
                config = RunConfig(
                    schema_version=1,
                    task_version=task.task_version,
                    seed=task.seed,
                    policy_id=policy_obj.policy_id,
                    policy_digest=policy_obj.policy_digest,
                    budget=100,
                    grants=("move", "pick", "place"),
                    fault_tape_spec=None,
                )
                deliveries = []

                def on_tool_result(step, call, result, deliveries=deliveries):
                    if result.status == "committed" and any(
                        event.event_type == "order_delivered"
                        for event in result.events
                    ):
                        deliveries.append(
                            (
                                step,
                                call.operation_key.rsplit("intent-", 1)[1],
                                call.arguments["order_id"],
                            )
                        )

                evidence = EvidenceCallbacks(on_tool_result=on_tool_result)
                result = Runner(evidence=evidence).run(
                    task, policy_obj, config, "execution-fault-target"
                )
                # The full run exceeds the 100-step budget (190 steps); the
                # kill switch is expected here and exercised in detail by the
                # lifecycle tests. The first delivery still happened.
                self.assertEqual(result.outcome, "safe_abort")
                self.assertGreaterEqual(len(deliveries), 1)
                first_step, first_intent, first_order = deliveries[0]
                self.assertEqual(first_step, target["policy_step"])
                self.assertEqual(first_intent, target["intent"])
                self.assertEqual(
                    first_order, target["intent"].removeprefix("place-")
                )


# --- T-12: declarative fault model, semantic resolver, injector (REQ-03/05/07) -

from dataclasses import FrozenInstanceError  # noqa: E402

from robot_factory_arena.contracts.tool_call import validate_operation_key  # noqa: E402
from robot_factory_arena.evidence.canonical import digest_json  # noqa: E402
from robot_factory_arena.faults.injector import FaultInjector  # noqa: E402
from robot_factory_arena.faults.model import (  # noqa: E402
    FAULT_ID_RESPONSE_LOST,
    HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
    KIND_RESPONSE_LOST,
    Fault,
    FaultContext,
    FaultDecision,
    FaultTape,
)
from robot_factory_arena.faults.tape import (  # noqa: E402
    FaultTapeSpec,
    FaultTarget,
    resolve_fault_tape,
)

MODEL_RUN_ID = "lr_" + "ab" * 32
OTHER_MODEL_RUN_ID = "lr_" + "cd" * 32
TARGET_KEY = f"op/{MODEL_RUN_ID}/step-8/intent-place-order-00"


def _spec(step=8, intent="place-order-00"):
    return FaultTapeSpec(
        schema_version=1,
        fault_id=FAULT_ID_RESPONSE_LOST,
        hook=HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
        kind=KIND_RESPONSE_LOST,
        target=FaultTarget(step, intent),
    )


class TestFaultModel(unittest.TestCase):
    """Frozen fault value objects with closed hook/kind sets."""

    def test_fault_has_exact_req05_shape_and_validates(self):
        fault = Fault(FAULT_ID_RESPONSE_LOST, TARGET_KEY,
                      HOOK_AFTER_COMMIT_BEFORE_RESPONSE, KIND_RESPONSE_LOST)
        self.assertEqual(
            sorted(fault.to_mapping()),
            ["fault_id", "hook", "kind", "operation_key"],
        )
        with self.assertRaises(FrozenInstanceError):
            fault.kind = "state_corrupt"
        with self.assertRaises(ValueError):
            Fault(FAULT_ID_RESPONSE_LOST, TARGET_KEY, "before_commit",
                  KIND_RESPONSE_LOST)
        with self.assertRaises(ValueError):
            Fault(FAULT_ID_RESPONSE_LOST, TARGET_KEY,
                  HOOK_AFTER_COMMIT_BEFORE_RESPONSE, "duplicate_effect")
        with self.assertRaises(ValueError):
            Fault("", TARGET_KEY, HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
                  KIND_RESPONSE_LOST)
        with self.assertRaises(ValueError):
            Fault(FAULT_ID_RESPONSE_LOST, "", HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
                  KIND_RESPONSE_LOST)

    def test_tape_is_frozen_with_deterministic_identity(self):
        fault = Fault(FAULT_ID_RESPONSE_LOST, TARGET_KEY,
                      HOOK_AFTER_COMMIT_BEFORE_RESPONSE, KIND_RESPONSE_LOST)
        tape = FaultTape(1, (fault,))
        self.assertEqual(tape.identity, digest_json(tape.to_mapping()))
        self.assertRegex(tape.identity, r"^sha256:[0-9a-f]{64}$")
        with self.assertRaises(FrozenInstanceError):
            tape.faults = ()
        with self.assertRaises(ValueError):
            FaultTape(2, (fault,))
        with self.assertRaises(ValueError):
            FaultTape(1, ())
        with self.assertRaises(TypeError):
            FaultTape(1, ({"fault_id": "x"},))

    def test_decision_carries_its_fault_only_when_fired(self):
        fault = Fault(FAULT_ID_RESPONSE_LOST, TARGET_KEY,
                      HOOK_AFTER_COMMIT_BEFORE_RESPONSE, KIND_RESPONSE_LOST)
        fired = FaultDecision(True, fault)
        self.assertTrue(fired.fired)
        self.assertIs(fired.fault, fault)
        quiet = FaultDecision(False, None)
        self.assertFalse(quiet.fired)
        self.assertIsNone(quiet.fault)
        with self.assertRaises(ValueError):
            FaultDecision(True, None)
        with self.assertRaises(ValueError):
            FaultDecision(False, fault)

    def test_context_validates_key_and_supported_tool(self):
        context = FaultContext(TARGET_KEY, "place")
        self.assertEqual(context.operation_key, TARGET_KEY)
        with self.assertRaises(ValueError):
            FaultContext(TARGET_KEY, "assemble")
        with self.assertRaises(ValueError):
            FaultContext("", "place")


class TestFaultTapeResolution(unittest.TestCase):
    """Two-stage resolution: semantic spec first, concrete key after the id."""

    def test_resolution_expands_the_concrete_operation_key(self):
        tape = resolve_fault_tape(_spec(), MODEL_RUN_ID)
        self.assertEqual(len(tape.faults), 1)
        fault = tape.faults[0]
        self.assertEqual(
            fault.operation_key,
            f"op/{MODEL_RUN_ID}/step-8/intent-place-order-00",
        )
        self.assertEqual(fault.fault_id, FAULT_ID_RESPONSE_LOST)
        self.assertEqual(fault.hook, HOOK_AFTER_COMMIT_BEFORE_RESPONSE)
        self.assertEqual(fault.kind, KIND_RESPONSE_LOST)
        self.assertTrue(
            validate_operation_key(fault.operation_key, MODEL_RUN_ID)
        )

    def test_semantic_spec_carries_no_concrete_key_locked_decision_4(self):
        mapping = _spec().to_mapping()
        self.assertNotIn("operation_key", mapping)
        self.assertNotIn("operation_key", mapping["target"])
        self.assertEqual(
            sorted(mapping),
            ["fault_id", "hook", "kind", "schema_version", "target"],
        )

    def test_identity_is_deterministic_and_execution_independent(self):
        first = resolve_fault_tape(_spec(), MODEL_RUN_ID)
        second = resolve_fault_tape(_spec(), MODEL_RUN_ID)
        self.assertEqual(first, second)
        self.assertEqual(first.identity, second.identity)
        other_run = resolve_fault_tape(_spec(), OTHER_MODEL_RUN_ID)
        self.assertNotEqual(first.identity, other_run.identity)
        other_target = resolve_fault_tape(_spec(9, "place-order-03"), MODEL_RUN_ID)
        self.assertNotEqual(first.identity, other_target.identity)

    def test_resolved_tape_is_fully_resolved_before_execution(self):
        # Resolution is a pure function of frozen constants: no RNG, no
        # wall clock, no runtime state, and no policy is consulted.
        class ExplodingPolicy:
            policy_id = "must-never-be-called"

            def decide(self, observation):
                raise AssertionError("resolver must never call a policy")

        tape = resolve_fault_tape(_spec(), MODEL_RUN_ID)
        self.assertIsInstance(tape, FaultTape)
        del ExplodingPolicy

    def test_resolver_rejects_invalid_specs_and_run_ids(self):
        with self.assertRaises(TypeError):
            resolve_fault_tape({"fault_id": "x"}, MODEL_RUN_ID)
        with self.assertRaises(ValueError):
            resolve_fault_tape(_spec(), "run-1")
        with self.assertRaises(ValueError):
            resolve_fault_tape(_spec(), "lr_" + "ZZ" * 32)

    def test_spec_rejects_unknown_fault_kinds_hooks_and_ids(self):
        with self.assertRaises(ValueError):
            FaultTapeSpec(1, "hang_forever", HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
                          KIND_RESPONSE_LOST, FaultTarget(8, "place-order-00"))
        with self.assertRaises(ValueError):
            FaultTapeSpec(1, FAULT_ID_RESPONSE_LOST, "before_commit",
                          KIND_RESPONSE_LOST, FaultTarget(8, "place-order-00"))
        with self.assertRaises(ValueError):
            FaultTapeSpec(1, FAULT_ID_RESPONSE_LOST,
                          HOOK_AFTER_COMMIT_BEFORE_RESPONSE, "state_corrupt",
                          FaultTarget(8, "place-order-00"))
        with self.assertRaises(ValueError):
            FaultTapeSpec(2, FAULT_ID_RESPONSE_LOST,
                          HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
                          KIND_RESPONSE_LOST, FaultTarget(8, "place-order-00"))

    def test_spec_rejects_ambiguous_target_constants(self):
        # Not a delivery place intent: can never target the demonstrator.
        with self.assertRaises(ValueError):
            _spec(8, "goto-base")
        with self.assertRaises(ValueError):
            _spec(8, "pick-shelf-03")
        with self.assertRaises(ValueError):
            _spec(8, "place-order-99")
        # Step 0 is not a valid attempt number (attempts are 1-based).
        with self.assertRaises(ValueError):
            _spec(0, "place-order-00")
        with self.assertRaises(ValueError):
            _spec(-1, "place-order-00")


class TestFaultInjector(unittest.TestCase):
    """One-shot response-only matching on the concrete operation key."""

    def setUp(self):
        self.tape = resolve_fault_tape(_spec(), MODEL_RUN_ID)
        self.injector = FaultInjector(self.tape)

    def test_after_commit_hook_fires_only_for_the_target_key(self):
        fired = self.injector.after_commit_before_response(
            FaultContext(TARGET_KEY, "place")
        )
        self.assertTrue(fired.fired)
        self.assertEqual(fired.fault.kind, KIND_RESPONSE_LOST)
        self.assertEqual(
            fired.fault.hook, HOOK_AFTER_COMMIT_BEFORE_RESPONSE
        )
        quiet = self.injector.after_commit_before_response(
            FaultContext(f"op/{MODEL_RUN_ID}/step-9/intent-place-order-01",
                         "place")
        )
        self.assertFalse(quiet.fired)

    def test_hook_is_one_shot_per_operation(self):
        first = self.injector.after_commit_before_response(
            FaultContext(TARGET_KEY, "place")
        )
        self.assertTrue(first.fired)
        second = self.injector.after_commit_before_response(
            FaultContext(TARGET_KEY, "place")
        )
        self.assertFalse(second.fired)

    def test_before_commit_never_fires_in_v0(self):
        decision = self.injector.before_commit(FaultContext(TARGET_KEY, "place"))
        self.assertFalse(decision.fired)

    def test_injector_validates_tape_and_context(self):
        with self.assertRaises(TypeError):
            FaultInjector({"faults": []})
        with self.assertRaises(TypeError):
            self.injector.after_commit_before_response("place")
        with self.assertRaises(TypeError):
            self.injector.before_commit(None)
        with self.assertRaises(ValueError):
            self.injector.after_commit_before_response(
                FaultContext(TARGET_KEY, "move-north-invalid")
            )

    def test_injector_exposes_no_mutable_authority(self):
        self.assertFalse(hasattr(self.injector, "consumed"))
        self.assertFalse(hasattr(self.injector, "state"))
        with self.assertRaises(FrozenInstanceError):
            self.tape.faults[0].kind = "duplicate_effect"


# --- T-13: response-loss retry integrated into the runner (REQ-02/04/05) -------

from robot_factory_arena.contracts.outcomes import Counters  # noqa: E402
from robot_factory_arena.contracts.policy import policy_manifest_digest  # noqa: E402
from robot_factory_arena.domain.model import (  # noqa: E402
    OrderState,
    RobotState,
    ShelfState,
    WorldState,
)
from robot_factory_arena.domain.task import SLOT_POSITIONS, TaskDefinition  # noqa: E402


def _carrying_one_order_task(seed_text="17"):
    """Minimal hand-built task: robot at base already carrying the order item."""
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


class _Scripted:
    """Self-contained scripted policy (last candidate repeats forever)."""

    policy_id = "scripted-v0"

    def __init__(self, candidates):
        self._candidates = list(candidates)
        self.calls = 0
        self.observations = []

    @property
    def policy_digest(self):
        return policy_manifest_digest(self.policy_id)

    def decide(self, observation):
        self.calls += 1
        self.observations.append(observation)
        index = min(self.calls, len(self._candidates)) - 1
        return self._candidates[index]


class TestResponseLossRetry(unittest.TestCase):
    """The faulted fixture: one effect, one retry, completed outcome."""

    def _faulted_run(self, spec_step=1, order_id="order-00", candidates=None,
                     sink=None):
        """Run the hand-built delivery task with the tape targeting the place.

        ``sink``, when given, is invoked inside the persistence boundary
        (after the runner's own bookkeeping entry point is reached) and may
        raise to simulate a durability failure.
        """
        task = _carrying_one_order_task()
        spec = FaultTapeSpec(
            schema_version=1,
            fault_id=FAULT_ID_RESPONSE_LOST,
            hook=HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            kind=KIND_RESPONSE_LOST,
            target=FaultTarget(spec_step, f"place-{order_id}"),
        )
        config = RunConfig(
            schema_version=1,
            task_version=task.task_version,
            seed=task.seed,
            policy_id=_Scripted.policy_id,
            policy_digest=_Scripted("[]").policy_digest,
            budget=100,
            grants=("move", "pick", "place"),
            fault_tape_spec=spec,
        )
        run_id = compute_logical_run_id(task, config)
        target_key = f"op/{run_id}/step-{spec_step}/intent-place-{order_id}"
        if candidates is None:
            candidates = [
                {
                    "schema_version": 1,
                    "tool": "place",
                    "arguments": {"order_id": order_id},
                    "operation_key": target_key,
                }
            ]
        policy = _Scripted(candidates)
        events = []

        def boundary(step, call, result):
            events.append(("commit_boundary", step, call.operation_key))
            if sink is not None:
                sink(step, call, result)

        evidence = EvidenceCallbacks(
            on_run_started=lambda *payload: events.append(("run_started",)),
            on_observation=lambda step, obs: events.append(("observation", step)),
            on_policy_decision=lambda step, cand: events.append(("decision", step)),
            on_commit_boundary=boundary,
            on_fault_injected=lambda step, fault_id, key: events.append(
                ("fault_injected", step, fault_id)
            ),
            on_retry=lambda step, call, result: events.append(
                ("retry", step, result.status)
            ),
            on_run_finished=lambda outcome, counters, state: events.append(
                ("run_finished", outcome)
            ),
        )
        result = Runner(evidence=evidence).run(
            task, policy, config, "execution-fault"
        )
        return result, events, target_key, run_id

    def test_faulted_delivery_retries_and_completes(self):
        result, events, target_key, run_id = self._faulted_run()
        self.assertEqual(result.outcome, "completed")
        # policy_steps=1 (retry is free), tool_attempts=2, one effect, one
        # retry, no violations, checks: initial + post-fault + post-retry.
        self.assertEqual(result.counters, Counters(1, 2, 1, 1, 0, 3))
        self.assertEqual(result.final_state.completed_orders, ("order-00",))
        self.assertEqual(
            result.fault_tape,
            resolve_fault_tape(_spec(1, "place-order-00"), run_id),
        )
        # Ordering: the boundary precedes the fault signal, which precedes
        # the retry; the lost response is never observed as a tool result.
        self.assertEqual(
            events,
            [
                ("run_started",),
                ("observation", 1),
                ("decision", 1),
                ("commit_boundary", 1, target_key),
                ("fault_injected", 1, FAULT_ID_RESPONSE_LOST),
                ("retry", 1, "idempotent_replay"),
                ("run_finished", "completed"),
            ],
        )

    def test_retry_reuses_the_exact_call_and_key(self):
        captured = {}
        task = _carrying_one_order_task()
        spec = FaultTapeSpec(
            1, FAULT_ID_RESPONSE_LOST, HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST, FaultTarget(1, "place-order-00"),
        )
        config = RunConfig(
            1, task.task_version, task.seed, _Scripted.policy_id,
            _Scripted([]).policy_digest, 100, ("move", "pick", "place"), spec,
        )
        run_id = compute_logical_run_id(task, config)
        target_key = f"op/{run_id}/step-1/intent-place-order-00"
        policy = _Scripted(
            [
                {
                    "schema_version": 1,
                    "tool": "place",
                    "arguments": {"order_id": "order-00"},
                    "operation_key": target_key,
                }
            ]
        )

        def on_retry(step, call, result):
            captured.update(
                step=step,
                tool=call.tool,
                arguments=dict(call.arguments),
                key=call.operation_key,
                status=result.status,
                reason=result.reason,
                events=result.events,
            )

        evidence = EvidenceCallbacks(on_retry=on_retry)
        outcome = Runner(evidence=evidence).run(
            task, policy, config, "execution-fault"
        )
        self.assertEqual(outcome.outcome, "completed")
        self.assertEqual(captured["step"], 1)
        self.assertEqual(captured["tool"], "place")
        self.assertEqual(captured["arguments"], {"order_id": "order-00"})
        self.assertEqual(captured["key"], target_key)
        # The retry returns the stored first result as an idempotent replay
        # with no reason (the first result committed) and no new events.
        self.assertEqual(captured["status"], "idempotent_replay")
        self.assertIsNone(captured["reason"])
        self.assertEqual(captured["events"], ())

    def test_one_delivery_effect_and_no_duplicate_reward(self):
        delivered = []
        boundaries = []

        def sink(step, call, result):
            boundaries.append(call.operation_key)
            delivered.extend(result.events)

        result, _, target_key, _ = self._faulted_run(sink=sink)
        self.assertEqual(boundaries, [target_key])  # exactly one boundary
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].event_type, "order_delivered")
        self.assertEqual(result.counters.committed_effects, 1)
        self.assertEqual(
            result.final_state.completed_orders.count("order-00"), 1
        )
        self.assertEqual(result.counters.internal_retries, 1)

    def test_terminal_completion_precedence_with_fault_at_step_100(self):
        task = _carrying_one_order_task()
        spec = FaultTapeSpec(
            1, FAULT_ID_RESPONSE_LOST, HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST, FaultTarget(100, "place-order-00"),
        )
        config = RunConfig(
            1, task.task_version, task.seed, _Scripted.policy_id,
            _Scripted([]).policy_digest, 100, ("move", "pick", "place"), spec,
        )
        run_id = compute_logical_run_id(task, config)
        candidates = [{"nonsense": True}] * 99 + [
            {
                "schema_version": 1,
                "tool": "place",
                "arguments": {"order_id": "order-00"},
                "operation_key": f"op/{run_id}/step-100/intent-place-order-00",
            }
        ]
        policy = _Scripted(candidates)
        result = Runner().run(task, policy, config, "execution-fault")
        # Completion at the step-100 post-retry check beats budget_exhausted.
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.counters, Counters(100, 2, 1, 1, 99, 102))
        self.assertEqual(policy.calls, 100)

    def test_unresolved_boundary_proof_is_never_upgraded_to_success(self):
        def broken_store(step, call, result):
            raise RuntimeError("artifact store unavailable")

        result, events, target_key, _ = self._faulted_run(sink=broken_store)
        # The boundary could not be durably recorded, so whether the commit
        # crossed it is unprovable: unresolved_outcome, never success.
        self.assertEqual(result.outcome, "unresolved_outcome")
        self.assertEqual(result.counters, Counters(1, 1, 0, 0, 0, 1))
        # No retry was attempted: recovery requires proven boundary evidence.
        self.assertNotIn(("retry", 1, "idempotent_replay"), events)
        self.assertNotIn("fault_injected", [event[0] for event in events])
        self.assertEqual(result.final_state.completed_orders, ())


# ---------------------------------------------------------------------------
# T-16: fault persistence proof through the artifact store (REQ-05, REQ-09)
# ---------------------------------------------------------------------------

import json as _fjson  # noqa: E402
import tempfile as _ftempfile  # noqa: E402
from pathlib import Path as _fPath  # noqa: E402

from robot_factory_arena.evidence.artifact_store import (  # noqa: E402
    RunEvidenceRecorder,
)

from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes as _fcanonical,  # noqa: E402
)


def _faulted_recorded_run(root, execution_id="execution-fault", sink=None):
    """The B3 faulted delivery, fully evidenced by a RunEvidenceRecorder."""
    task = _carrying_one_order_task()
    spec = FaultTapeSpec(
        1, FAULT_ID_RESPONSE_LOST, HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
        KIND_RESPONSE_LOST, FaultTarget(1, "place-order-00"),
    )
    config = RunConfig(
        1, task.task_version, task.seed, _Scripted.policy_id,
        _Scripted("[]").policy_digest, 100, ("move", "pick", "place"), spec,
    )
    logical_run_id = compute_logical_run_id(task, config)
    recorder = RunEvidenceRecorder(root, task, config, logical_run_id,
                                   execution_id)
    # Pre-execution persistence (REQ-05/design step 2): the RESOLVED tape is
    # on disk before any observation/callback fires.
    tape_bytes = (recorder.directory / "fault_tape.json").read_bytes()
    tape_mapping = _fjson.loads(tape_bytes.decode("utf-8"))
    pre_execution_key = tape_mapping["faults"][0]["operation_key"]
    target_key = f"op/{logical_run_id}/step-1/intent-place-order-00"
    assert tape_bytes == _fcanonical(tape_mapping)
    assert pre_execution_key == target_key

    def boundary(step, call, result):
        if sink is not None:
            sink(step, call, result)

    callbacks = recorder.callback_map()
    recorder_boundary = callbacks["on_commit_boundary"]

    def wrapped_boundary(step, call, result):
        # The failing store is unavailable DURING the write: the sink raises
        # BEFORE the recorder records, so no commit evidence can appear.
        boundary(step, call, result)
        recorder_boundary(step, call, result)

    callbacks["on_commit_boundary"] = wrapped_boundary
    evidence = EvidenceCallbacks(**callbacks)
    policy = _Scripted(
        [
            {
                "schema_version": 1,
                "tool": "place",
                "arguments": {"order_id": "order-00"},
                "operation_key": target_key,
            }
        ]
    )
    result = Runner(evidence=evidence).run(task, policy, config, execution_id)
    recorder.close()
    return result, recorder, target_key


def _fread(path):
    return _fjson.loads(_fPath(path).read_text(encoding="utf-8"))


class TestFaultedRunArtifactProof(unittest.TestCase):
    """T-16: the boundary, op log, and replay input prove the fault path."""

    def test_tape_is_persisted_before_execution_with_the_concrete_key(self):
        with _ftempfile.TemporaryDirectory() as root:
            _, recorder, target_key = _faulted_recorded_run(root)
            tape = _fread(recorder.directory / "fault_tape.json")
            self.assertEqual(tape["faults"][0]["operation_key"], target_key)
            self.assertEqual(tape["faults"][0]["kind"], "response_lost")
            self.assertEqual(recorder.receipt_candidate.fault_tape_digest,
                             None or __import__(
                                 "robot_factory_arena.evidence.canonical",
                                 fromlist=["digest_json"]
                             ).digest_json(tape))

    def test_operation_log_preserves_commit_fault_and_retry_evidence(self):
        with _ftempfile.TemporaryDirectory() as root:
            result, recorder, target_key = _faulted_recorded_run(root)
            self.assertEqual(result.outcome, "completed")
            log = _fread(recorder.directory / "operation-log.json")
            operations = log["operations"]
            self.assertEqual(len(operations), 2)
            committed, retry = operations
            self.assertEqual(committed["status"], "committed")
            self.assertFalse(committed["retry"])
            self.assertEqual(committed["fault_ids"], [])
            self.assertEqual(committed["operation_key"], target_key)
            self.assertEqual(len(committed["events"]), 1)
            self.assertIsNotNone(committed["state_after"])
            self.assertEqual(retry["status"], "idempotent_replay")
            self.assertTrue(retry["retry"])
            self.assertEqual(retry["fault_ids"], [FAULT_ID_RESPONSE_LOST])
            self.assertEqual(retry["operation_key"], target_key)
            self.assertEqual(retry["events"], [])
            self.assertIsNone(retry["state_after"])
            # The trace carries the fault and retry records between the
            # commit and run_finished (locked decision 3 ordering intact).
            raw = (recorder.directory / "trace.jsonl").read_text("utf-8")
            events = [_fjson.loads(line)["event"]
                      for line in raw.splitlines()]
            self.assertEqual(
                events,
                ["run_started", "observation_emitted", "policy_decision",
                 "tool_committed", "order_delivered", "fault_injected",
                 "retry", "run_finished"],
            )
            self.assertNotIn("verification_finished", events)

    def test_boundary_file_proves_the_crossing_before_the_fault(self):
        with _ftempfile.TemporaryDirectory() as root:
            _, recorder, target_key = _faulted_recorded_run(root)
            boundary = _fread(recorder.directory / "boundaries"
                              / "step-1.json")
            self.assertEqual(boundary["operation_key"], target_key)
            self.assertTrue(boundary["commit_marker"]["commit_proven"])
            self.assertIsNotNone(boundary["commit_marker"]["snapshot_digest"])
            self.assertEqual(boundary["operation_record"]["status"],
                             "committed")
            replay_input = _fread(recorder.directory / "replay-input.json")
            self.assertEqual(
                replay_input["fault_tape"]["faults"][0]["operation_key"],
                target_key,
            )

    def test_unresolved_boundary_writes_no_commit_evidence(self):
        def broken_store(step, call, result):
            raise RuntimeError("artifact store unavailable")

        with _ftempfile.TemporaryDirectory() as root:
            result, recorder, _ = _faulted_recorded_run(
                root, sink=broken_store
            )
            self.assertEqual(result.outcome, "unresolved_outcome")
            log = _fread(recorder.directory / "operation-log.json")
            self.assertEqual(log["operations"], [])  # no proven commit entry
            raw = (recorder.directory / "trace.jsonl").read_text("utf-8")
            events = [_fjson.loads(line)["event"]
                      for line in raw.splitlines()]
            self.assertNotIn("tool_committed", events)
            self.assertNotIn("fault_injected", events)
            self.assertEqual(events[-1], "run_finished")
            self.assertEqual(
                recorder.receipt_candidate.outcome, "unresolved_outcome"
            )
            # No boundary file exists: the crossing is unproven.
            self.assertFalse(
                (recorder.directory / "boundaries" / "step-1.json").exists()
            )
