"""Unit 3 R2 autonomous-path tests (design §6/§8): one agent, no human prompt,
no discoveries, no silent fallback. First valid completed receipt stops; only
failures/timeouts/unresolved evidence retry, into NEW attempt ordinals until
exactly K; the registered-but-unbound crash window is ambiguous, never rerun,
and recovery creates an explicit event plus a new attempt.

PR #20 remediation: pair progress is reconstructed EXPLICITLY from the
append-only log, so a crash after launch_observed binds the existing
attempt, a crash after evidence_bound never reruns the terminal receipt,
and a crash after the K-th evidence_unresolved never exceeds K. A timeout
or non-zero exit classifies evidence_unresolved BEFORE any artifact is
bound, even when the runner left a perfectly valid run dir behind.

Unit 4 update: R1 joins R2 in the same wiring. These tests run the R1 pairs
with the deterministic auto-decline terminal, so every R1 deferral is an
explicit logged decline event and the R2 loop below behaves exactly as in
Unit 3 (rows are filtered to condition R2 by default).
"""

import hashlib
import importlib.util
import json
import shutil
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

# Unit 6: drive moved to harness.driver — the identical walker is now the
# user-facing stdlib seam; this module re-exports it for its own tests.
from harness.driver import drive
from tests.test_harness_evidence import FIXTURE as SEALED_FIXTURE

LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None

ID = "tournament-unit3-r2"


def _dig(data):
    return strategy_context.sha256_digest(data)


POLICY = _dig(b"policy")
PROMPT = _dig(b"prompt")
TOOL = _dig(b"tool")
STATIC_LR = "lr_" + "3a" * 32

CONFIG = {
    "schema_version": 1,
    "tournament_id": ID,
    "harness_version": "0.1.0",
    "core_version": "0",
    "provider": "fake",
    "model": "fake-v1",
    "generation": {"temperature": 0},
    "prompt_policy_version": "amend-4a-3/llm-v1",
    "context_digest_scheme": "sha256:canonical-json-v1",
    "budget": 100,
    "tool_grants": ["move"],
    "fault_config": None,
    "seeds": [18, 24, 30],
    "conditions": ["R1", "R2", "R3"],
    "k_attempts": 3,
    "verification_version": "1",
    "evidence_schema_version": "1",
    "artifact_root": "artifacts/tournaments",
    "context_source_policy": None,
}


def _write_triple(core_root, seed, execution_id, outcome, score, prompt=PROMPT):
    lr = "lr_" + hashlib.sha256(b"lr").hexdigest()
    run_dir = Path(core_root) / "runs" / lr / execution_id
    (run_dir / "llm").mkdir(parents=True)
    receipt = {
        "schema_version": 1,
        "logical_run_id": lr,
        "execution_id": execution_id,
        "seed": str(seed),
        "policy_id": "llm-v1",
        "policy_digest": POLICY,
        "outcome": outcome,
        "verified_score": score,
        "verification": {"status": "valid", "verified_score": score},
    }
    rb = strategy_context.canonical_json_bytes(receipt)
    provider_run = {
        "schema_version": 1,
        "logical_run_id": lr,
        "execution_id": execution_id,
        "requests": [
            {
                "policy_step": 0,
                "provider": "fake",
                "model": "fake-v1",
                "request_id": f"req-{execution_id}",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "latency_ms": 42,
                "raw_visible_response": "ok",
                "tool_call": None,
                "error": None,
            }
        ],
    }
    pb = strategy_context.canonical_json_bytes(provider_run)
    llm = {
        "schema_version": 1,
        "logical_run_id": lr,
        "execution_id": execution_id,
        "provider": "fake",
        "model": "fake-v1",
        "prompt_digest": prompt,
        "tool_schema_digest": TOOL,
        "provider_evidence_digest": strategy_context.digest_json(provider_run),
        "core_receipt_digest": _dig(rb),
    }
    llm["llm_receipt_digest"] = strategy_context.digest_json(llm)
    strategy_context.atomic_write_bytes(run_dir / "receipt.json", rb)
    strategy_context.atomic_write_bytes(run_dir / "llm" / "provider-run.json", pb)
    strategy_context.atomic_write_bytes(
        run_dir / "llm" / "llm-receipt.json", strategy_context.canonical_json_bytes(llm)
    )


def _script_fn(script):
    return {
        "complete": lambda seed, n: ("ok", "completed", 100),
        "fail_fail_complete": lambda seed, n: ("fail", None, None)
        if n < 3
        else ("ok", "completed", 100),
        "always_fail": lambda seed, n: ("fail", None, None),
        "timeout_then_complete": lambda seed, n: ("timeout", None, None)
        if n == 1
        else ("ok", "completed", 100),
        "safe_abort": lambda seed, n: ("ok", "safe_abort", 40),
        "drift_after_first": lambda seed, n: ("ok", "completed", 100)
        if n == 1
        else ("drift", "completed", 100),
        "static_then_complete": lambda seed, n: ("static", None, None)
        if (seed == "18" and n == 1)
        else ("ok", "completed", 100),
        "valid_then_timeout": lambda seed, n: ("valid-timeout", None, None),
        "valid_then_exit1": lambda seed, n: ("valid-exit1", None, None),
        "never": lambda seed, n: (_ for _ in ()).throw(
            AssertionError("runner invoked")
        ),
    }[script]


def fake_runner(script, tag=""):
    """tag makes execution ids unique per runner instance, so a restart runner
    never collides with run dirs written before the simulated crash."""
    fn = _script_fn(script)
    calls = []

    def runner(argv, timeout=None):
        calls.append(list(argv))
        n = len(calls)
        seed = argv[argv.index("--seed") + 1]
        root = Path(argv[argv.index("--artifacts-root") + 1])
        stdout = b"strategy_context_digest: null\nverified_score: 999\n"
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
            return subprocess_adapter.RunnerResult(1, False, stdout, b"")
        if action == "static":
            shutil.copytree(
                SEALED_FIXTURE, root / "runs" / STATIC_LR / "sealed-fixture-01"
            )
            return subprocess_adapter.RunnerResult(0, False, stdout, b"")
        prompt = PROMPT if action != "drift" else "sha256:" + "e" * 64
        _write_triple(root, seed, f"exec-{tag}{n:02d}", outcome, score, prompt)
        return subprocess_adapter.RunnerResult(0, False, stdout, b"")

    runner.calls = calls
    return runner


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def deps(self, script, config=None):
        return graph.build_deps(
            self.root,
            config or CONFIG,
            runner=fake_runner(script),
            approval=interruption.auto_decline,
        )

    def attempt_records(self, deps=None):
        deps = deps or self.deps("never")
        return logs.AppendOnlyLog(
            tournament.expected_layout(self.root, ID)["attempt-log.jsonl"]
        ).records()

    def rows(self, deps, condition="R2"):
        all_rows = json.loads(
            tournament.expected_layout(self.root, ID)["leaderboard.json"].read_bytes()
        )["rows"]
        return [row for row in all_rows if row["condition"] == condition]


class TestFreezeAndRouting(Base):
    def test_tournament_config_rejects_extra_keys(self):
        extra = dict(CONFIG, legacy_score_field=1)
        with self.assertRaises(tournament.TournamentError):
            tournament.validate_config(extra)

    def test_named_config_fixture_is_valid_and_freeze_is_idempotent(self):
        fixture = json.loads(
            (
                Path(__file__).parent / "fixtures/harness/unit3-r2-config.json"
            ).read_bytes()
        )
        tournament.validate_config(fixture)
        self.deps("complete", config=fixture)
        state_value = graph.node_freeze_tournament_config(
            state.initial_state(), self.deps("complete", config=fixture)
        )
        self.assertEqual(state_value["status"], "tournament_frozen")
        sealed = tournament.expected_layout(self.root, ID)["config.json"].read_bytes()
        self.assertEqual(
            sealed, strategy_context.canonical_json_bytes(json.loads(sealed))
        )
        drifted = dict(fixture, k_attempts=9)
        with self.assertRaises(tournament.TournamentError):
            graph.node_freeze_tournament_config(
                state.initial_state(), self.deps("complete", config=drifted)
            )

    def test_r1_r2_r3_are_all_implemented_without_deferrals(self):
        deps = self.deps("complete")
        state_value = graph.node_assign_work(state.initial_state(), deps)
        # deterministic work queue: R2 pairs, then R1, then the R3 fixed
        # three-branch block (Unit 5 wires R3 in; nothing defers silently)
        self.assertEqual(state_value["work_count"], 9)
        deferred = [
            r for r in deps.decision_log.records() if r["event"] == "routes_deferred"
        ]
        self.assertEqual(deferred, [])


class TestR2Loop(Base):
    def test_happy_path_stops_at_first_completed_receipt(self):
        drive(state.initial_state(), self.deps("complete"))
        registered = [
            r
            for r in self.attempt_records()
            if r["event"] == "launch_registered" and r["condition"] == "R2"
        ]
        self.assertEqual(len(registered), 3)  # one per seed, never a retry
        rows = {r["seed"]: r for r in self.rows(None)}
        self.assertEqual(rows["18"]["status"], "completed")
        self.assertEqual(rows["18"]["attempts_used"], 1)
        # stdout decoy never becomes official
        self.assertEqual(rows["18"]["verified_score"], 100)

    def test_null_snapshot_and_registration_identity(self):
        drive(state.initial_state(), self.deps("static_then_complete"))
        layout = tournament.expected_layout(self.root, ID)
        snapshot = (layout["contexts"] / "strategy-context-000.json").read_bytes()
        self.assertEqual(snapshot, b"null")
        launch = json.loads(
            (
                layout["attempts"] / "r2-agent-01-seed-18-attempt-01" / "launch.json"
            ).read_bytes()
        )
        self.assertEqual(launch["context_digest"], _dig(b"null"))
        self.assertTrue(launch["registered_before_start"])
        self.assertIn("--strategy-context", launch["argv"])

    def test_static_sealed_fixture_binds_row_metrics(self):
        drive(state.initial_state(), self.deps("static_then_complete"))
        row = {r["seed"]: r for r in self.rows(None)}["18"]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(
            (row["input_tokens"], row["total_tokens"], row["latency_ms"]), (10, 15, 42)
        )
        self.assertIsNotNone(row["core_receipt_digest"])

    def test_failure_retries_into_new_ordinals_until_completed(self):
        drive(state.initial_state(), self.deps("fail_fail_complete"))
        registered = [
            r
            for r in self.attempt_records()
            if r["event"] == "launch_registered"
            and r["condition"] == "R2"
            and r["seed"] == "18"
        ]
        self.assertEqual(
            [r["attempt_id"] for r in registered],
            [
                "r2-agent-01-seed-18-attempt-01",
                "r2-agent-01-seed-18-attempt-02",
                "r2-agent-01-seed-18-attempt-03",
            ],
        )
        # attempts 1-2 stay unresolved, so the row fails closed on usage while
        # the receipt-owned audit values survive
        row = {r["seed"]: r for r in self.rows(None)}["18"]
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertEqual(row["outcome"], "completed")
        self.assertIsNone(row["input_tokens"])

    def test_k_exhaustion_fails_closed_with_nulls(self):
        drive(state.initial_state(), self.deps("always_fail"))
        row = {r["seed"]: r for r in self.rows(None)}["18"]
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertEqual(row["attempts_used"], 3)
        self.assertIsNone(row["verified_score"])
        self.assertIsNone(row["input_tokens"])
        reasons = [
            r["reason"]
            for r in self.attempt_records()
            if r["event"] == "evidence_unresolved"
            and r["condition"] == "R2"
            and r["seed"] == "18"
        ]
        self.assertEqual(len(reasons), 3)

    def test_timeout_counts_as_explicit_launch(self):
        drive(state.initial_state(), self.deps("timeout_then_complete"))
        observed = [
            r for r in self.attempt_records() if r["event"] == "launch_observed"
        ]
        self.assertTrue(observed[0]["timed_out"])
        self.assertEqual(
            {r["seed"]: r["attempts_used"] for r in self.rows(None)}["18"], 2
        )

    def test_valid_safe_abort_is_terminal_not_retried(self):
        drive(state.initial_state(), self.deps("safe_abort"))
        registered = [
            r
            for r in self.attempt_records()
            if r["event"] == "launch_registered"
            and r["condition"] == "R2"
            and r["seed"] == "18"
        ]
        self.assertEqual(len(registered), 1)
        row = {r["seed"]: r for r in self.rows(None)}["18"]
        self.assertEqual(row["status"], "aborted")
        self.assertEqual(row["outcome"], "safe_abort")
        self.assertEqual(row["verified_score"], 40)

    def test_expectation_drift_fails_closed_and_records_once(self):
        drive(state.initial_state(), self.deps("drift_after_first"))
        row = {r["seed"]: r for r in self.rows(None)}["24"]
        self.assertEqual(row["status"], "evidence_invalid")
        reasons = " ".join(
            r["reason"]
            for r in self.attempt_records()
            if r["event"] == "evidence_unresolved"
        )
        self.assertIn("expectations", reasons)
        recorded = [
            r
            for r in self.deps("never").decision_log.records()
            if r["event"] == "expectations_recorded"
        ]
        self.assertEqual(len(recorded), 1)


class TestCrashWindow(Base):
    def test_registered_unbound_window_never_reruns_and_recovers(self):
        deps = self.deps("never")
        state_value = state.initial_state()
        state_value = graph.node_freeze_tournament_config(state_value, deps)
        state_value = graph.node_assign_work(state_value, deps)
        state_value = graph.node_plan_context(state_value, deps)
        state_value = graph.node_merge_and_freeze_context(state_value, deps)
        state_value = graph.node_register_launch(state_value, deps)
        # --- crash: no launch, no observation, no evidence ---
        runner = fake_runner("complete")
        deps2 = graph.build_deps(
            self.root,
            CONFIG,
            runner=runner,
            approval=interruption.auto_decline,
        )
        drive(state.initial_state(), deps2)
        records = [
            r
            for r in self.attempt_records(deps2)
            if r.get("seed") == "18" and r.get("condition") == "R2"
        ]
        registered = [
            r["attempt_id"] for r in records if r["event"] == "launch_registered"
        ]
        observed = [r["attempt_id"] for r in records if r["event"] == "launch_observed"]
        self.assertEqual(
            registered,
            ["r2-agent-01-seed-18-attempt-01", "r2-agent-01-seed-18-attempt-02"],
        )
        self.assertEqual(observed, ["r2-agent-01-seed-18-attempt-02"])
        decisions = [r["event"] for r in deps2.decision_log.records()]
        self.assertIn("recovery", decisions)
        self.assertLess(
            decisions.index("recovery"), decisions.index("leaderboard_written")
        )
        seed18_calls = [c for c in runner.calls if c[c.index("--seed") + 1] == "18"]
        # one call for the R2 recovery attempt plus one for the R3 seed-18
        # round-1 launch; the crashed attempt-01 itself is never re-spawned
        self.assertEqual(len(seed18_calls), 2)
        rows = {r["seed"]: r for r in self.rows(deps2)}
        self.assertEqual(rows["18"]["attempts_used"], 2)
        # the crashed launch stays unresolved, so the row fails closed on usage
        self.assertEqual(rows["18"]["status"], "evidence_invalid")
        self.assertEqual(rows["18"]["outcome"], "completed")
        self.assertIsNone(rows["18"]["input_tokens"])


class TestSubprocessResultPrecedesBinding(Base):
    """PR #20 blocker 2: timeout / non-zero exit resolve evidence BEFORE any
    artifact binding, even when the runner left a perfectly valid run dir."""

    def test_timeout_with_valid_run_dir_is_unresolved_before_binding(self):
        drive(state.initial_state(), self.deps("valid_then_timeout"))
        records = self.attempt_records()
        self.assertEqual(
            [r for r in records if r["event"] == "evidence_bound"],
            [],
            "a timed-out launch must never bind evidence",
        )
        reasons = [
            r["reason"]
            for r in records
            if r["event"] == "evidence_unresolved"
            and r["condition"] == "R2"
            and r["seed"] == "18"
        ]
        self.assertEqual(reasons, ["subprocess_timeout"] * 3)
        row = {r["seed"]: r for r in self.rows(None)}["18"]
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertIsNone(row["verified_score"])  # receipt score never official

    def test_nonzero_exit_with_valid_run_dir_is_unresolved_before_binding(self):
        drive(state.initial_state(), self.deps("valid_then_exit1"))
        records = self.attempt_records()
        self.assertEqual(
            [r for r in records if r["event"] == "evidence_bound"],
            [],
            "a non-zero exit must never bind evidence",
        )
        reasons = [
            r["reason"]
            for r in records
            if r["event"] == "evidence_unresolved"
            and r["condition"] == "R2"
            and r["seed"] == "18"
        ]
        self.assertEqual(reasons, ["subprocess_nonzero_exit"] * 3)
        row = {r["seed"]: r for r in self.rows(None)}["18"]
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertIsNone(row["verified_score"])


class TestRestartRecovery(Base):
    """PR #20 blocker 1: pair state is reconstructed from the append-only log,
    so every crash window resumes to the identical pair decision."""

    def _run_to(self, deps, node):
        state_value = state.initial_state()
        state_value = graph.node_freeze_tournament_config(state_value, deps)
        state_value = graph.node_assign_work(state_value, deps)
        state_value = graph.node_plan_context(state_value, deps)
        for step in ("merge", "register", "launch", "collect"):
            if step == "merge":
                state_value = graph.node_merge_and_freeze_context(state_value, deps)
            elif step == "register":
                state_value = graph.node_register_launch(state_value, deps)
            elif step == "launch":
                state_value = graph.node_launch_subprocess(state_value, deps)
            elif step == "collect":
                state_value = graph.node_collect_and_bind_evidence(state_value, deps)
            if step == node:
                return state_value
        return state_value

    def test_crash_after_launch_observed_binds_the_existing_attempt(self):
        deps = self.deps("complete")
        self._run_to(deps, "launch")  # crash: observed, no evidence resolution
        restart = fake_runner("complete", tag="r2-")
        deps2 = graph.build_deps(
            self.root,
            CONFIG,
            runner=restart,
            approval=interruption.auto_decline,
        )
        drive(state.initial_state(), deps2)
        seed18 = [
            r
            for r in self.attempt_records(deps2)
            if r.get("seed") == "18" and r.get("condition") == "R2"
        ]
        registered = [
            r["attempt_id"] for r in seed18 if r["event"] == "launch_registered"
        ]
        # invariant: the same attempt_id is never relaunched — the observed
        # attempt binds its existing evidence instead of a new ordinal
        self.assertEqual(registered, ["r2-agent-01-seed-18-attempt-01"])
        bound = [r["attempt_id"] for r in seed18 if r["event"] == "evidence_bound"]
        self.assertEqual(bound, ["r2-agent-01-seed-18-attempt-01"])
        seed18_calls = [c for c in restart.calls if c[c.index("--seed") + 1] == "18"]
        # exactly one spawn: the R3 seed-18 round-1 launch; the crashed r2
        # attempt-01 is bound from its own evidence, never re-spawned
        self.assertEqual(len(seed18_calls), 1)
        row = {r["seed"]: r for r in self.rows(deps2)}["18"]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["attempts_used"], 1)
        self.assertEqual(row["verified_score"], 100)

    def test_crash_after_evidence_bound_never_reruns_the_terminal_receipt(self):
        deps = self.deps("complete")
        self._run_to(deps, "collect")  # crash: bound, seed never advanced
        restart = fake_runner("complete", tag="r2-")
        deps2 = graph.build_deps(
            self.root,
            CONFIG,
            runner=restart,
            approval=interruption.auto_decline,
        )
        drive(state.initial_state(), deps2)
        seed18 = [
            r
            for r in self.attempt_records(deps2)
            if r.get("seed") == "18" and r.get("condition") == "R2"
        ]
        registered = [
            r["attempt_id"] for r in seed18 if r["event"] == "launch_registered"
        ]
        # invariant: a bound terminal receipt is never repeated
        self.assertEqual(registered, ["r2-agent-01-seed-18-attempt-01"])
        seed18_calls = [c for c in restart.calls if c[c.index("--seed") + 1] == "18"]
        # the one spawn is the R3 seed-18 round-1 launch, never the bound r2
        # terminal receipt
        self.assertEqual(len(seed18_calls), 1)
        terminals = [
            r
            for r in deps2.decision_log.records()
            if r.get("event") == "attempt_terminal"
            and r.get("attempt_id") == "r2-agent-01-seed-18-attempt-01"
        ]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["outcome"], "completed")
        rows = {r["seed"]: r for r in self.rows(deps2)}
        self.assertEqual(rows["18"]["status"], "completed")
        self.assertEqual(rows["18"]["attempts_used"], 1)
        self.assertEqual(sorted(rows), ["18", "24", "30"])

    def test_crash_after_kth_unresolved_is_terminal_and_never_exceeds_k(self):
        deps = self.deps("always_fail")
        for _ in range(3):  # exactly K launches, none routed through retry_or_finish
            self._run_to(deps, "collect")
        # --- crash: K-th evidence_unresolved recorded, no terminal decision ---
        restart = fake_runner("always_fail", tag="r2-")
        deps2 = graph.build_deps(
            self.root,
            CONFIG,
            runner=restart,
            approval=interruption.auto_decline,
        )
        drive(state.initial_state(), deps2)
        registered = [
            r["attempt_id"]
            for r in self.attempt_records(deps2)
            if r["event"] == "launch_registered"
            and r["condition"] == "R2"
            and r["seed"] == "18"
        ]
        # invariant: the exact-K limit is never exceeded
        self.assertEqual(len(registered), 3)
        self.assertEqual(registered[-1], "r2-agent-01-seed-18-attempt-03")
        terminals = [
            r
            for r in deps2.decision_log.records()
            if r.get("event") == "attempt_terminal"
            and r.get("attempt_id") == "r2-agent-01-seed-18-attempt-03"
        ]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["reason"], "k_exhausted")
        row = {r["seed"]: r for r in self.rows(deps2)}["18"]
        self.assertEqual(row["attempts_used"], 3)
        self.assertEqual(row["status"], "evidence_invalid")


@unittest.skipUnless(
    LANGGRAPH_AVAILABLE, "optional harness extra (langgraph) not installed"
)
class TestGraphCompile(Base):
    def test_build_graph_compiles(self):
        compiled = graph.build_graph(self.deps("complete"))
        self.assertTrue(hasattr(compiled, "invoke"))


if __name__ == "__main__":
    unittest.main()
