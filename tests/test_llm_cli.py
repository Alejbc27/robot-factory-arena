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
    llm_policy_digest,
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
        1, task.task_version, task.seed, "llm-v0",
        llm_policy_digest("fake", "fake-v1"), 100, GRANTS, None,
    )
    observations = {}

    def on_observation(step, serialized):
        observations[step] = serialized

    result = Runner(
        evidence=EvidenceCallbacks(on_observation=on_observation)
    ).run(task, GreedyPolicy(), config, "exec-plan")
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


class TestFakeCliRunEmitsSidecars(unittest.TestCase):
    def test_fake_run_emits_core_artifacts_and_all_three_sidecars(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, _completion_script())
            completed = _cli(
                "run", "--seed", "18", "--provider", "fake",
                "--model", "fake-v1", "--script", str(script_path),
                "--artifacts-root", str(root_path / "artifacts"),
                "--execution-id", "exec-llm-cli",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("llm_report:", completed.stdout)
            run_dir = next(
                (root_path / "artifacts" / "runs").glob("lr_*/exec-llm-cli")
            )
            for name in (
                "config.json", "task.json", "trace.jsonl", "final_state.json",
                "operation-log.json", "replay-input.json", "receipt.json",
                "replay-result.json", "report.html",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            for name in (
                "provider-run.json", "llm-receipt.json", "llm-report.html",
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
                "rerun", "--seed", "18", "--provider", "fake",
                "--model", "fake-v1", "--script", str(script_path),
                "--artifacts-root", str(root_path / "artifacts"),
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
                ("--provider", "fake"),                      # no model/script
                ("--model", "fake-v1"),                      # no provider
                ("--script", str(script_path)),              # no provider
                ("--provider", "openai", "--model", "m",
                 "--script", str(script_path)),              # openai + script
            )
            for case in cases:
                with self.subTest(case=case):
                    completed = _cli("run", *case, "--artifacts-root",
                                     str(root_path / "a"))
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("error:", completed.stderr)

    def test_missing_script_file_exits_2(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            completed = _cli(
                "run", "--provider", "fake", "--model", "fake-v1",
                "--script", str(root_path / "absent.json"),
                "--artifacts-root", str(root_path / "a"),
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
            usage=Usage(poisoned["usage"]["input_tokens"],
                        poisoned["usage"]["output_tokens"],
                        poisoned["usage"]["total_tokens"]),
            latency_ms=poisoned["latency_ms"],
            raw_visible_response=poisoned["raw_visible_response"],
            tool_call=ToolCallPayload(poisoned["tool_call"]["tool"],
                                      poisoned["tool_call"]["arguments"]),
            error=None,
        )
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, script)
            completed = _cli(
                "run", "--seed", "18", "--provider", "fake",
                "--model", "fake-v1", "--script", str(script_path),
                "--artifacts-root", str(root_path / "artifacts"),
                "--execution-id", "exec-secret",
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("secret_detected", completed.stderr)
            run_dir = next(
                (root_path / "artifacts" / "runs").glob("lr_*/exec-secret")
            )
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


if __name__ == "__main__":
    unittest.main()
