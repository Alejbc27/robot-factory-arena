"""Phase 4A runner-integration tests (T4A-06, P4A-REQ-02/03/09).

The REAL unchanged v0 Runner executes LLMPolicy: a provider error at used=7
reserves step 8, records malformed_call, increments contract_violations,
makes no ToolBay submission/world effect, and exposes last_result.status ==
"rejected"; valid calls dispatch through the unchanged ToolBay; provider/model
identity flows only through policy_digest/logical_run_id; and the trace
carries only the existing normalized fields.
"""
import json
import tempfile
import unittest
from pathlib import Path

from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.evidence.artifact_store import RunEvidenceRecorder
from robot_factory_arena.policies.llm import LLMPolicy, llm_policy_digest
from robot_factory_arena.providers.adapter import (
    ProviderError,
    ProviderResult,
    ToolCallPayload,
    Usage,
)
from robot_factory_arena.providers.fake import FakeProvider
from robot_factory_arena.runtime.runner import (
    RUN_CONFIG_KEYS,
    EvidenceCallbacks,
    RunConfig,
    Runner,
    compute_logical_run_id,
)

GRANTS = ("move", "pick", "place")


def _success(direction, request_id):
    return ProviderResult(
        provider="fake",
        model="fake-v1",
        request_id=request_id,
        usage=Usage(4, 6, 10),
        latency_ms=2,
        raw_visible_response=f"moving {direction}",
        tool_call=ToolCallPayload("move", {"direction": direction}),
        error=None,
    )


def _place_success(request_id):
    return ProviderResult(
        provider="fake",
        model="fake-v1",
        request_id=request_id,
        usage=Usage(4, 6, 10),
        latency_ms=2,
        raw_visible_response="placing order-00",
        tool_call=ToolCallPayload("place", {"order_id": "order-00"}),
        error=None,
    )


def _error(code, message, request_id):
    return ProviderResult(
        provider="fake",
        model="fake-v1",
        request_id=request_id,
        usage=Usage(4, 0, 4),
        latency_ms=1,
        raw_visible_response=None,
        tool_call=None,
        error=ProviderError(code, message),
    )


def _script(moves, trailing_error):
    script = [_success("north", f"request-{step}") for step in range(1, moves + 1)]
    if trailing_error:
        script.append(_error(*trailing_error, request_id=f"request-{moves + 1}"))
    return script


def _config(task, provider="fake", model="fake-v1"):
    return RunConfig(
        1,
        task.task_version,
        task.seed,
        "llm-v0",
        llm_policy_digest(provider, model),
        100,
        GRANTS,
        None,
    )


class _ObservationCollector:
    """Captures serialized observations and the finished counters in-run."""

    def __init__(self):
        self.observations = {}
        self.counters = None
        self.outcome = None
        self.final_state = None

    def callback_map(self):
        def on_observation(policy_step, serialized):
            self.observations[policy_step] = serialized

        def on_run_finished(outcome, counters, final_state):
            self.outcome = outcome
            self.counters = counters
            self.final_state = final_state

        return EvidenceCallbacks(
            on_observation=on_observation, on_run_finished=on_run_finished
        )


class TestProviderErrorConsumesOneStepAndCannotMutateTheWorld(unittest.TestCase):
    """P4A-REQ-02 scenario: used=7 error -> step 8 reserved, malformed_call."""

    def test_error_at_step_8_rejects_without_world_effect(self):
        task = generate_task("17")
        config = _config(task)
        policy = LLMPolicy(FakeProvider(script=_script(7, ("no_tool_call", "prose"))))
        collector = _ObservationCollector()
        result = Runner(evidence=collector.callback_map()).run(
            task, policy, config, "exec-integration-1"
        )

        # Step 8's rejection is exposed to the NEXT observation (step 9).
        after = collector.observations[9]
        self.assertEqual(after["policy_steps"]["used"], 8)
        self.assertEqual(
            after["last_result"]["status"], "rejected"
        )
        self.assertEqual(after["last_result"]["reason"], "malformed_call")
        self.assertEqual(
            after["last_result"]["operation_key"],
            f"op/{result.logical_run_id}/step-8/intent-llm-reject",
        )

        # Accounting: seven scripted committed moves; every later garbage
        # candidate consumes exactly one reserved step as a contract violation.
        counters = collector.counters
        assert counters is not None
        self.assertEqual(counters.policy_steps, 100)
        self.assertEqual(counters.tool_attempts, 7)
        self.assertEqual(counters.committed_effects, 7)
        self.assertEqual(counters.internal_retries, 0)
        self.assertEqual(counters.contract_violations, 93)
        self.assertEqual(counters.terminal_checks, 101)
        self.assertEqual(collector.outcome, "safe_abort")

        # No ToolBay submission after the seven moves: no order was completed
        # and the robot never left the north corridor (0,0)->(0,7).
        final_state = collector.final_state
        assert final_state is not None
        self.assertEqual(list(final_state.completed_orders), [])
        self.assertEqual(final_state.robot.position, (0, 7))
        self.assertEqual(result.outcome, "safe_abort")


class TestValidCallsDispatchThroughTheUnchangedCore(unittest.TestCase):
    """Valid provider calls become legal ToolCalls handled by the real core."""

    def test_valid_move_commits_and_place_is_domain_rejected_without_violation(self):
        task = generate_task("17")
        config = _config(task)
        policy = LLMPolicy(
            FakeProvider(
                script=[
                    _place_success("request-1"),
                    _success("north", "request-2"),
                ]
            )
        )
        collector = _ObservationCollector()
        Runner(evidence=collector.callback_map()).run(
            task, policy, config, "exec-integration-2"
        )

        # Step 1: a VALID place normalized by the policy is dispatched to the
        # unchanged ToolBay and rejected by the DOMAIN (empty inventory), not
        # by the protocol: no contract violation is recorded for it.
        after_place = collector.observations[2]
        self.assertEqual(after_place["last_result"]["status"], "rejected")
        self.assertEqual(after_place["last_result"]["reason"], "inventory_empty")

        # Step 2: the valid move commits through the unchanged transition
        # authority; the world effect is real and counted once.
        after_move = collector.observations[3]
        self.assertEqual(after_move["last_result"]["status"], "committed")
        counters = collector.counters
        final_state = collector.final_state
        assert counters is not None
        assert final_state is not None
        self.assertEqual(counters.committed_effects, 1)
        self.assertEqual(counters.tool_attempts, 2)
        self.assertEqual(final_state.robot.position, (0, 1))
        # The two scripted calls were protocol-clean; the remaining 98 budget
        # steps exhaust the finite script (script_exhausted -> malformed_call),
        # each consuming exactly one reserved step as a contract violation.
        self.assertEqual(counters.policy_steps, 100)
        self.assertEqual(counters.contract_violations, 98)


class TestIdentityFlowsOnlyThroughPolicyDigest(unittest.TestCase):
    """P4A-REQ-03: provider/model changes move logical identity, nothing else."""

    def test_two_models_produce_different_digests_and_logical_ids(self):
        task = generate_task("17")
        config_a = _config(task, model="model-a")
        config_b = _config(task, model="model-b")
        self.assertNotEqual(config_a.policy_digest, config_b.policy_digest)
        logical_a = compute_logical_run_id(task, config_a)
        logical_b = compute_logical_run_id(task, config_b)
        self.assertNotEqual(logical_a, logical_b)

    def test_runconfig_retains_exactly_the_frozen_key_set(self):
        task = generate_task("17")
        mapping = _config(task).to_mapping()
        self.assertEqual(tuple(sorted(mapping)), tuple(sorted(RUN_CONFIG_KEYS)))


class TestTraceCarriesOnlyNormalizedFields(unittest.TestCase):
    """P4A-REQ-09: raw provider output never reaches the core trace."""

    LOCKED_TRACE_FIELDS = {
        "schema_version", "seq", "logical_run_id", "policy_step", "event",
        "operation_key", "tool", "arguments", "state_before", "state_after",
        "payload", "prev_event_hash", "event_hash",
    }

    def test_trace_records_stay_within_the_locked_normalized_shape(self):
        task = generate_task("17")
        config = _config(task)
        policy = LLMPolicy(
            FakeProvider(
                script=[
                    _success("north", "request-1"),
                    _error("no_tool_call", "prose", "request-2"),
                ]
            )
        )
        with tempfile.TemporaryDirectory() as root:
            recorder = RunEvidenceRecorder(
                root, task, config, compute_logical_run_id(task, config),
                "exec-integration-3",
            )
            Runner(evidence=EvidenceCallbacks(**recorder.callback_map())).run(
                task, policy, config, "exec-integration-3"
            )
            recorder.close()
            raw = (Path(recorder.directory) / "trace.jsonl").read_text("utf-8")
        for line in raw.splitlines():
            record = json.loads(line)
            self.assertEqual(set(record), self.LOCKED_TRACE_FIELDS)
        self.assertIn('"policy_decision"', raw)
        for forbidden in (
            "raw_visible_response", "latency_ms", "tool_schema", "prompt",
            "script_exhausted", "moving north",
        ):
            self.assertNotIn(forbidden, raw)


if __name__ == "__main__":
    unittest.main()
