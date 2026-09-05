"""Unit 5 R3 tests (design §6): exactly three fixed seed branches with round
barriers. The merge is deterministic — ordered by declared identity
``(source_attempt_id, explorer_id, discovery_ordinal)``, never by completion
timing — with no LLM coordinator, no ranking, no deduplication by content, and
no semantic filtering. The barrier runs only when every contributing branch
round is resolved: a PRIOR-ROUND LAUNCH WITHOUT EVIDENCE RESOLUTION blocks
the merge, and so does a fresh active branch that never launched its prior
round (``missing_prior_round``) — the barrier records explicit waiting
evidence and drains deterministically by routing to the first pending
attempt's collector or, for a missing prior round, to that branch's own
plan/launch flow, while an already-resolved retry pair simply participates in
the next round. Only finished receipt-bound runs admit candidate
discoveries, and the same merged DiscoverySnapshot is frozen once per round for
every branch's next launch, so an in-flight run can never see its own
same-round discovery. Admission is write-ahead: candidates are sealed into
the append-only discovery log before the terminal bind append, and a crash
in the bind→admit window promotes exactly once from the sealed bytes — the
mutable candidate channel is never re-read after the bind. Restarts
reconstruct deterministically from the append-only logs under the Unit-3
pair-state contracts.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness import graph, logs, state, strategy_context, tournament
from tests.test_harness_r1 import decline_terminal, scripted_terminal
from tests.test_harness_r2 import _write_triple, drive

LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None

NULL_DIGEST = strategy_context.sha256_digest(b"null")
DISCOVERIES_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/harness/unit5-r3-discoveries.json").read_bytes()
)


def config(tournament_id="tournament-unit5-r3", **overrides):
    base = json.loads(
        (Path(__file__).parent / "fixtures/harness/unit5-r3-config.json").read_bytes()
    )
    value = dict(base, tournament_id=tournament_id, **overrides)
    tournament.validate_config(value)
    return value


def r3_runner(per_seed=None, tag="u5-", r3_from=3):
    """Fake branch runner with per-seed scripted R3 round actions.

    Launches whose frozen-snapshot ordinal is below ``r3_from`` belong to the
    R2/R1 phase that always precedes R3 in the work queue: they complete
    trivially and never touch the per-seed scripts or counters (R2 shares the
    same seeds, so an unscoped counter would consume the R3 script). A
    guided-R1 tournament freezes one more snapshot before R3 starts, so those
    tests pass ``r3_from=4``. ``per_seed`` maps seed -> list of (action,
    outcome, score) consumed one per R3 round; missing seeds complete every
    round, and exhausting a list raises, which proves no branch launches after
    completion/exhaustion. stdout reports the REAL digest of the frozen
    snapshot, so merged-discovery rounds verify; execution ids carry only
    tag+seed+round so identical R3 rounds stay byte-identical across
    tournaments regardless of the R1 shape.
    """
    counts = {}

    def _ordinal(argv):
        context = Path(argv[argv.index("--strategy-context") + 1])
        return int(context.stem.rsplit("-", 1)[1])

    def runner(argv, timeout=None):
        seed = argv[argv.index("--seed") + 1]
        root = Path(argv[argv.index("--artifacts-root") + 1])
        snapshot = Path(argv[argv.index("--strategy-context") + 1]).read_bytes()
        reported = (
            "null" if snapshot == b"null" else strategy_context.sha256_digest(snapshot)
        )
        stdout = f"strategy_context_digest: {reported}\nverified_score: 999\n".encode()
        if _ordinal(argv) < r3_from:
            _write_triple(
                root, seed, f"exec-{tag}pre-{_ordinal(argv)}", "completed", 100
            )
            return graph.subprocess_adapter.RunnerResult(0, False, stdout, b"")
        counts[seed] = counts.get(seed, 0) + 1
        rounds = counts[seed]
        script = (per_seed or {}).get(seed, [("ok", "completed", 100)])
        if rounds > len(script):
            raise AssertionError(
                f"seed {seed} launched R3 round {rounds} beyond its script"
            )
        action, outcome, score = script[rounds - 1]
        if action == "timeout":
            return graph.subprocess_adapter.RunnerResult(None, True, stdout, b"")
        _write_triple(root, seed, f"exec-{tag}{seed}-{rounds:02d}", outcome, score)
        return graph.subprocess_adapter.RunnerResult(0, False, stdout, b"")

    runner.counts = counts
    return runner


def _walk(deps, terminal, crash=None):
    """Sequential drive over the exact build_graph topology, including the R3
    barrier branch (extends the Unit-4 walker per handoff note viii)."""
    value = state.initial_state()
    value = graph.node_freeze_tournament_config(value, deps)
    value = graph.node_assign_work(value, deps)
    value = graph.node_plan_context(value, deps)
    freeze = (graph.node_merge_and_freeze_context, "merge")
    barrier = (graph.node_barrier_merge_r3, "barrier")
    tail_steps = (
        (graph.node_register_launch, "register"),
        (graph.node_launch_subprocess, "launch"),
        (graph.node_collect_and_bind_evidence, "collect"),
    )
    steps_by_route = {
        "merge_and_freeze_context": (freeze,) + tail_steps,
        "barrier_merge_r3": (barrier,) + tail_steps,
    }
    for _ in range(400):
        plan_route = graph.route_after_plan(value, deps)
        if plan_route == "approve_r1":
            value = graph.node_approve_r1(value, deps)
            approval_route = graph.route_after_approval(value)
            if approval_route == "approve_r1":
                continue
            if approval_route == "retry_or_finish":
                value = graph.node_retry_or_finish(value, deps)
            else:
                for function, name in (freeze,) + tail_steps:
                    value = function(value, deps)
                    if crash == (value["condition"], value["semilla"], name):
                        return value
                value = graph.node_retry_or_finish(value, deps)
        elif plan_route in ("collect_and_bind_evidence", "retry_or_finish"):
            value = getattr(graph, f"node_{plan_route}")(value, deps)
            if plan_route == "collect_and_bind_evidence":
                value = graph.node_retry_or_finish(value, deps)
        else:
            first, first_name = steps_by_route[plan_route][0]
            value = first(value, deps)
            if crash == (value["condition"], value["semilla"], first_name):
                return value
            barrier_route = (
                graph.route_after_barrier(value)
                if plan_route == "barrier_merge_r3"
                else None
            )
            if barrier_route == "collect_and_bind_evidence":
                # barrier drain: resolve the pending prior-round launch first
                value = graph.node_collect_and_bind_evidence(value, deps)
            elif barrier_route == "plan_context":
                # barrier drain: a branch missing its prior-round work is
                # routed through its own plan/launch flow (conditional edge)
                value = graph.node_plan_context(value, deps)
                continue
            else:
                for function, name in steps_by_route[plan_route][1:]:
                    value = function(value, deps)
                    if crash == (value["condition"], value["semilla"], name):
                        return value
            value = graph.node_retry_or_finish(value, deps)
        retry_route = graph.route_after_retry(value)
        if retry_route == "aggregate_leaderboard":
            return graph.node_aggregate_leaderboard(value, deps)
        if retry_route == "plan_context":
            value = graph.node_plan_context(value, deps)
    raise AssertionError("topology step budget exceeded")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def deps(self, runner, cfg=None, tournament_id="tournament-unit5-r3"):
        return graph.build_deps(
            self.root,
            cfg or config(tournament_id),
            runner=runner,
            approval=decline_terminal(),
        )

    def layout(self, cfg=None):
        cfg = cfg or config()
        return tournament.expected_layout(
            self.root, cfg["tournament_id"], cfg["artifact_root"]
        )

    def records(self, name, cfg=None):
        return logs.AppendOnlyLog(self.layout(cfg)[name]).records()

    def attempts(self, cfg=None):
        return self.records("attempt-log.jsonl", cfg)

    def decisions(self, cfg=None):
        return self.records("decision-log.jsonl", cfg)

    def admitted(self, cfg=None):
        return [
            r["discovery"]
            for r in self.records("discoveries.jsonl", cfg)
            if r["event"] == "discovery_admitted"
        ]

    def rows(self, cfg=None):
        return json.loads(self.layout(cfg)["leaderboard.json"].read_bytes())["rows"]

    def row(self, condition, seed, cfg=None):
        return next(
            r
            for r in self.rows(cfg)
            if r["condition"] == condition and r["seed"] == seed
        )

    def launch(self, attempt_id, cfg=None):
        return json.loads(
            (self.layout(cfg)["attempts"] / attempt_id / "launch.json").read_bytes()
        )

    def snapshot(self, ordinal, cfg=None):
        return (
            self.layout(cfg)["contexts"] / f"strategy-context-{ordinal:03d}.json"
        ).read_bytes()

    def context_names(self, cfg=None):
        return sorted(p.name for p in self.layout(cfg)["contexts"].iterdir())

    def write_candidates(self, attempt_id, cfg=None, records=None):
        path = self.layout(cfg)["attempts"] / attempt_id / "discoveries.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = records if records is not None else DISCOVERIES_FIXTURE
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            strategy_context.atomic_write_bytes(
                path, strategy_context.canonical_json_bytes(payload)
            )

    def r3_launches(self, cfg=None):
        return [
            r
            for r in self.attempts(cfg)
            if r["event"] == "launch_registered" and r["condition"] == "R3"
        ]

    def lineage_runner(self, tag="u5-"):
        """agent-01 (seed 18) times out round 1 and completes round 2; the other
        branches complete round 1 — the minimal two-round lineage scenario."""
        return r3_runner(
            {"18": [("timeout", None, None), ("ok", "completed", 100)]}, tag=tag
        )

    def full_lineage(self, cfg=None, tag="u5-", runner=None):
        runner = runner or self.lineage_runner(tag)
        drive(state.initial_state(), self.deps(runner, cfg))
        return runner


class TestFixedBranches(Base):
    """Task 1 (RED): exact agent↔seed mapping, one launch per branch per round,
    and no launch after completion or exhaustion."""

    def test_exact_agent_seed_mapping_without_duplicate_ownership(self):
        self.full_lineage()
        registered = self.r3_launches()
        by_seed = {}
        for record in registered:
            by_seed.setdefault(record["seed"], []).append(record)
        self.assertEqual(sorted(by_seed), ["18", "24", "30"])
        expected = {
            "18": ("agent-01", "r3-agent-01-seed-18-attempt-01"),
            "24": ("agent-02", "r3-agent-02-seed-24-attempt-01"),
            "30": ("agent-03", "r3-agent-03-seed-30-attempt-01"),
        }
        seen_agents = set()
        for seed, records in by_seed.items():
            agent, first_id = expected[seed]
            self.assertEqual(records[0]["attempt_id"], first_id)
            self.assertEqual(records[0]["agent_id"], agent)
            seen_agents.add(self.launch(first_id)["agent_id"])
        self.assertEqual(seen_agents, {"agent-01", "agent-02", "agent-03"})

    def test_at_most_one_launch_per_branch_per_round(self):
        self.full_lineage()
        rounds = {}
        for record in self.r3_launches():
            rounds.setdefault(record["seed"], []).append(record["attempt_ordinal"])
        for seed, ordinals in rounds.items():
            self.assertEqual(sorted(ordinals), list(range(1, len(ordinals) + 1)))

    def test_no_launch_after_completion_or_exhaustion(self):
        # completion: each branch binds a receipt in round 1 and stops
        self.full_lineage(runner=r3_runner(tag="c1-"))
        self.assertEqual(len(self.r3_launches()), 3)
        self.assertEqual(
            [self.row("R3", s)["attempts_used"] for s in ("18", "24", "30")], [1, 1, 1]
        )
        # exhaustion: K scripted timeouts, and a 4th round raises in the runner
        # (its own tournament: the completion phase already terminalised this one)
        exhausting = r3_runner(
            {seed: [("timeout", None, None)] * 3 for seed in ("18", "24", "30")},
            tag="c2-",
        )
        exhausted_cfg = config(tournament_id="tournament-unit5-r3-x")
        drive(state.initial_state(), self.deps(exhausting, exhausted_cfg))
        self.assertEqual(len(self.r3_launches(exhausted_cfg)), 9)
        for seed in ("18", "24", "30"):
            self.assertEqual(
                self.row("R3", seed, exhausted_cfg)["status"], "evidence_invalid"
            )
            self.assertEqual(self.row("R3", seed, exhausted_cfg)["attempts_used"], 3)


class TestBarrierMerge(Base):
    """Task 2: the deterministic round barrier — identity ordering, explicit
    waiting, one identical snapshot per round."""

    def test_merge_ordering_is_byte_identical_under_permuted_admission(self):
        records = [
            {
                "source_attempt_id": f"r3-agent-0{index}-seed-{18 * index}-attempt-01",
                "explorer_id": f"explorer-{index}",
                "discovery_ordinal": index - 1,
                "producing_condition": "R3",
                "producing_agent_id": f"agent-0{index}",
                "producing_seed": str(18 * index),
                "producing_attempt_ordinal": 1,
                "evidence": {
                    "run_status": "finished",
                    "core_receipt_digest": "sha256:" + format(index, "064x"),
                    "llm_receipt_digest": "sha256:" + format(index + 9, "064x"),
                },
                "text": f"finding {index}",
            }
            for index in (1, 2, 3)
        ]
        digests = []
        for order in ((0, 1, 2), (2, 0, 1), (1, 2, 0)):
            with tempfile.TemporaryDirectory() as tmp:
                deps = graph.build_deps(
                    Path(tmp), config(), runner=r3_runner(), approval=decline_terminal()
                )
                for index in order:
                    deps.discovery_log.append(
                        {
                            "event": "discovery_admitted",
                            "attempt_id": records[index]["source_attempt_id"],
                            "condition": "R3",
                            "seed": records[index]["producing_seed"],
                            "discovery": records[index],
                        }
                    )
                context = graph._r3_round_context(deps)
                assert context is not None  # static narrowing
                path = deps.layout["contexts"] / "permuted.json"
                frozen = strategy_context.freeze_snapshot(context, path)
                digests.append((frozen["digest"], path.read_bytes()))
                merged = graph._r3_merged_discoveries(deps)
                self.assertEqual(
                    [d["source_attempt_id"] for d in merged],
                    sorted(d["source_attempt_id"] for d in merged),
                )
        self.assertEqual(len({digest for digest, _ in digests}), 1)
        self.assertEqual(len({data for _, data in digests}), 1)

    def test_barrier_keeps_waiting_evidence_and_drains_to_the_pending_collector(
        self,
    ):
        # agent-01's round-1 launch resolved unresolved (retry pair);
        # agent-02 is observed but uncollected — the pending prior round
        _walk(
            self.deps(self.lineage_runner(tag="w0-")),
            decline_terminal(),
            crash=("R3", "24", "launch"),
        )
        blocked_state = state.set_current(
            state.initial_state(), condition="R3", semilla="18"
        )
        drained = graph.node_barrier_merge_r3(blocked_state, self.deps(r3_runner()))
        # the barrier keeps its explicit waiting evidence, then drains to
        # the FIRST pending (unbound) prior-round attempt instead of raising
        waiting_events = [
            r for r in self.decisions() if r["event"] == "round_barrier_waiting"
        ]
        self.assertEqual(len(waiting_events), 1)
        self.assertEqual(waiting_events[0]["round"], 2)
        self.assertEqual(
            waiting_events[0]["waiting"],
            [
                {
                    "seed": "24",
                    "attempt_id": "r3-agent-02-seed-24-attempt-01",
                    "reason": "evidence_pending",
                },
                {
                    "seed": "30",
                    "attempt_id": None,
                    "reason": "missing_prior_round",
                },
            ],
        )
        self.assertEqual(drained["status"], "barrier_drain")
        self.assertEqual((drained["condition"], drained["semilla"]), ("R3", "24"))
        self.assertEqual(
            graph.route_after_barrier(drained), "collect_and_bind_evidence"
        )
        self.assertEqual(
            [
                r
                for r in self.decisions()
                if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
            ],
            [],
        )
        self.assertNotIn("strategy-context-004.json", self.context_names())
        # the drained collector resolves the pending attempt exactly once
        bound_state = graph.node_collect_and_bind_evidence(
            drained, self.deps(r3_runner())
        )
        self.assertEqual(bound_state["status"], "evidence_collected")
        self.assertEqual(
            [
                r["attempt_id"]
                for r in self.attempts()
                if r["event"] == "evidence_bound"
                and r["condition"] == "R3"
                and r["seed"] == "24"
            ],
            ["r3-agent-02-seed-24-attempt-01"],
        )

    def test_pending_prior_round_launch_drains_deterministically(self):
        """18 unresolved/retry + 24 observed-unbound: the restart collects 24
        and the round-2 barrier eventually opens (never livelocks)."""
        cfg = config()
        runner = r3_runner(
            {"18": [("timeout", None, None), ("ok", "completed", 100)]}, tag="w1-"
        )
        _walk(self.deps(runner, cfg), decline_terminal(), crash=("R3", "24", "launch"))
        drive(state.initial_state(), self.deps(r3_runner(tag="r1-"), cfg))
        waiting = [
            r for r in self.decisions(cfg) if r["event"] == "round_barrier_waiting"
        ]
        # one waiting event carrying BOTH kinds of pending work: draining 24
        # makes it terminal, so the queue reaches 30's own round-1 barrier
        # (reuse) before any second round-2 visit can block again
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["round"], 2)
        self.assertEqual(
            waiting[0]["waiting"],
            [
                {
                    "seed": "24",
                    "attempt_id": "r3-agent-02-seed-24-attempt-01",
                    "reason": "evidence_pending",
                },
                {
                    "seed": "30",
                    "attempt_id": None,
                    "reason": "missing_prior_round",
                },
            ],
        )
        self.assertEqual(
            [
                r["attempt_id"]
                for r in self.attempts(cfg)
                if r["event"] == "evidence_bound"
                and r["condition"] == "R3"
                and r["seed"] == "24"
            ],
            ["r3-agent-02-seed-24-attempt-01"],
        )
        round_two = [
            r
            for r in self.decisions(cfg)
            if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
        ]
        self.assertEqual(len(round_two), 1)
        second = self.launch("r3-agent-01-seed-18-attempt-02", cfg)
        self.assertEqual(second["context_digest"], round_two[0]["context_digest"])
        self.assertEqual(self.row("R3", "18", cfg)["attempts_used"], 2)

    def test_registered_unobserved_pending_classifies_crash_window_and_progresses(
        self,
    ):
        """18 retry + 24 registered-unobserved: the U3 crash-window
        classification applies, the retry gets a new ordinal where
        appropriate, and the barrier still eventually progresses."""
        cfg = config()
        runner = r3_runner(
            {"18": [("timeout", None, None), ("ok", "completed", 100)]}, tag="w2-"
        )
        _walk(
            self.deps(runner, cfg), decline_terminal(), crash=("R3", "24", "register")
        )
        drive(state.initial_state(), self.deps(r3_runner(tag="r2-"), cfg))
        unresolved = {
            (r["attempt_id"], r["reason"])
            for r in self.attempts(cfg)
            if r["event"] == "evidence_unresolved"
        }
        self.assertIn(
            ("r3-agent-02-seed-24-attempt-01", "crash_window_unobserved"),
            unresolved,
        )
        self.assertTrue(
            any(
                r["event"] == "recovery"
                and r.get("attempt_id") == "r3-agent-02-seed-24-attempt-01"
                for r in self.decisions(cfg)
            )
        )
        ordinals = sorted(
            r["attempt_ordinal"]
            for r in self.attempts(cfg)
            if r["event"] == "launch_registered"
            and r["condition"] == "R3"
            and r["seed"] == "24"
        )
        self.assertEqual(ordinals, [1, 2])
        round_two = [
            r
            for r in self.decisions(cfg)
            if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
        ]
        self.assertEqual(len(round_two), 1)
        self.assertEqual(self.row("R3", "24", cfg)["attempts_used"], 2)
        # ordering contract: seed-30's round-1 attempt launches AND resolves
        # before the round-2 snapshot freezes, and no branch's attempt-2
        # registration precedes it (the fixed three-branch round contract)
        attempts = self.attempts(cfg)

        def first_index(predicate):
            return next(i for i, r in enumerate(attempts) if predicate(r))

        resolve_30 = first_index(
            lambda r: r.get("event") == "evidence_bound"
            and r.get("attempt_id") == "r3-agent-03-seed-30-attempt-01"
        )
        attempt_two = [
            i
            for i, r in enumerate(attempts)
            if r.get("event") == "launch_registered"
            and r.get("condition") == "R3"
            and r.get("attempt_ordinal") == 2
        ]
        self.assertTrue(attempt_two)
        self.assertGreater(min(attempt_two), resolve_30)

    def test_missing_prior_round_branch_drains_to_its_own_launch_flow(self):
        """18 retry + 24 registered-unobserved + 30 FRESH: a round-2 snapshot
        must never freeze without seed-30's round-1 evidence. The barrier
        blocks with ``missing_prior_round``, drains deterministically to seed
        30's own plan/launch flow, and only then opens round 2 — 30 attempt-1
        launches and resolves BEFORE any round-2 freeze and before any branch's
        attempt-2 registration."""
        cfg = config()
        runner = r3_runner({"18": [("timeout", None, None)]}, tag="b0-")
        _walk(
            self.deps(runner, cfg), decline_terminal(), crash=("R3", "24", "register")
        )
        deps2 = self.deps(r3_runner(tag="b1-"), cfg)
        original = deps2.decision_log.append
        freeze_order = []

        def append(record):
            if (
                record.get("event") == "context_snapshot_frozen"
                and record.get("condition") == "R3"
                and record.get("round") == 2
            ):
                freeze_order.append(len(deps2.attempt_log.records()))
            return original(record)

        deps2.decision_log.append = append
        drive(state.initial_state(), deps2)
        self.assertEqual(len(freeze_order), 1)
        # explicit waiting evidence carries the missing prior-round work
        waiting = [
            r for r in self.decisions(cfg) if r["event"] == "round_barrier_waiting"
        ]
        self.assertTrue(
            any(
                {
                    "seed": "30",
                    "attempt_id": None,
                    "reason": "missing_prior_round",
                }
                in w["waiting"]
                for w in waiting
            )
        )
        attempts = self.attempts(cfg)

        def first_index(predicate):
            return next(i for i, r in enumerate(attempts) if predicate(r))

        launch_30 = first_index(
            lambda r: r.get("event") == "launch_registered"
            and r.get("attempt_id") == "r3-agent-03-seed-30-attempt-01"
        )
        resolve_30 = first_index(
            lambda r: r.get("event") == "evidence_bound"
            and r.get("attempt_id") == "r3-agent-03-seed-30-attempt-01"
        )
        self.assertLess(resolve_30, freeze_order[0])
        # no attempt-2 of any branch may precede seed-30 attempt-1's resolution
        attempt_two = [
            i
            for i, r in enumerate(attempts)
            if r.get("event") == "launch_registered"
            and r.get("condition") == "R3"
            and r.get("attempt_ordinal") == 2
        ]
        self.assertTrue(attempt_two)
        self.assertGreater(min(attempt_two), resolve_30)
        self.assertGreater(min(attempt_two), launch_30)
        self.assertEqual(self.row("R3", "30", cfg)["attempts_used"], 1)
        self.assertEqual(self.row("R3", "30", cfg)["status"], "completed")
        self.assertEqual(self.row("R3", "18", cfg)["attempts_used"], 2)
        self.assertEqual(self.row("R3", "24", cfg)["attempts_used"], 2)

    def test_double_restart_never_livelocks_or_repeats_attempt_ids(self):
        cfg = config()
        runner = r3_runner(
            {"18": [("timeout", None, None), ("ok", "completed", 100)]}, tag="w3-"
        )
        _walk(self.deps(runner, cfg), decline_terminal(), crash=("R3", "24", "launch"))
        deps2 = self.deps(r3_runner(tag="r3a-"), cfg)
        original = deps2.decision_log.append

        def append(record):
            if (
                record.get("event") == "context_snapshot_frozen"
                and record.get("round") == 2
            ):
                raise _RestartCrash()
            return original(record)

        deps2.decision_log.append = append
        with self.assertRaises(_RestartCrash):
            drive(state.initial_state(), deps2)
        drive(state.initial_state(), self.deps(r3_runner(tag="r3b-"), cfg))
        registrations = [
            r["attempt_id"]
            for r in self.attempts(cfg)
            if r["event"] == "launch_registered" and r["condition"] == "R3"
        ]
        self.assertEqual(len(registrations), len(set(registrations)))
        self.assertEqual(self.row("R3", "18", cfg)["attempts_used"], 2)
        self.assertEqual(self.row("R3", "24", cfg)["attempts_used"], 1)
        self.assertEqual(self.row("R3", "30", cfg)["attempts_used"], 1)

    def test_one_identical_snapshot_per_round_for_every_branch(self):
        cfg = config()
        for attempt_id in (
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        self.full_lineage(cfg=cfg)
        round_one = [
            r
            for r in self.decisions(cfg)
            if r["event"] == "context_snapshot_frozen" and r.get("round") == 1
        ]
        round_two = [
            r
            for r in self.decisions(cfg)
            if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
        ]
        self.assertEqual(len(round_one), 1)
        self.assertEqual(len(round_two), 1)
        first_launches = [
            self.launch(f"r3-agent-0{i}-seed-{s}-attempt-01", cfg)
            for i, s in ((1, 18), (2, 24), (3, 30))
        ]
        self.assertEqual(
            {launch["context_digest"] for launch in first_launches},
            {round_one[0]["context_digest"]},
        )
        self.assertEqual(round_one[0]["context_digest"], NULL_DIGEST)
        second_launch = self.launch("r3-agent-01-seed-18-attempt-02", cfg)
        self.assertEqual(
            second_launch["context_digest"], round_two[0]["context_digest"]
        )
        self.assertNotEqual(round_two[0]["context_digest"], NULL_DIGEST)
        self.assertEqual(round_two[0]["source"], "r3-merge")


class TestDiscoveryAdmission(Base):
    """Only finished receipt-bound runs admit candidates; duplicates resolve by
    declared identity; the mirror contract rejects malformed candidates."""

    def test_only_bound_finished_runs_are_admitted(self):
        # candidates pre-written for ALL round-1 attempts, including the
        # timed-out agent-01 attempt that never binds
        deps = self.deps(self.lineage_runner())
        for attempt_id in (
            "r3-agent-01-seed-18-attempt-01",
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id)
        drive(state.initial_state(), deps)
        sources = {d["source_attempt_id"] for d in self.admitted()}
        self.assertEqual(
            sources,
            {
                "r3-agent-02-seed-24-attempt-01",
                "r3-agent-03-seed-30-attempt-01",
            },
        )

    def test_equal_duplicate_identity_admits_once(self):
        twin = {
            "schema_version": 1,
            "discoveries": [
                {
                    "explorer_id": "explorer-01",
                    "discovery_ordinal": 0,
                    "text": "same text",
                },
                {
                    "explorer_id": "explorer-01",
                    "discovery_ordinal": 0,
                    "text": "same text",
                },
            ],
        }
        cfg = config()
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg, twin)
        drive(state.initial_state(), self.deps(self.lineage_runner(), cfg))
        twins = [
            d
            for d in self.admitted()
            if d["explorer_id"] == "explorer-01"
            and d["source_attempt_id"] == "r3-agent-02-seed-24-attempt-01"
        ]
        self.assertEqual(len(twins), 1)
        self.assertEqual(twins[0]["text"], "same text")

    def test_conflicting_reseal_fails_closed_and_first_seal_wins(self):
        """The sealed record is immutable: a re-seal carrying different
        candidate content for the same attempt fails closed and the first
        seal stands; an identical re-seal is an idempotent skip."""
        cfg = config()
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg)
        self.full_lineage(cfg=cfg)
        deps = self.deps(r3_runner(), cfg)
        launch = next(
            r
            for r in self.attempts(cfg)
            if r["event"] == "launch_registered"
            and r["attempt_id"] == "r3-agent-02-seed-24-attempt-01"
        )
        bound = next(
            r
            for r in self.attempts(cfg)
            if r["event"] == "evidence_bound"
            and r["attempt_id"] == "r3-agent-02-seed-24-attempt-01"
        )
        result = SimpleNamespace(
            core_receipt_digest=bound["core_receipt_digest"],
            llm_receipt_digest=bound["llm_receipt_digest"],
        )

        def prepared():
            return [
                r
                for r in self.records("discoveries.jsonl", cfg)
                if r["event"] == "discovery_prepared"
            ]

        graph._seal_discoveries(deps, launch, result)
        self.assertEqual(len(prepared()), 1)
        self.write_candidates(
            "r3-agent-02-seed-24-attempt-01",
            cfg,
            {
                "schema_version": 1,
                "discoveries": [
                    {
                        "explorer_id": "explorer-01",
                        "discovery_ordinal": 0,
                        "text": "tampered text",
                    }
                ],
            },
        )
        with self.assertRaises(graph.HarnessGraphError):
            graph._seal_discoveries(deps, launch, result)
        self.assertEqual(len(prepared()), 1)
        self.assertEqual(len(prepared()[0]["discoveries"]), 2)

    def test_malformed_candidates_fail_closed(self):
        bad_payloads = (
            b"{not json",
            b"[]",
            b'{"schema_version": 2, "discoveries": []}',
            b'{"schema_version": 1}',
            b'{"schema_version": 1, "discoveries": {}}',
            b'{"schema_version": 1, "discoveries": [{"explorer_id": "e"}]}',
            b'{"schema_version": 1, "discoveries": [{"explorer_id": "e",'
            b' "discovery_ordinal": true, "text": "t"}]}',
            b'{"schema_version": 1, "discoveries": [{"explorer_id": "",'
            b' "discovery_ordinal": 0, "text": "t"}]}',
            b'{"schema_version": 1, "discoveries": [{"explorer_id": "e",'
            b' "discovery_ordinal": 0, "text": ""}]}',
            b'{"schema_version": 1, "discoveries": [{"explorer_id": "e",'
            b' "discovery_ordinal": 0, "text": "t", "score": 1}]}',
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload[:48]):
                with tempfile.TemporaryDirectory() as tmp:
                    deps = graph.build_deps(
                        Path(tmp),
                        config(),
                        runner=r3_runner(),
                        approval=decline_terminal(),
                    )
                    attempt_dir = (
                        deps.tournament_root
                        / "attempts"
                        / ("r3-agent-02-seed-24-attempt-01")
                    )
                    attempt_dir.mkdir(parents=True)
                    (attempt_dir / "discoveries.json").write_bytes(payload)
                    with self.assertRaises(graph.HarnessGraphError):
                        graph._load_candidates(deps, "r3-agent-02-seed-24-attempt-01")

    def test_sealed_bytes_win_over_a_mutated_candidate_channel(self):
        """A mutated candidate artifact for a BOUND attempt is inert: the
        record sealed before the bind is immutable, the mutable channel is
        never re-read after the bind, and admission is unaffected."""
        cfg = config()
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg)
        self.full_lineage(cfg=cfg)
        self.assertEqual(len(self.admitted(cfg)), 2)
        self.write_candidates(
            "r3-agent-02-seed-24-attempt-01",
            cfg,
            b'{"schema_version": 1, "discoveries": [{"explorer_id": "e"}]}',
        )
        self.assertEqual(graph.record_discoveries(self.deps(r3_runner(), cfg)), 0)
        self.assertEqual(len(self.admitted(cfg)), 2)
        self.assertEqual(
            [d["text"] for d in self.admitted(cfg)],
            [
                "The north corridor gate opens only after the blue switch flips",
                "Carrying two boxes past the ramp stalls the walker",
            ],
        )

    def test_all_round_one_discoveries_admit_exactly_once_without_round_two(
        self,
    ):
        """All-terminal round 1: every receipt-bound discovery is admitted
        exactly once and NO round-2 snapshot is ever required."""
        cfg = config()
        for attempt_id in (
            "r3-agent-01-seed-18-attempt-01",
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        drive(state.initial_state(), self.deps(r3_runner(tag="a1-"), cfg))
        identities = [
            (d["source_attempt_id"], d["explorer_id"], d["discovery_ordinal"])
            for d in self.admitted(cfg)
        ]
        expected = [
            (f"r3-agent-0{agent}-seed-{seed}-attempt-01", explorer, ordinal)
            for agent, seed in ((1, 18), (2, 24), (3, 30))
            for explorer, ordinal in (("explorer-01", 0), ("explorer-02", 1))
        ]
        self.assertEqual(sorted(identities), sorted(expected))
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(
            [
                r
                for r in self.decisions(cfg)
                if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
            ],
            [],
        )
        self.assertEqual(
            self.context_names(cfg),
            [
                "strategy-context-000.json",
                "strategy-context-001.json",
                "strategy-context-002.json",
                "strategy-context-003.json",
            ],
        )

    def test_final_round_discovery_persists_without_any_later_launch(self):
        """A terminal final-round attempt's discoveries reach
        discoveries.jsonl even though no later round ever runs."""
        cfg = config()
        self.write_candidates("r3-agent-01-seed-18-attempt-02", cfg)
        self.full_lineage(cfg=cfg)
        identities = [
            (d["source_attempt_id"], d["explorer_id"])
            for d in self.admitted(cfg)
            if d["source_attempt_id"] == "r3-agent-01-seed-18-attempt-02"
        ]
        self.assertEqual(
            identities,
            [
                ("r3-agent-01-seed-18-attempt-02", "explorer-01"),
                ("r3-agent-01-seed-18-attempt-02", "explorer-02"),
            ],
        )
        # the terminal launch never saw its own discovery: the round-2
        # snapshot predates the attempt-02 admission
        self.assertEqual(self.row("R3", "18", cfg)["attempts_used"], 2)

    def test_post_bind_candidate_changes_never_enter_any_snapshot(self):
        """Candidate content changes for a BOUND attempt after the bind are
        inert: the record sealed before the bind won, nothing new is admitted,
        and no frozen snapshot can change."""
        cfg = config()
        for attempt_id in (
            "r3-agent-01-seed-18-attempt-02",
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        self.full_lineage(cfg=cfg)
        round_two_before = self.snapshot(4, cfg)
        round_one_before = self.snapshot(3, cfg)
        # a NEW valid candidate identity appears for a bound attempt AFTER
        # the tournament completed: the sealed bytes won at bind time, so the
        # late identity is never admitted and the snapshots never change
        extra = {
            "schema_version": 1,
            "discoveries": [
                {
                    "explorer_id": "explorer-01",
                    "discovery_ordinal": 0,
                    "text": "The north corridor gate opens only after the blue "
                    "switch flips",
                },
                {
                    "explorer_id": "explorer-02",
                    "discovery_ordinal": 1,
                    "text": "Carrying two boxes past the ramp stalls the walker",
                },
                {
                    "explorer_id": "explorer-03",
                    "discovery_ordinal": 2,
                    "text": "A late candidate discovered after the final freeze",
                },
            ],
        }
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg, extra)
        self.assertEqual(graph.record_discoveries(self.deps(r3_runner(), cfg)), 0)
        self.assertEqual(
            [d for d in self.admitted(cfg) if d["explorer_id"] == "explorer-03"],
            [],
        )
        self.assertEqual(self.snapshot(4, cfg), round_two_before)
        self.assertEqual(self.snapshot(3, cfg), round_one_before)

    def test_unregistered_attempt_files_are_never_read(self):
        cfg = config()
        self.write_candidates("r3-agent-99-seed-99-attempt-01", cfg)
        for attempt_id in (
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        self.full_lineage(cfg=cfg)
        self.assertEqual(
            {d["source_attempt_id"] for d in self.admitted()},
            {
                "r3-agent-02-seed-24-attempt-01",
                "r3-agent-03-seed-30-attempt-01",
            },
        )


class TestSnapshotLineage(Base):
    """Task 3: merged discoveries with typed digests reach the next round's
    frozen snapshot; launches record the exact path/digest/ordinal; an
    in-flight run never sees its own same-round discovery."""

    def test_round_two_snapshot_carries_typed_receipt_bound_discoveries(self):
        cfg = config()
        for attempt_id in (
            "r3-agent-01-seed-18-attempt-01",
            "r3-agent-01-seed-18-attempt-02",
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        self.full_lineage(cfg=cfg)
        data = self.snapshot(4, cfg)
        parsed = json.loads(data)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertEqual(parsed["source_tournament_id"], cfg["tournament_id"])
        self.assertEqual(parsed["guidance"], [])
        bound = {
            r["attempt_id"]: r
            for r in self.attempts(cfg)
            if r["event"] == "evidence_bound" and r["condition"] == "R3"
        }
        expected_identities = [
            ("r3-agent-02-seed-24-attempt-01", "explorer-01", 0),
            ("r3-agent-02-seed-24-attempt-01", "explorer-02", 1),
            ("r3-agent-03-seed-30-attempt-01", "explorer-01", 0),
            ("r3-agent-03-seed-30-attempt-01", "explorer-02", 1),
        ]
        self.assertEqual(
            [
                (
                    d["source_attempt_id"],
                    d["explorer_id"],
                    d["discovery_ordinal"],
                )
                for d in parsed["discoveries"]
            ],
            expected_identities,
        )
        for discovery in parsed["discoveries"]:
            attempt = bound[discovery["source_attempt_id"]]
            self.assertEqual(discovery["producing_condition"], "R3")
            self.assertGreaterEqual(discovery["producing_attempt_ordinal"], 1)
            self.assertEqual(discovery["evidence"]["run_status"], "finished")
            self.assertEqual(
                discovery["evidence"]["core_receipt_digest"],
                attempt["core_receipt_digest"],
            )
            self.assertEqual(
                discovery["evidence"]["llm_receipt_digest"],
                attempt["llm_receipt_digest"],
            )
        # no branch sees its own same-round discovery: the frozen snapshot
        # predates every round-2 launch and contains no agent-01 record at all
        self.assertNotIn(
            "r3-agent-01-seed-18-attempt-02",
            [d["source_attempt_id"] for d in parsed["discoveries"]],
        )
        second = self.launch("r3-agent-01-seed-18-attempt-02", cfg)
        self.assertEqual(second["context_digest"], strategy_context.sha256_digest(data))
        self.assertTrue(second["context_path"].endswith("strategy-context-004.json"))
        registration = next(
            r
            for r in self.attempts(cfg)
            if r["event"] == "launch_registered"
            and r["attempt_id"] == "r3-agent-01-seed-18-attempt-02"
        )
        self.assertEqual(registration["snapshot_ordinal"], 4)

    def test_row_lineage_is_exposed_separately_from_receipt_score(self):
        cfg = config()
        for attempt_id in (
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        self.full_lineage(cfg=cfg)
        row_18 = self.row("R3", "18", cfg)
        row_24 = self.row("R3", "24", cfg)
        # authority hierarchy: the terminal receipt owns outcome/score, but the
        # unresolved round-1 timeout keeps agent-01's row status evidence_invalid
        self.assertEqual(row_18["verified_score"], 100)
        self.assertEqual(row_18["outcome"], "completed")
        self.assertEqual(row_18["status"], "evidence_invalid")
        self.assertIsNone(row_18["input_tokens"])
        self.assertEqual(row_24["verified_score"], 100)
        self.assertEqual(row_24["status"], "completed")
        self.assertEqual(row_24["attempts_used"], 1)
        # context lineage is a separate column: agent-01's terminal launch used
        # the merged round-2 snapshot; agent-24's used the round-1 null
        self.assertEqual(
            row_18["strategy_context_digest"],
            strategy_context.sha256_digest(self.snapshot(4, cfg)),
        )
        self.assertEqual(row_24["strategy_context_digest"], NULL_DIGEST)
        self.assertNotEqual(
            row_18["strategy_context_digest"], row_24["strategy_context_digest"]
        )


class TestR3Restart(Base):
    """Crash windows around the round barrier reconstruct deterministically:
    no duplicate snapshot, no lost discoveries, no duplicate admission."""

    def test_crash_between_freeze_and_event_rewrites_identical_bytes(self):
        cfg = config()
        for attempt_id in (
            "r3-agent-01-seed-18-attempt-02",
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        runner = self.lineage_runner()
        deps = self.deps(runner, cfg)
        original = deps.decision_log.append

        def append(record):
            if (
                record.get("event") == "context_snapshot_frozen"
                and record.get("condition") == "R3"
                and record.get("round") == 2
            ):
                raise _CrashBeforeRoundFreezeEvent()
            return original(record)

        deps.decision_log.append = append
        with self.assertRaises(_CrashBeforeRoundFreezeEvent):
            drive(state.initial_state(), deps)
        pre_crash = self.snapshot(4, cfg)
        frozen_events = [
            r
            for r in self.decisions(cfg)
            if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
        ]
        self.assertEqual(frozen_events, [])
        # restart: deterministic reconstruction, no duplicate snapshot
        drive(
            state.initial_state(),
            self.deps(r3_runner(tag="r2-"), cfg),
        )
        self.assertEqual(self.snapshot(4, cfg), pre_crash)
        self.assertEqual(
            self.context_names(),
            [
                "strategy-context-000.json",
                "strategy-context-001.json",
                "strategy-context-002.json",
                "strategy-context-003.json",
                "strategy-context-004.json",
            ],
        )
        identities = [
            (
                d["source_attempt_id"],
                d["explorer_id"],
                d["discovery_ordinal"],
            )
            for d in self.admitted(cfg)
        ]
        # two explorers per candidate file: two bound round-1 attempts plus
        # the restarted round-2 attempt-02 — six identity-unique records,
        # none duplicated, and the final-round records persist
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(len(identities), 6)
        second = self.launch("r3-agent-01-seed-18-attempt-02", cfg)
        self.assertEqual(
            second["context_digest"], strategy_context.sha256_digest(pre_crash)
        )
        row = self.row("R3", "18", cfg)
        # the pre-crash timeout keeps the row status evidence_invalid while the
        # restarted attempt-02 binds the receipt-owned completed outcome
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertEqual(row["outcome"], "completed")
        self.assertEqual(row["attempts_used"], 2)

    def test_crash_after_the_frozen_event_reuses_the_snapshot(self):
        cfg = config()
        self.write_candidates("r3-agent-01-seed-18-attempt-02", cfg)
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg)
        self.write_candidates("r3-agent-03-seed-30-attempt-01", cfg)
        runner = self.lineage_runner()
        deps = self.deps(runner, cfg)
        original = deps.decision_log.append

        def append(record):
            stored = original(record)
            if (
                record.get("event") == "context_snapshot_frozen"
                and record.get("condition") == "R3"
                and record.get("round") == 2
            ):
                raise _CrashAfterRoundFreezeEvent()
            return stored

        deps.decision_log.append = append
        with self.assertRaises(_CrashAfterRoundFreezeEvent):
            drive(state.initial_state(), deps)
        names_before = self.context_names(cfg)
        events_before = [
            r
            for r in self.decisions(cfg)
            if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
        ]
        self.assertEqual(len(events_before), 1)
        drive(
            state.initial_state(),
            self.deps(r3_runner(tag="r3-"), cfg),
        )
        # the round-2 snapshot is reused: no new file, no duplicated event
        self.assertEqual(self.context_names(cfg), names_before)
        events_after = [
            r
            for r in self.decisions(cfg)
            if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
        ]
        self.assertEqual(len(events_after), 1)
        self.assertEqual(
            events_after[0]["context_digest"], events_before[0]["context_digest"]
        )
        second = self.launch("r3-agent-01-seed-18-attempt-02", cfg)
        self.assertEqual(second["context_digest"], events_before[0]["context_digest"])
        self.assertEqual(self.row("R3", "18", cfg)["attempts_used"], 2)


class TestFrozenSnapshotOverwrite(Base):
    """The freeze crash window reconstructs deterministically: identical bytes
    are reused (the missing frozen event is re-appended). With the write-ahead
    admission seam, candidate mutations between the crash and the restart can
    no longer change the would-be frozen bytes at all — the sealed records
    reproduce the identical snapshot."""

    def test_restart_after_post_crash_candidate_mutation_is_byte_identical(self):
        cfg = config()
        for attempt_id in (
            "r3-agent-01-seed-18-attempt-02",
            "r3-agent-02-seed-24-attempt-01",
            "r3-agent-03-seed-30-attempt-01",
        ):
            self.write_candidates(attempt_id, cfg)
        runner = self.lineage_runner()
        deps = self.deps(runner, cfg)
        original = deps.decision_log.append

        def append(record):
            if (
                record.get("event") == "context_snapshot_frozen"
                and record.get("condition") == "R3"
                and record.get("round") == 2
            ):
                raise _CrashBeforeRoundFreezeEvent()
            return original(record)

        deps.decision_log.append = append
        with self.assertRaises(_CrashBeforeRoundFreezeEvent):
            drive(state.initial_state(), deps)
        pre_crash = self.snapshot(4, cfg)
        # a NEW valid candidate identity is admitted after the crash
        extra = {
            "schema_version": 1,
            "discoveries": [
                {
                    "explorer_id": "explorer-01",
                    "discovery_ordinal": 0,
                    "text": "The north corridor gate opens only after the blue "
                    "switch flips",
                },
                {
                    "explorer_id": "explorer-02",
                    "discovery_ordinal": 1,
                    "text": "Carrying two boxes past the ramp stalls the walker",
                },
                {
                    "explorer_id": "explorer-03",
                    "discovery_ordinal": 2,
                    "text": "Discovered between the freeze and the restart",
                },
            ],
        }
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg, extra)
        # the restart composes from the SEALED records: the mutated identity
        # never enters, the identical bytes are reused, and the restart completes
        drive(state.initial_state(), self.deps(r3_runner(tag="o1-"), cfg))
        self.assertEqual(self.snapshot(4, cfg), pre_crash)
        self.assertEqual(
            [d for d in self.admitted(cfg) if d["explorer_id"] == "explorer-03"],
            [],
        )
        self.assertEqual(
            len(
                [
                    r
                    for r in self.decisions(cfg)
                    if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
                ]
            ),
            1,
        )


class TestWriteAheadAdmission(Base):
    """PR #24 review round 2, blocker 2: the write-ahead admission seam.
    Candidates are validated once and sealed into the append-only discovery
    log BEFORE the terminal evidence_bound append, so a crash in the
    bind→admit window promotes exactly once from the sealed bytes on restart —
    the mutable discoveries.json is never re-read after the bind, the attempt
    never reruns, and the next-round snapshot is byte-identical to the
    no-crash control."""

    def test_crash_after_bind_before_admission_promotes_sealed_bytes(self):
        cfg = config()
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg)
        deps = self.deps(self.lineage_runner(), cfg)
        original = deps.discovery_log.append

        def append(record):
            if (
                record.get("event") == "discovery_admitted"
                and record.get("attempt_id") == "r3-agent-02-seed-24-attempt-01"
            ):
                raise _CrashBeforeAdmission()
            return original(record)

        deps.discovery_log.append = append
        with self.assertRaises(_CrashBeforeAdmission):
            drive(state.initial_state(), deps)
        # crash window reached exactly: sealed + bound, never admitted
        prepared = [
            r
            for r in self.records("discoveries.jsonl", cfg)
            if r["event"] == "discovery_prepared"
            and r["attempt_id"] == "r3-agent-02-seed-24-attempt-01"
        ]
        self.assertEqual(len(prepared), 1)
        self.assertEqual(len(prepared[0]["discoveries"]), 2)
        self.assertEqual(
            [
                r
                for r in self.records("discoveries.jsonl", cfg)
                if r["event"] == "discovery_admitted"
            ],
            [],
        )
        self.assertEqual(
            [
                r["attempt_id"]
                for r in self.attempts(cfg)
                if r["event"] == "evidence_bound" and r["condition"] == "R3"
            ],
            # the crash fires during seed-24's bind-time promotion, so only
            # seed-24 is bound: 18 timed out and 30 never launched yet
            ["r3-agent-02-seed-24-attempt-01"],
        )
        # the no-crash control run in a separate root (identical config,
        # candidates, and runner scripts)
        with tempfile.TemporaryDirectory() as control_dir:
            control_layout = tournament.expected_layout(
                Path(control_dir), cfg["tournament_id"], cfg["artifact_root"]
            )
            # the control is seeded IDENTICALLY: same candidates in its own
            # root before the drive seals them at bind time
            control_candidates = (
                control_layout["attempts"]
                / "r3-agent-02-seed-24-attempt-01"
                / "discoveries.json"
            )
            control_candidates.parent.mkdir(parents=True, exist_ok=True)
            strategy_context.atomic_write_bytes(
                control_candidates,
                strategy_context.canonical_json_bytes(DISCOVERIES_FIXTURE),
            )
            control_deps = graph.build_deps(
                Path(control_dir),
                cfg,
                runner=self.lineage_runner(),
                approval=decline_terminal(),
            )
            drive(state.initial_state(), control_deps)
            # restart: the sealed record promotes exactly once, the attempt
            # never reruns, and the round-2 snapshot contains the discovery
            drive(state.initial_state(), self.deps(r3_runner(tag="wa1-"), cfg))
            registrations = [
                r["attempt_id"]
                for r in self.attempts(cfg)
                if r["event"] == "launch_registered"
                and r["condition"] == "R3"
                and r["seed"] == "24"
            ]
            self.assertEqual(registrations, ["r3-agent-02-seed-24-attempt-01"])
            recovered = [
                r
                for r in self.records("discoveries.jsonl", cfg)
                if r["event"] == "discovery_admitted"
                and r["attempt_id"] == "r3-agent-02-seed-24-attempt-01"
            ]
            self.assertEqual(
                [r["discovery"]["explorer_id"] for r in recovered],
                ["explorer-01", "explorer-02"],
            )
            round_two = next(
                r
                for r in self.decisions(cfg)
                if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
            )
            snapshot = self.snapshot(round_two["snapshot_ordinal"], cfg)
            self.assertIn("r3-agent-02-seed-24-attempt-01", snapshot.decode())
            control_snapshot = (
                control_layout["contexts"]
                / f"strategy-context-{round_two['snapshot_ordinal']:03d}.json"
            ).read_bytes()
            self.assertEqual(snapshot, control_snapshot)
            # a second restart adds no duplicate admission
            before = len(self.records("discoveries.jsonl", cfg))
            drive(state.initial_state(), self.deps(r3_runner(tag="wa2-"), cfg))
            self.assertEqual(len(self.records("discoveries.jsonl", cfg)), before)
            self.assertEqual(
                len(
                    [
                        r
                        for r in self.records("discoveries.jsonl", cfg)
                        if r["event"] == "discovery_admitted"
                        and r["attempt_id"] == "r3-agent-02-seed-24-attempt-01"
                    ]
                ),
                2,
            )


class TestR3Coexistence(Base):
    """R3 wiring leaves R1/R2 behavior intact and the whole tournament
    deterministic across identical tournaments."""

    def test_identical_tournaments_produce_byte_identical_rows(self):
        cfg_a = config("tournament-unit5-r3-a")
        cfg_b = config("tournament-unit5-r3-b")
        drive(state.initial_state(), self.deps(r3_runner(tag="co-"), cfg_a))
        drive(
            state.initial_state(),
            self.deps(r3_runner(tag="co-"), cfg_b),
        )
        self.assertEqual(len(self.rows(cfg_a)), 9)
        self.assertEqual(
            strategy_context.canonical_json_bytes(self.rows(cfg_a)),
            strategy_context.canonical_json_bytes(self.rows(cfg_b)),
        )
        # every condition landed exactly one row per seed
        self.assertEqual(
            sorted((r["condition"], r["seed"]) for r in self.rows(cfg_a)),
            [(c, s) for c in ("R1", "R2", "R3") for s in ("18", "24", "30")],
        )
        # R3 completed: one round per branch, receipt-owned scores
        for seed in ("18", "24", "30"):
            row = self.row("R3", seed, cfg_a)
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["verified_score"], 100)
            self.assertEqual(row["attempts_used"], 1)

    def test_r1_guidance_does_not_disturb_r2_or_r3_rows(self):
        cfg_yes = config("tournament-unit5-r3-yes")
        cfg_no = config("tournament-unit5-r3-no")
        guidance = "Probe the north corridor before extracting"
        drive(
            state.initial_state(),
            graph.build_deps(
                self.root,
                cfg_yes,
                runner=r3_runner(tag="c-", r3_from=6),
                approval=scripted_terminal(
                    [{"decision": "edit", "guidance": [guidance]}, {"decision": "yes"}]
                ),
            ),
        )
        drive(
            state.initial_state(),
            graph.build_deps(
                self.root,
                cfg_no,
                runner=r3_runner(tag="c-"),
                approval=decline_terminal(),
            ),
        )

        def rows_for(cfg, condition):
            return [r for r in self.rows(cfg) if r["condition"] == condition]

        self.assertEqual(
            strategy_context.canonical_json_bytes(rows_for(cfg_yes, "R2")),
            strategy_context.canonical_json_bytes(rows_for(cfg_no, "R2")),
        )
        self.assertEqual(
            strategy_context.canonical_json_bytes(rows_for(cfg_yes, "R3")),
            strategy_context.canonical_json_bytes(rows_for(cfg_no, "R3")),
        )
        # the R1 side genuinely differs: a granted guidance launch vs a decline
        self.assertEqual(self.row("R1", "18", cfg_yes)["attempts_used"], 1)
        self.assertEqual(self.row("R1", "18", cfg_no)["status"], "declined")

    def test_snapshot_ordinal_shift_alone_never_changes_identity(self):
        cfg = config()
        self.full_lineage(cfg=cfg)
        # round-1 null snapshots: R2 pairs at ordinals 000-002, R3 at 003 —
        # same canonical bytes, therefore one digest
        self.assertEqual(self.snapshot(0, cfg), b"null")
        self.assertEqual(self.snapshot(3, cfg), b"null")
        self.assertEqual(
            strategy_context.sha256_digest(self.snapshot(0, cfg)), NULL_DIGEST
        )
        self.assertEqual(
            strategy_context.sha256_digest(self.snapshot(3, cfg)), NULL_DIGEST
        )


class _RestartCrash(Exception):
    """Simulated process death during a restart drive."""


class _CrashBeforeRoundFreezeEvent(Exception):
    """Simulated process death between the snapshot write and its event."""


class _CrashAfterRoundFreezeEvent(Exception):
    """Simulated process death right after the round's frozen event."""


class _CrashBeforeAdmission(Exception):
    """Simulated process death between the evidence_bound append and the
    discovery_admitted promotion."""


@unittest.skipUnless(
    LANGGRAPH_AVAILABLE, "optional harness extra (langgraph) not installed"
)
class TestGraphCompile(Base):
    def test_build_graph_compiles_with_r3_barrier(self):
        compiled = graph.build_graph(self.deps(r3_runner()))
        self.assertTrue(hasattr(compiled, "invoke"))


if __name__ == "__main__":
    unittest.main()
