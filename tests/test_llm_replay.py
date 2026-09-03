"""Phase 4A offline-replay tests (T4A-09, P4A-REQ-06).

The documented replay command reproduces final-state digest, verifier-owned
score, and outcome for an LLM run with NO provider credentials in the
environment, while the providers package remains importable; an import
tripwire proves replay never imports ``LLMPolicy`` or ``providers``; deleting
``llm/`` leaves core replay/verifier/report evidence untouched; and replay
never modifies the sidecars.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.policies.llm import llm_vnext_policy_digest
from robot_factory_arena.providers.adapter import (
    ProviderResult,
    ToolCallPayload,
    Usage,
)
from robot_factory_arena.runtime.runner import (
    EvidenceCallbacks,
    RunConfig,
    Runner,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GRANTS = ("move", "pick", "place")
_SCRUB_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _scrubbed_environment():
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _SCRUB_MARKERS)
    }


def _completion_script(seed="18"):
    """The deterministic greedy completion plan as fake ProviderResults."""
    from robot_factory_arena.policies.greedy import GreedyPolicy

    task = generate_task(seed)
    config = RunConfig(
        1, task.task_version, task.seed, "llm-v1",
        llm_vnext_policy_digest("fake", "fake-v1"), 100, GRANTS, None,
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


class TestLLMRunReplaysOffline(unittest.TestCase):
    """P4A-REQ-06: replay without an API, without policy, without providers."""

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        script = _completion_script()
        script_path = cls.root / "llm-script.json"
        script_path.write_text(
            json.dumps([result.to_mapping() for result in script]),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable, "-m", "robot_factory_arena.cli", "run",
                "--seed", "18", "--provider", "fake", "--model", "fake-v1",
                "--script", str(script_path),
                "--artifacts-root", str(cls.root / "artifacts"),
                "--execution-id", "exec-replay",
            ],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=240,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        cls.run_dir = next(
            (cls.root / "artifacts" / "runs").glob("lr_*/exec-replay")
        )

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def _replay(self, directory):
        return subprocess.run(
            [
                sys.executable, "-m", "robot_factory_arena.cli", "replay",
                str(directory),
            ],
            cwd=REPO_ROOT,
            env=_scrubbed_environment(),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )

    def test_documented_replay_matches_with_no_provider_credentials(self):
        completed = self._replay(self.run_dir)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("receipt_agreement: match", completed.stdout)
        self.assertIn("digest_match: true", completed.stdout)
        self.assertIn("outcome_match: true", completed.stdout)
        self.assertNotIn("API_KEY", json.dumps(_scrubbed_environment()))

    def test_replay_process_never_imports_policy_or_providers(self):
        probe = (
            "import sys;"
            "from robot_factory_arena import cli as arena_cli;"
            "code = arena_cli.main(['replay', sys.argv[1]]);"
            "forbidden = sorted("
            "m for m in sys.modules"
            " if m.startswith(('robot_factory_arena.policies',"
            "'robot_factory_arena.providers')));"
            "assert not forbidden, forbidden;"
            "print('NO_POLICY_OR_PROVIDER_IMPORT_OK');"
            "raise SystemExit(code)"
        )
        replayed = subprocess.run(
            [sys.executable, "-c", probe, str(self.run_dir)],
            cwd=REPO_ROOT,
            env=_scrubbed_environment(),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        self.assertIn("NO_POLICY_OR_PROVIDER_IMPORT_OK", replayed.stdout)

    def test_replay_never_modifies_the_sidecars(self):
        before = {
            name: (self.run_dir / "llm" / name).read_bytes()
            for name in ("provider-run.json", "llm-receipt.json",
                         "llm-report.html")
        }
        completed = self._replay(self.run_dir)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        after = {
            name: (self.run_dir / "llm" / name).read_bytes()
            for name in before
        }
        self.assertEqual(before, after)

    def test_deleting_llm_does_not_affect_core_replay_or_evidence(self):
        copy_root = self.root / "copy-without-llm"
        shutil.copytree(self.run_dir, copy_root)
        shutil.rmtree(copy_root / "llm")
        core_names = (
            "config.json", "task.json", "trace.jsonl", "final_state.json",
            "operation-log.json", "replay-input.json", "receipt.json",
            "report.html",
        )
        for name in core_names:
            self.assertEqual(
                (self.run_dir / name).read_bytes(),
                (copy_root / name).read_bytes(),
                name,
            )
        completed = self._replay(copy_root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("receipt_agreement: match", completed.stdout)
        self.assertIn("verified_score: 100", completed.stdout)
        self.assertIn("outcome: completed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
