"""Unit 2 foundation tests: tournament freeze, state, logs, checkpoints,
subprocess adapter, artifact layout, and the StrategyContext digest chain.

The mandatory acceptance binds: canonical StrategyContext -> SHA-256 ->
strategy_context_digest -> CLI reported digest -> policy_digest ->
logical_run_id / receipt.
"""

import copy
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
from robot_factory_arena.policies import llm as core_llm

BASE_CONFIG = {
    "schema_version": 1,
    "tournament_id": "tournament-unit2",
    "harness_version": versions.HARNESS_VERSION,
    "core_version": "0",
    "provider": "fake",
    "model": "fake-v1",
    "generation": {},
    "prompt_policy_version": "amend-4a-3/llm-v1",
    "context_digest_scheme": "sha256:canonical-json-v1",
    "budget": 100,
    "tool_grants": ["move", "pick", "place"],
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
        # verification_version passes validation but differs from the sealed
        # value: a pure comparability-drift probe (k_attempts is pinned and
        # would be rejected by validate_config before drift is even reached).
        drifted = dict(BASE_CONFIG, verification_version="2")
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

    def test_execution_profile_is_pinned_fail_closed(self):
        """PR #26 remediation: config.json may only declare the profile the
        core/adapters actually execute — anything else is a declared condition
        that cannot produce the score, so validation fails closed."""
        rejections = (
            ("budget", 99),
            ("budget", "100"),
            ("tool_grants", ["move"]),
            ("tool_grants", ["place", "pick", "move"]),
            ("fault_config", {}),
            ("k_attempts", 2),
            ("k_attempts", 4),
            ("generation", {"temperature": 0}),
            ("generation", {"temperature": 999}),
            ("generation", {"reasoning_effort": "none"}),
        )
        for field, value in rejections:
            with self.subTest(field=field, value=value):
                bad = dict(BASE_CONFIG)
                bad[field] = value
                with self.assertRaises(tournament.TournamentError):
                    tournament.validate_config(bad)

    def test_unknown_provider_model_has_no_frozen_profile(self):
        for provider, model in (("openai", "fake-v1"), ("glm", "gpt-5.6-luna")):
            with self.subTest(provider=provider, model=model):
                bad = dict(BASE_CONFIG, provider=provider, model=model)
                with self.assertRaises(tournament.TournamentError) as caught:
                    tournament.validate_config(bad)
                self.assertIn("no frozen execution profile", str(caught.exception))
                self.assertIn("supported", str(caught.exception))

    def test_supported_generation_profiles_are_the_closed_contract(self):
        # Exact per-(provider, model) adapter payloads: the OpenAI adapter
        # hardcodes reasoning_effort="none" for gpt-5.6-luna; the fake and GLM
        # adapters send NO generation parameters ({} = adapter defaults).
        supported = {
            ("fake", "fake-v1"): {},
            ("openai", "gpt-5.6-luna"): {"reasoning_effort": "none"},
            ("glm", "glm-5.3-flash"): {},
        }
        self.assertEqual(
            tournament.GENERATION_PROFILES,
            supported,
            "the closed generation contract must mirror the real adapters",
        )
        for (provider, model), generation in supported.items():
            with self.subTest(provider=provider, model=model):
                config = dict(
                    BASE_CONFIG,
                    tournament_id=f"tournament-profile-{provider}",
                    provider=provider,
                    model=model,
                    generation=generation,
                )
                tournament.validate_config(config)

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

    def test_gap_fails_closed_on_write_and_latest(self):
        # PR #18 blocker 4: a missing middle checkpoint is a gap; both
        # writes and reads fail closed instead of recomputing the next
        # seq from len() and silently overwriting the tail.
        store = checkpointing.CheckpointStore(self.root / "checkpoints")
        for boundary in checkpointing.BOUNDARIES:
            store.write(boundary, {"step": boundary})
        (self.root / "checkpoints" / "checkpoint-002.json").unlink()
        surviving = (self.root / "checkpoints" / "checkpoint-004.json").read_bytes()
        with self.assertRaises(checkpointing.CheckpointError):
            store.write(checkpointing.POST_BINDING, {"retry": True})
        with self.assertRaises(checkpointing.CheckpointError):
            store.latest()
        self.assertEqual(
            surviving,
            (self.root / "checkpoints" / "checkpoint-004.json").read_bytes(),
        )

    def test_write_never_overwrites_existing_destination(self):
        # Simulates the race the guard exists for: the destination file
        # appears between continuity validation and the write.
        store = checkpointing.CheckpointStore(self.root / "checkpoints")
        store.write(checkpointing.PRE_PROMPT, {"n": 0})
        original = store._validated_records

        def validate_with_race():
            records = original()
            (self.root / "checkpoints" / "checkpoint-001.json").write_bytes(b"{}")
            return records

        store._validated_records = validate_with_race
        with self.assertRaises(checkpointing.CheckpointError):
            store.write(checkpointing.POST_ANSWER, {"n": 1})

    def test_unexpected_checkpoint_filename_fails_closed(self):
        store = checkpointing.CheckpointStore(self.root / "checkpoints")
        directory = self.root / "checkpoints"
        directory.mkdir(parents=True, exist_ok=True)
        directory.joinpath("checkpoint-junk.json").write_bytes(b"{}")
        with self.assertRaises(checkpointing.CheckpointError):
            store.latest()
        with self.assertRaises(checkpointing.CheckpointError):
            store.write(checkpointing.PRE_PROMPT, {})

    def test_checkpoint_seq_field_mismatch_fails_closed(self):
        store = checkpointing.CheckpointStore(self.root / "checkpoints")
        store.write(checkpointing.PRE_PROMPT, {"n": 0})
        target = self.root / "checkpoints" / "checkpoint-000.json"
        record = json.loads(target.read_bytes())
        record["seq"] = 7
        target.write_bytes(strategy_context.canonical_json_bytes(record))
        with self.assertRaises(checkpointing.CheckpointError):
            store.latest()


class TestSubprocessAdapter(Base):
    def _register(self, root):
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
        return frozen, meta, argv

    def _launch(self, root, stdout_text=b""):
        frozen, meta, argv = self._register(root)
        calls = []

        def runner(argv, timeout=None):
            calls.append(list(argv))
            self.assertTrue(
                subprocess_adapter.launch_is_registered(root, "attempt-000")
            )
            return subprocess_adapter.RunnerResult(
                exit_code=0, timed_out=False, stdout=stdout_text, stderr=b""
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

    def _silent_runner(self, calls):
        def runner(argv, timeout=None):
            calls.append(list(argv))
            return subprocess_adapter.RunnerResult(
                exit_code=0, timed_out=False, stdout=b"", stderr=b""
            )

        return runner

    def test_run_launch_rejects_unregistered_argv(self):
        # PR #18 blocker 2: execution is bound to the registered argv.
        _, meta, argv = self._register(self.root)
        calls = []
        tampered = list(argv)
        tampered[tampered.index("18")] = "24"
        with self.assertRaises(subprocess_adapter.LaunchError):
            subprocess_adapter.run_launch(
                self.root, "attempt-000", tampered, self._silent_runner(calls)
            )
        self.assertEqual(calls, [])
        result = subprocess_adapter.run_launch(
            self.root,
            "attempt-000",
            argv,
            self._silent_runner(calls),
            timeout=60,
        )
        self.assertEqual(result.exit_code, 0)

    def test_run_launch_rejects_context_mutated_after_registration(self):
        # PR #18 blocker 2: the snapshot is re-verified JUST BEFORE spawn.
        _, meta, argv = self._register(self.root)
        path = Path(meta["context_path"])
        path.write_bytes(path.read_bytes() + b" ")
        calls = []
        with self.assertRaises(subprocess_adapter.LaunchError):
            subprocess_adapter.run_launch(
                self.root, "attempt-000", argv, self._silent_runner(calls)
            )
        self.assertEqual(calls, [])

    def test_run_launch_rejects_missing_context_snapshot(self):
        _, meta, argv = self._register(self.root)
        Path(meta["context_path"]).unlink()
        calls = []
        with self.assertRaises(subprocess_adapter.LaunchError):
            subprocess_adapter.run_launch(
                self.root, "attempt-000", argv, self._silent_runner(calls)
            )
        self.assertEqual(calls, [])

    def test_second_run_launch_never_overwrites_evidence(self):
        # PR #18 blocker 2: a second run for an attempt that already has
        # observed evidence is rejected; recovery is a new attempt.
        _, meta, result, _ = self._launch(self.root, b"first run stdout\n")
        stdout_before = result.stdout_ref.read_bytes()
        with self.assertRaises(subprocess_adapter.LaunchError):
            subprocess_adapter.run_launch(
                self.root, "attempt-000", meta["argv"], None, timeout=60
            )
        self.assertEqual(result.stdout_ref.read_bytes(), stdout_before)

    def test_partial_evidence_from_crash_still_blocks_overwrite(self):
        _, _, argv = self._register(self.root)
        directory = self.root / "attempts" / "attempt-000"
        directory.joinpath("stdout.bin").write_bytes(b"orphan output\n")
        with self.assertRaises(subprocess_adapter.LaunchError):
            subprocess_adapter.run_launch(
                self.root, "attempt-000", argv, None, timeout=60
            )
        self.assertEqual(
            directory.joinpath("stdout.bin").read_bytes(), b"orphan output\n"
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


def _context_variant(**changes):
    context = json.loads(json.dumps(SAMPLE_CONTEXT))
    context.update(changes)
    return context


def _discovery_variant(**changes):
    context = json.loads(json.dumps(SAMPLE_CONTEXT))
    context["discoveries"][0].update(changes)
    return context


def _evidence_variant(**changes):
    context = json.loads(json.dumps(SAMPLE_CONTEXT))
    context["discoveries"][0]["evidence"].update(changes)
    return context


def _discovery_variant_without(key):
    context = json.loads(json.dumps(SAMPLE_CONTEXT))
    del context["discoveries"][0][key]
    return context


def _guidance_entry():
    return {
        "guidance_id": "guidance-0001",
        "author_id": "human",
        "guidance_ordinal": 0,
        "text": "prefer spiral over sweep",
    }


def _discovery_entry(source_attempt_id, explorer_id, ordinal):
    return {
        "source_attempt_id": source_attempt_id,
        "explorer_id": explorer_id,
        "discovery_ordinal": ordinal,
        "producing_condition": "R3",
        "producing_agent_id": "agent-01",
        "producing_seed": "18",
        "producing_attempt_ordinal": 1,
        "evidence": {
            "run_status": "finished",
            "core_receipt_digest": "sha256:" + "a" * 64,
            "llm_receipt_digest": "sha256:" + "b" * 64,
        },
        "text": "shared finding",
    }


# PR #18 blocker 3: every sample is asserted against BOTH the harness mirror
# and the core policy validator (tests may import the core; harness source
# may not) — they must accept and reject identically.
PARITY_SAMPLES = (
    ("valid-context", SAMPLE_CONTEXT, True),
    ("valid-null-context", None, True),
    (
        "valid-guidance-and-multi-discoveries",
        _context_variant(
            guidance=[_guidance_entry()],
            discoveries=[
                _discovery_entry("a", "e1", 0),
                _discovery_entry("a", "e2", 0),
                _discovery_entry("b", "e1", 1),
            ],
        ),
        True,
    ),
    ("bool-schema-version", _context_variant(schema_version=True), False),
    ("float-schema-version", _context_variant(schema_version=1.0), False),
    ("wrong-schema-version", _context_variant(schema_version=2), False),
    ("string-schema-version", _context_variant(schema_version="1"), False),
    (
        "empty-source-tournament-id",
        _context_variant(source_tournament_id=""),
        False,
    ),
    (
        "non-string-source-tournament-id",
        _context_variant(source_tournament_id=7),
        False,
    ),
    ("guidance-not-a-list", _context_variant(guidance={}), False),
    ("discoveries-not-a-list", _context_variant(discoveries={}), False),
    (
        "empty-guidance-id",
        _context_variant(guidance=[dict(_guidance_entry(), guidance_id="")]),
        False,
    ),
    (
        "empty-author-id",
        _context_variant(guidance=[dict(_guidance_entry(), author_id="")]),
        False,
    ),
    (
        "bool-guidance-ordinal",
        _context_variant(guidance=[dict(_guidance_entry(), guidance_ordinal=True)]),
        False,
    ),
    (
        "negative-guidance-ordinal",
        _context_variant(guidance=[dict(_guidance_entry(), guidance_ordinal=-1)]),
        False,
    ),
    (
        "empty-guidance-text",
        _context_variant(guidance=[dict(_guidance_entry(), text="")]),
        False,
    ),
    (
        "duplicate-guidance-identity",
        _context_variant(guidance=[_guidance_entry(), _guidance_entry()]),
        False,
    ),
    ("empty-source-attempt-id", _discovery_variant(source_attempt_id=""), False),
    ("empty-explorer-id", _discovery_variant(explorer_id=""), False),
    ("bool-discovery-ordinal", _discovery_variant(discovery_ordinal=True), False),
    (
        "negative-discovery-ordinal",
        _discovery_variant(discovery_ordinal=-1),
        False,
    ),
    ("condition-not-R3", _discovery_variant(producing_condition="R1"), False),
    ("condition-lowercase-r3", _discovery_variant(producing_condition="r3"), False),
    (
        "empty-producing-agent-id",
        _discovery_variant(producing_agent_id=""),
        False,
    ),
    ("empty-producing-seed", _discovery_variant(producing_seed=""), False),
    ("seed-as-int", _discovery_variant(producing_seed=18), False),
    (
        "producing-attempt-ordinal-zero",
        _discovery_variant(producing_attempt_ordinal=0),
        False,
    ),
    (
        "bool-producing-attempt-ordinal",
        _discovery_variant(producing_attempt_ordinal=True),
        False,
    ),
    ("empty-discovery-text", _discovery_variant(text=""), False),
    ("missing-discovery-key", _discovery_variant_without("text"), False),
    ("evidence-not-finished", _evidence_variant(run_status="completed"), False),
    (
        "evidence-digest-trailing-newline",
        _evidence_variant(core_receipt_digest="sha256:" + "a" * 64 + "\n"),
        False,
    ),
    (
        "evidence-digest-uppercase",
        _evidence_variant(llm_receipt_digest="sha256:" + "A" * 64),
        False,
    ),
    (
        "evidence-digest-short",
        _evidence_variant(core_receipt_digest="sha256:" + "a" * 63),
        False,
    ),
    ("evidence-digest-null", _evidence_variant(llm_receipt_digest=None), False),
    (
        "duplicate-discovery-identity",
        _context_variant(
            discoveries=[
                _discovery_entry("a", "e1", 0),
                _discovery_entry("a", "e1", 0),
            ]
        ),
        False,
    ),
    ("extra-top-key", _context_variant(extra=1), False),
)


class TestMirrorParity(unittest.TestCase):
    """PR #18 blocker 3: the harness mirror must accept and reject EXACTLY
    like the core AMEND-4A-3 validator on the same samples (isolation means
    the rules are deliberately duplicated; parity is proven, not assumed)."""

    def test_mirror_and_core_agree_on_every_sample(self):
        for name, sample, should_pass in PARITY_SAMPLES:
            with self.subTest(sample=name):
                mirror_error = None
                core_error = None
                try:
                    strategy_context.validate_context(copy.deepcopy(sample))
                except strategy_context.ContextError as error:
                    mirror_error = error
                try:
                    core_llm.validate_strategy_context(copy.deepcopy(sample))
                except ValueError as error:
                    core_error = error
                self.assertEqual(
                    mirror_error is None,
                    core_error is None,
                    f"{name}: mirror={mirror_error!r} core={core_error!r}",
                )
                self.assertEqual(
                    core_error is None,
                    should_pass,
                    f"{name}: expected valid={should_pass}, core disagrees",
                )

    def test_mirror_normalization_matches_core_canonical_order(self):
        context = _context_variant(
            guidance=[_guidance_entry()],
            discoveries=[
                _discovery_entry("b", "e1", 0),
                _discovery_entry("a", "e2", 1),
                _discovery_entry("a", "e1", 0),
            ],
        )
        mirrored = strategy_context.normalize_context(copy.deepcopy(context))
        canonical = core_llm.canonical_strategy_context(copy.deepcopy(context))
        self.assertEqual(
            strategy_context.canonical_json_bytes(mirrored),
            core_llm.canonical_json(canonical).encode("utf-8"),
        )
        self.assertEqual(
            strategy_context.digest_json(mirrored),
            core_llm.strategy_context_digest(canonical),
        )


if __name__ == "__main__":
    unittest.main()
