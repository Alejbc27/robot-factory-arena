"""Unit 6 fake acceptance + invocation tests (design §9/§10).

The acceptance gate drives the USER-FACING entry point (``harness.cli.main``)
with the deterministic fake subprocess CLI wrapper: one full R1+R2+R3
tournament over seeds 18/24/30 with K=3, valid receipt triples, and R3
discoveries. The end state must show exactly nine rows with receipt-owned
scores, provider-evidence-owned tokens, an intact digest chain, unique attempt
ids, continuous append-only logs, and byte-identical leaderboards across two
independent runs. The same module locks the invocation contract: missing or
invalid config, unknown flags, the injectable R1 terminals, and fail-closed
non-zero exits on digest-chain/log/config violations. No live provider is ever
contacted; the Luna procedure is documentation-only (docs/sdd).
"""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import logs, state, strategy_context, tournament

LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures/harness/unit6-acceptance-config.json"
GUIDANCE = "Approve the scripted acceptance guidance"


def _write_answers(root, answers, name="answers.json"):
    path = root / name
    path.write_bytes(strategy_context.canonical_json_bytes(answers))
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def main(self, argv, root=None):
        """Run the entry in-process; returns (exit_code, stdout, stderr)."""
        from harness import cli

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([*argv, "--root", str(root or self.root)])
        return code, out.getvalue(), err.getvalue()

    def fake_argv(self, config_path=None, extra=()):
        return [
            "--config",
            str(config_path or FIXTURE),
            "--runner",
            "fake",
            "--non-interactive",
            *extra,
        ]

    def layout(self, root=None, config=None):
        config = config or json.loads(FIXTURE.read_bytes())
        return tournament.expected_layout(
            root or self.root, config["tournament_id"], config["artifact_root"]
        )

    def rows(self, root=None, config=None):
        data = json.loads(self.layout(root, config)["leaderboard.json"].read_bytes())
        return data["rows"]

    def row_for(self, condition, seed, root=None, config=None):
        return next(
            r
            for r in self.rows(root, config)
            if r["condition"] == condition and r["seed"] == seed
        )

    def attempt_records(self, root=None, config=None):
        path = self.layout(root, config)["attempt-log.jsonl"]
        return logs.AppendOnlyLog(path).records()

    def decision_records(self, root=None, config=None):
        path = self.layout(root, config)["decision-log.jsonl"]
        return logs.AppendOnlyLog(path).records()


class TestInvocationContract(Base):
    def test_missing_config_file_exits_non_zero_with_clear_error(self):
        code, _, err = self.main(["--config", str(self.root / "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("cannot read tournament config", err)

    def test_unknown_flag_exits_non_zero(self):
        with self.assertRaises(SystemExit) as caught:
            self.main(self.fake_argv(extra=["--bogus"]))
        self.assertEqual(caught.exception.code, 2)

    def test_malformed_json_config_exits_non_zero(self):
        path = self.root / "bad.json"
        path.write_bytes(b"{not json")
        code, _, err = self.main(["--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("invalid tournament config", err)

    def test_config_failing_validation_exits_non_zero(self):
        config = json.loads(FIXTURE.read_bytes())
        config["seeds"] = [18, 24]
        path = self.root / "drift.json"
        path.write_bytes(strategy_context.canonical_json_bytes(config))
        code, _, err = self.main(["--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("invalid tournament config", err)

    def test_non_interactive_full_tournament_completes_with_nine_rows(self):
        code, out, _ = self.main(self.fake_argv())
        self.assertEqual(code, 0, out)
        rows = self.rows()
        self.assertEqual(len(rows), 9)
        for seed in ("18", "24", "30"):
            declined = self.row_for("R1", seed)
            self.assertEqual(declined["status"], "declined")
            self.assertEqual(declined["attempts_used"], 0)
            self.assertEqual(declined["declined_launches"], 1)
            self.assertIsNone(declined["verified_score"])
            self.assertIsNone(declined["input_tokens"])
            for condition in ("R2", "R3"):
                row = self.row_for(condition, seed)
                self.assertEqual(row["status"], "completed")
                self.assertEqual(row["attempts_used"], 1)
        self.assertIn("leaderboard.json", out)

    def test_stdin_terminal_is_the_default_and_fails_closed_on_closed_input(self):
        from harness import cli, interruption

        def closed():
            raise EOFError

        terminal = cli.stdin_terminal(read=closed, write=lambda text: None)
        with self.assertRaises(interruption.InterruptionError):
            terminal({"condition": "R1", "seed": "18", "proposal_round": 0})

    def test_stdin_terminal_parses_one_json_answer_per_prompt(self):
        from harness import cli

        lines = iter(['{"decision": "yes"}'])
        seen = []
        terminal = cli.stdin_terminal(read=lambda: next(lines), write=seen.append)
        answer = terminal({"condition": "R1", "seed": "18", "proposal_round": 0})
        self.assertEqual(answer, {"decision": "yes"})
        self.assertIn("R1 approval proposal", seen[0])

    def test_answers_file_replays_scripted_answers_then_defaults_to_yes(self):
        answers = _write_answers(
            self.root,
            [{"decision": "edit", "guidance": [GUIDANCE]}, {"decision": "yes"}],
        )
        code, _, _ = self.main(
            ["--config", str(FIXTURE), "--runner", "fake", "--answers", str(answers)]
        )
        self.assertEqual(code, 0)
        launched = self.row_for("R1", "18")
        self.assertEqual(launched["status"], "completed")
        self.assertEqual(launched["attempts_used"], 1)
        # the approved guidance reached the launch as frozen canonical bytes
        launch = json.loads(
            (
                self.layout()["attempts"]
                / "r1-agent-01-seed-18-attempt-01"
                / "launch.json"
            ).read_bytes()
        )
        snapshot = json.loads(Path(launch["context_path"]).read_bytes())
        self.assertEqual([g["text"] for g in snapshot["guidance"]], [GUIDANCE])
        self.assertEqual(
            launch["context_digest"],
            strategy_context.sha256_digest(Path(launch["context_path"]).read_bytes()),
        )
        # later prompts default to yes: every R1 pair launched exactly once
        for seed in ("24", "30"):
            self.assertEqual(self.row_for("R1", seed)["attempts_used"], 1)

    def test_invalid_answers_file_fails_closed_before_any_run(self):
        answers = _write_answers(self.root, [{"decision": "yes", "seed": "99"}])
        code, _, err = self.main(
            ["--config", str(FIXTURE), "--runner", "fake", "--answers", str(answers)]
        )
        self.assertEqual(code, 2)
        self.assertIn("side channel", err)
        self.assertFalse(self.layout()["decision-log.jsonl"].exists())

    def test_module_entry_runs_standalone_via_subprocess(self):
        for module in ("harness", "harness.run"):
            target = self.root / module.replace(".", "-")
            done = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    module,
                    "--config",
                    str(FIXTURE),
                    "--runner",
                    "fake",
                    "--non-interactive",
                    "--root",
                    str(target),
                ],
                capture_output=True,
                cwd=REPO_ROOT,
                timeout=300,
            )
            self.assertEqual(done.returncode, 0, done.stderr.decode())
            self.assertEqual(len(self.rows(target)), 9)


class TestFakeAcceptance(Base):
    def _run(self, root, answers=None):
        if answers is None:
            return self.main(self.fake_argv(), root=root)
        path = _write_answers(root, answers, name="answers-scripted.json")
        return self.main(
            ["--config", str(FIXTURE), "--runner", "fake", "--answers", str(path)],
            root=root,
        )

    def test_two_independent_runs_produce_byte_identical_leaderboards(self):
        first, second = self.root / "a", self.root / "b"
        self.assertEqual(self._run(first)[0], 0)
        self.assertEqual(self._run(second)[0], 0)
        self.assertEqual(
            self.layout(first)["leaderboard.json"].read_bytes(),
            self.layout(second)["leaderboard.json"].read_bytes(),
        )

    def test_scores_are_receipt_owned_and_tokens_are_provider_evidence_owned(self):
        self.assertEqual(self._run(self.root)[0], 0)
        for row in self.rows():
            if row["status"] == "declined":
                self.assertIsNone(row["core_receipt_digest"])
                continue
            bound = [
                r
                for r in self.attempt_records()
                if r.get("condition") == row["condition"]
                and r.get("seed") == row["seed"]
                and r["event"] == "evidence_bound"
            ]
            terminal = max(bound, key=lambda r: r["attempt_ordinal"])
            run_dir = self.layout()["core"] / "runs" / terminal["run_dir"]
            receipt_raw = (run_dir / "receipt.json").read_bytes()
            receipt = json.loads(receipt_raw)
            # score/outcome come from the sealed receipt only, digest-cited
            self.assertEqual(
                row["core_receipt_digest"],
                strategy_context.sha256_digest(receipt_raw),
            )
            self.assertEqual(row["verified_score"], receipt["verified_score"])
            self.assertEqual(row["outcome"], receipt["outcome"])
            # the CLI stdout decoy (999) never becomes an official score;
            # stdout is captured in the attempt dir, never parsed for evidence
            self.assertEqual(row["verified_score"], 100)
            stdout_bin = (
                self.layout()["attempts"] / terminal["attempt_id"] / "stdout.bin"
            ).read_bytes()
            self.assertIn(b"verified_score: 999", stdout_bin)
            # tokens exist only because provider evidence is digest-bound
            provider_run = json.loads(
                (run_dir / "llm" / "provider-run.json").read_bytes()
            )
            self.assertEqual(
                row["input_tokens"],
                sum(r["usage"]["input_tokens"] for r in provider_run["requests"]),
            )
            self.assertEqual(
                row["llm_receipt_digest"],
                json.loads((run_dir / "llm" / "llm-receipt.json").read_bytes())[
                    "llm_receipt_digest"
                ],
            )

    def test_end_state_is_chain_intact_unique_and_continuous(self):
        self.assertEqual(self._run(self.root)[0], 0)
        # every launch's context digest still matches its frozen snapshot bytes
        attempts_dir = self.layout()["attempts"]
        ids = []
        for attempt_dir in sorted(attempts_dir.iterdir()):
            launch = json.loads((attempt_dir / "launch.json").read_bytes())
            ids.append(launch["attempt_id"])
            self.assertEqual(
                strategy_context.sha256_digest(
                    Path(launch["context_path"]).read_bytes()
                ),
                launch["context_digest"],
            )
            self.assertTrue((attempt_dir / "observed.json").exists())
        self.assertEqual(len(ids), len(set(ids)), "attempt ids must be unique")
        # auto-declined R1 pairs allocate no attempt at all: 6 launched attempts
        self.assertEqual(len(ids), 6)
        # all three append-only logs verify as continuous chains
        for name in ("attempt-log.jsonl", "decision-log.jsonl", "discoveries.jsonl"):
            log = logs.AppendOnlyLog(self.layout()[name])
            self.assertGreaterEqual(log.verify(), 1, name)
        # R3 discoveries were sealed and admitted from valid receipt triples
        admitted = [
            r
            for r in logs.AppendOnlyLog(self.layout()["discoveries.jsonl"]).records()
            if r["event"] == "discovery_admitted"
        ]
        self.assertEqual(len(admitted), 3)
        for record in admitted:
            self.assertEqual(record["discovery"]["producing_condition"], "R3")
            self.assertIn(
                "sha256:", record["discovery"]["evidence"]["core_receipt_digest"]
            )

    def test_digest_chain_violation_exits_non_zero(self):
        self.assertEqual(self._run(self.root)[0], 0)
        log = self.layout()["decision-log.jsonl"]
        lines = log.read_bytes().splitlines(keepends=True)
        record = json.loads(lines[1])
        record["event"] = "tampered_event"
        lines[1] = strategy_context.canonical_json_bytes(record) + b"\n"
        log.write_bytes(b"".join(lines))
        code, _, err = self.main(self.fake_argv())
        self.assertEqual(code, 1)
        self.assertIn("integrity", err.lower())

    def test_log_gap_fails_closed(self):
        self.assertEqual(self._run(self.root)[0], 0)
        log = self.layout()["attempt-log.jsonl"]
        lines = log.read_bytes().splitlines(keepends=True)
        del lines[2]
        log.write_bytes(b"".join(lines))
        code, _, err = self.main(self.fake_argv())
        self.assertEqual(code, 1)
        self.assertIn("sequence/integrity", err.lower())

    def test_config_drift_exits_non_zero(self):
        self.assertEqual(self._run(self.root)[0], 0)
        config = json.loads(FIXTURE.read_bytes())
        config["k_attempts"] = 9
        path = self.root / "drift.json"
        path.write_bytes(strategy_context.canonical_json_bytes(config))
        code, _, err = self.main(["--config", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("drift", err.lower())

    @unittest.skipUnless(
        LANGGRAPH_AVAILABLE, "optional harness extra (langgraph) not installed"
    )
    def test_compiled_graph_and_walker_agree_byte_for_byte(self):
        from harness import driver, fake_provider, graph, interruption

        def deps_for(root):
            return graph.build_deps(
                root,
                json.loads(FIXTURE.read_bytes()),
                runner=fake_provider.fake_runner,
                approval=interruption.auto_decline,
            )

        compiled_root, walker_root = self.root / "compiled", self.root / "walker"
        driver.run_tournament(deps_for(compiled_root))
        driver.drive(state.initial_state(), deps_for(walker_root))
        self.assertEqual(
            self.layout(compiled_root)["leaderboard.json"].read_bytes(),
            self.layout(walker_root)["leaderboard.json"].read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
