"""Release-gate tests for the optional, non-authoritative visualizer."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from robot_factory_arena.contracts.outcomes import VerificationResult
from robot_factory_arena.evidence.visualizer_projection import VisualizerLogicalMetadata


class VisualizerReleaseTests(unittest.TestCase):
    def _release(self, directory: Path, *, valid=True, replay_matches=True):
        from robot_factory_arena.evidence.visualizer_release import VerifiedRelease

        (directory / "receipt.json").write_bytes(b"{}")
        report = b"<html>verified report</html>"
        (directory / "report.html").write_bytes(report)
        return VerifiedRelease(
            directory=directory,
            artifacts=SimpleNamespace(receipt={"logical_run_id": "lr_" + "a" * 64}),
            receipt={"logical_run_id": "lr_" + "a" * 64},
            verification=VerificationResult(
                valid=valid,
                outcome="completed",
                verified_score=100,
                completed_count=1,
                total_count=1,
                violations=(),
                verifier_version="v1",
            ),
            replay_result=SimpleNamespace(matches_receipt=replay_matches),
            report_path=directory / "report.html",
            report_bytes=report,
            logical_metadata=VisualizerLogicalMetadata(None, None, None, None, None),
        )

    def test_requires_verified_replay_bound_receipt_backed_report(self):
        from robot_factory_arena.evidence.visualizer_release import (
            VisualizerGenerationError,
        )

        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            with self.assertRaises(VisualizerGenerationError):
                self._release(directory, valid=False)
            with self.assertRaises(VisualizerGenerationError):
                self._release(directory, replay_matches=False)

    def test_writes_only_after_gate_and_cleans_failed_output(self):
        from robot_factory_arena.evidence.visualizer_release import (
            VisualizerGenerationError,
            release_visualizer,
        )

        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            release = self._release(directory)
            with (
                patch(
                    "robot_factory_arena.evidence.visualizer_release.build_projection",
                    return_value=object(),
                ),
                patch(
                    "robot_factory_arena.evidence.visualizer_release.render_visualizer",
                    return_value=b"offline bytes",
                ),
            ):
                self.assertEqual(
                    release_visualizer(release), directory / "visualizer.html"
                )
            self.assertEqual(
                (directory / "visualizer.html").read_bytes(), b"offline bytes"
            )
            (directory / "visualizer.html").write_bytes(b"stale")
            (directory / "visualizer.html.tmp").write_bytes(b"partial")
            with (
                patch(
                    "robot_factory_arena.evidence.visualizer_release.build_projection",
                    side_effect=RuntimeError("renderer input failed"),
                ),
                self.assertRaises(VisualizerGenerationError) as raised,
            ):
                release_visualizer(release)
            self.assertIn(
                "non-authoritative visualizer generation failed", str(raised.exception)
            )
            self.assertFalse((directory / "visualizer.html").exists())
            self.assertFalse((directory / "visualizer.html.tmp").exists())


class VisualizerReleaseAcceptanceTests(unittest.TestCase):
    """Verify-report 7011 acceptance coverage for the verified release gate."""

    CORE_FILES = (
        "receipt.json",
        "report.html",
        "replay-result.json",
        "boundaries/step-1.json",
    )

    def _release(
        self, directory: Path, *, valid=True, replay_matches=True, outcome="completed"
    ):
        from robot_factory_arena.evidence.visualizer_release import VerifiedRelease

        report = b"<html>verified report</html>"
        (directory / "report.html").write_bytes(report)
        (directory / "receipt.json").write_bytes(b"{}")
        (directory / "replay-result.json").write_bytes(b'{"matches_receipt": true}')
        boundaries = directory / "boundaries"
        boundaries.mkdir(exist_ok=True)
        (boundaries / "step-1.json").write_bytes(b'{"step": 1}')
        return VerifiedRelease(
            directory=directory,
            artifacts=SimpleNamespace(receipt={"logical_run_id": "lr_" + "a" * 64}),
            receipt={"logical_run_id": "lr_" + "a" * 64},
            verification=VerificationResult(
                valid=valid,
                outcome=outcome,
                verified_score=100,
                completed_count=1,
                total_count=1,
                violations=(),
                verifier_version="v1",
            ),
            replay_result=SimpleNamespace(matches_receipt=replay_matches),
            report_path=directory / "report.html",
            report_bytes=report,
            logical_metadata=VisualizerLogicalMetadata(None, None, None, None, None),
        )

    def _core_bytes(self, directory: Path) -> dict:
        return {name: (directory / name).read_bytes() for name in self.CORE_FILES}

    def test_generation_failure_preserves_every_core_byte(self):
        from robot_factory_arena.evidence.visualizer_release import (
            VisualizerGenerationError,
            release_visualizer,
        )

        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            release = self._release(directory)
            before = self._core_bytes(directory)
            with (
                patch(
                    "robot_factory_arena.evidence.visualizer_release.build_projection",
                    side_effect=RuntimeError("projection source gap"),
                ),
                self.assertRaises(VisualizerGenerationError),
            ):
                release_visualizer(release)
            self.assertEqual(self._core_bytes(directory), before)
            self.assertFalse((directory / "visualizer.html").exists())
            self.assertFalse((directory / "visualizer.html.tmp").exists())

    def test_invalid_unresolved_and_replay_mismatch_have_no_official_file(self):
        from robot_factory_arena.evidence.visualizer_release import (
            VisualizerGenerationError,
        )

        gates = (
            lambda directory: self._release(directory, valid=False),
            lambda directory: self._release(
                directory, valid=False, outcome="unresolved_outcome"
            ),
            lambda directory: self._release(directory, replay_matches=False),
        )
        for index, gate in enumerate(gates):
            with self.subTest(gate=index), tempfile.TemporaryDirectory() as root:
                directory = Path(root)
                with self.assertRaises(VisualizerGenerationError):
                    gate(directory)
                self.assertFalse((directory / "visualizer.html").exists())
                self.assertFalse((directory / "visualizer.html.tmp").exists())

    def test_official_bytes_are_deterministic_and_recreated_after_deletion(self):
        from robot_factory_arena.evidence.visualizer_projection import (
            validate_projection,
        )
        from robot_factory_arena.evidence.visualizer_release import release_visualizer
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer
        from tests.test_visualizer_projection import projection

        expected = render_visualizer(validate_projection(projection()))
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            release = self._release(directory)
            before = self._core_bytes(directory)
            with patch(
                "robot_factory_arena.evidence.visualizer_release.build_projection",
                return_value=validate_projection(projection()),
            ):
                first = release_visualizer(release)
                self.assertEqual(first.read_bytes(), expected)
                first.unlink()
                second = release_visualizer(release)
            self.assertEqual(second.read_bytes(), expected)
            self.assertEqual(self._core_bytes(directory), before)


if __name__ == "__main__":
    unittest.main()
