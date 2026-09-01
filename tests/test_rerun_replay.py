"""Rerun identity and policy-free replay tests (REQ-07, REQ-09).

T-17 (B4) fills the operation-only offline replay suite: locked ReplayInput
validation, policy-free reconstruction from recorded operations, seed/config
binding, receipt agreement, and rerun identity. T-23..T-25 (B6) add the CLI
flow, golden rerun equality, and the final gate.
"""
import json
import tempfile
import unittest
from pathlib import Path

from robot_factory_arena.contracts.outcomes import (
    VerificationResult,
)
from robot_factory_arena.contracts.policy import policy_manifest_digest
from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.evidence.artifact_store import RunEvidenceRecorder
from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes,
)
from robot_factory_arena.evidence.receipt import ReceiptWriter
from robot_factory_arena.policies.greedy import GreedyPolicy
from robot_factory_arena.runtime.runner import (
    EvidenceCallbacks,
    RunConfig,
    Runner,
    compute_logical_run_id,
)

RUN_ID = "lr_" + "0123456789abcdef" * 4


def _completed_task():
    """The formula task whose greedy run fits the budget (AMEND-01 seed).

    Seed 18 (q=8, n=5) completes all orders in 92 greedy steps, so a
    recorded run can COMPLETE — the completion demonstrator that seeds
    17/29 cannot provide within the locked 100-step budget.
    """
    return generate_task(18)


def _greedy_config(task):
    return RunConfig(
        1, task.task_version, task.seed, "greedy-v0",
        policy_manifest_digest("greedy-v0"), 100, ("move", "pick", "place"),
        None,
    )


def _recorded_run(root, execution_id="execution-rr"):
    """One evidenced COMPLETED greedy run on the formula task (seed 18).

    Replay regenerates the task from the recorded seed via the formula
    generator, so replay tests record REAL formula-task runs (a hand-built
    task would correctly fail the state_before binding check).
    """
    task = _completed_task()
    config = _greedy_config(task)
    logical_run_id = compute_logical_run_id(task, config)
    recorder = RunEvidenceRecorder(root, task, config, logical_run_id,
                                   execution_id)
    evidence = EvidenceCallbacks(**recorder.callback_map())
    result = Runner(evidence=evidence).run(task, GreedyPolicy(), config,
                                           execution_id)
    recorder.close()
    return result, recorder


class _RaisingPolicy:
    """A policy that must NEVER be called by replay (REQ-07)."""

    policy_id = "must-never-run"

    def decide(self, observation):
        raise AssertionError("replay must never invoke a policy")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TestModuleDiscovered(unittest.TestCase):
    """T-01 placeholder kept for discovery; real suites live above."""

    def test_module_is_discovered(self):
        self.assertIsNotNone(__doc__)


class TestReplayInputSchema(unittest.TestCase):
    """The locked ReplayInput schema is enforced, not inferred."""

    def test_recorded_replay_input_parses_round_trip(self):
        from robot_factory_arena.evidence.replay import ReplayInput

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            mapping = _read(recorder.directory / "replay-input.json")
            parsed = ReplayInput.from_mapping(mapping)
            self.assertEqual(parsed.to_mapping(), mapping)
            self.assertEqual(parsed.logical_run_id,
                             recorder.receipt_candidate.logical_run_id)
            self.assertEqual(len(parsed.operations), 92)

    def test_replay_input_rejects_schema_drift(self):
        from robot_factory_arena.evidence.replay import (
            ReplayInput,
            ReplayInputError,
        )

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            base = _read(recorder.directory / "replay-input.json")

        def drifted(**changes):
            mapping = json.loads(json.dumps(base))
            mapping.update(changes)
            return mapping

        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(drifted(extra=True))
        with self.assertRaises(ReplayInputError):
            missing = json.loads(json.dumps(base))
            del missing["grants"]
            ReplayInput.from_mapping(missing)
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(drifted(schema_version=2))
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(drifted(logical_run_id="lr_short"))
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(drifted(budget=99))
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(drifted(grants=["place", "move"]))
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(drifted(
                task_identity={"task_version": "warehouse-v0", "seed": "17"}))
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(drifted(
                task_identity={"task_version": "warehouse-v1", "seed": "17",
                               "config_digest": base["task_identity"][
                                   "config_digest"]}))

    def test_replay_input_rejects_operation_drift(self):
        from robot_factory_arena.evidence.replay import (
            ReplayInput,
            ReplayInputError,
        )

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            base = _read(recorder.directory / "replay-input.json")
        operation = base["operations"][0]

        def with_ops(operations):
            mapping = json.loads(json.dumps(base))
            mapping["operations"] = operations
            return mapping

        broken = json.loads(json.dumps(operation))
        del broken["status"]
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        broken = json.loads(json.dumps(operation))
        broken["extra"] = True
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        broken = json.loads(json.dumps(operation))
        broken["status"] = "duplicate_effect"
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        broken = json.loads(json.dumps(operation))
        broken["policy_step"] = 0
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        broken = json.loads(json.dumps(operation))
        broken["retry"] = "yes"
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        # A committed op without state_after or events is inconsistent.
        broken = json.loads(json.dumps(operation))
        broken["state_after"] = None
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        broken = json.loads(json.dumps(operation))
        broken["events"] = []
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        # An op key embedding another logical run id is rejected.
        broken = json.loads(json.dumps(operation))
        broken["operation_key"] = broken["operation_key"].replace(
            base["logical_run_id"], "lr_" + "f" * 64
        )
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))
        # A rejected op carrying events/state_after is inconsistent.
        rejected = dict(operation)
        rejected.update(status="rejected", reason="not_at_base",
                        state_after=None, events=[])
        ok = ReplayInput.from_mapping(with_ops([rejected]))
        self.assertEqual(ok.operations[0]["status"], "rejected")
        broken = json.loads(json.dumps(rejected))
        broken["events"] = operation["events"]
        with self.assertRaises(ReplayInputError):
            ReplayInput.from_mapping(with_ops([broken]))

    def test_replay_input_rejects_non_json_values(self):
        from robot_factory_arena.evidence.replay import ReplayInputError

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            base = _read(recorder.directory / "replay-input.json")
        base["operations"][0]["arguments"] = {"order_id": 1.5}
        with self.assertRaises(ReplayInputError):
            # Floats are non-canonical (REQ-07) and fail the JSON gate.
            from robot_factory_arena.evidence.replay import ReplayInput
            ReplayInput.from_mapping(base)


class TestPolicyFreeReplay(unittest.TestCase):
    """Replay reproduces the recorded run without ever asking a policy."""

    def test_replay_never_calls_a_policy_and_reproduces_the_digest(self):
        from robot_factory_arena.evidence.replay import replay

        with tempfile.TemporaryDirectory() as root:
            result, recorder = _recorded_run(root)
            replay_input = _read(recorder.directory / "replay-input.json")
            policy = _RaisingPolicy()
            replayed = replay(replay_input, policy=policy)
            self.assertEqual(
                replayed.final_state_digest,
                recorder.receipt_candidate.final_state_digest,
            )
            self.assertEqual(replayed.replay_outcome, "completed")
            self.assertEqual(replayed.replayed_operations, 92)

    def test_replay_agrees_with_a_verified_receipt(self):
        from robot_factory_arena.evidence.replay import replay

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            replay_input = _read(recorder.directory / "replay-input.json")
            candidate = recorder.receipt_candidate
            receipt = ReceiptWriter.write_final(
                candidate,
                VerificationResult(
                    valid=True, outcome="completed", verified_score=100,
                    completed_count=1, total_count=1, violations=(),
                    verifier_version="stub-verifier-v0",
                ),
            )
            replayed = replay(replay_input, receipt=receipt)
            self.assertTrue(replayed.matches_receipt)
            self.assertTrue(replayed.digest_matches)
            self.assertTrue(replayed.outcome_matches)
            self.assertEqual(replayed.receipt_verified_score, 100)
            self.assertEqual(replayed.receipt_outcome, "completed")

    def test_replay_reproduces_the_faulted_final_state(self):
        # Done-check: replay reproduces the B3 faulted run's final state.
        from robot_factory_arena.evidence.replay import replay
        from robot_factory_arena.faults.model import (
            FAULT_ID_RESPONSE_LOST,
            HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST,
        )
        from robot_factory_arena.faults.tape import FaultTapeSpec, FaultTarget

        task = _completed_task()
        spec = FaultTapeSpec(
            1, FAULT_ID_RESPONSE_LOST, HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST, FaultTarget(8, "place-order-00"),
        )
        config = RunConfig(
            1, task.task_version, task.seed, "greedy-v0",
            policy_manifest_digest("greedy-v0"), 100,
            ("move", "pick", "place"), spec,
        )
        logical_run_id = compute_logical_run_id(task, config)
        with tempfile.TemporaryDirectory() as root:
            recorder = RunEvidenceRecorder(root, task, config, logical_run_id,
                                           "execution-faulted")
            evidence = EvidenceCallbacks(**recorder.callback_map())
            result = Runner(evidence=evidence).run(
                task, GreedyPolicy(), config, "execution-faulted"
            )
            recorder.close()
            self.assertEqual(result.outcome, "completed")
            replay_input = _read(recorder.directory / "replay-input.json")
            replayed = replay(replay_input, policy=_RaisingPolicy())
            self.assertEqual(
                replayed.final_state_digest,
                recorder.receipt_candidate.final_state_digest,
            )
            self.assertEqual(replayed.replay_outcome, "completed")
            # The retry (idempotent_replay, retry=true) consumed nothing:
            # 92 submissions plus the one internal retry, no second effect.
            self.assertEqual(replayed.replayed_operations, 93)

    def test_replay_binds_seed_and_config_identity(self):
        # Tampering with the recorded seed must break the first recorded
        # state_before: replay regenerates the task from identity only.
        from robot_factory_arena.evidence.replay import (
            ReplayInconsistentError,
            replay,
        )

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            replay_input = _read(recorder.directory / "replay-input.json")
        replay_input["task_identity"]["seed"] = "29"
        with self.assertRaises(ReplayInconsistentError):
            replay(replay_input, policy=_RaisingPolicy())

    def test_replay_detects_tampered_event_evidence(self):
        from robot_factory_arena.evidence.replay import (
            ReplayInconsistentError,
            replay,
        )

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            replay_input = _read(recorder.directory / "replay-input.json")
        replay_input["operations"][0]["events"][0]["data"]["order_id"] = \
            "order-01"
        with self.assertRaises(ReplayInconsistentError):
            replay(replay_input, policy=_RaisingPolicy())

    def test_replay_reports_digest_mismatch_without_raising(self):
        # Dropping the committed op changes the final state: the mismatch is
        # REPORTED (matches_receipt False), never upgraded to success.
        from robot_factory_arena.evidence.replay import replay

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            replay_input = _read(recorder.directory / "replay-input.json")
        receipt = {
            "logical_run_id": replay_input["logical_run_id"],
            "final_state_digest": recorder.receipt_candidate
            .final_state_digest,
            "verified_score": 100,
            "outcome": "completed",
        }
        replay_input["operations"] = []
        replayed = replay(replay_input, receipt=receipt)
        self.assertFalse(replayed.matches_receipt)
        self.assertFalse(replayed.digest_matches)
        # Zero recorded operations on a five-order task is NOT completed.
        self.assertEqual(replayed.replay_outcome, "unresolved_outcome")

    def test_replay_classifies_safe_abort_from_recorded_steps(self):
        # The full greedy run needs ~190 steps: the golden budget path ends
        # safe_abort with recorded steps exactly reaching the budget (100).
        from robot_factory_arena.domain.task import generate_task
        from robot_factory_arena.evidence.replay import replay

        task = generate_task(17)
        config = _greedy_config(task)
        logical_run_id = compute_logical_run_id(task, config)
        with tempfile.TemporaryDirectory() as root:
            recorder = RunEvidenceRecorder(root, task, config, logical_run_id,
                                           "execution-full")
            evidence = EvidenceCallbacks(**recorder.callback_map())
            result = Runner(evidence=evidence).run(
                task, GreedyPolicy(), config, "execution-full"
            )
            recorder.close()
            self.assertEqual(result.outcome, "safe_abort")
            replay_input = _read(recorder.directory / "replay-input.json")
            replayed = replay(replay_input, policy=_RaisingPolicy())
            self.assertEqual(replayed.replay_outcome, "safe_abort")
            self.assertEqual(
                replayed.final_state_digest,
                recorder.receipt_candidate.final_state_digest,
            )

    def test_replay_result_serializes_canonically_and_writes_to_disk(self):
        from robot_factory_arena.evidence.artifact_store import (
            ArtifactStore,
        )
        from robot_factory_arena.evidence.replay import replay

        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            replay_input = _read(recorder.directory / "replay-input.json")
            replayed = replay(replay_input, policy=_RaisingPolicy())
            raw = canonical_json_bytes(replayed.to_mapping())
            # The result is canonical: byte-identical through parse/serialize.
            self.assertEqual(raw, canonical_json_bytes(json.loads(raw.decode("utf-8"))))
            writer = ArtifactStore(root).begin(
                recorder.receipt_candidate.logical_run_id, "execution-writer"
            )
            writer.write_json("replay-result.json", replayed.to_mapping())
            written = (writer.directory / "replay-result.json").read_bytes()
            self.assertEqual(written, raw)
