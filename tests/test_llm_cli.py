"""Phase 4A CLI acceptance tests (T4A-09, P4A-REQ-05/06/08/09).

Fake-only CLI runs emit the complete core artifact set plus the three
``run_dir/llm`` sidecars (provider-run, llm-receipt, llm-report); LLM flags
are strictly validated; a residual secret fails the sidecar export closed
(exit 1, no ``llm/`` directory) without touching the released core receipt;
and rerun agreement works unchanged for LLM runs.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.policies.llm import (
    llm_vnext_policy_digest,
    strategy_context_digest,
)
from robot_factory_arena.providers.adapter import ProviderResult, ToolCallPayload, Usage
from robot_factory_arena.runtime.runner import (
    EvidenceCallbacks,
    RunConfig,
    Runner,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GRANTS = ("move", "pick", "place")


def _cli(*args, timeout=240):
    return subprocess.run(
        [sys.executable, "-m", "robot_factory_arena.cli", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _completion_script(seed="18"):
    """The deterministic greedy completion plan as fake ProviderResults."""
    from robot_factory_arena.policies.greedy import GreedyPolicy

    task = generate_task(seed)
    config = RunConfig(
        1,
        task.task_version,
        task.seed,
        "llm-v1",
        llm_vnext_policy_digest("fake", "fake-v1"),
        100,
        GRANTS,
        None,
    )
    observations = {}

    def on_observation(step, serialized):
        observations[step] = serialized

    result = Runner(evidence=EvidenceCallbacks(on_observation=on_observation)).run(
        task, GreedyPolicy(), config, "exec-plan"
    )
    assert result.outcome == "completed", result.outcome
    script = []
    for step in sorted(observations):
        call = GreedyPolicy().decide(observations[step])
        script.append(
            ProviderResult(
                provider="fake",
                model="fake-v1",
                request_id=f"request-{step}",
                usage=Usage(4, 6, 10),
                latency_ms=7,
                raw_visible_response="choosing",
                tool_call=ToolCallPayload(call.tool, dict(call.arguments)),
                error=None,
            )
        )
    return script


def _script_file(root: Path, script):
    path = root / "llm-script.json"
    path.write_text(
        json.dumps([result.to_mapping() for result in script]),
        encoding="utf-8",
    )
    return path


def _context_file(root: Path, name: str, text: str) -> Path:
    """A canonical-form §2.1 StrategyContext file with one guidance entry."""
    path = root / name
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_tournament_id": "tournament-test",
                "guidance": [
                    {
                        "guidance_id": "guidance-0001",
                        "author_id": "human",
                        "guidance_ordinal": 0,
                        "text": text,
                    }
                ],
                "discoveries": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _stdout_value(stdout: str, name: str) -> str:
    for line in stdout.splitlines():
        if line.startswith(f"{name}: "):
            return line.split(": ", 1)[1]
    raise AssertionError(f"{name} not printed")


class TestFakeCliRunEmitsSidecars(unittest.TestCase):
    def test_fake_run_emits_core_artifacts_and_all_three_sidecars(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, _completion_script())
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(script_path),
                "--artifacts-root",
                str(root_path / "artifacts"),
                "--execution-id",
                "exec-llm-cli",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("llm_report:", completed.stdout)
            run_dir = next((root_path / "artifacts" / "runs").glob("lr_*/exec-llm-cli"))
            for name in (
                "config.json",
                "task.json",
                "trace.jsonl",
                "final_state.json",
                "operation-log.json",
                "replay-input.json",
                "receipt.json",
                "replay-result.json",
                "report.html",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            for name in (
                "provider-run.json",
                "llm-receipt.json",
                "llm-report.html",
            ):
                self.assertTrue((run_dir / "llm" / name).is_file(), name)
            envelope = json.loads(
                (run_dir / "llm" / "llm-receipt.json").read_text("utf-8")
            )
            self.assertEqual(envelope["provider"], "fake")
            self.assertEqual(envelope["model"], "fake-v1")

    def test_rerun_with_fake_llm_reports_deterministic_agreement(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, _completion_script())
            base = [
                "rerun",
                "--seed",
                "18",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(script_path),
                "--artifacts-root",
                str(root_path / "artifacts"),
            ]
            first = _cli(*base, "--execution-id", "exec-llm-rerun-1")
            self.assertEqual(first.returncode, 0, first.stderr)
            second = _cli(*base, "--execution-id", "exec-llm-rerun-2")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("deterministic agreement: true", second.stdout)


class TestCliLlmArgumentValidation(unittest.TestCase):
    def test_incomplete_llm_flag_combinations_exit_2(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, _completion_script())
            cases = (
                ("--provider", "fake"),  # no model/script
                ("--model", "fake-v1"),  # no provider
                ("--script", str(script_path)),  # no provider
                (
                    "--provider",
                    "openai",
                    "--model",
                    "m",
                    "--script",
                    str(script_path),
                ),  # openai + script
            )
            for case in cases:
                with self.subTest(case=case):
                    completed = _cli(
                        "run", *case, "--artifacts-root", str(root_path / "a")
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("error:", completed.stderr)

    def test_missing_script_file_exits_2(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            completed = _cli(
                "run",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(root_path / "absent.json"),
                "--artifacts-root",
                str(root_path / "a"),
            )
            self.assertEqual(completed.returncode, 2)


class TestCliFailClosedSecretExport(unittest.TestCase):
    def test_secret_bearing_script_exits_1_without_an_llm_directory(self):
        script = _completion_script()
        poisoned = script[0].to_mapping()
        poisoned["raw_visible_response"] = "export APP_AUTH=zzz-abc123"
        script[0] = ProviderResult(
            provider=poisoned["provider"],
            model=poisoned["model"],
            request_id=poisoned["request_id"],
            usage=Usage(
                poisoned["usage"]["input_tokens"],
                poisoned["usage"]["output_tokens"],
                poisoned["usage"]["total_tokens"],
            ),
            latency_ms=poisoned["latency_ms"],
            raw_visible_response=poisoned["raw_visible_response"],
            tool_call=ToolCallPayload(
                poisoned["tool_call"]["tool"], poisoned["tool_call"]["arguments"]
            ),
            error=None,
        )
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, script)
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(script_path),
                "--artifacts-root",
                str(root_path / "artifacts"),
                "--execution-id",
                "exec-secret",
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("secret_detected", completed.stderr)
            run_dir = next((root_path / "artifacts" / "runs").glob("lr_*/exec-secret"))
            self.assertFalse((run_dir / "llm").exists())
            # The core receipt stands released and unchanged (REQ-07).
            self.assertTrue((run_dir / "receipt.json").is_file())
            self.assertNotIn(
                "abc123",
                "\n".join(
                    path.read_text("utf-8", "replace")
                    for path in run_dir.rglob("*")
                    if path.is_file()
                ),
            )


class TestStrategyContextTransport(unittest.TestCase):
    """AMEND-4A-3 (design §2.2/§3.3): the additive --strategy-context transport.

    The frozen canonical context file is loaded and strictly validated BEFORE
    LLMPolicy construction (file/JSON/schema errors exit 2); its digest enters
    the vNext policy identity and therefore the logical run id; the context
    bytes never reach any serialized artifact; replay never reads it.
    """

    MARKER = "advisory-guidance-marker-context-text"

    def _run_with_context(self, root_path, context_path, execution_id):
        script_path = _script_file(root_path, _completion_script())
        return _cli(
            "run",
            "--seed",
            "18",
            "--provider",
            "fake",
            "--model",
            "fake-v1",
            "--script",
            str(script_path),
            "--strategy-context",
            str(context_path),
            "--artifacts-root",
            str(root_path / "artifacts"),
            "--execution-id",
            execution_id,
        )

    def test_valid_context_run_reports_the_expected_context_identity(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            context_path = _context_file(root_path, "ctx.json", self.MARKER)
            completed = self._run_with_context(root_path, context_path, "exec-ctx-1")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            expected = strategy_context_digest(
                json.loads(context_path.read_text("utf-8"))
            )
            self.assertIn(f"strategy_context_digest: {expected}", completed.stdout)

    def test_changed_context_changes_the_logical_run_id(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            first = self._run_with_context(
                root_path,
                _context_file(root_path, "a.json", "first text"),
                "exec-ctx-a",
            )
            second = self._run_with_context(
                root_path,
                _context_file(root_path, "b.json", "second text"),
                "exec-ctx-b",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertNotEqual(
                _stdout_value(first.stdout, "logical_run_id"),
                _stdout_value(second.stdout, "logical_run_id"),
            )

    def test_rerun_with_the_same_context_agrees_under_one_logical_id(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            context_path = _context_file(root_path, "ctx.json", self.MARKER)
            script_path = _script_file(root_path, _completion_script())
            base = [
                "rerun",
                "--seed",
                "18",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(script_path),
                "--strategy-context",
                str(context_path),
                "--artifacts-root",
                str(root_path / "artifacts"),
            ]
            first = _cli(*base, "--execution-id", "exec-ctx-r1")
            self.assertEqual(first.returncode, 0, first.stderr)
            second = _cli(*base, "--execution-id", "exec-ctx-r2")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("deterministic agreement: true", second.stdout)

    def test_no_context_default_keeps_the_vnext_null_identity(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, _completion_script())
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(script_path),
                "--artifacts-root",
                str(root_path / "artifacts"),
                "--execution-id",
                "exec-null-ctx",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("strategy_context_digest: null", completed.stdout)
            run_dir = next(
                (root_path / "artifacts" / "runs").glob("lr_*/exec-null-ctx")
            )
            config = json.loads((run_dir / "config.json").read_text("utf-8"))
            self.assertEqual(config["policy_id"], "llm-v1")
            self.assertEqual(
                config["policy_digest"],
                llm_vnext_policy_digest("fake", "fake-v1"),
            )

    def test_context_identity_enters_config_and_no_artifact_carries_context_text(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            context_path = _context_file(root_path, "ctx.json", self.MARKER)
            completed = self._run_with_context(root_path, context_path, "exec-ctx-art")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = next((root_path / "artifacts" / "runs").glob("lr_*/exec-ctx-art"))
            config = json.loads((run_dir / "config.json").read_text("utf-8"))
            context = json.loads(context_path.read_text("utf-8"))
            self.assertEqual(config["policy_id"], "llm-v1")
            self.assertEqual(
                config["policy_digest"],
                llm_vnext_policy_digest("fake", "fake-v1", context),
            )
            # No context byte reaches any serialized artifact (observation,
            # trace, provider envelope): the digested identity is all that travels.
            for path in run_dir.rglob("*"):
                if path.is_file():
                    self.assertNotIn(
                        self.MARKER,
                        path.read_text("utf-8", "replace"),
                        path.name,
                    )

    def test_missing_context_file_exits_2(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, _completion_script())
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(script_path),
                "--strategy-context",
                str(root_path / "absent.json"),
                "--artifacts-root",
                str(root_path / "artifacts"),
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("cannot read strategy context", completed.stderr)

    def test_non_canonical_context_file_exits_2(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            path = root_path / "invalid.json"
            path.write_text(
                json.dumps({"schema_version": 1, "extra": True}), encoding="utf-8"
            )
            script_path = _script_file(root_path, _completion_script())
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--provider",
                "fake",
                "--model",
                "fake-v1",
                "--script",
                str(script_path),
                "--strategy-context",
                str(path),
                "--artifacts-root",
                str(root_path / "artifacts"),
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("exactly the §2.1 keys", completed.stderr)

    def test_strategy_context_requires_provider(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            context_path = _context_file(root_path, "ctx.json", self.MARKER)
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--policy",
                "greedy-v0",
                "--strategy-context",
                str(context_path),
                "--artifacts-root",
                str(root_path / "artifacts"),
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("require --provider", completed.stderr)

    def test_replay_never_reads_the_context_file(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            context_path = _context_file(root_path, "ctx.json", self.MARKER)
            completed = self._run_with_context(
                root_path, context_path, "exec-ctx-replay"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = next(
                (root_path / "artifacts" / "runs").glob("lr_*/exec-ctx-replay")
            )
            context_path.unlink()
            replayed = _cli("replay", str(run_dir))
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn("receipt_agreement: match", replayed.stdout)
            # The option is policy-layer only: the replay surface never grew it.
            rejected = _cli("replay", str(run_dir), "--strategy-context", "x")
            self.assertEqual(rejected.returncode, 2)


if __name__ == "__main__":
    unittest.main()
