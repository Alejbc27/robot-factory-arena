"""Unit 2 foundation tests: tournament freeze, state, logs, checkpoints,
subprocess adapter, artifact layout, and the StrategyContext digest chain.

The mandatory acceptance binds: canonical StrategyContext -> SHA-256 ->
strategy_context_digest -> CLI reported digest -> policy_digest ->
logical_run_id / receipt.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness import (
    checkpointing,
    logs,
    state,
    strategy_context,
    subprocess_adapter,
    tournament,
    versions,
)

BASE_CONFIG = {
    "schema_version": 1,
    "tournament_id": "tournament-unit2",
    "harness_version": versions.HARNESS_VERSION,
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

SAMPLE_CONTEXT = {
    "schema_version": 1,
    "source_tournament_id": "tournament-unit2",
    "guidance": [],
    "discoveries": [
        {
            "source_attempt_id": "r3-agent-01-seed-18-attempt-01",
            "explorer_id": "explorer-01",
            "discovery_ordinal": 0,
            "producing_condition": "R3",
            "producing_agent_id": "agent-01",
            "producing_seed": "18",
            "producing_attempt_ordinal": 1,
            "evidence": {
                "run_status": "finished",
                "core_receipt_digest": "sha256:" + "a" * 64,
                "llm_receipt_digest": "sha256:" + "b" * 64,
            },
            "text": "move order beats spiral",
        }
    ],
}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class TestTournamentFreeze(Base):
    def test_seal_writes_canonical_config_and_rejects_reseal(self):
        info = tournament.seal(self.root, BASE_CONFIG)
        raw = info["path"].read_bytes()
        self.assertNotIn(b"\n", raw)
        self.assertEqual(
            info["config_digest"],
            strategy_context.sha256_digest(raw),
        )
        with self.assertRaises(tournament.TournamentError):
            tournament.seal(self.root, BASE_CONFIG)

    def test_rejects_drift_and_unknown_core_version(self):
        tournament.seal(self.root, BASE_CONFIG)
        drifted = dict(BASE_CONFIG, k_attempts=4)
        with self.assertRaises(tournament.TournamentError):
            tournament.reject_drift(BASE_CONFIG, drifted)
        unknown = dict(BASE_CONFIG, core_version="99")
        with self.assertRaises(tournament.TournamentError):
            tournament.validate_config(unknown)
        for field in ("seeds", "conditions", "provider", "model", "budget"):
            bad = dict(BASE_CONFIG)
            bad[field] = [30, 24, 18] if field == "seeds" else None
            with self.assertRaises(tournament.TournamentError):
                tournament.validate_config(bad)

    def test_expected_layout_covers_design_tree(self):
        layout = tournament.expected_layout(self.root, "tournament-unit2")
        for name in (
            "config.json",
            "contexts",
            "checkpoints",
            "decision-log.jsonl",
            "attempt-log.jsonl",
            "discoveries.jsonl",
            "leaderboard.json",
            "attempts",
            "core",
        ):
            self.assertIn(name, layout)


class TestStateReducers(Base):
    def test_append_reducers_are_pure_and_append_only(self):
        st = state.initial_state()
        first = state.append_decision_log(st, {"seq": 0, "event": "proposed"})
        second = state.append_historial(first, {"seq": 0, "event": "launched"})
        self.assertEqual(st["decision_log"], [])
        self.assertEqual(len(first["decision_log"]), 1)
        self.assertEqual(len(second["historial"]), 1)
        third = state.append_descubrimientos(
            second,
            {
                "source_attempt_id": "a",
                "explorer_id": "e",
                "discovery_ordinal": 0,
            },
        )
        self.assertEqual(len(third["descubrimientos"]), 1)
        self.assertEqual(len(third["historial"]), 1)

    def test_replace_fields(self):
        st = state.set_current(
            state.initial_state(),
            intentos=1,
            semilla="18",
            condition="R3",
            agent_id="agent-01",
            context_snapshot_ref={"digest": "sha256:" + "c" * 64},
            status="launching",
        )
        self.assertEqual(
            (st["intentos"], st["semilla"], st["status"]),
            (1, "18", "launching"),
        )

    def test_deterministic_ordering_ignores_arrival_timing(self):
        st = state.initial_state()
        for sid, oid in (("b", 0), ("a", 1), ("a", 0)):
            st = state.append_descubrimientos(
                st,
                {
                    "source_attempt_id": sid,
                    "explorer_id": "e",
                    "discovery_ordinal": oid,
                },
            )
        ordered = state.ordered_records(st, "descubrimientos")
        self.assertEqual(
            [(r["source_attempt_id"], r["discovery_ordinal"]) for r in ordered],
            [("a", 0), ("a", 1), ("b", 0)],
        )


class TestAppendOnlyLogs(Base):
    def test_append_verify_roundtrip_and_integrity(self):
        log = logs.AppendOnlyLog(self.root / "attempt-log.jsonl")
        first = log.append({"event": "a"})
        second = log.append({"event": "b"})
        self.assertEqual((first["seq"], second["seq"]), (0, 1))
        self.assertTrue((self.root / "attempt-log.jsonl").read_bytes().endswith(b"\n"))
        log.verify()

    def test_truncation_and_overwrite_detected(self):
        path = self.root / "decision-log.jsonl"
        log = logs.AppendOnlyLog(path)
        log.append({"event": "a"})
        log.append({"event": "b"})
        log.verify()
        anchor = log.tail_digest()
        raw = path.read_bytes()
        path.write_bytes(raw[: -len(raw.splitlines(keepends=True)[-1])])
        with self.assertRaises(logs.LogIntegrityError):
            log.verify(expected_tail_digest=anchor)
        path.write_bytes(raw)
        lines = path.read_bytes().splitlines(keepends=True)
        lines[0] = (
            strategy_context.canonical_json_bytes(
                {"schema_version": 1, "seq": 0, "prev_digest": None, "event": "a!"}
            )
            + b"\n"
        )
        path.write_bytes(b"".join(lines))
        with self.assertRaises(logs.LogIntegrityError):
            log.verify()

    def test_missing_trailing_lf_rejected(self):
        path = self.root / "x.jsonl"
        path.write_bytes(b'{"a":1}')
        with self.assertRaises(logs.LogIntegrityError):
            logs.AppendOnlyLog(path).verify()


class TestCheckpoints(Base):
    def test_boundaries_persist_and_latest_wins(self):
        store = checkpointing.CheckpointStore(self.root / "checkpoints")
        for boundary in checkpointing.BOUNDARIES:
            store.write(boundary, {"step": boundary})
        latest = store.latest()
        if latest is None:
            self.fail("no checkpoint found after writing all boundaries")
        self.assertEqual(latest["boundary"], checkpointing.POST_BINDING)
        self.assertEqual(latest["seq"], len(checkpointing.BOUNDARIES) - 1)
        self.assertEqual(latest["schema_version"], versions.CHECKPOINT_SCHEMA_VERSION)

    def test_corrupt_checkpoint_fails_closed(self):
        store = checkpointing.CheckpointStore(self.root / "checkpoints")
        store.write(checkpointing.PRE_LAUNCH, {"step": 1})
        target = sorted((self.root / "checkpoints").glob("*.json"))[-1]
        target.write_text("{not json", encoding="utf-8")
        with self.assertRaises(checkpointing.CheckpointError):
            store.latest()


class TestSubprocessAdapter(Base):
    def _launch(self, root, stdout_text=b""):
        context_path = root / "contexts" / "strategy-context-000.json"
        context_path.parent.mkdir(parents=True, exist_ok=True)
        frozen = strategy_context.freeze_snapshot(SAMPLE_CONTEXT, context_path)
        config = dict(BASE_CONFIG, artifact_root=str(root / "tournaments"))
        argv = subprocess_adapter.build_command(
            config,
            seed=18,
            context_path=context_path,
            core_root=root / "core",
        )
        calls = []

        def runner(argv, timeout=None):
            calls.append(list(argv))
            self.assertTrue(
                subprocess_adapter.launch_is_registered(root, "attempt-000")
            )
            return subprocess_adapter.RunnerResult(
                exit_code=0, timed_out=False, stdout=stdout_text, stderr=b""
            )

        meta = subprocess_adapter.register_launch(
            root,
            "attempt-000",
            {
                "condition": "R3",
                "seed": "18",
                "agent_id": "agent-01",
                "attempt_ordinal": 1,
                "argv": argv,
                "context_path": str(context_path),
                "context_digest": frozen["digest"],
            },
        )
        result = subprocess_adapter.run_launch(
            root, "attempt-000", argv, runner, timeout=60
        )
        return frozen, meta, result, calls

    def test_exact_command_and_registration_before_start(self):
        frozen, meta, result, calls = self._launch(self.root)
        self.assertEqual(
            calls[0][calls[0].index("--strategy-context") + 1 :],
            [str(self.root / "contexts" / "strategy-context-000.json")],
        )
        self.assertTrue(
            all(flag in calls[0] for flag in ("--seed", "18", "--provider", "fake"))
        )
        self.assertEqual(meta["context_digest"], frozen["digest"])
        self.assertEqual(result.exit_code, 0)

    def test_launch_metadata_immutable(self):
        self._launch(self.root)
        with self.assertRaises(subprocess_adapter.LaunchError):
            subprocess_adapter.register_launch(
                self.root, "attempt-000", {"condition": "R1"}
            )

    def test_context_mutation_after_hashing_rejected(self):
        frozen, _, result, _ = self._launch(self.root)
        path = Path(self.root / "contexts" / "strategy-context-000.json")
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(strategy_context.ContextError):
            strategy_context.verify_snapshot(path, frozen["digest"])

    def test_reported_digest_extraction_and_chain_binding(self):
        digest = strategy_context.digest_json(SAMPLE_CONTEXT)
        frozen, meta, result, _ = self._launch(
            self.root, f"strategy_context_digest: {digest}\n".encode()
        )
        reported = subprocess_adapter.extract_reported_context_digest(
            result.stdout_ref.read_bytes().decode()
        )
        self.assertEqual(reported, digest)
        core_config = {
            "policy_id": "llm-v1",
            "policy_digest": "sha256:" + "d" * 64,
        }
        receipt = {
            "logical_run_id": "logical-run-1",
            "policy_digest": "sha256:" + "d" * 64,
        }
        chain = subprocess_adapter.bind_identity(meta, reported, core_config, receipt)
        self.assertEqual(chain["context_digest"], frozen["digest"])
        self.assertEqual(chain["reported_digest"], digest)
        self.assertEqual(chain["logical_run_id"], "logical-run-1")
        strategy_context.verify_context_chain(
            Path(meta["context_path"]), meta, chain, core_config, receipt
        )

    def test_chain_requires_the_cli_reported_digest_line(self):
        # PR #18 blocker 1: removing the ``strategy_context_digest:`` stdout
        # line from the simulated CLI output must break the chain; it can
        # never pass without a CLI report.
        frozen, meta, result, _ = self._launch(self.root, b"no digest line here\n")
        reported = subprocess_adapter.extract_reported_context_digest(
            result.stdout_ref.read_bytes().decode()
        )
        self.assertIsNone(reported)
        with self.assertRaises(subprocess_adapter.LaunchError):
            subprocess_adapter.bind_identity(
                meta,
                reported,
                {"policy_digest": "sha256:" + "d" * 64},
                {"logical_run_id": "logical-run-1"},
            )

    def test_chain_without_post_run_binding_raises(self):
        digest = strategy_context.digest_json(SAMPLE_CONTEXT)
        frozen, meta, _, _ = self._launch(
            self.root, f"strategy_context_digest: {digest}\n".encode()
        )
        good_cfg = {"policy_digest": "sha256:" + "d" * 64}
        good_rcpt = {
            "logical_run_id": "logical-run-1",
            "policy_digest": "sha256:" + "d" * 64,
        }
        context_path = Path(meta["context_path"])
        for broken_binding in (
            None,
            {},
            {"context_digest": meta["context_digest"]},
            {
                "context_digest": meta["context_digest"],
                "reported_digest": None,
                "policy_digest": good_cfg["policy_digest"],
                "logical_run_id": "logical-run-1",
            },
        ):
            with self.assertRaises(strategy_context.ContextError):
                strategy_context.verify_context_chain(
                    context_path, meta, broken_binding, good_cfg, good_rcpt
                )
        unbound = {
            "context_digest": meta["context_digest"],
            "reported_digest": digest,
            "policy_digest": good_cfg["policy_digest"],
            "logical_run_id": "other-run",
        }
        with self.assertRaises(strategy_context.ContextError):
            strategy_context.verify_context_chain(
                context_path, meta, unbound, good_cfg, good_rcpt
            )

    def test_chain_fails_closed_on_each_broken_link(self):
        digest = strategy_context.digest_json(SAMPLE_CONTEXT)
        frozen, meta, _, _ = self._launch(
            self.root, f"strategy_context_digest: {digest}\n".encode()
        )
        good_cfg = {"policy_digest": "sha256:" + "d" * 64}
        good_rcpt = {
            "logical_run_id": "logical-run-1",
            "policy_digest": "sha256:" + "d" * 64,
        }
        chain = subprocess_adapter.bind_identity(meta, digest, good_cfg, good_rcpt)
        context_path = Path(meta["context_path"])
        strategy_context.verify_context_chain(
            context_path, meta, chain, good_cfg, good_rcpt
        )
        wrong_cli = dict(chain, reported_digest="sha256:" + "e" * 64)
        with self.assertRaises(strategy_context.ContextError):
            strategy_context.verify_context_chain(
                context_path, meta, wrong_cli, good_cfg, good_rcpt
            )
        tampered = context_path.read_bytes() + b" "
        context_path.write_bytes(tampered)
        with self.assertRaises(strategy_context.ContextError):
            strategy_context.verify_context_chain(
                context_path, meta, chain, good_cfg, good_rcpt
            )
        context_path.write_bytes(context_path.read_bytes()[:-1])
        drifted_cfg = {"policy_digest": "sha256:" + "f" * 64}
        with self.assertRaises(strategy_context.ContextError):
            strategy_context.verify_context_chain(
                context_path, meta, chain, drifted_cfg, good_rcpt
            )
        drifted_rcpt = dict(good_rcpt, policy_digest="sha256:" + "f" * 64)
        with self.assertRaises(strategy_context.ContextError):
            strategy_context.verify_context_chain(
                context_path, meta, chain, good_cfg, drifted_rcpt
            )

    def test_identity_separation_same_vs_different_context(self):
        seen = {}
        for text, pdig in (
            ("alpha", "sha256:" + "1" * 64),
            ("alpha", "sha256:" + "1" * 64),
            ("beta", "sha256:" + "2" * 64),
        ):
            ctx = json.loads(json.dumps(SAMPLE_CONTEXT))
            ctx["discoveries"][0]["text"] = text
            seen.setdefault(text, []).append((pdig, ctx))
        digests = {}
        for text, entries in seen.items():
            for pdig, ctx in entries:
                d = strategy_context.digest_json(ctx)
                digests.setdefault(d, set()).add(pdig)
        self.assertEqual(len(digests), 2)
        for d, pdigs in digests.items():
            self.assertEqual(len(pdigs), 1)
        strategy_context.assert_identity_separation(digests)


if __name__ == "__main__":
    unittest.main()
