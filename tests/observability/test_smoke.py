"""Docker-free contract tests for the Langfuse PR smoke runtime (Unit C).

The smoke runtime (``observability/smoke.py``) and its thin entrypoint
(``scripts/langfuse_smoke.py``) are exercised through injected seams only:
fake command runner, fake API, fake sleeper, real subprocesses for the
command/security boundary. No test here starts Docker, Compose, a listener,
or the live stack — the live smoke is an explicit later task, never normal-test
evidence. Covered per the corrected design: embedded deterministic fixture,
two identical real CLI exports with exact counters, scoped API proof
0 -> 1 -> 1 with identical ID sets and zero growth, HTTP 207 never novelty,
mask-before-use, raw/file scanning, exposure fail-closed, bounded deadlines,
dynamic port and exact-label isolation, independent worker proof, six-service
provenance, cleanup/report precedence, and promotion evidence that never
self-promotes.
"""

import ast
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from collections import deque
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import observability_runtime.smoke as smoke  # pyright: ignore[reportMissingImports]

from observability import assemble_items, canonical_json_bytes, ids

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "langfuse_smoke.py"
_CORE_DIGEST = "sha256:07089cdb36c6dac54695d17f5fba222a50b5e55c7f92b39459b86bcc73b1df40"
_LLM_DIGEST = "sha256:9da9f5925defb8f6c89ae6409f679e96b19e5c746461b7fff5596f0c199f2e97"
_PROVIDER_DIGEST = (
    "sha256:63ccc19f22a27085c6247fc841f81da06a6613d1d700391da581aba2f869afd5"
)
_TRACE = ids.trace_id(
    "tournament-observed", "attempt-t1", "sealed-fixture-01", _CORE_DIGEST, _LLM_DIGEST
)
_GENERATION = ids.generation_id("sealed-fixture-01", "req-0", _PROVIDER_DIGEST)
EXPECTED_IDS = {
    "trace": _TRACE,
    "generation": _GENERATION,
    "score": ids.score_id(_TRACE, "verified_score", _CORE_DIGEST),
}


def _cli_report():
    return {
        "schema_version": 1,
        "counts": {
            "processed": 1,
            "accepted_items": 1,
            "accepted_events": 3,
            "error_events": 0,
            "failed": 0,
            "skipped": 0,
        },
        "exported": [],
        "skipped": [],
    }


class _Sink:
    """Recording mask sink preserving emission order."""

    def __init__(self):
        self.emitted = []

    def __call__(self, line):
        self.emitted.append(line)


class _Sleeper:
    def __init__(self):
        self.naps = []

    def __call__(self, seconds):
        self.naps.append(seconds)


class FakeApi:
    """Scripted stand-in for LangfuseApi with a per-snapshot hook."""

    def __init__(self):
        self.state = {
            "traces": [],
            "generations": [],
            "scores": [],
            "observations_total": 0,
        }
        self.on_snapshot: Callable[[FakeApi], None] | None = None
        self.snapshots = 0

    def snapshot(self):
        self.snapshots += 1
        if self.on_snapshot is not None:
            self.on_snapshot(self)
        counts = {
            kind: {"count": len(values), "ids": tuple(values)}
            for kind, values in self.state.items()
            if kind != "observations_total"
        }
        counts["observations_total"] = self.state["observations_total"]
        return counts

    def trace(self, trace_id):
        return {
            "id": trace_id,
            "name": "robot-factory-attempt",
            "metadata": {
                "tournament_id": "tournament-observed",
                "condition": "R1",
                "seed": "18",
                "status": "completed",
                "artifact_digests": {"core_receipt": _CORE_DIGEST},
            },
            "observations": [{"id": EXPECTED_IDS["generation"]}],
            "scores": [{"id": EXPECTED_IDS["score"]}],
        }

    def observation(self, observation_id):
        return {
            "id": observation_id,
            "type": "GENERATION",
            "traceId": EXPECTED_IDS["trace"],
            "model": "fake-v1",
        }

    def score(self, score_id):
        return {
            "id": score_id,
            "traceId": EXPECTED_IDS["trace"],
            "name": "verified_score",
            "dataType": "NUMERIC",
        }


class FakeInventory:
    def __init__(self, records=None):
        self.records = records or {name: {} for name in smoke.SERVICE_NAMES}
        self.collected = False

    def collect(self, timeout=None):
        self.collected = True
        self.collect_timeout = timeout
        return self.records


class FakeCleanup:
    def __init__(self, problems=()):
        self.problems = list(problems)
        self.executed = False

    def execute(self, down_timeout=None, query_timeout=30):
        self.executed = True
        self.execute_kwargs = {
            "down_timeout": down_timeout,
            "query_timeout": query_timeout,
        }
        return list(self.problems)


class FakeRunner:
    """Dispatching fake for docker/compose and CLI-export subprocesses."""

    def __init__(self):
        self.calls = []
        self.fail_up = False
        self.labelled = ""
        self.cli_outputs = deque([json.dumps(_cli_report())])
        self.inspect_states = deque(
            [{"Running": True, "RestartCount": 0}, {"Running": True, "RestartCount": 0}]
        )
        self.worker_logs = "boot\nNest application successfully started\nqueue ok\n"

    def run(self, argv, *, env=None, timeout=None):
        parts = [str(part) for part in argv]
        self.calls.append({"argv": parts, "env": dict(env or {}), "timeout": timeout})
        if "export" in parts:
            return smoke.RunResult(0, self.cli_outputs[0], "")
        if "up" in parts:
            if self.fail_up:
                return smoke.RunResult(1, "", "compose up failed")
        if "ps" in parts and "-a" not in parts and "-q" in parts:
            return smoke.RunResult(0, "container-id\n", "")
        if "logs" in parts:
            return smoke.RunResult(0, self.worker_logs, "")
        if "inspect" in parts:
            state = self.inspect_states[0]
            if len(self.inspect_states) > 1:
                self.inspect_states.rotate(-1)
            return smoke.RunResult(0, json.dumps([{"State": state}]), "")
        if "label=" in " ".join(parts):
            return smoke.RunResult(0, self.labelled, "")
        return smoke.RunResult(0, "", "")


def _make_deps(runner, api=None, **overrides):
    guard = smoke.LeakGuard(sink=_Sink())
    guard.register("sk-lf-secret", "secret_key")
    guard.register("pk-lf-public", "public_key")
    defaults = {
        "runner": runner,
        "guard": guard,
        "api_factory": lambda url, pk, sk: api if api is not None else FakeApi(),
        "sleeper": _Sleeper(),
        "inventory": FakeInventory(),
        "cleanup": FakeCleanup(),
        "port_is_free": lambda: True,
        "web_probe": lambda url: {"status": "OK", "version": smoke.LANGFUSE_VERSION},
    }
    defaults.update(overrides)
    return smoke.SmokeDeps(**defaults)


def _config(**bounds):
    return smoke.SmokeConfig(
        compose_file=ROOT / "observability" / "docker-compose.yml",
        source_env={},
        bounds=smoke.Bounds(**bounds),
    )


def _run_orchestrator(runner, api=None, bounds=None, **overrides):
    config_bounds = {"worker_stability_s": 0, **(bounds or {})}
    orchestrator = smoke.SmokeOrchestrator(
        _config(**config_bounds), _make_deps(runner, api, **overrides)
    )
    return orchestrator.run().finalize()


class LeakGuardTest(unittest.TestCase):
    def test_masks_every_value_before_first_use_and_redacts(self):
        sink = _Sink()
        guard = smoke.LeakGuard(sink=sink)
        guard.register("value-one", "a")
        guard.register("value-two", "b")
        self.assertEqual(
            sink.emitted, ["::add-mask::value-one", "::add-mask::value-two"]
        )
        text = "leak value-one and value-two end"
        self.assertNotIn("value-one", guard.redact(text))
        self.assertNotIn("value-two", guard.redact(text))
        self.assertEqual(guard.leaks(text), ("a", "b"))

    def test_header_patterns_are_detected_even_unregistered(self):
        guard = smoke.LeakGuard(sink=_Sink())
        self.assertTrue(guard.leaks("authorization: Basic abc"))
        self.assertTrue(guard.leaks("Bearer xyz"))
        self.assertEqual(guard.leaks("clean text"), ())

    def test_ensure_clean_fails_closed_with_redacted_message(self):
        guard = smoke.LeakGuard(sink=_Sink())
        guard.register("sup3rsecret", "k")
        with self.assertRaises(smoke.CredentialExposureError) as caught:
            guard.ensure_clean("out: sup3rsecret")
        self.assertNotIn("sup3rsecret", str(caught.exception))


class CommandRunnerTest(unittest.TestCase):
    def test_argv_list_minimal_env_and_no_shell(self):
        guard = smoke.LeakGuard(sink=_Sink())
        runner = smoke.CommandRunner(
            guard, cwd=ROOT, base_env={"PATH": "/usr/bin:/bin"}
        )
        done = runner.run(
            [sys.executable, "-c", "import sys;print(sys.argv[1:])", "hello"]
        )
        self.assertEqual(done.returncode, 0)
        self.assertIn("['hello']", done.stdout)

    def test_generated_values_reach_child_but_ambient_do_not(self):
        guard = smoke.LeakGuard(sink=_Sink())
        runner = smoke.CommandRunner(
            guard, cwd=ROOT, base_env={"PATH": "/usr/bin:/bin"}
        )
        probe = (
            "import os;print(os.environ.get('SMOKE_AMBIENT','absent'),"
            "os.environ.get('LANGFUSE_SECRET_KEY','missing'))"
        )
        done = runner.run(
            [sys.executable, "-c", probe],
            env={"LANGFUSE_SECRET_KEY": "sk-value"},
        )
        self.assertIn("absent sk-value", done.stdout)

    def test_registered_secret_in_argv_never_spawns(self):
        guard = smoke.LeakGuard(sink=_Sink())
        guard.register("sk-lf-secret", "secret_key")
        runner = smoke.CommandRunner(guard, cwd=ROOT, base_env={})
        with self.assertRaises(smoke.CredentialExposureError):
            runner.run([sys.executable, "-c", "print('x')", "--key", "sk-lf-secret"])

    def test_secret_in_output_fails_closed_and_is_redacted(self):
        guard = smoke.LeakGuard(sink=_Sink())
        guard.register("sk-lf-secret", "secret_key")
        runner = smoke.CommandRunner(guard, cwd=ROOT, base_env={})
        with self.assertRaises(smoke.CredentialExposureError) as caught:
            runner.run([sys.executable, "-c", "print('sk-lf-secret')"])
        self.assertNotIn("sk-lf-secret", str(caught.exception))
        self.assertNotIn("sk-lf-secret", caught.exception.redacted_output or "")

    def test_timeout_is_enforced(self):
        guard = smoke.LeakGuard(sink=_Sink())
        runner = smoke.CommandRunner(guard, cwd=ROOT, base_env={})
        with self.assertRaises(smoke.CommandTimeout):
            runner.run([sys.executable, "-c", "import time;time.sleep(9)"], timeout=1)


class FixtureFactoryTest(unittest.TestCase):
    def test_builds_loadable_root_with_expected_deterministic_ids(self):
        factory = smoke.FixtureFactory()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "fixture"
            expected = factory.create(root)
            self.assertEqual(expected, EXPECTED_IDS)
            items, skips = assemble_items(root)
            self.assertEqual((len(items), len(skips)), (1, 0))
            leaderboard = (root / "leaderboard.json").read_bytes()
            self.assertEqual(
                leaderboard, canonical_json_bytes(json.loads(leaderboard.decode()))
            )

    def test_same_inputs_produce_identical_bytes_and_detect_mutation(self):
        factory = smoke.FixtureFactory()
        with tempfile.TemporaryDirectory() as name:
            one = factory.create(Path(name) / "a")
            snap_a = factory.snapshot(Path(name) / "a")
            two = factory.create(Path(name) / "b")
            self.assertEqual(one, two)
            self.assertEqual(snap_a, factory.snapshot(Path(name) / "b"))
            factory.assert_unchanged(Path(name) / "a", snap_a)
            (Path(name) / "a" / "leaderboard.json").write_bytes(b"{}")
            with self.assertRaises(smoke.FixtureMutated):
                factory.assert_unchanged(Path(name) / "a", snap_a)

    def test_fixture_is_metadata_only_null_raw_response_chain_valid(self):
        """Verify-6873 blocker 1: no raw response evidence in the sealed fixture."""
        self.assertIsNone(smoke._PROVIDER_RUN["requests"][0]["raw_visible_response"])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "fixture"
            self.assertEqual(smoke.FixtureFactory().create(root), EXPECTED_IDS)
            provider_bytes = (
                root
                / "core"
                / "runs"
                / ("lr_" + "3a" * 32)
                / "sealed-fixture-01"
                / "llm"
                / "provider-run.json"
            ).read_bytes()
            self.assertIn(b'"raw_visible_response":null', provider_bytes)
            self.assertNotIn(b'"raw_visible_response":"', provider_bytes)
            items, skips = assemble_items(root)
            self.assertEqual((len(items), len(skips)), (1, 0))

    def test_runtime_never_imports_tests_or_fixture_paths(self):
        source = (ROOT / "observability" / "smoke.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(alias.name.startswith("tests"))
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                self.assertNotEqual((node.module or "").split(".")[0], "tests")
        self.assertNotIn("tests/fixtures", source)


class LangfuseApiTest(unittest.TestCase):
    @staticmethod
    def _api(bodies):
        def transport(url):
            payload = bodies[url.split("/api/public/", 1)[1]]
            if isinstance(payload, tuple):
                return payload
            return 200, canonical_json_bytes(payload)

        return smoke.LangfuseApi(
            "http://127.0.0.1:3000", "pk", "sk", transport=transport
        )

    @staticmethod
    def _envelope(ids_list, total=None):
        total = len(ids_list) if total is None else total
        limit, pages = 100, max(1, -(-(total or 0) // 100))
        return {
            "data": [{"id": value} for value in ids_list],
            "meta": {
                "page": 1,
                "limit": limit,
                "totalItems": total,
                "totalPages": pages,
            },
        }

    def test_snapshot_reads_scoped_lists_with_strict_envelope(self):
        api = self._api(
            {
                "traces?limit=100&page=1": self._envelope(["tr_a"]),
                "observations?limit=100&page=1": self._envelope(["gen_a"]),
                "scores?limit=100&page=1": self._envelope(["score_a"]),
            }
        )
        snapshot = api.snapshot()
        self.assertEqual(snapshot["traces"], {"count": 1, "ids": ("tr_a",)})
        self.assertEqual(snapshot["observations_total"], 1)

    def test_malformed_envelopes_fail_closed_without_body(self):
        for bad in (
            {"data": [], "meta": {"page": 1}},
            {
                "data": "no",
                "meta": {"page": 1, "limit": 100, "totalItems": 0, "totalPages": 0},
            },
            {"data": [], "meta": {"page": 1, "limit": 100, "totalItems": 0, "x": 1}},
        ):
            with self.subTest(envelope=bad):
                with self.assertRaises(smoke.ApiError) as caught:
                    self._api({"traces?limit=100&page=1": bad}).snapshot()
                self.assertNotIn("no", str(caught.exception))

    def test_isolation_cap_rejects_more_than_thousand_objects(self):
        api = self._api({"traces?limit=100&page=1": self._envelope([], total=1001)})
        with self.assertRaises(smoke.ApiError):
            api.snapshot()

    def test_non_200_status_and_207_never_yield_objects(self):
        for status in (207, 500):
            with self.subTest(status=status):
                api = self._api({"traces?limit=100&page=1": (status, b"{}")})
                with self.assertRaises(smoke.ApiError):
                    api.snapshot()

    def test_direct_objects_return_allowlisted_fields(self):
        api = self._api(
            {
                "traces/tr_a": {"id": "tr_a", "observations": [], "scores": []},
                "observations/gen_a": {"id": "gen_a", "type": "GENERATION"},
                "scores/score_a": {"id": "score_a", "name": "verified_score"},
            }
        )
        self.assertEqual(api.trace("tr_a")["id"], "tr_a")
        self.assertEqual(api.observation("gen_a")["type"], "GENERATION")
        self.assertEqual(api.score("score_a")["name"], "verified_score")


class ImageInventoryTest(unittest.TestCase):
    @staticmethod
    def _runner(missing=(), digests=None):
        runner = FakeRunner()

        def run(argv, **_):
            parts = [str(part) for part in argv]
            if "ps" in parts and "-q" in parts:
                service = parts[-1]
                if service in missing:
                    return smoke.RunResult(0, "", "")
                return smoke.RunResult(0, f"cid-{service}\n", "")
            if "inspect" in parts and "image" not in parts:
                state = {"Image": "image-id-1", "State": {"Running": True}}
                return smoke.RunResult(0, json.dumps([state]), "")
            if "image" in parts:
                payload = [
                    {
                        "RepoDigests": digests
                        if digests is not None
                        else ["repo@sha256:abc"]
                    }
                ]
                return smoke.RunResult(0, json.dumps(payload), "")
            return smoke.RunResult(0, "", "")

        runner.run = run
        return runner

    def test_six_exact_records_with_requested_digest_and_image_id(self):
        records = smoke.ImageInventory(
            self._runner(), base=["docker", "compose"]
        ).collect()
        self.assertEqual(tuple(records), smoke.SERVICE_NAMES)
        for name, record in records.items():
            with self.subTest(service=name):
                self.assertTrue(record["available"])
                self.assertTrue(record["requested_image"])
                self.assertEqual(record["resolved_digest"], "sha256:abc")
                self.assertEqual(record["image_id"], "image-id-1")

    def test_unmaterialized_service_is_explicit_unavailable_record(self):
        records = smoke.ImageInventory(
            self._runner(missing=("langfuse-worker",)), base=["docker"]
        ).collect()
        self.assertEqual(len(records), 6)
        worker = records["worker"]
        self.assertFalse(worker["available"])
        self.assertEqual(worker["unavailable_reason"], "not_materialized")
        self.assertIsNone(worker["resolved_digest"])
        self.assertIsNone(worker["image_id"])
        self.assertTrue(worker["requested_image"])

    def test_missing_digest_is_evidence_failure_not_omission(self):
        records = smoke.ImageInventory(
            self._runner(digests=[]), base=["docker"]
        ).collect()
        web = records["web"]
        self.assertFalse(web["available"])
        self.assertEqual(web["unavailable_reason"], "missing_digest")
        self.assertIn("requested_image", web)

    def test_ps_commands_carry_the_run_env(self):
        """compose ps loads and interpolates the guarded compose model."""
        runner = self._runner()
        inner = runner.run

        def run(argv, **kwargs):
            runner.calls.append(
                {
                    "argv": [str(part) for part in argv],
                    "env": dict(kwargs.get("env") or {}),
                }
            )
            return inner(argv, **kwargs)

        runner.run = run
        smoke.ImageInventory(
            runner, base=["docker", "compose"], env={"LANGFUSE_PUBLIC_KEY": "pk"}
        ).collect()
        ps_calls = [call for call in runner.calls if "ps" in call["argv"]]
        self.assertEqual(len(ps_calls), 6)
        for call in ps_calls:
            self.assertEqual(call["env"], {"LANGFUSE_PUBLIC_KEY": "pk"})


class CleanupManagerTest(unittest.TestCase):
    def test_down_v_remove_orphans_and_exact_label_residue_queries(self):
        runner = FakeRunner()
        problems = smoke.CleanupManager(
            runner, base=["docker", "compose", "-p", "proj"], port_is_free=lambda: True
        ).execute()
        self.assertEqual(problems, [])
        flat = [part for call in runner.calls for part in call["argv"]]
        self.assertIn("-v", flat)
        self.assertIn("--remove-orphans", flat)
        label = "label=com.docker.compose.project=proj"
        self.assertEqual(flat.count(label), 3)

    def test_residue_or_port_failures_are_reported(self):
        runner = FakeRunner()
        runner.labelled = "residue-id"
        problems = smoke.CleanupManager(
            runner, base=["docker", "compose", "-p", "proj"], port_is_free=lambda: False
        ).execute()
        self.assertTrue(any("containers" in problem for problem in problems))
        self.assertTrue(any("port" in problem for problem in problems))

    def test_down_failure_is_reported(self):
        runner = FakeRunner()

        def run(argv, **_):
            if "down" in [str(part) for part in argv]:
                return smoke.RunResult(1, "", "down error")
            return smoke.RunResult(0, "", "")

        runner.run = run
        problems = smoke.CleanupManager(
            runner, base=["docker", "compose", "-p", "proj"], port_is_free=lambda: True
        ).execute()
        self.assertTrue(any("down" in problem for problem in problems))

    def test_down_command_carries_the_run_env(self):
        """compose down re-interpolates the guarded compose/override files."""
        runner = FakeRunner()
        smoke.CleanupManager(
            runner,
            base=["docker", "compose", "-p", "proj"],
            port_is_free=lambda: True,
            env={"LANGFUSE_PUBLIC_KEY": "pk"},
        ).execute()
        down = [call for call in runner.calls if "down" in call["argv"]]
        self.assertEqual(len(down), 1)
        self.assertEqual(down[0]["env"], {"LANGFUSE_PUBLIC_KEY": "pk"})


class WorkerProofTest(unittest.TestCase):
    def test_marker_plus_stable_no_restart_passes(self):
        proof = smoke.worker_proof(
            FakeRunner(),
            base=["docker", "compose", "-p", "proj"],
            stability_s=0,
            sleeper=_Sleeper(),
        )
        self.assertTrue(proof["marker_found"])
        self.assertEqual(proof["restarts"], 0)

    def test_running_without_marker_is_unproven(self):
        runner = FakeRunner()
        runner.worker_logs = "running but silent\n"
        with self.assertRaises(smoke.SmokeFailure) as caught:
            smoke.worker_proof(
                runner,
                base=["docker", "compose", "-p", "proj"],
                stability_s=0,
                sleeper=_Sleeper(),
            )
        self.assertEqual(caught.exception.code, "worker_readiness_unproven")

    def test_restart_during_stability_window_fails(self):
        runner = FakeRunner()
        runner.inspect_states = deque(
            [{"Running": True, "RestartCount": 0}, {"Running": True, "RestartCount": 2}]
        )
        with self.assertRaises(smoke.SmokeFailure) as caught:
            smoke.worker_proof(
                runner,
                base=["docker", "compose", "-p", "proj"],
                stability_s=1,
                sleeper=_Sleeper(),
            )
        self.assertEqual(caught.exception.code, "worker_unstable")

    def test_dead_container_fails_before_marker_check(self):
        runner = FakeRunner()
        runner.inspect_states = deque(
            [
                {"Running": False, "RestartCount": 0},
                {"Running": False, "RestartCount": 0},
            ]
        )
        with self.assertRaises(smoke.SmokeFailure):
            smoke.worker_proof(
                runner,
                base=["docker", "compose", "-p", "proj"],
                stability_s=0,
                sleeper=_Sleeper(),
            )


class OrchestratorHappyPathTest(unittest.TestCase):
    @staticmethod
    def _seed_at(snapshot_number):
        def seed(fake):
            if fake.snapshots == snapshot_number:
                fake.state.update(
                    {
                        "traces": [EXPECTED_IDS["trace"]],
                        "generations": [EXPECTED_IDS["generation"]],
                        "scores": [EXPECTED_IDS["score"]],
                        "observations_total": 1,
                    }
                )

        return seed

    def test_full_pass_two_exports_zero_growth_and_report(self):
        runner = FakeRunner()
        api = FakeApi()
        api.on_snapshot = self._seed_at(2)
        report = _run_orchestrator(runner, api)
        self.assertEqual(
            list(report["phases"]),
            [
                "preflight",
                "dependencies",
                "web",
                "worker",
                "baseline",
                "export_1",
                "api_proof_1",
                "export_2",
                "api_proof_2",
                "diagnostics",
                "cleanup",
            ],
        )
        self.assertEqual(report["exporter_invocations"], 2)
        self.assertEqual(report["smoke_assertions"], "PASS")
        self.assertEqual(report["overall"], "PASS")
        self.assertIsNone(report["reason"])
        self.assertEqual(len(report["cli_exports"]), 2)
        for entry in report["cli_exports"]:
            self.assertEqual(entry["exit_code"], 0)
            self.assertEqual(entry["processed"], 1)
            self.assertEqual(entry["accepted_events"], 3)
            self.assertTrue(entry["metadata_only"])
        proof = report["api_proof"]
        self.assertEqual(
            proof["baseline_counts"], {"traces": 0, "generations": 0, "scores": 0}
        )
        self.assertEqual(
            proof["first_export_counts"], {"traces": 1, "generations": 1, "scores": 1}
        )
        self.assertEqual(
            proof["second_export_counts"], {"traces": 1, "generations": 1, "scores": 1}
        )
        self.assertEqual(
            proof["second_export_deltas"], {"traces": 0, "generations": 0, "scores": 0}
        )
        self.assertTrue(proof["id_sets_identical"])
        self.assertTrue(proof["no_growth"])
        self.assertTrue(proof["http_status_not_novelty_evidence"])
        self.assertEqual(tuple(report["images"]), smoke.SERVICE_NAMES)
        promotion = report["promotion_evidence"]
        self.assertFalse(promotion["counted_for_promotion"])
        self.assertFalse(promotion["promotion_eligible"])
        self.assertEqual(report["cleanup"]["status"], "PASS")

    def test_both_cli_invocations_are_identical_real_exports(self):
        runner = FakeRunner()
        api = FakeApi()
        api.on_snapshot = OrchestratorHappyPathTest._seed_at(2)
        _run_orchestrator(runner, api)
        exports = [call for call in runner.calls if "export" in call["argv"]]
        self.assertEqual(len(exports), 2)
        self.assertEqual(exports[0]["argv"], exports[1]["argv"])
        self.assertNotIn("--include-text", exports[0]["argv"])
        self.assertIn("--artifacts-root", exports[0]["argv"])
        self.assertIn("--langfuse-url", exports[0]["argv"])
        for call in exports:
            self.assertIn("LANGFUSE_PUBLIC_KEY", call["env"])
            self.assertIn("LANGFUSE_SECRET_KEY", call["env"])

    def test_masks_are_emitted_at_registration_before_any_spawn(self):
        sink = _Sink()
        runner = FakeRunner()
        guard = smoke.LeakGuard(sink=sink)
        guard.register("sk-lf-secret", "secret_key")
        guard.register("pk-lf-public", "public_key")
        deps = _make_deps(runner)
        deps.guard = guard
        self.assertEqual(
            sink.emitted, ["::add-mask::sk-lf-secret", "::add-mask::pk-lf-public"]
        )
        smoke.SmokeOrchestrator(_config(worker_stability_s=0), deps).run()

    def test_report_carries_run_identity_and_no_registered_secret(self):
        report = _run_orchestrator(FakeRunner())
        self.assertTrue(report["run"]["compose_project"].startswith("langfuse-smoke-"))
        self.assertNotIn("sk-lf-secret", json.dumps(report))
        self.assertNotIn("pk-lf-public", json.dumps(report))


class OrchestratorFailureMatrixTest(unittest.TestCase):
    SCENARIOS = {
        "deps": ("dependencies", "compose_up_failed"),
        "worker": ("worker", "worker_readiness_unproven"),
        "baseline": ("baseline", "baseline_not_empty"),
        "export1": ("export_1", "unexpected_export_counters"),
        "proof1": ("api_proof_1", "api_object_proof_failed"),
        "proof2_growth": ("api_proof_2", "api_object_proof_failed"),
        "exposure": ("export_1", "credential_exposure"),
        "cleanup_after_pass": (None, None),
        "cleanup_after_primary": ("export_1", "unexpected_export_counters"),
    }

    def _scenario(self, name):
        runner = FakeRunner()
        api = FakeApi()
        cleanup = FakeCleanup()
        if name == "deps":
            runner.fail_up = True
        elif name == "worker":
            runner.worker_logs = "silent\n"
        elif name == "baseline":
            api.state.update({"traces": ["tr_preexisting"], "observations_total": 1})
        elif name == "export1":
            runner.cli_outputs = deque([json.dumps({"counts": {"processed": 0}})])
        elif name == "proof2_growth":

            def grow(fake):
                if fake.snapshots == 2:
                    OrchestratorHappyPathTest._seed_at(2)(fake)
                if fake.snapshots >= 3 and "tr_extra" not in fake.state["traces"]:
                    fake.state["traces"].append("tr_extra")

            api.on_snapshot = grow
        elif name == "exposure":

            def run(argv, **kwargs):
                if "export" in [str(part) for part in argv]:
                    raise smoke.CredentialExposureError(
                        "exposed in output", redacted_output="leak [REDACTED]"
                    )
                return FakeRunner.run(runner, argv, **kwargs)

            runner.run = run
        elif name == "cleanup_after_pass":
            api.on_snapshot = OrchestratorHappyPathTest._seed_at(2)
        elif name == "cleanup_after_primary":
            runner.cli_outputs = deque([json.dumps({"counts": {"processed": 0}})])
        if name in {"cleanup_after_pass", "cleanup_after_primary"}:
            cleanup.problems = ["residue containers remain"]
        return _run_orchestrator(runner, api, cleanup=cleanup), cleanup

    def test_failure_matrix(self):
        for name, (phase, code) in self.SCENARIOS.items():
            with self.subTest(scenario=name):
                report, cleanup = self._scenario(name)
                self.assertEqual(report["overall"], "FAIL", name)
                self.assertTrue(cleanup.executed, name)
                self.assertTrue(report["phases"]["diagnostics"], name)
                if phase is not None:
                    self.assertEqual(report["primary_failure"]["phase"], phase, name)
                    self.assertEqual(report["primary_failure"]["code"], code, name)
                if name == "cleanup_after_pass":
                    self.assertEqual(report["smoke_assertions"], "PASS")
                    self.assertEqual(report["cleanup"]["status"], "FAIL")
                    self.assertEqual(report["reason"], "cleanup_failed")
                elif name == "cleanup_after_primary":
                    self.assertEqual(report["primary_failure"]["phase"], "export_1")
                    self.assertEqual(report["reason"], "cleanup_failed")
                elif name == "exposure":
                    self.assertTrue(report["security"]["exposure_detected"])
                    self.assertNotIn("sk-lf-secret", json.dumps(report))
                else:
                    self.assertEqual(report["reason"], code)
                if name in {"deps", "worker", "baseline", "exposure"}:
                    self.assertEqual(report["exporter_invocations"], 0)

    def test_diagnostics_failure_does_not_suppress_cleanup_or_pass(self):
        runner = FakeRunner()

        def run(argv, **kwargs):
            if "-a" in [str(part) for part in argv]:
                return smoke.RunResult(1, "", "diagnostics unavailable")
            return FakeRunner.run(runner, argv, **kwargs)

        runner.run = run
        api = FakeApi()
        api.on_snapshot = OrchestratorHappyPathTest._seed_at(2)
        report = _run_orchestrator(runner, api)
        self.assertEqual(report["overall"], "PASS")
        self.assertIn("incomplete", json.dumps(report["phases"]["diagnostics"]))

    def test_preflight_label_collision_fails_before_mutation(self):
        runner = FakeRunner()
        runner.labelled = "preexisting"
        report = _run_orchestrator(runner)
        self.assertEqual(report["primary_failure"]["code"], "preflight_collision")
        self.assertEqual(report["exporter_invocations"], 0)
        self.assertEqual([call for call in runner.calls if "up" in call["argv"]], [])

    def test_exit_code_mapping_zero_only_for_pass(self):
        self.assertEqual(
            smoke.exit_code_for({"overall": "PASS", "cleanup": {"status": "PASS"}}), 0
        )
        self.assertEqual(smoke.exit_code_for({"overall": "FAIL"}), 1)


class SmokeConfigBoundsTest(unittest.TestCase):
    def test_default_bounds_are_bounded_and_reported(self):
        bounds = smoke.Bounds()
        self.assertEqual(bounds.global_deadline_s, 1800)
        self.assertEqual(bounds.export_s, 120)
        self.assertEqual(bounds.poll_interval_s, 2)
        self.assertEqual(bounds.poll_max_attempts, 90)
        self.assertLess(bounds.dependencies_s, bounds.global_deadline_s)

    def test_config_defaults_are_non_secret_and_version_pinned(self):
        self.assertEqual(smoke.LANGFUSE_VERSION, "3.114.0")
        self.assertEqual(
            smoke.SERVICE_IMAGES["web"], "docker.io/langfuse/langfuse:3.114.0"
        )
        self.assertEqual(len(smoke.SERVICE_IMAGES), 6)


class TriangulationTest(unittest.TestCase):
    """Hardening matrix: mid-run mutation, exit codes, extra objects, timeouts."""

    def test_fixture_mutation_during_run_fails_at_export_2(self):
        runner = FakeRunner()
        api = FakeApi()
        api.on_snapshot = OrchestratorHappyPathTest._seed_at(2)

        def mutated(_factory, _root, _snapshot):
            raise smoke.FixtureMutated("fixture bytes changed during the smoke")

        with mock.patch.object(smoke.FixtureFactory, "assert_unchanged", mutated):
            report = _run_orchestrator(runner, api)
        self.assertEqual(report["primary_failure"]["phase"], "export_2")
        self.assertEqual(report["primary_failure"]["code"], "fixture_mutated")

    def test_export_exit_one_is_command_failure(self):
        runner = FakeRunner()

        def run(argv, **kwargs):
            if "export" in [str(part) for part in argv]:
                return smoke.RunResult(1, "", "exporter died")
            return FakeRunner.run(runner, argv, **kwargs)

        runner.run = run
        report = _run_orchestrator(runner)
        self.assertEqual(report["primary_failure"]["phase"], "export_1")
        self.assertEqual(report["primary_failure"]["code"], "export_command_failed")

    def test_extra_non_generation_observation_fails_proof(self):
        runner = FakeRunner()
        api = FakeApi()

        def seed_with_extra_span(fake):
            if fake.snapshots == 2:
                fake.state.update(
                    {
                        "traces": [EXPECTED_IDS["trace"]],
                        "generations": [EXPECTED_IDS["generation"]],
                        "scores": [EXPECTED_IDS["score"]],
                        "observations_total": 2,
                    }
                )

        api.on_snapshot = seed_with_extra_span
        report = _run_orchestrator(runner, api)
        self.assertEqual(report["primary_failure"]["phase"], "api_proof_1")
        self.assertEqual(report["primary_failure"]["code"], "api_object_proof_failed")

    def test_malformed_cli_stdout_is_counters_failure(self):
        runner = FakeRunner()
        runner.cli_outputs = deque(["this is not json"])
        report = _run_orchestrator(runner)
        self.assertEqual(report["primary_failure"]["phase"], "export_1")
        self.assertEqual(
            report["primary_failure"]["code"], "unexpected_export_counters"
        )

    def test_command_timeout_surfaces_as_primary_failure(self):
        runner = FakeRunner()

        def run(argv, **kwargs):
            if "export" in [str(part) for part in argv]:
                raise smoke.CommandTimeout("no completion within 120s")
            return FakeRunner.run(runner, argv, **kwargs)

        runner.run = run
        report = _run_orchestrator(runner)
        self.assertEqual(report["primary_failure"]["phase"], "export_1")
        self.assertEqual(report["primary_failure"]["code"], "command_timeout")
        self.assertEqual(report["exporter_invocations"], 0)


class RemediationGateTest(unittest.TestCase):
    """Verify-6873 remediation: the six accepted candidate-caused blockers.

    Blocker 1 (fixture purity) lives in FixtureFactoryTest; this class covers
    finalization exception safety, the allocated port-release callback,
    resource-query return codes, one global deadline with phase bounds, and
    six-record image inventory on every path.
    """

    @staticmethod
    def _happy_api():
        api = FakeApi()
        api.on_snapshot = OrchestratorHappyPathTest._seed_at(2)
        return api

    # Blocker 2: ApiError/unexpected Exception -> safe primary, then
    # diagnostics + cleanup; SystemExit/KeyboardInterrupt propagate unchanged
    # after explicit cleanup and print nothing.

    def test_api_error_becomes_safe_primary_with_diagnostics_and_cleanup(self):
        api = FakeApi()

        def unreachable(_fake):
            raise smoke.ApiError("api_unreachable: traces")

        api.on_snapshot = unreachable
        cleanup = FakeCleanup()
        report = _run_orchestrator(FakeRunner(), api, cleanup=cleanup)
        self.assertTrue(cleanup.executed)
        self.assertEqual(report["primary_failure"]["phase"], "baseline")
        self.assertEqual(report["primary_failure"]["code"], "api_read_failed")
        self.assertTrue(report["phases"]["diagnostics"])
        self.assertEqual(report["reason"], "api_read_failed")

    def test_unexpected_exception_is_safe_failure_without_message_leak(self):
        runner = FakeRunner()

        def boom(argv, **kwargs):
            if "export" in [str(part) for part in argv]:
                raise RuntimeError("raw internal boom detail")
            return FakeRunner.run(runner, argv, **kwargs)

        runner.run = boom
        report = _run_orchestrator(runner)
        self.assertEqual(report["primary_failure"]["code"], "unexpected_failure")
        self.assertNotIn("boom detail", json.dumps(report))
        self.assertEqual(report["overall"], "FAIL")

    def test_base_exceptions_propagate_after_explicit_cleanup(self):
        for signal in (KeyboardInterrupt, SystemExit):
            with self.subTest(signal=signal.__name__):
                runner = FakeRunner()
                api = FakeApi()

                def interrupted(_fake, _signal=signal):
                    raise _signal()

                api.on_snapshot = interrupted
                cleanup = FakeCleanup()
                orchestrator = smoke.SmokeOrchestrator(
                    _config(worker_stability_s=0),
                    _make_deps(runner, api, cleanup=cleanup),
                )
                with self.assertRaises(signal):
                    orchestrator.run()
                self.assertTrue(cleanup.executed)

    # Blocker 3: the preflight allocated port-release callback must be the
    # one real cleanup validator, built once, invoked exactly once.

    def test_cleanup_manager_is_built_once_with_the_allocated_port_validator(self):
        created = []

        def factory(base, port_free, env):
            created.append(port_free)
            return FakeCleanup()

        report = _run_orchestrator(
            FakeRunner(), self._happy_api(), cleanup_factory=factory
        )
        self.assertEqual(len(created), 1)
        self.assertTrue(report["cleanup"]["port_released"])

    def test_allocated_port_callback_runs_once_even_on_early_failure(self):
        runner = FakeRunner()
        runner.fail_up = True
        created = []
        with mock.patch.object(smoke, "host_port_free", return_value=True) as free:
            report = _run_orchestrator(
                runner,
                port_is_free=None,  # real path: closure over the allocated port
                cleanup_factory=lambda base, pf, env: (
                    created.append(pf)
                    or smoke.CleanupManager(runner, base, pf, env=env)
                ),
            )
        self.assertEqual(report["primary_failure"]["code"], "compose_up_failed")
        self.assertEqual(len(created), 1)  # one manager, built at preflight
        self.assertEqual(free.call_count, 2)  # preflight availability + cleanup release

    # Blocker 4: resource queries check return codes; query failure is a
    # safe preflight/cleanup failure, never empty-success.

    def test_preflight_label_query_failure_is_never_empty_success(self):
        runner = FakeRunner()

        def run(argv, **kwargs):
            if "label=" in " ".join(str(part) for part in argv):
                return smoke.RunResult(1, "", "docker died")
            return FakeRunner.run(runner, argv, **kwargs)

        runner.run = run
        report = _run_orchestrator(runner)
        self.assertEqual(report["primary_failure"]["phase"], "preflight")
        self.assertEqual(report["primary_failure"]["code"], "preflight_query_failed")
        self.assertEqual([call for call in runner.calls if "up" in call["argv"]], [])

    def test_cleanup_residue_query_failure_is_cleanup_failure(self):
        runner = FakeRunner()

        def run(argv, **_):
            if "label=" in " ".join(str(part) for part in argv):
                return smoke.RunResult(1, "", "docker died")
            return smoke.RunResult(0, "", "")

        runner.run = run
        problems = smoke.CleanupManager(
            runner, base=["docker", "compose", "-p", "proj"], port_is_free=lambda: True
        ).execute()
        self.assertTrue(any("query failed" in problem for problem in problems))

    # Blocker 5: one monotonic global deadline plus phase bounds; the
    # configured retry/bound policy is serialized in the report.

    def test_global_deadline_exhaustion_is_a_bounded_failure(self):
        report = _run_orchestrator(
            FakeRunner(), self._happy_api(), bounds={"global_deadline_s": 0}
        )
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["primary_failure"]["code"], "global_deadline_exceeded")
        self.assertIn("cleanup", report["phases"])

    def test_web_phase_bound_caps_poll_attempts(self):
        probes = []

        def unhealthy(_url):
            probes.append(1)
            return {"status": "down"}

        report = _run_orchestrator(
            FakeRunner(),
            bounds={"web_s": 10, "poll_interval_s": 2, "poll_max_attempts": 90},
            web_probe=unhealthy,
        )
        self.assertEqual(report["primary_failure"]["code"], "web_unhealthy")
        self.assertEqual(len(probes), 5)

    def test_api_poll_phase_bound_caps_snapshot_attempts(self):
        api = FakeApi()  # never converges
        report = _run_orchestrator(
            FakeRunner(),
            api,
            bounds={"api_poll_s": 8, "poll_interval_s": 2, "poll_max_attempts": 90},
        )
        self.assertEqual(report["primary_failure"]["phase"], "api_proof_1")
        # baseline snapshot + exactly api_poll_s/interval capped poll snapshots
        self.assertEqual(api.snapshots, 5)

    def test_report_serializes_bounds_and_retry_policy(self):
        report = _run_orchestrator(FakeRunner(), self._happy_api())
        self.assertEqual(report["bounds"]["global_deadline_s"], 1800)
        self.assertEqual(report["bounds"]["poll_interval_s"], 2)
        self.assertEqual(report["bounds"]["poll_max_attempts"], 90)

    # Blocker 6: exactly six explicit image records on every path.

    def test_images_are_six_records_even_when_diagnostics_capture_fails(self):
        runner = FakeRunner()

        def run(argv, **kwargs):
            if "-a" in [str(part) for part in argv]:
                return smoke.RunResult(1, "", "diagnostics unavailable")
            return FakeRunner.run(runner, argv, **kwargs)

        runner.run = run
        report = _run_orchestrator(runner, self._happy_api())
        self.assertEqual(tuple(report["images"]), smoke.SERVICE_NAMES)

    def test_inventory_failure_reports_six_explicit_unavailable_records(self):
        class ExplodingInventory:
            def collect(self):
                raise RuntimeError("inventory broke")

        report = _run_orchestrator(
            FakeRunner(), self._happy_api(), inventory=ExplodingInventory()
        )
        self.assertEqual(tuple(report["images"]), smoke.SERVICE_NAMES)
        for name, record in report["images"].items():
            with self.subTest(service=name):
                self.assertTrue(record["requested_image"])
                self.assertIsNone(record["resolved_digest"])
                self.assertIsNone(record["image_id"])
                self.assertFalse(record["available"])
                self.assertTrue(record["unavailable_reason"])

    def test_inventory_runner_error_cannot_omit_services(self):
        runner = FakeRunner()

        def run(argv, **_):
            parts = [str(part) for part in argv]
            if "langfuse-worker" in parts:
                raise smoke.CommandTimeout("inspect hung")
            if "ps" in parts and "-q" in parts:
                return smoke.RunResult(0, "cid\n", "")
            if "inspect" in parts and "image" not in parts:
                return smoke.RunResult(0, json.dumps([{"Image": "img-1"}]), "")
            if "image" in parts:
                payload = [{"RepoDigests": ["repo@sha256:abc"]}]
                return smoke.RunResult(0, json.dumps(payload), "")
            return smoke.RunResult(0, "", "")

        runner.run = run
        records = smoke.ImageInventory(runner, base=["docker", "compose"]).collect()
        self.assertEqual(tuple(records), smoke.SERVICE_NAMES)
        self.assertFalse(records["worker"]["available"])
        self.assertTrue(records["worker"]["unavailable_reason"])
        self.assertTrue(records["web"]["available"])


class FinalStaticRemediationTest(unittest.TestCase):
    """Verify-r2 blockers: phase deadline authority, written-artifact leak
    scanning, and baseline consistency."""

    def test_phase_deadline_bound_once_per_phase_not_reset_per_call(self):
        orch = smoke.SmokeOrchestrator(_config(), _make_deps(FakeRunner()))
        clock = {"t": 0.0}
        with mock.patch.object(smoke.time, "monotonic", lambda: clock["t"]):
            orch._deadline = 10_000.0
            orch._enter_phase("dependencies")
            self.assertEqual(orch._remaining(30), 30)
            clock["t"] = 705.0
            self.assertEqual(orch._remaining(30), 15)  # phase left, not fresh 30
            clock["t"] = 720.0
            with self.assertRaises(smoke.SmokeFailure) as caught:
                orch._remaining(30)
            self.assertEqual(caught.exception.code, "phase_deadline_exceeded")
            orch._enter_phase("web")  # new phase: new instance, global-clamped
            self.assertEqual(orch._remaining(30), 30)
            clock["t"] = 10_000.0
            with self.assertRaises(smoke.SmokeFailure) as caught:
                orch._remaining(30)
            self.assertEqual(caught.exception.code, "global_deadline_exceeded")

    def test_cumulative_timeouts_and_sleeps_never_exceed_ceilings(self):
        t = {"now": 0.0}
        runner, commands = FakeRunner(), []
        real_run = runner.run

        def timed_run(argv, **kwargs):
            budget = float(kwargs.get("timeout") or 0)
            commands.append([t["now"], budget])
            t["now"] += budget  # adversarial worst case: burn the whole budget
            return real_run(argv, **kwargs)

        runner.run = timed_run
        bounds = dict(
            worker_stability_s=0,
            preflight_s=120,
            dependencies_s=200,
            web_s=120,
            worker_s=180,
            export_s=50,
            api_poll_s=40,
            diagnostics_s=90,
            cleanup_s=180,
        )
        orchestrator = smoke.SmokeOrchestrator(
            _config(**bounds),
            _make_deps(
                runner, RemediationGateTest._happy_api(), sleeper=lambda s: None
            ),
        )
        marks = []
        real_enter = orchestrator._enter_phase

        def noting(name):
            marks.append((name, t["now"]))
            real_enter(name)

        orchestrator._enter_phase = noting
        with mock.patch.object(smoke.time, "monotonic", lambda: t["now"]):
            report = orchestrator.run().finalize()
        self.assertEqual(report["primary_failure"]["code"], "phase_deadline_exceeded")

        def span(name, start):
            ceiling = getattr(orchestrator.bounds, smoke._PHASE_CEILING_S[name])
            return (start, min(start + ceiling, orchestrator.bounds.global_deadline_s))

        # spans are global-clamped, so each segment bound proves both ceilings
        spans = [span(name, start) for name, start in marks]
        self.assertTrue(all(budget > 0.0 for _, budget in commands))
        for i, (start, deadline) in enumerate(spans):
            seg_end = min(spans[i + 1][0] if i + 1 < len(spans) else deadline, deadline)
            for at, budget in commands:
                if start <= at < seg_end:
                    self.assertLessEqual(
                        at + budget, deadline + 1e-6, (start, at, budget)
                    )
        for at, budget in commands:  # finalization: one explicit shared budget
            if at >= spans[-1][1]:
                self.assertLessEqual(
                    at + budget,
                    spans[-1][1] + bounds["diagnostics_s"] + bounds["cleanup_s"] + 1e-6,
                )

    def test_retry_sleep_never_exceeds_phase_ceiling(self):
        naps = []
        api = FakeApi()  # never converges: one capped poll attempt
        _run_orchestrator(
            FakeRunner(),
            api,
            bounds={"api_poll_s": 1, "poll_interval_s": 2},
            sleeper=lambda s: naps.append(s),
        )
        self.assertEqual(api.snapshots, 2)  # baseline + one capped attempt
        for nap in naps:
            self.assertLessEqual(nap, 1.0)

    def test_written_runtime_files_are_scanned_and_leak_is_terminal(self):
        cleanup = FakeCleanup()
        real_override = smoke.write_bootstrap_override
        real_create = smoke.FixtureFactory.create

        def override_leak(path, values):
            real_override(path, values)
            path.write_text("bootstrap: sk-lf-secret\n", encoding="utf-8")

        def fixture_leak(factory, root):
            expected = real_create(factory, root)
            board = Path(root) / "leaderboard.json"
            board.write_bytes(board.read_bytes() + b' "sk-lf-secret"')
            return expected

        for target, name, leak in (
            (smoke, "write_bootstrap_override", override_leak),
            (smoke.FixtureFactory, "create", fixture_leak),
        ):
            with self.subTest(artifact=name):
                with mock.patch.object(target, name, leak):
                    report = _run_orchestrator(FakeRunner(), cleanup=cleanup)
                self.assertEqual(
                    report["primary_failure"]["code"], "credential_exposure"
                )
                self.assertEqual(report["exporter_invocations"], 0)
                self.assertNotIn("sk-lf-secret", json.dumps(report))
                self.assertTrue(cleanup.executed)  # cleanup after exposure
                self.assertTrue(report["phases"]["diagnostics"])
                self.assertEqual(report["overall"], "FAIL")  # redaction never passes

    def test_final_report_exposure_is_terminal_and_redaction_cannot_pass(self):
        authority = smoke.ReportAuthority(smoke.LeakGuard(sink=_Sink()))
        authority.guard.register("r3port-secret", "run_id")
        authority.run = {"run_id": "r3port-secret"}
        report = authority.finalize()
        text = authority.emit_text(report)
        self.assertEqual(authority.primary_failure["code"], "credential_exposure")
        self.assertEqual(
            (report["smoke_assertions"], report["overall"], report["reason"]),
            ("FAIL", "FAIL", "credential_exposure"),
        )
        self.assertNotIn("r3port-secret", text)
        self.assertFalse(report["security"]["artifacts_sanitized"])
        second = smoke.ReportAuthority(smoke.LeakGuard(sink=_Sink()))
        second.guard.register("r3port-secret", "run_id")
        second.primary(smoke.SmokeFailure("export_1", "export_command_failed", "x"))
        second.run = {"run_id": "r3port-secret"}
        report2 = second.finalize()
        second.emit_text(report2)
        self.assertEqual(report2["primary_failure"]["code"], "export_command_failed")
        self.assertTrue(
            any(
                s["code"] == "credential_exposure"
                for s in report2["secondary_failures"]
            )
        )

    def test_written_files_scan_fail_closed_binary_and_redact_report(self):
        guard = smoke.LeakGuard(sink=_Sink())
        guard.register("file-secret", "k")
        with tempfile.TemporaryDirectory() as name:
            clean = Path(name) / "clean.json"
            clean.write_text("{}", encoding="utf-8")
            smoke.ensure_clean_file(guard, clean)  # clean text passes
            leaked = Path(name) / "leaked.json"
            leaked.write_text('{"a": "file-secret"}', encoding="utf-8")
            with self.assertRaises(smoke.CredentialExposureError):
                smoke.ensure_clean_file(guard, leaked)
            binary = Path(name) / "blob.bin"
            binary.write_bytes(b"\xff\xfe\x00file-secret")
            with self.assertRaises(smoke.SmokeFailure) as caught:
                smoke.ensure_clean_file(guard, binary)
            self.assertEqual(caught.exception.code, "artifact_unscannable")
            report = Path(name) / "report.json"
            smoke._write_scanned_report(guard, report, '{"overall": "PASS"}')
            self.assertEqual(
                report.read_text(encoding="utf-8"), '{"overall": "PASS"}\n'
            )
            with self.assertRaises(smoke.CredentialExposureError):
                smoke._write_scanned_report(guard, report, '{"v": "file-secret"}')
            self.assertNotIn("file-secret", report.read_text(encoding="utf-8"))
        source = (ROOT / "observability" / "smoke.py").read_text(encoding="utf-8")
        self.assertIn("config.report_path", source)

    def test_baseline_rejects_inconsistent_snapshots_before_export(self):
        def snap(t, g, s, total):
            return {
                "traces": {"count": t[0], "ids": tuple(t[1])},
                "generations": {"count": g[0], "ids": tuple(g[1])},
                "scores": {"count": s[0], "ids": tuple(s[1])},
                "observations_total": total,
            }

        e = (0, [])
        cases = {
            "zero_count_with_ids": snap((0, ["tr_pre"]), e, e, 0),
            "count_mismatch": snap((2, ["tr_a"]), e, e, 0),
            "duplicate_ids": snap((2, ["tr_a", "tr_a"]), e, e, 0),
            "negative_count": snap((-1, []), e, e, 0),
            "bool_count": snap((True, ["tr_a"]), e, e, 0),
            "non_string_ids": snap((1, [123]), e, e, 0),
            "bool_total": snap(e, e, e, True),
            "negative_total": snap(e, e, e, -2),
            "total_below_generations": snap(e, (1, ["gen_a"]), e, 0),
            "malformed": {"traces": {"count": 0}},
        }
        for name, snapshot in cases.items():
            with self.subTest(case=name):
                api = FakeApi()
                api.snapshot = lambda fixed=snapshot: fixed
                report = _run_orchestrator(FakeRunner(), api)
                failure = report["primary_failure"]
                self.assertEqual(
                    (failure["phase"], failure["code"]),
                    ("baseline", "baseline_inconsistent"),
                    name,
                )
                self.assertEqual(report["exporter_invocations"], 0, name)
        api = FakeApi()  # consistent but nonempty keeps the existing failure
        api.state.update({"traces": ["tr_pre"], "observations_total": 1})
        report = _run_orchestrator(FakeRunner(), api)
        self.assertEqual(report["primary_failure"]["code"], "baseline_not_empty")


class ContinuationAuditTest(unittest.TestCase):
    """Apply-continuation audit: real-run seams the fakes previously masked.

    RED evidence for the evidence-conformant reconciliation: the worker service
    must actually be started before its proof, every model-loading compose
    command must carry the generated runtime env (the compose files interpolate
    ``${VAR:?}`` guards at load), the cleanup report must carry the exact
    project label, a completed export must stay PASS when its API proof fails,
    temp-deletion failure must surface as ``cleanup_failed``, and concurrent
    runs must hold distinct isolation identities.
    """

    @staticmethod
    def _happy_api():
        api = FakeApi()
        api.on_snapshot = OrchestratorHappyPathTest._seed_at(2)
        return api

    def test_worker_service_is_started_before_worker_proof(self):
        runner = FakeRunner()
        _run_orchestrator(runner, self._happy_api())
        ups = [
            call
            for call in runner.calls
            if "up" in call["argv"] and "langfuse-worker" in call["argv"]
        ]
        self.assertEqual(len(ups), 1)
        self.assertIn("--no-deps", ups[0]["argv"])

    def test_model_loading_compose_commands_carry_generated_env(self):
        runner = FakeRunner()
        _run_orchestrator(runner, self._happy_api())
        # Six orchestrator-issued model-loading calls are visible here (deps/web
        # up, worker ps/logs, diagnostics ps/logs); the inventory ps and cleanup
        # down calls run behind FakeInventory/FakeCleanup and are covered at
        # seam level in ImageInventoryTest/CleanupManagerTest.
        model_calls = [
            call
            for call in runner.calls
            if "compose" in call["argv"]
            and {"up", "ps", "logs", "down"} & set(call["argv"])
        ]
        self.assertGreaterEqual(len(model_calls), 6)
        for call in model_calls:
            with self.subTest(argv=call["argv"][:6]):
                self.assertIn("LANGFUSE_PUBLIC_KEY", call["env"])
                self.assertIn("LANGFUSE_SECRET_KEY", call["env"])

    def test_temp_deletion_failure_is_cleanup_failed(self):
        runner = FakeRunner()

        def removed(_path, **_kwargs):
            raise OSError("temp busy")

        with mock.patch.object(smoke.shutil, "rmtree", removed):
            report = _run_orchestrator(runner, self._happy_api())
        self.assertEqual(report["smoke_assertions"], "PASS")
        self.assertEqual(report["cleanup"]["status"], "FAIL")
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["reason"], "cleanup_failed")
        self.assertTrue(
            any("temp" in p for p in report["phases"]["cleanup"]["problems"])
        )

    def test_concurrent_runs_hold_distinct_isolation_identities(self):
        first = _run_orchestrator(FakeRunner(), self._happy_api())
        second = _run_orchestrator(FakeRunner(), self._happy_api())
        self.assertNotEqual(
            first["run"]["compose_project"], second["run"]["compose_project"]
        )
        self.assertNotEqual(first["run"]["run_id"], second["run"]["run_id"])

    def test_cleanup_report_carries_exact_project_label(self):
        report = _run_orchestrator(FakeRunner(), self._happy_api())
        self.assertEqual(report["cleanup"]["label"], report["run"]["compose_project"])

    def test_completed_export_stays_pass_when_its_api_proof_fails(self):
        runner = FakeRunner()
        api = FakeApi()  # never converges: proof 1 exhausts the poll budget
        report = _run_orchestrator(runner, api)
        self.assertEqual(report["primary_failure"]["phase"], "api_proof_1")
        self.assertEqual(report["phases"]["export_1"]["status"], "PASS")
        self.assertEqual(len(report["cli_exports"]), 1)
        self.assertEqual(report["exporter_invocations"], 1)


def _entrypoint(fake_run_smoke):
    """Load the real thin script with a fake ``observability.smoke.run_smoke``."""
    stub = types.ModuleType("observability.smoke")
    package = types.ModuleType("observability")
    bindings = (
        (stub, "SmokeConfig", smoke.SmokeConfig),
        (stub, "Bounds", smoke.Bounds),
        (stub, "exit_code_for", smoke.exit_code_for),
        (stub, "run_smoke", fake_run_smoke),
        (package, "smoke", stub),
    )
    for target, name, value in bindings:
        setattr(target, name, value)
    spec = importlib.util.spec_from_file_location("langfuse_smoke_entrypoint", SCRIPT)
    assert spec is not None and spec.loader is not None  # script always resolves
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules, {"observability": package, "observability.smoke": stub}
    ):
        spec.loader.exec_module(module)
    return module


class EntryPointScriptTest(unittest.TestCase):
    def test_entrypoint_routes_report_path_through_runtime_and_writes_nothing(
        self,
    ):
        events = []

        def fake_run_smoke(config):
            events.append(config)
            return {"overall": "PASS"}, '{"overall": "PASS"}'

        with tempfile.TemporaryDirectory() as name:
            compose = Path(name) / "compose.yml"
            compose.write_text("services: {}\n", encoding="utf-8")
            report = Path(name) / "final-report.json"
            script = _entrypoint(fake_run_smoke)
            code = script.main(
                ["--report", str(report), "--compose-file", str(compose)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].report_path, report)
            self.assertFalse(report.exists())  # runtime owns the scanned write
            script.main(["--compose-file", str(compose)])
            self.assertIsNone(events[-1].report_path)  # CI default writes no file
        self.assertNotIn(".write_text(", SCRIPT.read_text(encoding="utf-8"))

    def test_entrypoint_written_report_is_scanned_and_leak_is_fail_closed(self):
        def leaking_run_smoke(config):
            guard = smoke.LeakGuard()
            guard.register("final-secret", "run_id")
            if config.report_path is not None:
                # Runtime contract: write, scan written bytes, redact in place,
                # fail closed. The thin script must route through it.
                smoke._write_scanned_report(
                    guard, config.report_path, '{"v": "final-secret"}'
                )
            return {"overall": "PASS"}, '{"v": "final-secret"}'

        with tempfile.TemporaryDirectory() as name:
            compose = Path(name) / "compose.yml"
            compose.write_text("services: {}\n", encoding="utf-8")
            report = Path(name) / "final-report.json"
            stdout = io.StringIO()
            script = _entrypoint(leaking_run_smoke)
            with mock.patch.object(sys, "stdout", stdout):
                with self.assertRaises(smoke.CredentialExposureError):
                    script.main(
                        ["--report", str(report), "--compose-file", str(compose)]
                    )
            self.assertNotIn("final-secret", stdout.getvalue())
            written = report.read_text(encoding="utf-8")
            self.assertNotIn("final-secret", written)
            self.assertIn("[REDACTED]", written)

    def test_script_is_thin_and_has_no_transport_imports(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        self.assertFalse(imported & {"subprocess", "urllib", "socket", "docker"})
        self.assertIn("observability", imported)

    def test_usage_errors_exit_two_without_docker(self):
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--compose-file", "/nonexistent/compose.yml"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 2)
        self.assertNotIn("traceback", done.stderr.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
