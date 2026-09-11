"""CLI integration tests for the additive visualizer release."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "robot_factory_arena.cli", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _projection_from_html(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    match = re.search(r'visualizer-projection-hex"[^>]*>([0-9a-f]+)<', html)
    if match is None:
        raise AssertionError("visualizer payload is absent")
    return json.loads(bytes.fromhex(match.group(1)).decode("utf-8"))


class VisualizerCliTests(unittest.TestCase):
    def test_run_rerun_release_and_compare_the_optional_sibling(self):
        with tempfile.TemporaryDirectory() as root:
            first = _cli(
                "run",
                "--seed",
                "18",
                "--artifacts-root",
                root,
                "--execution-id",
                "viz-a",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            second = _cli(
                "rerun",
                "--seed",
                "18",
                "--artifacts-root",
                root,
                "--execution-id",
                "viz-b",
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("visualizer:", first.stdout)
            self.assertIn("visualizer agreement: agree", second.stdout)
            run_dirs = sorted((Path(root) / "runs").glob("lr_*/viz-*"))
            self.assertEqual(len(run_dirs), 2)
            self.assertEqual(
                (run_dirs[0] / "visualizer.html").read_bytes(),
                (run_dirs[1] / "visualizer.html").read_bytes(),
            )

    def test_reference_projection_carries_explicit_null_launch_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--artifacts-root",
                root,
                "--execution-id",
                "viz-null",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            directory = next((Path(root) / "runs").glob("lr_*/viz-null"))
            logical = _projection_from_html(directory / "visualizer.html")[
                "logical_run"
            ]
            self.assertEqual(
                {
                    name: logical[name]
                    for name in (
                        "provider",
                        "model",
                        "strategy_context_digest",
                        "tool_schema_digest",
                        "prompt_digest",
                    )
                },
                {
                    "provider": None,
                    "model": None,
                    "strategy_context_digest": None,
                    "tool_schema_digest": None,
                    "prompt_digest": None,
                },
            )

    def test_replay_is_unchanged_when_optional_visualizer_is_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            completed = _cli(
                "run",
                "--seed",
                "18",
                "--artifacts-root",
                root,
                "--execution-id",
                "viz-delete",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            original = next((Path(root) / "runs").glob("lr_*/viz-delete"))
            with tempfile.TemporaryDirectory() as copy_root:
                copied = Path(copy_root) / "copy"
                shutil.copytree(original, copied)
                (copied / "visualizer.html").unlink()
                replayed = _cli("replay", str(copied))
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn("receipt_agreement: match", replayed.stdout)


class VisualizerCliAcceptanceTests(unittest.TestCase):
    """Verify-report 7011 acceptance coverage for run/rerun visualizer status."""

    def _run(self, root, execution_id, extra=()):
        completed = _cli(
            "run",
            "--seed",
            "18",
            "--artifacts-root",
            root,
            "--execution-id",
            execution_id,
            *extra,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def _rerun(self, root, execution_id):
        return _cli(
            "rerun",
            "--seed",
            "18",
            "--artifacts-root",
            root,
            "--execution-id",
            execution_id,
        )

    def test_unavailable_sibling_is_reported_separately_and_never_fails_core(self):
        with tempfile.TemporaryDirectory() as root:
            self._run(root, "viz-gone")
            missing = next((Path(root) / "runs").glob("lr_*/viz-gone"))
            (missing / "visualizer.html").unlink()
            rerun = self._rerun(root, "viz-back")
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertIn("visualizer agreement vs viz-gone: unavailable", rerun.stdout)
            # An unavailable optional file is never a plain core-style "agree".
            self.assertIn("visualizer agreement: unavailable", rerun.stdout)
            self.assertNotIn("visualizer agreement: agree", rerun.stdout)
            self.assertIn("deterministic agreement: true", rerun.stdout)

    def test_visualizer_only_byte_difference_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as root:
            self._run(root, "viz-diff-a")
            rerun_b = self._rerun(root, "viz-diff-b")
            self.assertEqual(rerun_b.returncode, 0, rerun_b.stderr)
            self.assertIn("visualizer agreement: agree", rerun_b.stdout)
            mutated = next((Path(root) / "runs").glob("lr_*/viz-diff-b"))
            receipt_before = (mutated / "receipt.json").read_bytes()
            with (mutated / "visualizer.html").open("ab") as handle:
                handle.write(b"<!-- physical-only tail -->")
            rerun_c = self._rerun(root, "viz-diff-c")
            self.assertEqual(rerun_c.returncode, 1)
            self.assertIn("visualizer agreement vs viz-diff-b: differ", rerun_c.stdout)
            self.assertIn(
                "visualizer agreement: differ (non-authoritative)", rerun_c.stdout
            )
            self.assertIn("deterministic agreement: true", rerun_c.stdout)
            self.assertEqual((mutated / "receipt.json").read_bytes(), receipt_before)

    def test_deletion_leaves_core_bytes_and_replay_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            self._run(root, "viz-delete-full")
            original = next((Path(root) / "runs").glob("lr_*/viz-delete-full"))
            core_names = (
                "receipt.json",
                "report.html",
                "replay-result.json",
                "replay-input.json",
                "trace.jsonl",
                "operation-log.json",
                "final_state.json",
            )
            with tempfile.TemporaryDirectory() as copy_root:
                copied = Path(copy_root) / "copy"
                shutil.copytree(original, copied)
                (copied / "visualizer.html").unlink()
                for name in core_names:
                    self.assertEqual(
                        (copied / name).read_bytes(), (original / name).read_bytes()
                    )
                replayed = _cli("replay", str(copied))
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn("receipt_agreement: match", replayed.stdout)

    def test_llm_projection_carries_pinned_metadata_without_sidecar_leakage(self):
        from tests.test_llm_cli import _completion_script, _script_file

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            script_path = _script_file(root_path, _completion_script())
            self._run(
                str(root_path / "artifacts"),
                "viz-llm",
                extra=(
                    "--provider",
                    "fake",
                    "--model",
                    "fake-v1",
                    "--script",
                    str(script_path),
                ),
            )
            directory = next((root_path / "artifacts" / "runs").glob("lr_*/viz-llm"))
            projection_mapping = _projection_from_html(directory / "visualizer.html")
            logical = projection_mapping["logical_run"]
            self.assertEqual(logical["provider"], "fake")
            self.assertEqual(logical["model"], "fake-v1")
            # No --strategy-context launch: the context digest is explicit null
            # while the pinned policy identity digests are non-null.
            self.assertIsNone(logical["strategy_context_digest"])
            for name in ("tool_schema_digest", "prompt_digest"):
                self.assertRegex(logical[name], r"^sha256:[0-9a-f]{64}$")
            encoded = json.dumps(projection_mapping)
            for leaked in ("request-", "latency_ms", "raw_visible_response", "usage"):
                self.assertNotIn(leaked, encoded)

    def test_fail_open_generation_keeps_core_release_valid(self):
        import io
        from contextlib import redirect_stderr

        from robot_factory_arena import cli
        from robot_factory_arena.evidence.visualizer_release import (
            VisualizerGenerationError,
        )
        from robot_factory_arena.verification.verifier import (
            Verifier,
            load_verification_artifacts,
        )

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as artifacts_root:
            with (
                patch(
                    "robot_factory_arena.cli.release_visualizer",
                    side_effect=VisualizerGenerationError("injected downstream"),
                ),
                redirect_stderr(stderr),
            ):
                code = cli.main(
                    [
                        "run",
                        "--seed",
                        "18",
                        "--artifacts-root",
                        artifacts_root,
                        "--execution-id",
                        "viz-failopen",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn(
                "visualizer: unavailable (non-authoritative", stderr.getvalue()
            )
            directory = next((Path(artifacts_root) / "runs").glob("lr_*/viz-failopen"))
            self.assertFalse((directory / "visualizer.html").is_file())
            self.assertTrue(
                Verifier().verify(load_verification_artifacts(directory)).valid
            )


if __name__ == "__main__":
    unittest.main()
