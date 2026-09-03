"""Phase 4A external-evidence tests (T4A-07/T4A-08, P4A-REQ-05/07/08).

Exact canonical sidecar shapes for ``llm/provider-run.json`` and
``llm/llm-receipt.json``; one ordered entry per decision attempt; digest
binding to the unchanged sibling core receipt; recursive + inline secret
scrubbing with fail-closed ``secret_detected`` export; and the static,
escaped, subordinate ``llm-report.html`` appendix.

Boundary note (recorded for the orchestrator): under the FROZEN verifier,
``tool_attempts`` is derived as ``len(operations)`` over ALL operation-log
entries while the B4 recorder writes parse-level rejections into that log —
so any run transcript containing >=1 parse-level rejection (which P4A-REQ-02
mandates for provider errors) cannot release a verified receipt. Receipted
acceptance paths here therefore use all-valid fake scripts; garbage-path
coverage lives at the policy/runner/entry-shape unit level. See
docs/sdd/phase4a/apply-progress.md for the full escalation.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.evidence.artifact_store import (
    RunEvidenceRecorder,
    atomic_write_bytes,
)
from robot_factory_arena.evidence.canonical import canonical_json_bytes
from robot_factory_arena.evidence.receipt import ReceiptWriter
from robot_factory_arena.policies.llm import (
    DecisionRecord,
    LLMPolicy,
    llm_vnext_policy_digest,
    prompt_composition_digest,
    tool_schema_digest,
)
from robot_factory_arena.providers.adapter import (
    ProviderError,
    ProviderResult,
    ToolCallPayload,
    Usage,
)
from robot_factory_arena.providers.evidence import (
    SECRET_DETECTED,
    SecretDetectedError,
    assert_scrubbed,
    build_llm_receipt,
    build_provider_run,
    digest_json,
    scrub,
    write_provider_artifacts,
)
from robot_factory_arena.providers.fake import FakeProvider
from robot_factory_arena.providers.report import render_llm_report
from robot_factory_arena.runtime.runner import (
    EvidenceCallbacks,
    RunConfig,
    Runner,
    compute_logical_run_id,
)
from robot_factory_arena.verification.verifier import (
    Verifier,
    load_verification_artifacts,
)

GRANTS = ("move", "pick", "place")


def _success(tool, arguments, request_id, raw="choosing"):
    return ProviderResult(
        provider="fake",
        model="fake-v1",
        request_id=request_id,
        usage=Usage(4, 6, 10),
        latency_ms=7,
        raw_visible_response=raw,
        tool_call=ToolCallPayload(tool, arguments),
        error=None,
    )


def _error(code, message, request_id):
    return ProviderResult(
        provider="fake",
        model="fake-v1",
        request_id=request_id,
        usage=Usage(4, 0, 4),
        latency_ms=1,
        raw_visible_response=None,
        tool_call=None,
        error=ProviderError(code, message),
    )


def _policy(*script):
    return LLMPolicy(FakeProvider(script=list(script)))


def _config(task):
    return RunConfig(
        1, task.task_version, task.seed, "llm-v1",
        llm_vnext_policy_digest("fake", "fake-v1"), 100, GRANTS, None,
    )


def _run_decide(policy, used):
    policy.decide({
        "schema_version": 1,
        "logical_run_id": "lr_" + "ab" * 32,
        "world_state": {"step": used},
        "grants": ["move", "pick", "place"],
        "policy_steps": {"used": used, "remaining": 100 - used, "maximum": 100},
        "last_result": None,
    })


def _completion_script(seed="18"):
    """The deterministic greedy completion plan for ``seed`` as a fake script.

    Every entry is a legal normalized call, so the LLM run replicates a
    completing run: all op-log entries are submissions (the frozen verifier's
    tool_attempts derivation holds) and the run terminates before the script
    could ever be exhausted. Seed 18 completes in 92 greedy steps (AMEND-01).
    """
    from robot_factory_arena.policies.greedy import GreedyPolicy

    task = generate_task(seed)
    config = RunConfig(
        1, task.task_version, task.seed, "llm-v1",
        llm_vnext_policy_digest("fake", "fake-v1"), 100, GRANTS, None,
    )
    observations = {}

    def on_observation(step, serialized):
        observations[step] = serialized

    result = Runner(
        evidence=EvidenceCallbacks(on_observation=on_observation)
    ).run(task, GreedyPolicy(), config, "exec-plan")
    assert result.outcome == "completed", result.outcome
    return [
        _success(call.tool, dict(call.arguments), f"request-{step}")
        for step, call in (
            (step, GreedyPolicy().decide(observations[step]))
            for step in sorted(observations)
        )
    ]


def _completed_run(root, script, seed="18"):
    """A verified, receipt-released LLM run directory (B5-proven composition).

    The script must be all-valid AND run to completion: under the frozen
    verifier a single parse-level rejection breaks the tool_attempts counter
    derivation (see the module docstring and apply-progress escalation note).
    """
    task = generate_task(seed)
    config = _config(task)
    policy = _policy(*script)
    lr = compute_logical_run_id(task, config)
    recorder = RunEvidenceRecorder(root, task, config, lr, "exec-llm-1")
    Runner(evidence=EvidenceCallbacks(**recorder.callback_map())).run(
        task, policy, config, "exec-llm-1"
    )
    recorder.close()
    verification = Verifier().verify(load_verification_artifacts(recorder.directory))
    assert verification.valid, [v.code for v in verification.violations]
    receipt = ReceiptWriter.write_final(recorder.receipt_candidate, verification)
    atomic_write_bytes(
        Path(recorder.directory) / "receipt.json", canonical_json_bytes(receipt)
    )
    # The core report.html (B6 renderer) — the appendix must never touch it.
    from robot_factory_arena.cli import collect_artifact_digests
    from robot_factory_arena.evidence.report import render_report

    atomic_write_bytes(
        Path(recorder.directory) / "report.html",
        render_report(
            receipt, verification, collect_artifact_digests(recorder.directory)
        ).encode("utf-8"),
    )
    return Path(recorder.directory), policy, lr


def _sidecars(directory):
    provider_run = json.loads(
        (directory / "llm" / "provider-run.json").read_text("utf-8")
    )
    llm_receipt = json.loads(
        (directory / "llm" / "llm-receipt.json").read_text("utf-8")
    )
    return provider_run, llm_receipt


class TestScrubbing(unittest.TestCase):
    """P4A-REQ-07: deterministic redaction, then fail-closed detection."""

    def test_sensitive_field_values_are_replaced_recursively(self):
        value = {
            "api_key": "sk-abc",
            "Authorization": "Bearer xyz",
            "nested": {"db_password": "hunter2", "keep": "plain"},
            "openai_token": "t-1",
            "MY_SECRET": "s",
            "service_credential": "c",
        }
        scrubbed = scrub(value)
        assert isinstance(scrubbed, dict)
        self.assertEqual(scrubbed["api_key"], "[REDACTED]")
        self.assertEqual(scrubbed["Authorization"], "[REDACTED]")
        self.assertEqual(scrubbed["nested"]["db_password"], "[REDACTED]")
        self.assertEqual(scrubbed["nested"]["keep"], "plain")
        self.assertEqual(scrubbed["openai_token"], "[REDACTED]")
        self.assertEqual(scrubbed["MY_SECRET"], "[REDACTED]")
        self.assertEqual(scrubbed["service_credential"], "[REDACTED]")

    def test_inline_credential_forms_are_redacted(self):
        text = "auth Bearer abc123 then api_key=xyz and token=t1 secret=s9 sk-abc"
        scrubbed = scrub(text)
        assert isinstance(scrubbed, str)
        self.assertNotIn("abc123", scrubbed)
        self.assertNotIn("xyz", scrubbed)
        self.assertNotIn("t1", scrubbed)
        self.assertNotIn("s9", scrubbed)
        self.assertNotIn("sk-abc", scrubbed)
        self.assertIn("[REDACTED]", scrubbed)

    def test_non_string_scalars_pass_through(self):
        self.assertEqual(scrub({"n": 3, "ok": True, "none": None}),
                         {"n": 3, "ok": True, "none": None})

    def test_residual_env_assignment_fails_closed_with_secret_detected(self):
        with self.assertRaises(SecretDetectedError) as caught:
            assert_scrubbed({"response": "export OPENAI_API_KEY=abc123"})
        self.assertTrue(
            str(caught.exception).startswith("secret_detected: provider evidence")
        )
        self.assertNotIn("abc123", str(caught.exception))
        self.assertNotIn("abc123", repr(caught.exception))

    def test_residual_inline_credential_fails_closed(self):
        with self.assertRaises(SecretDetectedError):
            assert_scrubbed({"note": "Bearer something-else-entirely"})

    def test_scrubbed_content_passes_the_gate(self):
        assert_scrubbed(scrub({"message": "Bearer abc", "api_key": "sk-z"}))

    def test_the_detection_marker_is_the_spec_error_code(self):
        self.assertEqual(SECRET_DETECTED, "secret_detected")


class TestProviderRunEntryShapes(unittest.TestCase):
    """P4A-REQ-05: exact entry shapes, ordered, conforming-or-null tool calls."""

    def test_entries_carry_the_exact_evidence_fields_in_step_order(self):
        policy = _policy(
            _success("move", {"direction": "north"}, "request-1"),
            _success("pick",
                     {"shelf_id": "shelf-03", "item_id": "item-04"},
                     "request-2"),
        )
        _run_decide(policy, 0)
        _run_decide(policy, 1)
        provider_run = build_provider_run(
            "lr_" + "ab" * 32, "exec-x", policy.decisions
        )
        self.assertEqual(
            set(provider_run),
            {"schema_version", "logical_run_id", "execution_id", "requests"},
        )
        first, second = provider_run["requests"]
        for entry in (first, second):
            self.assertEqual(
                set(entry),
                {
                    "policy_step", "provider", "model", "request_id", "usage",
                    "latency_ms", "raw_visible_response", "tool_call", "error",
                },
            )
            self.assertEqual(set(entry["usage"]),
                             {"input_tokens", "output_tokens", "total_tokens"})
            self.assertIsNone(entry["error"])
        self.assertEqual([entry["policy_step"] for entry in provider_run["requests"]],
                         [1, 2])
        self.assertEqual(first["tool_call"],
                         {"tool": "move", "arguments": {"direction": "north"}})
        self.assertEqual(
            second["tool_call"],
            {"tool": "pick",
             "arguments": {"shelf_id": "shelf-03", "item_id": "item-04"}},
        )

    def test_error_results_become_error_entries_with_null_tool_calls(self):
        policy = _policy(_error("no_tool_call", "model answered in prose",
                                "request-1"))
        _run_decide(policy, 0)
        provider_run = build_provider_run(
            "lr_" + "ab" * 32, "exec-x", policy.decisions
        )
        entry = provider_run["requests"][0]
        self.assertIsNone(entry["tool_call"])
        self.assertEqual(entry["error"],
                         {"code": "no_tool_call",
                          "message": "model answered in prose"})

    def test_non_conforming_tool_calls_are_recorded_as_null(self):
        policy = _policy(
            _success("assemble", {}, "request-1"),
            _success("move", {"direction": "up"}, "request-2"),
        )
        _run_decide(policy, 0)
        _run_decide(policy, 1)
        provider_run = build_provider_run(
            "lr_" + "ab" * 32, "exec-x", policy.decisions
        )
        self.assertIsNone(provider_run["requests"][0]["tool_call"])
        self.assertIsNone(provider_run["requests"][1]["tool_call"])

    def test_provider_run_rejects_disagreeing_pinned_identities(self):
        first = ProviderResult(
            provider="openai",
            model="gpt-x",
            request_id="request-1",
            usage=Usage(1, 1, 2),
            latency_ms=0,
            raw_visible_response=None,
            tool_call=None,
            error=ProviderError("no_tool_call", "prose"),
        )
        second = ProviderResult(
            provider="fake",
            model="fake-v1",
            request_id="request-2",
            usage=Usage(1, 1, 2),
            latency_ms=0,
            raw_visible_response=None,
            tool_call=None,
            error=ProviderError("no_tool_call", "prose"),
        )
        records = (
            DecisionRecord(1, "request-1", first),
            DecisionRecord(2, "request-2", second),
        )
        with self.assertRaises(ValueError):
            build_provider_run("lr_" + "ab" * 32, "exec-x", records)


class TestSidecarShapes(unittest.TestCase):
    """P4A-REQ-05: exact envelopes and digest binding for a RECEIPTED run."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.script = _completion_script()
        self.directory, self.policy, self.logical_run_id = _completed_run(
            self.root, self.script
        )
        write_provider_artifacts(
            self.directory,
            self.logical_run_id,
            "exec-llm-1",
            prompt_composition_digest(),
            tool_schema_digest(),
            self.policy.decisions,
            self.directory / "receipt.json",
        )

    def tearDown(self):
        self._temporary.cleanup()

    def test_both_sidecars_live_only_under_run_dir_llm(self):
        self.assertTrue((self.directory / "llm" / "provider-run.json").is_file())
        self.assertTrue((self.directory / "llm" / "llm-receipt.json").is_file())
        self.assertEqual(
            sorted(path.name for path in (self.directory / "llm").iterdir()),
            ["llm-receipt.json", "provider-run.json"],
        )

    def test_provider_run_has_the_exact_envelope_and_entry_shape(self):
        provider_run, _ = _sidecars(self.directory)
        self.assertEqual(
            set(provider_run),
            {"schema_version", "logical_run_id", "execution_id", "requests"},
        )
        self.assertEqual(provider_run["schema_version"], 1)
        self.assertEqual(provider_run["logical_run_id"], self.logical_run_id)
        self.assertEqual(provider_run["execution_id"], "exec-llm-1")
        self.assertEqual(len(provider_run["requests"]), len(self.script))
        first, second = provider_run["requests"][:2]
        for entry in (first, second):
            self.assertEqual(
                set(entry),
                {
                    "policy_step", "provider", "model", "request_id", "usage",
                    "latency_ms", "raw_visible_response", "tool_call", "error",
                },
            )
            self.assertEqual(set(entry["usage"]),
                             {"input_tokens", "output_tokens", "total_tokens"})
        self.assertEqual(first["policy_step"], 1)
        self.assertEqual(first["tool_call"],
                         {"tool": "move", "arguments": {"direction": "north"}})
        self.assertIsNone(first["error"])
        self.assertEqual(second["policy_step"], 2)
        self.assertIsNone(second["error"])

    def test_entries_are_ordered_by_ascending_policy_step_one_per_attempt(self):
        provider_run, _ = _sidecars(self.directory)
        steps = [entry["policy_step"] for entry in provider_run["requests"]]
        self.assertEqual(steps, list(range(1, len(self.script) + 1)))

    def test_llm_receipt_has_the_exact_envelope_and_all_five_digests(self):
        _, llm_receipt = _sidecars(self.directory)
        self.assertEqual(
            set(llm_receipt),
            {
                "schema_version", "logical_run_id", "execution_id", "provider",
                "model", "prompt_digest", "tool_schema_digest",
                "provider_evidence_digest", "core_receipt_digest",
                "llm_receipt_digest",
            },
        )
        self.assertEqual(llm_receipt["logical_run_id"], self.logical_run_id)
        self.assertEqual(llm_receipt["execution_id"], "exec-llm-1")
        self.assertEqual(llm_receipt["provider"], "fake")
        self.assertEqual(llm_receipt["model"], "fake-v1")
        self.assertEqual(llm_receipt["prompt_digest"], prompt_composition_digest())
        self.assertEqual(llm_receipt["tool_schema_digest"], tool_schema_digest())
        for value in llm_receipt.values():
            if isinstance(value, str) and value.startswith("sha256:"):
                self.assertRegex(value, r"sha256:[0-9a-f]{64}")

    def test_provider_evidence_digest_hashes_the_exact_provider_run(self):
        provider_run, llm_receipt = _sidecars(self.directory)
        self.assertEqual(
            llm_receipt["provider_evidence_digest"], digest_json(provider_run)
        )

    def test_core_receipt_digest_hashes_the_unchanged_sibling_receipt(self):
        from robot_factory_arena.evidence.canonical import sha256_digest

        _, llm_receipt = _sidecars(self.directory)
        raw = (self.directory / "receipt.json").read_bytes()
        self.assertEqual(llm_receipt["core_receipt_digest"], sha256_digest(raw))

    def test_llm_receipt_digest_is_not_self_referential(self):
        provider_run, llm_receipt = _sidecars(self.directory)
        without = {
            key: value
            for key, value in llm_receipt.items()
            if key != "llm_receipt_digest"
        }
        self.assertEqual(
            llm_receipt["llm_receipt_digest"], digest_json(without)
        )

    def test_sidecars_contain_no_prompt_text_headers_credentials_or_keys(self):
        text = "\n".join(
            path.read_text("utf-8")
            for path in (self.directory / "llm").iterdir()
        )
        for forbidden in ("operation_key", "Authorization", "api_key",
                          "Serialized observation", "Bearer", '"world_state"'):
            self.assertNotIn(forbidden, text)

    def test_deleting_llm_leaves_the_core_evidence_untouched(self):
        before = {
            name: (self.directory / name).read_bytes()
            for name in ("receipt.json", "trace.jsonl", "report.html")
            if (self.directory / name).is_file()
        }
        shutil.rmtree(self.directory / "llm")
        after = {
            name: (self.directory / name).read_bytes()
            for name in before
        }
        self.assertEqual(before, after)


class TestFailClosedExport(unittest.TestCase):
    """P4A-REQ-07: residual secrets prevent ANY sidecar from being written."""

    def test_secret_bearing_response_writes_nothing_and_never_leaks(self):
        # Scrubbing redacts Bearer/api-key=/sk- forms BEFORE serialization
        # (P4A-REQ-07 rule 2); fail-closed export is for RESIDUAL secrets a
        # scrub cannot normalize — here an env-looking APP_AUTH assignment.
        script = _completion_script()
        script[0] = _success(
            "move", {"direction": "north"}, "request-1",
            raw="export APP_AUTH=zzz-abc123",
        )
        with tempfile.TemporaryDirectory() as root:
            directory, policy, logical_run_id = _completed_run(
                Path(root), script
            )
            with self.assertRaises(SecretDetectedError) as caught:
                write_provider_artifacts(
                    directory, logical_run_id, "exec-llm-1",
                    prompt_composition_digest(), tool_schema_digest(),
                    policy.decisions, directory / "receipt.json",
                )
            self.assertIn("secret_detected", str(caught.exception))
            self.assertFalse((directory / "llm").exists())
            leaking = [
                path.name
                for path in directory.rglob("*")
                if path.is_file()
                and "abc123" in path.read_bytes().decode("utf-8", "replace")
            ]
            self.assertEqual(leaking, [])

    def test_bearer_forms_are_redacted_before_serialization(self):
        # The spec's own scenario: 'Authorization: Bearer secret-value' in the
        # visible response is redacted; the sidecars are written and contain
        # no original value.
        script = _completion_script()
        script[0] = _success(
            "move", {"direction": "north"}, "request-1",
            raw="Authorization: Bearer sk-live-abc123",
        )
        with tempfile.TemporaryDirectory() as root:
            directory, policy, logical_run_id = _completed_run(
                Path(root), script
            )
            write_provider_artifacts(
                directory, logical_run_id, "exec-llm-1",
                prompt_composition_digest(), tool_schema_digest(),
                policy.decisions, directory / "receipt.json",
            )
            text = "\n".join(
                path.read_text("utf-8")
                for path in (directory / "llm").iterdir()
            )
            self.assertNotIn("abc123", text)
            self.assertIn("[REDACTED]", text)


class TestStaticLLMReport(unittest.TestCase):
    """P4A-REQ-08: escaped, dependency-free, subordinate appendix."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.script = _completion_script()
        self.script[0] = _success(
            "move", {"direction": "north"}, "request-1",
            raw="moving <script>alert(1)</script>",
        )
        self.directory, self.policy, self.logical_run_id = _completed_run(
            self.root, self.script
        )
        self.llm_receipt = write_provider_artifacts(
            self.directory,
            self.logical_run_id,
            "exec-llm-1",
            prompt_composition_digest(),
            tool_schema_digest(),
            self.policy.decisions,
            self.directory / "receipt.json",
        )
        self.provider_run, _ = _sidecars(self.directory)
        self.core_receipt = json.loads(
            (self.directory / "receipt.json").read_text("utf-8")
        )

    def tearDown(self):
        self._temporary.cleanup()

    def _render(self, llm_receipt=None, provider_run=None):
        return render_llm_report(
            self.core_receipt,
            self.llm_receipt if llm_receipt is None else llm_receipt,
            self.provider_run if provider_run is None else provider_run,
        )

    def test_page_shows_provider_model_and_all_five_digests(self):
        html = self._render()
        self.assertIn("fake", html)
        self.assertIn("fake-v1", html)
        for digest in (
            self.llm_receipt["prompt_digest"],
            self.llm_receipt["tool_schema_digest"],
            self.llm_receipt["provider_evidence_digest"],
            self.llm_receipt["core_receipt_digest"],
            self.llm_receipt["llm_receipt_digest"],
        ):
            self.assertIn(digest, html)

    def test_usage_totals_are_deterministic_sums_of_the_entries(self):
        html = self._render()
        total = len(self.script)
        self.assertIn(str(4 * total), html)   # input tokens
        self.assertIn(str(6 * total), html)   # output tokens
        self.assertIn(str(10 * total), html)  # total tokens

    def test_per_request_table_rows_carry_the_evidence_fields(self):
        html = self._render()
        self.assertIn("request-1", html)
        self.assertIn("request-2", html)
        self.assertIn("none", html)  # error column for clean entries
        self.assertGreaterEqual(html.count("<tr>"), 3)  # header + two rows

    def test_error_entries_render_their_code_and_message(self):
        provider_run = build_provider_run(
            self.logical_run_id,
            "exec-llm-1",
            (
                DecisionRecord(1, "request-1",
                               _error("no_tool_call",
                                      "model answered in prose", "request-1")),
            ),
        )
        core_bytes = (self.directory / "receipt.json").read_bytes()
        receipt = build_llm_receipt(
            self.logical_run_id, "exec-llm-1", "fake", "fake-v1",
            prompt_composition_digest(), tool_schema_digest(),
            provider_run, core_bytes,
        )
        html = render_llm_report(self.core_receipt, receipt, provider_run)
        self.assertIn("no_tool_call", html)
        self.assertIn("model answered in prose", html)
        self.assertIn("rejected", html)

    def test_dynamic_values_are_html_escaped(self):
        html = self._render()
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_no_script_javascript_network_urls_cdns_or_sdks(self):
        html = self._render().lower()
        for forbidden in ("<script", "javascript:", "http://", "https://",
                          "cdn", "fetch(", "xmlhttprequest", "sdk"):
            self.assertNotIn(forbidden, html)

    def test_core_outcome_and_score_are_authoritative_with_marker(self):
        html = self._render()
        self.assertIn(self.core_receipt["outcome"], html)
        self.assertIn(
            str(self.core_receipt["verification"]["verified_score"]), html
        )
        self.assertIn("report.html", html)
        self.assertIn("authoritative", html)

    def test_missing_sidecars_render_an_external_evidence_error(self):
        html = render_llm_report(self.core_receipt, None, None)
        self.assertIn("external-evidence error", html)
        self.assertIn(self.core_receipt["outcome"], html)
        self.assertNotIn("provider_evidence_digest", html)

    def test_mismatched_binding_renders_an_error_not_a_core_change(self):
        tampered = dict(self.provider_run)
        tampered["logical_run_id"] = "lr_" + "ff" * 32
        html = self._render(provider_run=tampered)
        self.assertIn("external-evidence error", html)
        self.assertIn(self.core_receipt["outcome"], html)

    def test_rendering_is_deterministic(self):
        self.assertEqual(self._render(), self._render())

    def test_core_report_is_never_touched_by_the_appendix(self):
        core_before = (self.directory / "report.html").read_bytes()
        self._render()
        self._render(llm_receipt=None, provider_run=None)
        self.assertEqual(
            core_before, (self.directory / "report.html").read_bytes()
        )


if __name__ == "__main__":
    unittest.main()
