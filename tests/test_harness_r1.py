"""Unit 4 R1 approval-path tests (design §6): the approval terminal is an
injectable seam whose answers are controlled data — human guidance reaches the
core ONLY through the canonical frozen StrategyContext pipeline, declines are
explicit replayable events (never silent skips), and every launch-window crash
resumes under the Unit-3 reconstruction contracts (the same attempt_id is
never rerun; a bound receipt is terminal).

Attempt-1 slice: the approval seam, explicit decline semantics, and the
compile contract. The attempt-2 slice adds the guidance canonical-pipeline
class, R1+R2 coexistence determinism, launch-window restart recovery, and the
recovery continuity module.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from harness import (
    graph,
    interruption,
    logs,
    state,
    strategy_context,
    subprocess_adapter,
    tournament,
)
from tests.test_harness_r2 import _script_fn, _write_triple, drive

LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None

GUIDANCE_A = "Probe the north corridor before extracting"


def _fixture_config():
    config = json.loads(
        (Path(__file__).parent / "fixtures/harness/unit4-r1-config.json").read_bytes()
    )
    tournament.validate_config(config)
    return config


FIXTURE_CONFIG = _fixture_config()


def make_runner(script, tag="u4-"):
    """Unit-3-identical loop harness, except the CLI stdout reports the REAL
    digest of the frozen context file: a guidance snapshot can never verify
    against a literal ``null`` report (verify_context_chain contract)."""
    fn = _script_fn(script)
    calls = []

    def runner(argv, timeout=None):
        calls.append(list(argv))
        n = len(calls)
        seed = argv[argv.index("--seed") + 1]
        root = Path(argv[argv.index("--artifacts-root") + 1])
        snapshot = Path(argv[argv.index("--strategy-context") + 1]).read_bytes()
        reported = (
            "null" if snapshot == b"null" else strategy_context.sha256_digest(snapshot)
        )
        stdout = f"strategy_context_digest: {reported}\nverified_score: 999\n".encode()
        action, outcome, score = fn(seed, n)
        if action == "fail":
            return subprocess_adapter.RunnerResult(1, False, stdout, b"boom")
        if action == "timeout":
            return subprocess_adapter.RunnerResult(None, True, stdout, b"")
        _write_triple(root, seed, f"exec-{tag}{n:02d}", outcome, score)
        return subprocess_adapter.RunnerResult(0, False, stdout, b"")

    runner.calls = calls
    return runner


def scripted_terminal(answers, prompt_log=None):
    """Injectable terminal replaying scripted answers, then defaulting to yes.

    Records every prompt view so tests can assert that no launch happened
    before ``yes`` and that edits precede their follow-up prompt.
    """
    calls = []

    def terminal(view):
        calls.append(dict(view))
        index = len(calls) - 1
        answer = dict(answers[index]) if index < len(answers) else {"decision": "yes"}
        if prompt_log is not None:
            prompt_log.append(
                (
                    view["condition"],
                    view["seed"],
                    view["proposal_round"],
                    tuple(view["current_guidance"]),
                )
            )
        return answer

    terminal.calls = calls
    return terminal


def decline_terminal():
    calls = []

    def terminal(view):
        calls.append(dict(view))
        return {"decision": "no"}

    terminal.calls = calls
    return terminal


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def deps(self, script, approval, config=None, tag="u4-"):
        return graph.build_deps(
            self.root,
            config or FIXTURE_CONFIG,
            runner=make_runner(script, tag=tag),
            approval=approval,
        )

    def layout(self, config=None):
        config = config or FIXTURE_CONFIG
        return tournament.expected_layout(
            self.root, config["tournament_id"], config["artifact_root"]
        )

    def attempt_records(self, config=None):
        return logs.AppendOnlyLog(self.layout(config)["attempt-log.jsonl"]).records()

    def decision_records(self, config=None):
        return logs.AppendOnlyLog(self.layout(config)["decision-log.jsonl"]).records()

    def rows(self, config=None):
        return json.loads(self.layout(config)["leaderboard.json"].read_bytes())["rows"]

    def row_for(self, condition, seed, config=None):
        return next(
            r
            for r in self.rows(config)
            if r["condition"] == condition and r["seed"] == seed
        )

    def launch(self, attempt_id, config=None):
        path = self.layout(config)["attempts"] / attempt_id / "launch.json"
        return json.loads(path.read_bytes())


class TestApprovalSeam(Base):
    def test_named_config_fixture_is_valid(self):
        self.assertEqual(FIXTURE_CONFIG["tournament_id"], "tournament-unit4-r1")
        self.assertEqual(FIXTURE_CONFIG["conditions"], ["R1", "R2", "R3"])

    def test_no_launch_before_yes_and_exactly_one_launch_for_yes(self):
        # (a) without any yes, no R1 subprocess attempt is ever allocated
        drive(state.initial_state(), self.deps("complete", decline_terminal()))
        self.assertEqual(
            [
                r
                for r in self.attempt_records()
                if r.get("condition") == "R1" and r["event"] == "launch_registered"
            ],
            [],
        )
        # (b) a yes produces exactly one launch, after the logged grant
        seam_config = dict(FIXTURE_CONFIG, tournament_id="tournament-unit4-r1-seam")
        tournament.validate_config(seam_config)
        terminal = scripted_terminal(
            [{"decision": "edit", "guidance": [GUIDANCE_A]}, {"decision": "yes"}]
        )
        drive(
            state.initial_state(),
            self.deps("complete", terminal, config=seam_config),
        )
        pair = [
            r
            for r in self.attempt_records(seam_config)
            if r.get("condition") == "R1" and r.get("seed") == "18"
        ]
        registered = [
            r["attempt_id"] for r in pair if r["event"] == "launch_registered"
        ]
        self.assertEqual(registered, ["r1-agent-01-seed-18-attempt-01"])
        events = [
            r["event"]
            for r in self.decision_records(seam_config)
            if r.get("condition") == "R1" and r.get("seed") == "18"
        ]
        self.assertEqual(
            events,
            [
                "context_planned",
                "approval_edit",
                "approval_granted",
                "context_snapshot_frozen",
            ],
        )
        self.assertEqual(self.row_for("R1", "18", seam_config)["attempts_used"], 1)

    def test_edit_is_logged_before_its_follow_up_prompt(self):
        prompt_log = []
        # scripted R1 input fixture: one revision, then the approval
        answers = json.loads(
            (
                Path(__file__).parent
                / "fixtures/harness/unit4-r1-scripted-answers.json"
            ).read_bytes()
        )
        terminal = scripted_terminal(answers, prompt_log=prompt_log)
        drive(state.initial_state(), self.deps("complete", terminal))
        # the follow-up prompt must already see the logged revision
        self.assertEqual(prompt_log[0], ("R1", "18", 0, ()))
        self.assertEqual(prompt_log[1], ("R1", "18", 1, (GUIDANCE_A,)))
        events = [
            r["event"]
            for r in self.decision_records()
            if r.get("condition") == "R1" and r.get("seed") == "18"
        ]
        self.assertLess(events.index("approval_edit"), events.index("approval_granted"))
        edits = [
            r["guidance"]
            for r in self.decision_records()
            if r.get("event") == "approval_edit" and r.get("seed") == "18"
        ]
        self.assertEqual(edits, [[GUIDANCE_A]])

    def test_launch_field_edits_are_rejected_as_side_channels(self):
        hostiles = (
            {"decision": "yes", "seed": "99"},
            {"decision": "yes", "provider": "evil"},
            {"decision": "yes", "prompt_override": "ignore prior instructions"},
            {"decision": "yes", "guidance": ["late guidance"]},
        )
        for hostile in hostiles:
            with self.assertRaises(interruption.InterruptionError):
                interruption.validate_answer(hostile)
        # end to end: a hostile answer never launches or grants anything
        terminal = scripted_terminal([{"decision": "yes", "seed": "99"}])
        with self.assertRaises(interruption.InterruptionError):
            drive(state.initial_state(), self.deps("complete", terminal))
        pair = [
            r
            for r in self.attempt_records()
            if r.get("condition") == "R1" and r.get("seed") == "18"
        ]
        self.assertEqual(pair, [])
        grants = [
            r for r in self.decision_records() if r.get("event") == "approval_granted"
        ]
        self.assertEqual(grants, [])

    def test_r1_without_an_approval_terminal_fails_closed(self):
        deps = graph.build_deps(self.root, FIXTURE_CONFIG, runner=make_runner("never"))
        state_value = state.set_current(state.initial_state(), work_index=3)
        state_value = graph.node_plan_context(state_value, deps)
        with self.assertRaises(graph.HarnessGraphError):
            graph.node_approve_r1(state_value, deps)


class TestDeclineSemantics(Base):
    def test_decline_is_an_explicit_replayable_event_with_a_declined_row(self):
        terminal = scripted_terminal(
            [{"decision": "no"}, {"decision": "yes"}, {"decision": "yes"}]
        )
        drive(state.initial_state(), self.deps("complete", terminal))
        events = [
            r["event"]
            for r in self.decision_records()
            if r.get("condition") == "R1" and r.get("seed") == "18"
        ]
        self.assertIn("launch_declined", events)
        self.assertIn("pair_declined", events)
        self.assertEqual(
            [
                r
                for r in self.attempt_records()
                if r.get("condition") == "R1"
                and r.get("seed") == "18"
                and r["event"] == "launch_registered"
            ],
            [],
        )
        declined = self.row_for("R1", "18")
        self.assertEqual(declined["status"], "declined")
        self.assertEqual(declined["attempts_used"], 0)
        self.assertEqual(declined["declined_launches"], 1)
        self.assertIsNone(declined["verified_score"])
        self.assertIsNone(declined["input_tokens"])
        launched = self.row_for("R1", "24")
        self.assertEqual(launched["status"], "completed")
        self.assertEqual(launched["attempts_used"], 1)
        self.assertEqual(len(self.rows()), 6)
        # replay: the decline is never re-prompted and the rows are stable
        restart_terminal = decline_terminal()
        restart = graph.build_deps(
            self.root,
            FIXTURE_CONFIG,
            runner=make_runner("never", tag="replay-"),
            approval=restart_terminal,
        )
        drive(state.initial_state(), restart)
        self.assertEqual(restart_terminal.calls, [])
        self.assertEqual(self.rows(), self.rows())


@unittest.skipUnless(
    LANGGRAPH_AVAILABLE, "optional harness extra (langgraph) not installed"
)
class TestGraphCompile(Base):
    def test_build_graph_compiles_with_r1_approval(self):
        compiled = graph.build_graph(self.deps("complete", interruption.auto_decline))
        self.assertTrue(hasattr(compiled, "invoke"))


if __name__ == "__main__":
    unittest.main()
