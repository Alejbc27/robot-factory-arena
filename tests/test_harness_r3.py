"""Unit 5 R3 tests (design §6): exactly three fixed seed branches with round
barriers. The merge is deterministic — ordered by declared identity
``(source_attempt_id, explorer_id, discovery_ordinal)``, never by completion
timing — with no LLM coordinator, no ranking, no deduplication by content, and
no semantic filtering. The barrier runs only when every contributing branch
round is resolved; a non-terminal pair blocks it with an explicit waiting
event, never a silent skip. Only finished receipt-bound runs admit candidate
discoveries, and the same merged DiscoverySnapshot is frozen once per round for
every branch's next launch, so an in-flight run can never see its own
same-round discovery. Restarts reconstruct deterministically from the
append-only logs under the Unit-3 pair-state contracts.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

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
            for function, name in steps_by_route[plan_route]:
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

    def test_non_terminal_pair_blocks_the_merge_with_explicit_waiting(self):
        # agent-01 completes round 1; agent-02 is observed but uncollected
        _walk(
            self.deps(r3_runner(tag="w0-"), tournament_id="tournament-unit5-r3"),
            decline_terminal(),
            crash=("R3", "24", "launch"),
        )
        blocked_state = state.set_current(
            state.initial_state(), condition="R3", semilla="18"
        )
        with self.assertRaises(graph.HarnessGraphError) as raised:
            graph.node_barrier_merge_r3(blocked_state, self.deps(r3_runner()))
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
                }
            ],
        )
        self.assertIn("r3-agent-02-seed-24-attempt-01", str(raised.exception))
        self.assertEqual(
            [
                r
                for r in self.decisions()
                if r["event"] == "context_snapshot_frozen" and r.get("round") == 2
            ],
            [],
        )
        self.assertNotIn("strategy-context-004.json", self.context_names())

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

    def test_equal_duplicate_identity_admits_once_and_conflict_fails_closed(self):
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
        # restart with the same identity now carrying DIFFERENT text
        conflict = {
            "schema_version": 1,
            "discoveries": [
                {
                    "explorer_id": "explorer-01",
                    "discovery_ordinal": 0,
                    "text": "tampered text",
                }
            ],
        }
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg, conflict)
        with self.assertRaises(graph.HarnessGraphError):
            graph.record_discoveries(self.deps(r3_runner(), cfg))
        twins = [
            d
            for d in self.admitted()
            if d["explorer_id"] == "explorer-01"
            and d["source_attempt_id"] == "r3-agent-02-seed-24-attempt-01"
        ]
        self.assertEqual(len(twins), 1)
        self.assertEqual(twins[0]["text"], "same text")

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

    def test_tampered_bound_candidate_fails_closed_end_to_end(self):
        cfg = config()
        self.write_candidates("r3-agent-02-seed-24-attempt-01", cfg)
        self.full_lineage(cfg=cfg)
        self.assertEqual(len(self.admitted(cfg)), 2)
        # a tampered candidate artifact for a BOUND attempt fails the restart
        # sweep closed instead of being silently skipped
        self.write_candidates(
            "r3-agent-02-seed-24-attempt-01",
            cfg,
            b'{"schema_version": 1, "discoveries": [{"explorer_id": "e"}]}',
        )
        with self.assertRaises(graph.HarnessGraphError):
            graph.record_discoveries(self.deps(r3_runner(), cfg))
        self.assertEqual(len(self.admitted(cfg)), 2)
        self.assertEqual(
            [d["text"] for d in self.admitted(cfg)],
            [
                "The north corridor gate opens only after the blue switch flips",
                "Carrying two boxes past the ramp stalls the walker",
            ],
        )

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
        # two explorers per candidate file, two bound round-1 attempts: four
        # identity-unique records, none duplicated by the restart re-sweep
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(len(identities), 4)
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


class _CrashBeforeRoundFreezeEvent(Exception):
    """Simulated process death between the snapshot write and its event."""


class _CrashAfterRoundFreezeEvent(Exception):
    """Simulated process death right after the round's frozen event."""


@unittest.skipUnless(
    LANGGRAPH_AVAILABLE, "optional harness extra (langgraph) not installed"
)
class TestGraphCompile(Base):
    def test_build_graph_compiles_with_r3_barrier(self):
        compiled = graph.build_graph(self.deps(r3_runner()))
        self.assertTrue(hasattr(compiled, "invoke"))


if __name__ == "__main__":
    unittest.main()
