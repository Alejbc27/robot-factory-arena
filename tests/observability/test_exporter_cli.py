"""Unit B export/CLI tests: composition, guards, orchestration, exit codes.

Covers the remaining Unit B halves (tasks 1/2/4/5/6): validated-item batch
composition with the design's deterministic IDs, metadata-only default,
``--include-text`` eligibility (secret scan + 64 KiB field / 512 KiB item
limits), the final recursive payload guard, independent item continuation with
a redacted machine-readable report, and CLI exit semantics (2 usage/config,
0 clean, 1 skip/failure) over the in-process stub from the transport tests.
No live Langfuse, no external network, no retained background process.
"""

import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from unittest import mock

# tests/observability/__init__.py loads the runtime package under a private
# alias; the Unit B modules are bound through it. The alias exists only at
# runtime, so static import resolution is impossible by design; execution
# (python3 -m unittest) is the authority — same recorded pattern as the
# transport tests.
import observability_runtime.cli as cli  # pyright: ignore[reportMissingImports]
import observability_runtime.exporter as exporter  # pyright: ignore[reportMissingImports]
import observability_runtime.langfuse_http as langfuse_http  # pyright: ignore[reportMissingImports]
import observability_runtime.report as report  # pyright: ignore[reportMissingImports]

from observability import assemble_items, canonical_json_bytes, ids, model

from .test_artifacts import (
    _TOURNAMENT,
    _bound_row,
    _row,
    _sealed_root,
    write_leaderboard,
)
from .test_langfuse_http import StubLangfuse, _closed_port, _Recorder


def _digest(byte):
    return "sha256:" + byte * 32


def _declined_root(prefix, condition="R2", seed="19"):
    """Leaderboard-only root with one declined trace-only row."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    write_leaderboard(tmp, [_row(condition, seed, "declined")])
    return tmp


def _root_bytes(root):
    """Every artifact byte the export may read, for source-immutability checks."""
    paths = [
        root / "leaderboard.json",
        root / "attempts" / "attempt-t1" / "launch.json",
    ]
    run_dir = root / "core" / "runs"
    for logical in sorted(run_dir.iterdir()):
        for execution in sorted(logical.iterdir()):
            paths += [
                execution / "receipt.json",
                execution / "llm" / "provider-run.json",
                execution / "llm" / "llm-receipt.json",
            ]
    return {str(path): path.read_bytes() for path in paths}


def _marker_root(prefix):
    """Fixture root with distinctive response text and a move tool call."""

    def inject(run_dir):
        path = run_dir / "llm" / "provider-run.json"
        value = json.loads(path.read_text())
        value["requests"][0]["raw_visible_response"] = "RESPONSE-MARKER-7q"
        value["requests"][0]["tool_call"] = {
            "tool": "move",
            "arguments": {"direction": "north"},
        }
        path.write_bytes(canonical_json_bytes(value))

    return _sealed_root(prefix, [_bound_row()], patch=inject)[0]


def _secret_root(prefix):
    """Fixture root whose provider response carries a secret pattern."""

    def inject(run_dir):
        path = run_dir / "llm" / "provider-run.json"
        value = json.loads(path.read_text())
        value["requests"][0]["raw_visible_response"] = "sk-secret-response"
        path.write_bytes(canonical_json_bytes(value))

    return _sealed_root(prefix, [_bound_row()], patch=inject)[0]


def _synthetic_run(response: str | None = "response-text-marker"):
    """A ValidatedRun under full test control (no fixture binding needed)."""
    request = model.ProviderRequest(
        policy_step=0,
        provider="fake",
        model="fake-v1",
        request_id="req-0",
        latency_ms=42,
        usage=MappingProxyType(
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        ),
        raw_visible_response=None if response is None else model.GuardedText(response),
        tool_call=model.ToolCall(
            "move", MappingProxyType({}), model.GuardedText({"direction": "north"})
        ),
        error=None,
    )
    return model.ValidatedRun(
        tournament_id=_TOURNAMENT,
        condition="R1",
        seed="18",
        status="completed",
        outcome="completed",
        verified_score=100,
        attempt_id="attempt-t1",
        attempt_ordinal=0,
        execution_id="exec-1",
        logical_run_id="lr_" + "aa" * 32,
        requests=(request,),
        launch=MappingProxyType({}),
        digests=model.EvidenceDigests(
            _digest("11"), _digest("22"), _digest("33"), None, None, None, None
        ),
        attempts_used=1,
        declined_launches=0,
        harness_version="h-test",
        core_version="c-test",
    )


def _with_requests(item, **request_changes):
    """One validated run whose single request carries ``request_changes``."""
    request = replace(item.requests[0], **request_changes)
    return replace(item, requests=(request,))


def _bound_fixture_ids(root, digests):
    """Design IDs for the bound fixture row of ``_sealed_root`` roots."""
    trace = ids.trace_id(
        _TOURNAMENT, "attempt-t1", "sealed-fixture-01", digests[0], digests[2]
    )
    return {
        "trace": trace,
        "generation": ids.generation_id("sealed-fixture-01", "req-0", digests[1]),
        "score": ids.score_id(trace, "verified_score", digests[0]),
    }


def _post_script(*outcomes, fail_first=0):
    """Health-OK stub script: the first ``fail_first`` POSTs fail, then upsert."""
    state = {"posts": 0}

    def behavior(method, path, body, server):
        if method == "GET":
            return 200, {"status": "OK", "version": "3.114.0"}
        state["posts"] += 1
        if state["posts"] <= fail_first:
            return outcomes[0] if outcomes else (400, {})
        return server.upsert_ok(method, path, body, server)

    return behavior


def _always(status, payload):
    return lambda method, path, body, server: (status, payload)


class TestBatchComposition(unittest.TestCase):
    """Task 2: one request batch per validated item with the design IDs."""

    def test_bound_item_batch_matches_design_contract(self):
        batch = exporter.build_batch(_synthetic_run())
        self.assertEqual(
            [event["type"] for event in batch],
            ["trace-create", "generation-create", "score-create"],
        )
        trace_id = ids.trace_id(
            _TOURNAMENT, "attempt-t1", "exec-1", _digest("11"), _digest("33")
        )
        trace, generation, score = batch
        self.assertEqual(trace["id"], ids.event_id("trace-create", trace_id))
        body = trace["body"]
        self.assertEqual(body["id"], trace_id)
        self.assertEqual(body["name"], "robot-factory-attempt")
        self.assertEqual(body["userId"], _TOURNAMENT)
        self.assertEqual(
            body["metadata"],
            {
                "artifact_digests": {
                    "core_receipt": _digest("11"),
                    "llm_receipt": _digest("33"),
                    "provider_evidence": _digest("22"),
                    "policy": None,
                    "prompt": None,
                    "strategy_context": None,
                    "tool_schema": None,
                },
                "attempt_id": "attempt-t1",
                "attempt_ordinal": 0,
                "attempts_used": 1,
                "condition": "R1",
                "core_version": "c-test",
                "declined_launches": 0,
                "execution_id": "exec-1",
                "harness_version": "h-test",
                "logical_run_id": "lr_" + "aa" * 32,
                "outcome": "completed",
                "seed": "18",
                "status": "completed",
                "tournament_id": _TOURNAMENT,
            },
        )
        gen_id = ids.generation_id("exec-1", "req-0", _digest("22"))
        self.assertEqual(generation["id"], ids.event_id("generation-create", gen_id))
        gen_body = generation["body"]
        self.assertEqual(gen_body["id"], gen_id)
        self.assertEqual(gen_body["traceId"], trace_id)
        self.assertEqual(gen_body["name"], "provider-request")
        self.assertEqual(gen_body["model"], "fake-v1")
        self.assertEqual(gen_body["usage"], {"input": 10, "output": 5, "total": 15})
        self.assertEqual(
            gen_body["metadata"],
            {
                "request_id": "req-0",
                "policy_step": 0,
                "latency_ms": 42,
                "provider": "fake",
                "error": None,
                "tool_call": {"tool": "move", "structural_fields": {}},
            },
        )
        score_id = ids.score_id(trace_id, "verified_score", _digest("11"))
        self.assertEqual(score["id"], ids.event_id("score-create", score_id))
        self.assertEqual(
            score["body"],
            {
                "id": score_id,
                "traceId": trace_id,
                "name": "verified_score",
                "value": 100,
                "dataType": "NUMERIC",
                "metadata": {"status": "valid", "source": "receipt"},
            },
        )

    def test_metadata_only_default_omits_every_text_capable_value(self):
        item = _synthetic_run()
        payload = canonical_json_bytes({"batch": exporter.build_batch(item)})
        text = payload.decode("utf-8")
        self.assertNotIn("response-text-marker", text)
        self.assertNotIn("north", text)
        self.assertNotIn("raw_visible_response", text)
        self.assertNotIn('"arguments"', text)
        generation_body = exporter.build_batch(item)[1]["body"]
        self.assertNotIn("output", generation_body)
        self.assertNotIn("input", generation_body)

    def test_include_text_copies_eligible_values_into_exact_fields(self):
        batch = exporter.build_batch(_synthetic_run(), include_text=True)
        generation_body = batch[1]["body"]
        self.assertEqual(generation_body["output"], "response-text-marker")
        self.assertEqual(
            generation_body["metadata"]["tool_call"],
            {
                "tool": "move",
                "structural_fields": {},
                "arguments": {"direction": "north"},
            },
        )

    def test_include_text_with_declared_nulls_omits_output(self):
        item = _with_requests(_synthetic_run(response=None), tool_call=None)
        batch = exporter.build_batch(item, include_text=True)
        generation_body = batch[1]["body"]
        self.assertNotIn("output", generation_body)
        self.assertIsNone(generation_body["metadata"]["tool_call"])

    def test_ids_do_not_depend_on_the_flag_state(self):
        item = _synthetic_run()
        metadata_ids = [
            (e["type"], e["body"]["id"]) for e in exporter.build_batch(item)
        ]
        text_ids = [
            (e["type"], e["body"]["id"])
            for e in exporter.build_batch(item, include_text=True)
        ]
        self.assertEqual(metadata_ids, text_ids)

    def test_timestamp_is_shared_per_batch_and_excluded_from_ids(self):
        times = iter(
            (
                datetime(2026, 7, 8, 12, tzinfo=timezone.utc),
                datetime(2026, 7, 8, 13, tzinfo=timezone.utc),
            )
        )
        first_batch = exporter.build_batch(_synthetic_run(), clock=lambda: next(times))
        second_batch = exporter.build_batch(_synthetic_run(), clock=lambda: next(times))
        self.assertEqual(len({event["timestamp"] for event in first_batch}), 1)
        self.assertTrue(all(event["metadata"] is None for event in first_batch))
        self.assertNotEqual(first_batch[0]["timestamp"], second_batch[0]["timestamp"])
        self.assertEqual(
            [event["id"] for event in first_batch],
            [event["id"] for event in second_batch],
        )

    def test_batch_bytes_are_deterministic_with_injected_clock_and_copies(self):
        def clock():
            return datetime(2026, 7, 8, 12, tzinfo=timezone.utc)

        first = canonical_json_bytes(
            {"batch": exporter.build_batch(_synthetic_run(), clock=clock)}
        )
        second = canonical_json_bytes(
            {"batch": exporter.build_batch(_synthetic_run(), clock=clock)}
        )
        self.assertEqual(first, second)
        root_a, _ = _sealed_root("obsb-copy-a-", [_bound_row()])
        root_b, _ = _sealed_root("obsb-copy-b-", [_bound_row()])
        batch_a, _ = assemble_items(root_a)
        batch_b, _ = assemble_items(root_b)
        self.assertEqual(
            canonical_json_bytes(
                {"batch": exporter.build_batch(batch_a[0], root=root_a, clock=clock)}
            ),
            canonical_json_bytes(
                {"batch": exporter.build_batch(batch_b[0], root=root_b, clock=clock)}
            ),
        )

    def test_trace_only_row_composes_a_single_trace_event(self):
        root = _declined_root("obsb-traceonly-")
        (item,), skips = assemble_items(root)
        self.assertEqual(skips, [])
        batch = exporter.build_batch(item)
        self.assertEqual([event["type"] for event in batch], ["trace-create"])
        trace_id = ids.trace_only_trace_id(_TOURNAMENT, "R2", "19", [], 1)
        (event,) = batch
        self.assertEqual(event["id"], ids.event_id("trace-create", trace_id))
        self.assertEqual(event["body"]["id"], trace_id)
        metadata = event["body"]["metadata"]
        self.assertIsNone(metadata["outcome"])
        self.assertIsNone(metadata["attempt_id"])
        self.assertIsNone(metadata["execution_id"])
        self.assertIsNone(metadata["attempt_ordinal"])
        self.assertIsNone(metadata["logical_run_id"])
        self.assertEqual(set(metadata["artifact_digests"].values()), {None})
        self.assertEqual(metadata["status"], "declined")
        self.assertEqual(metadata["attempts_used"], 0)
        self.assertEqual(metadata["declined_launches"], 1)

    def test_error_information_is_preserved_in_generation_metadata(self):
        item = _with_requests(
            _synthetic_run(),
            error=MappingProxyType({"kind": "timeout", "message": None}),
        )
        batch = exporter.build_batch(item)
        self.assertEqual(
            batch[1]["body"]["metadata"]["error"],
            {"kind": "timeout", "message": None},
        )


class TestPayloadGuards(unittest.TestCase):
    """Task 6: recursive payload guard and text limits, fail-closed."""

    def test_guard_rejects_generation_text_fields_in_metadata_mode(self):
        batch = [{"type": "generation-create", "body": {"output": "x"}}]
        with self.assertRaises(exporter.ExportError) as raised:
            exporter.guard_payload(batch, include_text=False, path="p")
        self.assertEqual(raised.exception.reason, "unsupported_schema")

    def test_guard_allows_generation_text_fields_only_with_the_flag(self):
        batch = [{"type": "generation-create", "body": {"output": "x"}}]
        exporter.guard_payload(batch, include_text=True, path="p")

    def test_guard_rejects_raw_response_key_in_both_modes(self):
        batch = [{"body": {"metadata": {"raw_visible_response": "x"}}}]
        for include_text in (False, True):
            with self.subTest(include_text=include_text):
                with self.assertRaises(exporter.ExportError) as raised:
                    exporter.guard_payload(batch, include_text=include_text, path="p")
                self.assertEqual(raised.exception.reason, "unsupported_schema")

    def test_guard_scans_payload_bytes_for_secrets_in_both_modes(self):
        batch = [{"body": {"metadata": {"note": "sk-secret-value"}}}]
        for include_text in (False, True):
            with self.subTest(include_text=include_text):
                with self.assertRaises(exporter.ExportError) as raised:
                    exporter.guard_payload(batch, include_text=include_text, path="p")
                self.assertEqual(raised.exception.reason, "secret_detected")
                self.assertNotIn("sk-secret-value", str(raised.exception))

    def test_included_response_secret_fails_closed_without_value_leakage(self):
        item = _synthetic_run(response="sk-secret-response")
        with self.assertRaises(exporter.ExportError) as raised:
            exporter.build_batch(item, include_text=True)
        self.assertEqual(raised.exception.reason, "secret_detected")
        self.assertNotIn("sk-secret-response", str(raised.exception))

    def test_metadata_mode_never_projects_guarded_text_even_when_secret(self):
        item = _synthetic_run(response="sk-secret-response")
        batch = exporter.build_batch(item, include_text=False)
        self.assertNotIn(b"sk-secret-response", canonical_json_bytes({"batch": batch}))

    def test_field_limit_is_exact_64kib(self):
        at_limit = "x" * exporter.TEXT_FIELD_LIMIT_BYTES
        over_limit = at_limit + "!"
        exporter.build_batch(_synthetic_run(response=at_limit), include_text=True)
        with self.assertRaises(exporter.ExportError) as raised:
            exporter.build_batch(_synthetic_run(response=over_limit), include_text=True)
        self.assertEqual(raised.exception.reason, "text_field_limit_exceeded")

    def test_arguments_field_limit_applies_to_included_arguments(self):
        item = _with_requests(
            _synthetic_run(),
            tool_call=model.ToolCall(
                "move",
                MappingProxyType({}),
                model.GuardedText(
                    {"blob": "x" * (exporter.TEXT_FIELD_LIMIT_BYTES + 1)}
                ),
            ),
        )
        with self.assertRaises(exporter.ExportError) as raised:
            exporter.build_batch(item, include_text=True)
        self.assertEqual(raised.exception.reason, "text_field_limit_exceeded")

    def test_item_limit_is_512kib_with_flag(self):
        base = _synthetic_run()
        requests = tuple(
            replace(
                base.requests[0],
                request_id=f"req-{index}",
                raw_visible_response=model.GuardedText("x" * (60 * 1024)),
            )
            for index in range(10)
        )
        item = replace(base, requests=requests)
        exporter.build_batch(item, include_text=False)
        with self.assertRaises(exporter.ExportError) as raised:
            exporter.build_batch(item, include_text=True)
        self.assertEqual(raised.exception.reason, "item_size_limit_exceeded")


class TestRunExport(unittest.TestCase):
    """Tasks 2/4: one-shot orchestration over the stub, continuation, counters."""

    def _run(self, root, behavior=None, include_text=False):
        stub = StubLangfuse(behavior)
        recorder = _Recorder()
        client = langfuse_http.LangfuseClient(
            stub.url, "pub-test", "sec-test", sleep=recorder
        )
        try:
            result = exporter.run_export(root, client, include_text=include_text)
        finally:
            self.assertTrue(stub.close())
        return result, stub, recorder

    def test_clean_run_exports_every_item_with_design_ids(self):
        root, digests = _sealed_root(
            "obsb-clean-", [_bound_row(), _row("R2", "19", "declined")]
        )
        result, stub, _ = self._run(root)
        self.assertIsNone(result.error)
        self.assertEqual(
            result.counts,
            {
                "processed": 2,
                "accepted_items": 2,
                "accepted_events": 4,
                "error_events": 0,
                "failed": 0,
                "skipped": 0,
            },
        )
        identity = _bound_fixture_ids(root, digests)
        trace_only = ids.trace_only_trace_id(_TOURNAMENT, "R2", "19", [], 1)
        self.assertEqual(
            [entry.trace_id for entry in result.exported],
            [identity["trace"], trace_only],
        )
        self.assertEqual(len(stub.objects), 4)
        self.assertEqual(stub.requests[0]["method"], "GET")
        self.assertEqual(
            [request["method"] for request in stub.requests[1:]], ["POST", "POST"]
        )

    def test_second_export_replays_with_zero_new_objects(self):
        root, _ = _sealed_root("obsb-replay-", [_bound_row()])
        stub = StubLangfuse()
        recorder = _Recorder()
        client = langfuse_http.LangfuseClient(
            stub.url, "pub-test", "sec-test", sleep=recorder
        )
        try:
            first = exporter.run_export(root, client)
            objects_after_first = dict(stub.objects)
            second = exporter.run_export(root, client)
        finally:
            self.assertTrue(stub.close())
        self.assertEqual(first.counts["accepted_events"], 3)
        self.assertEqual(second.counts["accepted_events"], 3)
        self.assertEqual(second.counts["accepted_events"], 3)
        self.assertEqual(second.counts["skipped"], 0)
        self.assertEqual(objects_after_first, stub.objects)
        self.assertEqual(
            [entry.trace_id for entry in first.exported],
            [entry.trace_id for entry in second.exported],
        )

    def test_invalid_item_skips_while_valid_items_continue(self):
        root = _secret_root("obsb-secret-")
        result, stub, _ = self._run(root)
        self.assertIsNone(result.error)
        self.assertEqual(result.counts["processed"], 2)
        self.assertEqual(result.counts["accepted_items"], 0)
        self.assertEqual(result.counts["failed"], 0)
        self.assertEqual(result.counts["skipped"], 2)
        self.assertEqual(
            sorted(skip.reason for skip in result.skipped),
            ["missing_artifact", "secret_detected"],
        )
        self.assertEqual([request["method"] for request in stub.requests], ["GET"])

    def test_ingestion_failure_isolates_only_the_failed_item(self):
        root, digests = _sealed_root(
            "obsb-ingest-fail-", [_bound_row(), _row("R2", "19", "declined")]
        )
        result, stub, _ = self._run(root, _post_script(fail_first=1))
        self.assertEqual(result.counts["processed"], 2)
        self.assertEqual(result.counts["accepted_items"], 1)
        self.assertEqual(result.counts["failed"], 1)
        self.assertEqual(result.counts["skipped"], 1)
        (skip,) = result.skipped
        self.assertEqual(skip.reason, "ingestion_failed")
        receipt = (
            root
            / "core"
            / "runs"
            / ("lr_" + "3a" * 32)
            / "sealed-fixture-01"
            / "receipt.json"
        )
        self.assertEqual(skip.path, str(receipt))
        identity = _bound_fixture_ids(root, digests)
        self.assertNotIn(("trace-create", identity["trace"]), stub.objects)
        self.assertEqual(len(stub.objects), 1)

    def test_unsupported_envelope_skips_the_item(self):
        root, _ = _sealed_root("obsb-envelope-", [_bound_row()])

        def behavior(method, path, body, server):
            if method == "GET":
                return 200, {"status": "OK", "version": "3.114.0"}
            return 207, {"nope": True}

        result, stub, _ = self._run(root, behavior)
        self.assertEqual(result.counts["failed"], 1)
        self.assertEqual(result.counts["skipped"], 1)
        self.assertEqual(result.skipped[0].reason, "unsupported_response")
        self.assertEqual(stub.objects, {})

    def test_partial_ingestion_accounting_is_honest(self):
        """A partial item appears in exported event accounting AND skipped/failed."""
        root, digests = _sealed_root("obsb-partial-", [_bound_row()])
        identity = _bound_fixture_ids(root, digests)

        def behavior(method, path, body, server):
            if method == "GET":
                return 200, {"status": "OK", "version": "3.114.0"}
            return 207, {
                "successes": [
                    {
                        "id": ids.event_id("trace-create", identity["trace"]),
                        "status": 201,
                    },
                    {
                        "id": ids.event_id("generation-create", identity["generation"]),
                        "status": 201,
                    },
                ],
                "errors": [
                    {
                        "id": ids.event_id("score-create", identity["score"]),
                        "status": 400,
                    }
                ],
            }

        result, stub, _ = self._run(root, behavior)
        self.assertEqual(
            result.counts,
            {
                "processed": 1,
                "accepted_items": 0,
                "accepted_events": 2,
                "error_events": 1,
                "failed": 1,
                "skipped": 1,
            },
        )
        (entry,) = result.exported
        self.assertEqual(entry.trace_id, identity["trace"])
        self.assertEqual(entry.accepted_events, 2)
        self.assertEqual(entry.error_events, 1)
        (skip,) = result.skipped
        self.assertEqual(skip.reason, "ingestion_failed")
        receipt = (
            root
            / "core"
            / "runs"
            / ("lr_" + "3a" * 32)
            / "sealed-fixture-01"
            / "receipt.json"
        )
        self.assertEqual(skip.path, str(receipt))
        # Exact identity: each processed item is exactly one accounted unit —
        # one accepted item or one skipped item; nothing is lost or counted twice.
        self.assertEqual(
            result.counts["accepted_items"] + result.counts["skipped"],
            result.counts["processed"],
        )

    def test_unhealthy_endpoint_skips_every_item_without_ingesting(self):
        root, _ = _sealed_root(
            "obsb-unhealthy-", [_bound_row(), _row("R2", "19", "declined")]
        )
        result, stub, _ = self._run(root, _always(503, {}))
        self.assertEqual(result.error, "endpoint_unhealthy")
        self.assertEqual(result.counts["accepted_items"], 0)
        self.assertEqual(result.counts["skipped"], 2)
        self.assertEqual(result.counts["failed"], 0)
        self.assertEqual(
            {skip.reason for skip in result.skipped}, {"endpoint_unhealthy"}
        )
        self.assertEqual({request["method"] for request in stub.requests}, {"GET"})

    def test_transient_recovery_uses_the_bounded_backoff(self):
        root, _ = _sealed_root("obsb-retry-", [_bound_row()])
        state = {"posts": 0}

        def behavior(method, path, body, server):
            if method == "GET":
                return 200, {"status": "OK", "version": "3.114.0"}
            state["posts"] += 1
            if state["posts"] <= 2:
                return 503, {}
            return server.upsert_ok(method, path, body, server)

        result, stub, recorder = self._run(root, behavior)
        self.assertEqual(result.counts["accepted_items"], 1)
        self.assertEqual(result.counts["skipped"], 0)
        self.assertEqual(state["posts"], 3)
        self.assertEqual(recorder.delays, [0.25, 0.5])

    def test_include_text_run_keeps_source_bytes_and_report_clean(self):
        root = _marker_root("obsb-marker-")
        before = _root_bytes(root)
        result, stub, _ = self._run(root, include_text=True)
        self.assertEqual(_root_bytes(root), before)
        self.assertEqual(result.counts["skipped"], 0)
        rendered = result.render()
        self.assertNotIn("RESPONSE-MARKER-7q", rendered)
        self.assertNotIn("north", rendered)
        (post,) = [request for request in stub.requests if request["method"] == "POST"]
        body = post["body"].decode("utf-8")
        self.assertIn("RESPONSE-MARKER-7q", body)
        self.assertIn("north", body)

    def test_report_is_redacted_against_credentials_and_text(self):
        root = _marker_root("obsb-redact-")
        result, _, _ = self._run(root, include_text=False)
        rendered = result.render() + " " + str(result.to_dict())
        self.assertNotIn("RESPONSE-MARKER-7q", rendered)
        self.assertNotIn("north", rendered)
        self.assertNotIn("pub-test", rendered)
        self.assertNotIn("sec-test", rendered)
        self.assertNotIn('"output"', rendered)


class TestReportShape(unittest.TestCase):
    """Task 4: deterministic machine-readable report with exact counters."""

    def test_render_is_deterministic_canonical_json(self):
        counts = dict.fromkeys(report.COUNT_KEYS, 0)
        first = report.ExportReport(counts=counts, exported=(), skipped=())
        second = report.ExportReport(counts=dict(counts), skipped=(), exported=())
        self.assertEqual(first.render(), second.render())
        document = json.loads(first.render())
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            set(document), {"schema_version", "counts", "exported", "skipped"}
        )
        self.assertEqual(set(document["counts"]), set(report.COUNT_KEYS))

    def test_error_report_carries_only_the_reason_code(self):
        value = report.ExportReport(
            counts=dict.fromkeys(report.COUNT_KEYS, 0),
            exported=(),
            skipped=(),
            error="endpoint_unhealthy",
        )
        document = json.loads(value.render())
        self.assertEqual(document["error"], "endpoint_unhealthy")

    def test_report_docstring_states_the_honest_partial_invariant(self):
        """The documented invariants match the real partial-ingestion accounting."""
        doc = report.ExportReport.__doc__
        self.assertIsNotNone(doc)
        self.assertNotIn("exported + skipped ==", doc)
        self.assertIn("accepted_items + skipped", doc)

    def test_skip_records_expose_path_and_reason_only(self):
        value = report.ExportReport(
            counts=dict.fromkeys(report.COUNT_KEYS, 1),
            exported=(),
            skipped=(
                model.SkipRecord("/tmp/evidence/receipt.json", "digest_mismatch"),
            ),
        )
        document = json.loads(value.render())
        self.assertEqual(
            document["skipped"],
            [{"path": "/tmp/evidence/receipt.json", "reason": "digest_mismatch"}],
        )


class TestCliExitCodes(unittest.TestCase):
    """Task 5/1: the only command, environment-only credentials, exits 2/0/1."""

    ENV = {"LANGFUSE_PUBLIC_KEY": "pub-test", "LANGFUSE_SECRET_KEY": "sec-test"}

    def _main(self, argv, environ=None):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            argv,
            environ=self.ENV if environ is None else environ,
            stdout=out,
            stderr=err,
        )
        return code, out.getvalue(), err.getvalue()

    def _exit(self, argv, environ=None):
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            cli.main(
                argv,
                environ=self.ENV if environ is None else environ,
                stdout=out,
                stderr=err,
            )
        return raised.exception.code, out.getvalue(), err.getvalue()

    def test_usage_errors_exit_two(self):
        root = _declined_root("obsb-usage-")
        for argv in (
            [],
            ["frobnicate"],
            ["export"],
            ["export", "--artifacts-root", str(root)],
            ["export", "--langfuse-url", "http://localhost:3000"],
            [
                "export",
                "--artifacts-root",
                "",
                "--langfuse-url",
                "http://localhost:3000",
            ],
        ):
            with self.subTest(argv=argv):
                code, out, _ = self._exit(argv)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")

    def test_missing_credentials_exit_two_without_processing(self):
        root = _declined_root("obsb-cred-")
        code, out, err = self._exit(
            [
                "export",
                "--artifacts-root",
                str(root),
                "--langfuse-url",
                "http://localhost:3000",
            ],
            environ={},
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("LANGFUSE_PUBLIC_KEY", err)

    def test_empty_credentials_exit_two(self):
        root = _declined_root("obsb-cred-empty-")
        code, _, _ = self._exit(
            [
                "export",
                "--artifacts-root",
                str(root),
                "--langfuse-url",
                "http://localhost:3000",
            ],
            environ={"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "x"},
        )
        self.assertEqual(code, 2)

    def test_invalid_url_exit_two_without_echoing_the_value(self):
        root = _declined_root("obsb-url-")
        for url in (
            "ftp://example.com",
            "not-a-url",
            "http://",
            "http://example.com/path",
            "http://example.com?query=1",
            "http://example.com#fragment",
            "http://example.com/path;params",
            "http://127.0.0.1:9?",
            "http://127.0.0.1:9#",
            "http://[::1",
            "http://127.0.0.1:notaport",
        ):
            with self.subTest(url=url):
                code, out, err = self._exit(
                    ["export", "--artifacts-root", str(root), "--langfuse-url", url]
                )
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertNotIn(url, err)

    def test_url_with_embedded_credentials_is_rejected(self):
        root = _declined_root("obsb-urlcred-")
        code, out, err = self._exit(
            [
                "export",
                "--artifacts-root",
                str(root),
                "--langfuse-url",
                "http://user:pass@localhost:3000",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotIn("user:pass", err)

    def test_trailing_slash_origin_url_is_accepted(self):
        """Origin-only contract preserves the normal root and its trailing slash."""
        root = _declined_root("obsb-url-slash-")
        stub = StubLangfuse()
        try:
            code, out, err = self._main(
                [
                    "export",
                    "--artifacts-root",
                    str(root),
                    "--langfuse-url",
                    stub.url + "/",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("export ok", err)
        finally:
            self.assertTrue(stub.close())

    def test_clean_run_exits_zero_with_report_on_stdout(self):
        root = _declined_root("obsb-cli-clean-")
        stub = StubLangfuse()
        try:
            code, out, err = self._main(
                ["export", "--artifacts-root", str(root), "--langfuse-url", stub.url]
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["counts"]["skipped"], 0)
            self.assertTrue(out.endswith("\n"))
            self.assertIn("export ok", err)
            self.assertTrue(all(request["has_auth"] for request in stub.requests))
        finally:
            self.assertTrue(stub.close())

    def test_mixed_validity_run_exits_one_with_skip_diagnostics(self):
        root = _secret_root("obsb-cli-mixed-")
        stub = StubLangfuse()
        try:
            code, out, err = self._main(
                ["export", "--artifacts-root", str(root), "--langfuse-url", stub.url]
            )
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out)["counts"]["skipped"], 2)
            self.assertIn("secret_detected", err)
            self.assertIn("missing_artifact", err)
            self.assertNotIn("sk-secret-response", out + err)
        finally:
            self.assertTrue(stub.close())

    def test_ingestion_failure_run_exits_one(self):
        root, _ = _sealed_root("obsb-cli-fail-", [_bound_row()])
        stub = StubLangfuse(_post_script(fail_first=1))
        try:
            code, out, err = self._main(
                ["export", "--artifacts-root", str(root), "--langfuse-url", stub.url]
            )
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out)["counts"]["failed"], 1)
            self.assertIn("ingestion_failed", err)
        finally:
            self.assertTrue(stub.close())

    def test_include_text_flag_reaches_the_stub_but_not_the_report(self):
        root = _marker_root("obsb-cli-text-")
        before = _root_bytes(root)
        stub = StubLangfuse()
        try:
            code, out, _ = self._main(
                [
                    "export",
                    "--artifacts-root",
                    str(root),
                    "--langfuse-url",
                    stub.url,
                    "--include-text",
                ]
            )
            self.assertEqual(code, 0)
            self.assertNotIn("RESPONSE-MARKER-7q", out)
            self.assertNotIn("north", out)
            (post,) = [r for r in stub.requests if r["method"] == "POST"]
            self.assertIn("RESPONSE-MARKER-7q", post["body"].decode("utf-8"))
        finally:
            self.assertTrue(stub.close())
        self.assertEqual(_root_bytes(root), before)

    def test_metadata_only_cli_never_sends_text(self):
        root = _marker_root("obsb-cli-metatext-")
        stub = StubLangfuse()
        try:
            code, out, _ = self._main(
                ["export", "--artifacts-root", str(root), "--langfuse-url", stub.url]
            )
            self.assertEqual(code, 0)
            (post,) = [r for r in stub.requests if r["method"] == "POST"]
            body = post["body"].decode("utf-8")
            self.assertNotIn("RESPONSE-MARKER-7q", body)
            self.assertNotIn("north", body)
            self.assertNotIn("RESPONSE-MARKER-7q", out)
        finally:
            self.assertTrue(stub.close())

    def test_module_entrypoint_exports_and_exits_zero(self):
        root = _declined_root("obsb-main-")
        stub = StubLangfuse()
        original_argv = sys.argv
        try:
            sys.argv = [
                "observability",
                "export",
                "--artifacts-root",
                str(root),
                "--langfuse-url",
                stub.url,
            ]
            out, err = io.StringIO(), io.StringIO()
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
                mock.patch.dict(os.environ, self.ENV),
            ):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_module("observability_runtime", run_name="__main__")
            self.assertEqual(raised.exception.code, 0)
            self.assertEqual(json.loads(out.getvalue())["counts"]["skipped"], 0)
        finally:
            sys.argv = original_argv
            self.assertTrue(stub.close())

    def test_closed_endpoint_exits_one(self):
        root = _declined_root("obsb-closed-")
        code, out, err = self._main(
            [
                "export",
                "--artifacts-root",
                str(root),
                "--langfuse-url",
                f"http://127.0.0.1:{_closed_port()}",
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"], "endpoint_unhealthy")
        self.assertIn("endpoint_unhealthy", err)


if __name__ == "__main__":
    unittest.main()
