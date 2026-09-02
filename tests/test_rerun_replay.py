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


# ---------------------------------------------------------------------------
# T-23: CLI run, rerun, and replay (REQ-07, REQ-09, REQ-10)
# ---------------------------------------------------------------------------

import subprocess  # noqa: E402
import sys  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTED_REPLAY_COMMAND = (
    "python3 -m robot_factory_arena.cli replay "
    "artifacts/runs/<logical_run_id>/<execution_id>"
)

# The declared REQ-09 artifact set of a completed B6 run: the seven pre-final
# artifacts written by the recorder plus receipt.json, replay-result.json, and
# report.html released by the CLI verified-run flow.
COMPLETE_RUN_ARTIFACTS = (
    "config.json", "task.json", "fault_tape.json", "trace.jsonl",
    "final_state.json", "operation-log.json", "replay-input.json",
    "receipt.json", "replay-result.json", "report.html",
)


def _cli(*args, timeout=180):
    """Run the documented CLI entry point with the gate interpreter."""
    return subprocess.run(
        [sys.executable, "-m", "robot_factory_arena.cli", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class TestCliCommands(unittest.TestCase):
    """T-23: run/rerun/replay are distinct operations with locked boundaries.

    run/rerun execute the policy again under a fresh execution id; replay
    reads ONLY the run directory's artifacts (replay-input.json + receipt)
    and never imports or calls a policy/provider.
    """

    def test_cli_run_produces_complete_artifacts_and_a_released_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            completed = _cli(
                "run", "--seed", "18", "--policy", "greedy-v0",
                "--artifacts-root", root, "--execution-id", "exec-cli-1",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            runs_root = Path(root) / "runs"
            run_dirs = list(runs_root.glob("lr_*" + "/exec-cli-1"))
            self.assertEqual(len(run_dirs), 1)
            directory = run_dirs[0]
            for name in COMPLETE_RUN_ARTIFACTS:
                self.assertTrue((directory / name).is_file(), name)
            receipt = _read(directory / "receipt.json")
            self.assertEqual(receipt["outcome"], "completed")
            self.assertEqual(receipt["verified_score"], 100)
            self.assertIn("logical_run_id:", completed.stdout)
            self.assertIn(receipt["logical_run_id"], completed.stdout)
            # The released receipt re-verifies (B6 composition order).
            from robot_factory_arena.verification.verifier import (
                Verifier,
                load_verification_artifacts,
            )

            recheck = Verifier().verify(load_verification_artifacts(directory))
            self.assertTrue(recheck.valid)
            self.assertEqual(recheck.outcome, "completed")

    def test_cli_run_writes_a_static_offline_report(self):
        with tempfile.TemporaryDirectory() as root:
            completed = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-report",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            html = next(
                Path(root).glob("runs/lr_*/exec-report/report.html")
            ).read_text(encoding="utf-8")
            for marker in (
                "signal-task-quality", "signal-efficiency",
                "signal-robustness", "signal-contract-hygiene",
                "not authentication", "exactly-once", "hostile-Python",
                "plumbing-only", "REFUGIO",
            ):
                self.assertIn(marker, html)
            lowered = html.lower()
            for forbidden in ("<script", "http://", "https://"):
                self.assertNotIn(forbidden, lowered)

    def test_cli_rerun_same_logical_id_distinct_execution_deterministic_bytes(
            self):
        deterministic = (
            "config.json", "task.json", "fault_tape.json", "trace.jsonl",
            "final_state.json", "operation-log.json", "replay-input.json",
            "replay-result.json", "report.html",
        )
        with tempfile.TemporaryDirectory() as root:
            first = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-rerun-a",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            second = _cli(
                "rerun", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-rerun-b",
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("deterministic agreement: true", second.stdout)
            runs = sorted((Path(root) / "runs").glob("lr_*"))
            self.assertEqual(len(runs), 1)  # SAME logical run id directory
            executions = sorted(path.name for path in runs[0].iterdir())
            self.assertEqual(
                executions, ["exec-rerun-a", "exec-rerun-b"]
            )  # DISTINCT physical executions
            dir_a = runs[0] / "exec-rerun-a"
            dir_b = runs[0] / "exec-rerun-b"
            for name in deterministic:
                self.assertEqual(
                    (dir_a / name).read_bytes(),
                    (dir_b / name).read_bytes(),
                    name,
                )
            receipt_a = _read(dir_a / "receipt.json")
            receipt_b = _read(dir_b / "receipt.json")
            self.assertNotEqual(
                receipt_a["execution_id"], receipt_b["execution_id"]
            )
            del receipt_a["execution_id"], receipt_b["execution_id"]
            self.assertEqual(receipt_a, receipt_b)

    def test_cli_replay_reports_receipt_agreement(self):
        with tempfile.TemporaryDirectory() as root:
            first = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-replay-me",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            directory = next((Path(root) / "runs").glob("lr_*"
                                                        "/exec-replay-me"))
            receipt = _read(directory / "receipt.json")
            replayed = _cli("replay", str(directory))
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn(receipt["final_state_digest"], replayed.stdout)
            self.assertIn("verified_score: 100", replayed.stdout)
            self.assertIn("outcome: completed", replayed.stdout)
            self.assertIn("receipt_agreement: match", replayed.stdout)

    def test_cli_replay_reads_only_run_artifacts(self):
        # Replay consumes replay-input.json + receipt.json ONLY: delete the
        # policy-adjacent and evidence artifacts from a COPY and replay still
        # reports exact receipt agreement.
        import shutil

        with tempfile.TemporaryDirectory() as root:
            first = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-stripped",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            original = next(
                (Path(root) / "runs").glob("lr_*/exec-stripped")
            )
            with tempfile.TemporaryDirectory() as copy_root:
                copy = Path(copy_root) / "run-copy"
                shutil.copytree(original, copy)
                for name in (
                    "config.json", "task.json", "trace.jsonl",
                    "final_state.json", "operation-log.json", "report.html",
                ):
                    (copy / name).unlink()
                shutil.rmtree(copy / "boundaries")
                replayed = _cli("replay", str(copy))
                self.assertEqual(replayed.returncode, 0, replayed.stderr)
                self.assertIn("receipt_agreement: match", replayed.stdout)

    def test_cli_replay_never_imports_policies_process_level(self):
        with tempfile.TemporaryDirectory() as root:
            first = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-nopolicy",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            directory = next(
                (Path(root) / "runs").glob("lr_*/exec-nopolicy")
            )
            probe = (
                "import sys;"
                "from robot_factory_arena import cli as arena_cli;"
                "code = arena_cli.main(['replay', sys.argv[1]]);"
                "assert 'robot_factory_arena.policies' not in sys.modules,"
                " 'POLICIES IMPORTED';"
                "print('NO_POLICY_IMPORT_OK');"
                "raise SystemExit(code)"
            )
            replayed = subprocess.run(
                [sys.executable, "-c", probe, str(directory)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
                check=False,
            )
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn("NO_POLICY_IMPORT_OK", replayed.stdout)

    def test_cli_replay_detects_a_tampered_receipt(self):
        import shutil

        from robot_factory_arena.evidence.canonical import canonical_json_bytes

        with tempfile.TemporaryDirectory() as root:
            first = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-tamper",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            original = next(
                (Path(root) / "runs").glob("lr_*/exec-tamper")
            )
            with tempfile.TemporaryDirectory() as copy_root:
                copy = Path(copy_root) / "run-copy"
                shutil.copytree(original, copy)
                receipt = _read(copy / "receipt.json")
                receipt["outcome"] = "safe_abort"  # claim drift
                (copy / "receipt.json").write_bytes(
                    canonical_json_bytes(receipt)
                )
                replayed = _cli("replay", str(copy))
                self.assertEqual(replayed.returncode, 1)
                self.assertIn("receipt_agreement: MISMATCH", replayed.stdout)
                self.assertIn("outcome_match: false", replayed.stdout)

    def test_cli_argument_validation(self):
        with tempfile.TemporaryDirectory() as root:
            bad_seed = _cli("run", "--seed", "abc",
                            "--artifacts-root", root)
            self.assertEqual(bad_seed.returncode, 2)
            bad_policy = _cli("run", "--seed", "18",
                              "--policy", "greedy-v9",
                              "--artifacts-root", root)
            self.assertEqual(bad_policy.returncode, 2)
            partial_fault = _cli("run", "--seed", "18",
                                 "--artifacts-root", root,
                                 "--fault-step", "8")
            self.assertEqual(partial_fault.returncode, 2)
            bad_fault_intent = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--fault-step", "8", "--fault-intent", "pick-shelf-00",
            )
            self.assertEqual(bad_fault_intent.returncode, 2)
            missing_replay_target = _cli(
                "replay", str(Path(root) / "runs" / "lr_missing" / "nope")
            )
            self.assertEqual(missing_replay_target.returncode, 2)
            # A reused execution id cannot silently overwrite evidence.
            first = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-dup",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            duplicate = _cli(
                "run", "--seed", "18", "--artifacts-root", root,
                "--execution-id", "exec-dup",
            )
            self.assertEqual(duplicate.returncode, 2)


class TestReadmeV0Boundary(unittest.TestCase):
    """T-22/T-25: README documents the v0 boundary and replay command exactly."""

    def test_readme_documents_the_exact_replay_command(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(DOCUMENTED_REPLAY_COMMAND, readme)

    def test_readme_states_the_v0_honesty_boundary(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for phrase in (
            "not authentication",
            "no exactly-once",
            "hostile-Python",
            "plumbing-only",
            "REFUGIO",
        ):
            self.assertIn(phrase, readme)

    def test_readme_makes_no_v0_forbidden_claims(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        lowered = readme.lower()
        # The v0 implementation section must not claim scientific results, a
        # leaderboard, security sandboxing, or exactly-once effects. (The
        # charter roadmap may NAME future phases; the claims must stay out of
        # the v0 boundary section.)
        v0_section = lowered.split("v0 implementation")[1]
        self.assertNotIn("scientific refugio reproduction", v0_section)
        self.assertNotIn("leaderboard result", v0_section)


class TestGoldenEvidenceReplay(unittest.TestCase):
    """T-24: the committed golden receipts replay offline without a policy.

    The replay input is regenerated by a fresh in-test execution (byte-
    identical across executions, REQ-07) and paired with the COMMITTED
    golden receipt fixtures: the reviewer's offline path.
    """

    def _regenerated_replay_input(self, root, seed, policy_id, execution_id,
                                  fault_step=None, fault_intent=None):
        from robot_factory_arena.contracts.policy import (
            policy_manifest_digest,
        )
        from robot_factory_arena.domain.task import generate_task
        from robot_factory_arena.evidence.artifact_store import (
            RunEvidenceRecorder,
        )
        from robot_factory_arena.faults.model import (
            FAULT_ID_RESPONSE_LOST,
            HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST,
        )
        from robot_factory_arena.faults.tape import (
            FaultTapeSpec,
            FaultTarget,
        )
        from robot_factory_arena.policies.bounded import BoundedPolicy
        from robot_factory_arena.policies.greedy import GreedyPolicy
        from robot_factory_arena.runtime.runner import (
            EvidenceCallbacks,
            RunConfig,
            Runner,
            compute_logical_run_id,
        )

        task = generate_task(seed)
        policy = GreedyPolicy() if policy_id == "greedy-v0" else BoundedPolicy()
        spec = None
        if fault_step is not None and fault_intent is not None:
            # Callers always declare the pair together (the CLI enforces it
            # too); the explicit narrowing keeps the frozen target typed str.
            spec = FaultTapeSpec(
                1, FAULT_ID_RESPONSE_LOST, HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
                KIND_RESPONSE_LOST, FaultTarget(fault_step, fault_intent),
            )
        config = RunConfig(
            1, task.task_version, task.seed, policy_id,
            policy_manifest_digest(policy_id), 100,
            ("move", "pick", "place"), spec,
        )
        recorder = RunEvidenceRecorder(
            root, task, config, compute_logical_run_id(task, config),
            execution_id,
        )
        evidence = EvidenceCallbacks(**recorder.callback_map())
        Runner(evidence=evidence).run(task, policy, config, execution_id)
        recorder.close()
        return _read(recorder.directory / "replay-input.json")

    def test_seed_17_golden_receipt_replays_without_policy(self):
        from robot_factory_arena.evidence.replay import replay

        fixture_receipt = _read(
            Path(__file__).resolve().parent / "fixtures"
            / "golden-receipt-17.json"
        )
        with tempfile.TemporaryDirectory() as root:
            replay_input = self._regenerated_replay_input(
                root, 17, "greedy-v0", "execution-offline-17"
            )
            replayed = replay(
                replay_input, receipt=fixture_receipt, policy=_RaisingPolicy()
            )
        self.assertTrue(replayed.matches_receipt)
        self.assertTrue(replayed.digest_matches)
        self.assertTrue(replayed.outcome_matches)
        self.assertEqual(replayed.replay_outcome, "safe_abort")
        self.assertEqual(replayed.receipt_verified_score, 50)

    def test_seed_18_golden_receipt_replays_without_policy(self):
        from robot_factory_arena.evidence.replay import replay

        fixture_receipt = _read(
            Path(__file__).resolve().parent / "fixtures"
            / "golden-receipt-18.json"
        )
        with tempfile.TemporaryDirectory() as root:
            replay_input = self._regenerated_replay_input(
                root, 18, "greedy-v0", "execution-offline-18",
                fault_step=8, fault_intent="place-order-00",
            )
            replayed = replay(
                replay_input, receipt=fixture_receipt, policy=_RaisingPolicy()
            )
        self.assertTrue(replayed.matches_receipt)
        self.assertEqual(replayed.replay_outcome, "completed")
        self.assertEqual(replayed.receipt_verified_score, 100)
        self.assertEqual(replayed.replayed_operations, 93)

    def test_seed_29_golden_receipt_replays_without_policy(self):
        from robot_factory_arena.evidence.replay import replay

        fixture_receipt = _read(
            Path(__file__).resolve().parent / "fixtures"
            / "golden-receipt-29.json"
        )
        with tempfile.TemporaryDirectory() as root:
            replay_input = self._regenerated_replay_input(
                root, 29, "bounded-v0", "execution-offline-29"
            )
            replayed = replay(
                replay_input, receipt=fixture_receipt, policy=_RaisingPolicy()
            )
        self.assertTrue(replayed.matches_receipt)
        self.assertEqual(replayed.replay_outcome, "safe_abort")
        self.assertEqual(replayed.receipt_verified_score, 60)
