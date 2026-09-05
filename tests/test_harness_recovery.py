"""Unit 4 recovery tests (design §8): restart fails closed on any break in
the append-only evidence chain.

Attempt-1 slice: append-only log continuity is verified at deps construction,
so a truncated or overwritten log fails closed at restart instead of silently
replaying fewer records. The extended R1 recovery matrix (repeated-restart
combinations, checkpoint boundary audit, timeout/exit inheritance for R1
pairs) is the explicit attempt-2 slice.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness import graph, logs, state, strategy_context, tournament
from tests.test_harness_r1 import FIXTURE_CONFIG, decline_terminal, drive, make_runner


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _completed_layout(self):
        deps = graph.build_deps(
            self.root,
            FIXTURE_CONFIG,
            runner=make_runner("complete"),
            approval=decline_terminal(),
        )
        drive(state.initial_state(), deps)
        return tournament.expected_layout(
            self.root, FIXTURE_CONFIG["tournament_id"], FIXTURE_CONFIG["artifact_root"]
        )

    def _restart_deps(self):
        return graph.build_deps(
            self.root,
            FIXTURE_CONFIG,
            runner=make_runner("never", tag="restart-"),
            approval=decline_terminal(),
        )


class TestRestartContinuity(Base):
    def test_clean_restart_rebuilds_deps_and_reads_checkpoints(self):
        layout = self._completed_layout()
        deps = self._restart_deps()
        self.assertIsNotNone(deps)
        latest = deps.checkpoints.latest()
        if not isinstance(latest, dict):  # fail closed: no readable checkpoint
            self.fail("restart found no readable latest checkpoint")
        # decline-all R1 pairs advance the queue into the R3 branch rounds,
        # whose final launch persists the post-binding checkpoint
        self.assertEqual(latest["boundary"], "post_binding")
        # every leaderboard row survives the restart untouched
        rows = json.loads(layout["leaderboard.json"].read_bytes())["rows"]
        self.assertEqual(len(rows), 9)

    def test_truncated_attempt_log_fails_closed_at_restart(self):
        layout = self._completed_layout()
        path = layout["attempt-log.jsonl"]
        lines = path.read_bytes().splitlines(keepends=True)
        # partial final line: neither valid JSON nor LF-terminated
        path.write_bytes(lines[0] + lines[1][: len(lines[1]) - 10])
        with self.assertRaises(logs.LogIntegrityError):
            self._restart_deps()

    def test_overwritten_attempt_log_line_fails_closed_at_restart(self):
        layout = self._completed_layout()
        path = layout["attempt-log.jsonl"]
        lines = path.read_bytes().splitlines(keepends=True)
        record = json.loads(lines[1])
        record["seed"] = "99"  # keep valid JSON and seq; break the digest chain
        lines[1] = strategy_context.canonical_json_bytes(record) + b"\n"
        path.write_bytes(b"".join(lines))
        with self.assertRaises(logs.LogIntegrityError):
            self._restart_deps()

    def test_overwritten_decision_log_line_fails_closed_at_restart(self):
        layout = self._completed_layout()
        path = layout["decision-log.jsonl"]
        lines = path.read_bytes().splitlines(keepends=True)
        record = json.loads(lines[0])
        record["tournament_id"] = "tournament-tampered"
        lines[0] = strategy_context.canonical_json_bytes(record) + b"\n"
        path.write_bytes(b"".join(lines))
        with self.assertRaises(logs.LogIntegrityError):
            self._restart_deps()


if __name__ == "__main__":
    unittest.main()
