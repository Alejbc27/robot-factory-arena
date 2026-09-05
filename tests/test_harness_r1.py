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
        if action == "valid-timeout":
            _write_triple(root, seed, f"exec-{tag}{n:02d}", "completed", 100)
            return subprocess_adapter.RunnerResult(None, True, stdout, b"")
        if action == "valid-exit1":
            _write_triple(root, seed, f"exec-{tag}{n:02d}", "completed", 100)
            return subprocess_adapter.RunnerResult(1, False, stdout, b"boom")
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


class TestGuidancePipeline(Base):
    """Attempt-2 slice: approved guidance reaches a launch ONLY as canonical
    frozen StrategyContext bytes. The side-channel door stays shut end to end,
    the mirror validator rejects every invalid canonical shape, and a revision
    supersedes the prior guidance without ever resurrecting it."""

    def test_canonical_snapshot_bytes_match_the_full_context_object(self):
        seam_config = dict(FIXTURE_CONFIG, tournament_id="tournament-unit4-guidance")
        tournament.validate_config(seam_config)
        terminal = scripted_terminal(
            [{"decision": "edit", "guidance": [GUIDANCE_A]}, {"decision": "yes"}]
        )
        drive(
            state.initial_state(),
            self.deps("complete", terminal, config=seam_config),
        )
        snapshot = self.layout(seam_config)["contexts"] / "strategy-context-003.json"
        data = snapshot.read_bytes()
        context = interruption.context_from_guidance_records(
            seam_config["tournament_id"],
            interruption.guidance_records([GUIDANCE_A]),
        )
        self.assertEqual(
            data,
            strategy_context.canonical_json_bytes(
                strategy_context.normalize_context(context)
            ),
        )
        parsed = json.loads(data)
        self.assertEqual(parsed["source_tournament_id"], "tournament-unit4-guidance")
        self.assertEqual(parsed["discoveries"], [])
        self.assertEqual(len(parsed["guidance"]), 1)
        self.assertEqual(
            parsed["guidance"][0],
            {
                "guidance_id": "guidance-0001",
                "author_id": "human",
                "guidance_ordinal": 0,
                "text": GUIDANCE_A,
            },
        )
        # the launch and its frozen event both carry the snapshot digest
        launch = self.launch("r1-agent-01-seed-18-attempt-01", seam_config)
        self.assertEqual(launch["context_digest"], strategy_context.sha256_digest(data))
        event = next(
            r
            for r in self.decision_records(seam_config)
            if r["event"] == "context_snapshot_frozen" and r.get("condition") == "R1"
        )
        self.assertEqual(event["context_digest"], launch["context_digest"])
        self.assertEqual(event["source"], "r1-guidance")
        # guidance changes identity: the R2 null snapshot digest differs
        null_launch = self.launch("r2-agent-01-seed-18-attempt-01", seam_config)
        self.assertNotEqual(null_launch["context_digest"], launch["context_digest"])

    def test_side_channel_answer_cannot_reach_the_pipeline_end_to_end(self):
        terminal = scripted_terminal([{"decision": "yes", "guidance": ["poison"]}])
        with self.assertRaises(interruption.InterruptionError):
            drive(state.initial_state(), self.deps("complete", terminal))
        # only the three R2 first-route null snapshots exist; the R1 proposal
        # never froze, granted, or launched anything
        contexts = sorted(p.name for p in self.layout()["contexts"].iterdir())
        self.assertEqual(
            contexts,
            [
                "strategy-context-000.json",
                "strategy-context-001.json",
                "strategy-context-002.json",
            ],
        )
        sources = [
            r["source"]
            for r in self.decision_records()
            if r["event"] == "context_snapshot_frozen"
        ]
        self.assertEqual(sources, ["null-first-route"] * 3)
        grants = [
            r for r in self.decision_records() if r["event"] == "approval_granted"
        ]
        self.assertEqual(grants, [])
        self.assertEqual(
            [
                r
                for r in self.attempt_records()
                if r.get("condition") == "R1" and r["event"] == "launch_registered"
            ],
            [],
        )

    def test_invalid_canonical_shapes_are_rejected_by_the_mirror_validator(self):
        record = interruption.guidance_records(["ok"])[0]
        for mutation in (
            {"guidance_ordinal": -1},
            {"guidance_ordinal": True},
            {"text": ""},
            {"exploit": "prompt-injection"},
        ):
            bad = dict(record, **mutation)
            with self.assertRaises(strategy_context.ContextError):
                interruption.context_from_guidance_records(
                    FIXTURE_CONFIG["tournament_id"], [bad]
                )
        good = interruption.build_guidance_context(
            FIXTURE_CONFIG["tournament_id"], ["ok"]
        )
        assert good is not None  # static narrowing; the runtime result is a context
        self.assertEqual(good["guidance"][0]["guidance_id"], "guidance-0001")

    def test_revision_supersedes_prior_guidance_and_never_resurrects(self):
        guidance_b = "Extract via the loading dock instead"
        terminal = scripted_terminal(
            [
                {"decision": "edit", "guidance": [GUIDANCE_A]},
                {"decision": "edit", "guidance": [guidance_b]},
                {"decision": "yes"},
            ]
        )
        drive(state.initial_state(), self.deps("complete", terminal))
        # both revisions stay in the append-only log as history
        edits = [
            r["guidance"]
            for r in self.decision_records()
            if r.get("event") == "approval_edit" and r.get("seed") == "18"
        ]
        self.assertEqual(edits, [[GUIDANCE_A], [guidance_b]])
        # the frozen snapshot contains ONLY the latest revision
        data = (self.layout()["contexts"] / "strategy-context-003.json").read_bytes()
        parsed = json.loads(data)
        self.assertEqual([g["text"] for g in parsed["guidance"]], [guidance_b])
        launch = self.launch("r1-agent-01-seed-18-attempt-01")
        self.assertEqual(launch["context_digest"], strategy_context.sha256_digest(data))
        # different approved guidance means a different launch identity: the
        # no-guidance pair freezes the canonical null digest
        null_launch = self.launch("r1-agent-01-seed-24-attempt-01")
        self.assertEqual(
            null_launch["context_digest"], strategy_context.sha256_digest(b"null")
        )
        self.assertNotEqual(launch["context_digest"], null_launch["context_digest"])


class TestR1R2Coexistence(Base):
    """Attempt-2 slice: R1 and R2 share one deterministic work queue — same
    seeds, same queue order, zero cross-contamination in either direction."""

    def _drive_two_tournaments(self, yes_answers):
        yes_config = dict(FIXTURE_CONFIG, tournament_id="tournament-unit4-coexist-yes")
        no_config = dict(FIXTURE_CONFIG, tournament_id="tournament-unit4-coexist-no")
        for config in (yes_config, no_config):
            tournament.validate_config(config)
        drive(
            state.initial_state(),
            self.deps(
                "complete", scripted_terminal(list(yes_answers)), config=yes_config
            ),
        )
        drive(
            state.initial_state(),
            self.deps("complete", decline_terminal(), config=no_config),
        )
        return yes_config, no_config

    def test_r2_rows_byte_identical_between_guidance_and_decline_runs(self):
        yes_config, no_config = self._drive_two_tournaments(
            [{"decision": "edit", "guidance": [GUIDANCE_A]}, {"decision": "yes"}] * 3
        )

        def r2_rows(config):
            return [r for r in self.rows(config) if r["condition"] == "R2"]

        self.assertEqual(
            strategy_context.canonical_json_bytes(r2_rows(yes_config)),
            strategy_context.canonical_json_bytes(r2_rows(no_config)),
        )
        for config in (yes_config, no_config):
            deferred = [
                r["conditions"]
                for r in self.decision_records(config)
                if r["event"] == "routes_deferred"
            ]
            self.assertEqual(deferred, [["R3"]])
            approvals = [
                r
                for r in self.decision_records(config)
                if r.get("condition") == "R2"
                and r["event"]
                in ("approval_edit", "approval_granted", "launch_declined")
            ]
            self.assertEqual(approvals, [])
        # the R1 side genuinely differs: a granted launch vs an explicit decline
        self.assertEqual(self.row_for("R1", "18", yes_config)["attempts_used"], 1)
        self.assertEqual(self.row_for("R1", "18", no_config)["attempts_used"], 0)


class TestLaunchWindowRestart(Base):
    """Attempt-2 slice: a crash inside the R1 launch window resumes under the
    Unit-3 reconstruction contracts — bind or new ordinal, the same attempt_id
    is never rerun, and a standing grant is never re-prompted."""

    def _walk(self, deps, approval, crash=None):
        """Sequential drive() walk over the exact build_graph topology;
        ``crash`` = (condition, seed, step) aborts right after that step for
        that pair, simulating the launch-window interruption."""
        steps = (
            ("merge", graph.node_merge_and_freeze_context),
            ("register", graph.node_register_launch),
            ("launch", graph.node_launch_subprocess),
            ("collect", graph.node_collect_and_bind_evidence),
        )
        value = state.initial_state()
        value = graph.node_freeze_tournament_config(value, deps)
        value = graph.node_assign_work(value, deps)
        value = graph.node_plan_context(value, deps)
        for _ in range(200):
            plan_route = graph.route_after_plan(value, deps)
            if plan_route == "approve_r1":
                value = graph.node_approve_r1(value, deps)
                approval_route = graph.route_after_approval(value)
                if approval_route == "approve_r1":
                    continue
                if approval_route == "retry_or_finish":
                    value = graph.node_retry_or_finish(value, deps)
                else:
                    for name, function in steps:
                        value = function(value, deps)
                        if crash == (value["condition"], value["semilla"], name):
                            return value
                    # fixed graph edge: collect always flows into retry_or_finish
                    value = graph.node_retry_or_finish(value, deps)
            elif plan_route == "collect_and_bind_evidence":
                value = graph.node_collect_and_bind_evidence(value, deps)
                value = graph.node_retry_or_finish(value, deps)
            elif plan_route == "retry_or_finish":
                value = graph.node_retry_or_finish(value, deps)
            else:
                for _, function in steps:
                    value = function(value, deps)
                # fixed graph edge: collect always flows into retry_or_finish
                value = graph.node_retry_or_finish(value, deps)
            retry_route = graph.route_after_retry(value)
            if retry_route == "aggregate_leaderboard":
                return graph.node_aggregate_leaderboard(value, deps)
            if retry_route == "plan_context":
                value = graph.node_plan_context(value, deps)
        raise AssertionError("topology step budget exceeded")

    def _prompted(self, terminal, condition, seed):
        return [
            c
            for c in terminal.calls
            if c["condition"] == condition and c["seed"] == seed
        ]

    def test_registration_window_crash_recovers_with_a_new_ordinal(self):
        # K=2 so the recovery attempt exhausts the pair deterministically
        config = dict(FIXTURE_CONFIG, k_attempts=2)
        tournament.validate_config(config)
        terminal = scripted_terminal([{"decision": "yes"}])
        deps = self.deps("valid_then_timeout", terminal, config=config, tag="w1-")
        self._walk(deps, terminal, crash=("R1", "18", "register"))
        first_id = "r1-agent-01-seed-18-attempt-01"
        # registered but never observed: the ambiguous launch window
        self.assertEqual(
            [
                r["event"]
                for r in self.attempt_records(config)
                if r.get("attempt_id") == first_id
            ],
            ["launch_registered"],
        )
        # restart: the standing grant is replayed from the log, never
        # re-prompted, and attempt-01 is never rerun — a NEW ordinal launches
        restart_runner = make_runner("valid_then_timeout", tag="w2-")
        restart_terminal = decline_terminal()
        drive(
            state.initial_state(),
            graph.build_deps(
                self.root, config, runner=restart_runner, approval=restart_terminal
            ),
        )
        self.assertEqual(self._prompted(restart_terminal, "R1", "18"), [])
        registered = [
            r["attempt_id"]
            for r in self.attempt_records(config)
            if r.get("condition") == "R1"
            and r.get("seed") == "18"
            and r["event"] == "launch_registered"
        ]
        self.assertEqual(registered, [first_id, "r1-agent-01-seed-18-attempt-02"])
        # explicit recovery evidence for the ambiguous window
        recovery = [
            r
            for r in self.decision_records(config)
            if r["event"] == "recovery" and r.get("attempt_id") == first_id
        ]
        self.assertEqual(len(recovery), 1)
        unresolved = {
            r["attempt_id"]: r["reason"]
            for r in self.attempt_records(config)
            if r.get("condition") == "R1"
            and r.get("seed") == "18"
            and r["event"] == "evidence_unresolved"
        }
        self.assertEqual(
            unresolved,
            {
                first_id: "crash_window_unobserved",
                "r1-agent-01-seed-18-attempt-02": "subprocess_timeout",
            },
        )
        row = self.row_for("R1", "18", config)
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertEqual(row["attempts_used"], 2)
        self.assertIsNone(row["verified_score"])
        # the pre-crash registration is preserved, never overwritten
        self.assertTrue(
            (self.layout(config)["attempts"] / first_id / "launch.json").exists()
        )

    def test_observation_window_crash_binds_the_same_attempt(self):
        terminal = scripted_terminal([{"decision": "yes"}])
        deps = self.deps("complete", terminal, tag="w1-")
        self._walk(deps, terminal, crash=("R1", "18", "launch"))
        first_id = "r1-agent-01-seed-18-attempt-01"
        restart_terminal = decline_terminal()
        drive(
            state.initial_state(),
            graph.build_deps(
                self.root,
                FIXTURE_CONFIG,
                runner=make_runner("complete", tag="w2-"),
                approval=restart_terminal,
            ),
        )
        self.assertEqual(self._prompted(restart_terminal, "R1", "18"), [])
        registered = [
            r["attempt_id"]
            for r in self.attempt_records()
            if r.get("condition") == "R1"
            and r.get("seed") == "18"
            and r["event"] == "launch_registered"
        ]
        # the observed attempt binds its own evidence; no new ordinal, no rerun
        self.assertEqual(registered, [first_id])
        self.assertEqual(
            [
                r["attempt_id"]
                for r in self.attempt_records()
                if r.get("condition") == "R1"
                and r.get("seed") == "18"
                and r["event"] == "evidence_bound"
            ],
            [first_id],
        )
        # the observation window is NOT ambiguous: no recovery decision at all
        self.assertEqual(
            [r for r in self.decision_records() if r["event"] == "recovery"],
            [],
        )
        row = self.row_for("R1", "18")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["attempts_used"], 1)
        self.assertEqual(row["verified_score"], 100)


class TestAnswerExactKeyContract(Base):
    """PR #22 blocker 1: the seam fails closed on incomplete answers.

    The exact contract: ``yes`` and ``no`` carry exactly ``{"decision"}``;
    ``edit`` carries exactly ``{"decision", "guidance"}`` with guidance a list
    of non-empty strings (``[]`` is legal and clears the pending guidance).
    A missing or null guidance on an edit answer must be rejected HERE, so the
    graph node can never crash on it outside the contract.
    """

    def test_edit_requires_exactly_decision_and_guidance(self):
        for bad in (
            {"decision": "edit"},
            {"decision": "edit", "guidance": None},
        ):
            with self.assertRaises(interruption.InterruptionError):
                interruption.validate_answer(bad)
        # [] clears the pending guidance: legal, freezes the canonical null
        self.assertEqual(
            interruption.validate_answer({"decision": "edit", "guidance": []}),
            "edit",
        )
        self.assertEqual(
            interruption.validate_answer(
                {"decision": "edit", "guidance": [GUIDANCE_A]}
            ),
            "edit",
        )

    def test_guidance_exists_only_with_the_edit_decision(self):
        for bad in (
            {"decision": "yes", "guidance": None},
            {"decision": "no", "guidance": None},
            {"decision": "yes", "guidance": [GUIDANCE_A]},
        ):
            with self.assertRaises(interruption.InterruptionError):
                interruption.validate_answer(bad)
        self.assertEqual(interruption.validate_answer({"decision": "yes"}), "yes")
        self.assertEqual(interruption.validate_answer({"decision": "no"}), "no")

    def test_incomplete_edit_answer_fails_closed_end_to_end(self):
        terminal = scripted_terminal([{"decision": "edit"}])
        with self.assertRaises(interruption.InterruptionError):
            drive(state.initial_state(), self.deps("complete", terminal))
        grants = [
            r for r in self.decision_records() if r.get("event") == "approval_granted"
        ]
        self.assertEqual(grants, [])
        self.assertEqual(
            [
                r
                for r in self.attempt_records()
                if r.get("condition") == "R1" and r["event"] == "launch_registered"
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
