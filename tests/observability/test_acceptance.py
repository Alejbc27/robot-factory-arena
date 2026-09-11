"""Unit C end-to-end acceptance (task 5): layout-shaped stub export runs.

Drives the one-shot CLI and :func:`exporter.run_export` against temporary
artifact roots shaped exactly like the sealed evidence layout —
``leaderboard.json``, ``attempts/*/launch.json`` and
``core/runs/*/{receipt.json,llm/provider-run.json,llm/llm-receipt.json}`` —
with valid independent items (bound ``completed``, trace-only ``declined`` and
trace-only ``evidence_invalid``), one invalid item, an ``aborted`` status root,
a partial 207 batch, and secret/markup variants. Asserts authority
preservation, terminal-status fidelity, metadata/text policy at the HTTP
stub, and the inherited exact PR32 ingestion contract: HTTP 207 envelopes
``{successes, errors}`` with 201 successes, required per-event offset-aware
timestamps, honest accepted/error counters, and no created/replay novelty
or atomic claims anywhere in the report — replay stability is observed
independently on the stub's object-ID set, never claimed by the report.
Stub only: no LLM, no live Langfuse, no retained process; source artifact
bytes stay untouched throughout.
"""

import contextlib
import io
import json
import unittest
from datetime import datetime

import observability_runtime.cli as cli  # pyright: ignore[reportMissingImports]

from observability import assemble_items, build_trace_records, canonical_json_bytes

from .test_artifacts import (
    _DIGEST,
    _bound_row,
    _row,
    _sealed_root,
    _unresolved_row,
)
from .test_langfuse_http import StubLangfuse

RESPONSE_MARKER = "RESPONSE-MARKER-acceptance-9z"
ARGUMENTS_MARKER = "north"
SECRET_RESPONSE = "sk-secret-acceptance"

_ENV = {
    "LANGFUSE_PUBLIC_KEY": "pub-acceptance-dummy",
    "LANGFUSE_SECRET_KEY": "sec-acceptance-dummy",
}


def _inject(run_dir, response, arguments):
    """Patch the fixture copy's provider evidence with text/arguments."""
    path = run_dir / "llm" / "provider-run.json"
    value = json.loads(path.read_text())
    value["requests"][0]["raw_visible_response"] = response
    if arguments is not None:
        value["requests"][0]["tool_call"] = {
            "tool": "move",
            "arguments": arguments,
        }
    path.write_bytes(canonical_json_bytes(value))


def _layout_root(prefix, *, ghost=True, response=None, arguments=None):
    """Sealed layout root: three valid independent items plus options.

    Default adds a fourth row whose ``core_receipt_digest`` cannot bind any
    sealed run — the one invalid item. ``response``/``arguments`` inject
    eligible text into the bound run's provider evidence.
    """

    def patch(run_dir):
        if response is not None:
            _inject(run_dir, response, arguments)

    rows = [
        _bound_row(),
        _row("R2", "19", "declined"),
        _unresolved_row(),
    ]
    if ghost:
        rows.append(_bound_row(condition="R4", seed="21", core_receipt_digest=_DIGEST))
    root, _ = _sealed_root(
        prefix,
        rows,
        extra_launches=[("attempt-u1", "R3", "20", 0)],
        **({"patch": patch} if response is not None else {}),
    )
    return root


def _aborted_root(prefix):
    """Bound run whose outcome is a safe abort: terminal status ``aborted``.

    The receipt outcome is patched before reseal so the row's declared
    outcome agrees with its digest-bound evidence.
    """

    def safe_abort(run_dir):
        path = run_dir / "receipt.json"
        value = json.loads(path.read_text())
        value["outcome"] = "safe_abort"
        path.write_bytes(canonical_json_bytes(value))

    root, _ = _sealed_root(
        prefix,
        [_bound_row(status="aborted", outcome="safe_abort")],
        patch=safe_abort,
    )
    return root


def _run_cli(root, stub, *flags):
    """One CLI invocation against the stub; returns (exit, report, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code = cli.main(
            [
                "export",
                "--artifacts-root",
                str(root),
                "--langfuse-url",
                stub.url,
                *flags,
            ],
            environ=dict(_ENV),
            stdout=stdout,
            stderr=stderr,
        )
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


def _post_bodies(stub):
    """Decoded canonical bodies of every POST the stub recorded."""
    return [json.loads(r["body"]) for r in stub.requests if r["method"] == "POST"]


_NOVELTY_KEYS = ("created", "updated_or_replayed", "unchanged")


def _assert_wire_shape(test, stub):
    """Inherited wire contract on every posted event.

    Offset-aware ``timestamp`` plus a ``metadata`` key are required; the
    client fails closed on anything but the exact v3 207 envelope with 201
    successes, so accepted counters additionally prove the envelope round-trip.
    """
    for batch in _post_bodies(stub):
        for event in batch["batch"]:
            timestamp = datetime.fromisoformat(event["timestamp"])
            test.assertIsNotNone(timestamp.utcoffset())
            test.assertIn("metadata", event)


def _assert_no_novelty_claims(test, report):
    """The report carries item/event accounting only — never novelty/atomicity."""
    for key in _NOVELTY_KEYS:
        test.assertNotIn(key, report)
        test.assertNotIn(key, report["counts"])


def _root_bytes(root):
    """Every artifact byte the export may read, for immutability checks."""
    paths = [root / "leaderboard.json"]
    for launch in sorted((root / "attempts").glob("*/launch.json")):
        paths.append(launch)
    for receipt in sorted((root / "core" / "runs").glob("*/*/receipt.json")):
        paths.append(receipt)
        paths.append(receipt.parent / "llm" / "provider-run.json")
        paths.append(receipt.parent / "llm" / "llm-receipt.json")
    return {str(path): path.read_bytes() for path in paths}


class TestLayoutShapedAcceptance(unittest.TestCase):
    def test_mixed_validity_report_status_authority_and_exit_one(self):
        root = _layout_root("obs-acc-mixed-")
        before = _root_bytes(root)
        stub = StubLangfuse()
        try:
            code, report, _ = _run_cli(root, stub)
            self.assertEqual(code, 1)
            self.assertEqual(
                report["counts"],
                {
                    "processed": 4,
                    "accepted_items": 3,
                    "accepted_events": 5,
                    "error_events": 0,
                    "failed": 0,
                    "skipped": 1,
                },
            )
            _assert_no_novelty_claims(self, report)
            for entry in report["exported"]:
                self.assertEqual(
                    set(entry), {"trace_id", "accepted_events", "error_events"}
                )
            (skip,) = report["skipped"]
            self.assertEqual(skip["reason"], "missing_artifact")
            self.assertTrue(skip["path"].endswith("core/runs"))
            statuses = {
                record.status for record in build_trace_records(assemble_items(root)[0])
            }
            self.assertEqual(statuses, {"completed", "declined", "evidence_invalid"})
            self.assertEqual(
                sorted(entry["trace_id"] for entry in report["exported"]),
                sorted(
                    entry["body"]["id"]
                    for batch in _post_bodies(stub)
                    for entry in batch["batch"]
                    if entry["type"] == "trace-create"
                ),
            )
            self.assertEqual(len(stub.objects), 5)
            for batch in _post_bodies(stub):
                self.assertNotIn(RESPONSE_MARKER.encode(), canonical_json_bytes(batch))
            self.assertEqual(_root_bytes(root), before)
            _assert_wire_shape(self, stub)
        finally:
            self.assertTrue(stub.close())

    def test_clean_root_exit_zero_and_replay_reuses_stable_object_ids(self):
        root = _layout_root("obs-acc-clean-", ghost=False, response="plain-ok")
        stub = StubLangfuse()
        try:
            first_code, first, _ = _run_cli(root, stub)
            self.assertEqual(first_code, 0)
            self.assertEqual(
                first["counts"],
                {
                    "processed": 3,
                    "accepted_items": 3,
                    "accepted_events": 5,
                    "error_events": 0,
                    "failed": 0,
                    "skipped": 0,
                },
            )
            _assert_no_novelty_claims(self, first)
            identities_after_first = set(stub.objects)
            self.assertEqual(len(identities_after_first), 5)
            second_code, second, _ = _run_cli(root, stub)
            self.assertEqual(second_code, 0)
            self.assertEqual(second["counts"], first["counts"])
            # Independent stub-side observation only: the report itself
            # makes no novelty or replay claim; stable object IDs are
            # verified here, on the stub, never in the report.
            self.assertEqual(set(stub.objects), identities_after_first)
            _assert_no_novelty_claims(self, second)
        finally:
            self.assertTrue(stub.close())

    def test_include_text_exports_eligible_text_report_stays_redacted(self):
        root = _layout_root(
            "obs-acc-text-",
            ghost=False,
            response=RESPONSE_MARKER,
            arguments={"direction": ARGUMENTS_MARKER},
        )
        before = _root_bytes(root)
        stub = StubLangfuse()
        try:
            code, report, _ = _run_cli(root, stub, "--include-text")
            self.assertEqual(code, 0)
            bodies = canonical_json_bytes({"batches": _post_bodies(stub)})
            self.assertIn(RESPONSE_MARKER.encode(), bodies)
            self.assertIn(ARGUMENTS_MARKER.encode(), bodies)
            self.assertNotIn(RESPONSE_MARKER, report)
            self.assertNotIn(ARGUMENTS_MARKER, report)
            self.assertEqual(_root_bytes(root), before)
            self.assertEqual(len(stub.objects), 5)
        finally:
            self.assertTrue(stub.close())

    def test_secret_item_skips_in_both_modes_and_leaks_nowhere(self):
        root = _layout_root("obs-acc-secret-", ghost=False, response=SECRET_RESPONSE)
        for flags in ((), ("--include-text",)):
            stub = StubLangfuse()
            try:
                code, report, stderr = _run_cli(root, stub, *flags)
                self.assertEqual(code, 1)
                # The loader-level scan skips the unreadable run itself and the
                # bound row whose receipt digest can no longer bind any
                # readable evidence — both fail closed, nothing is exported.
                self.assertEqual(report["counts"]["skipped"], 2)
                self.assertEqual(
                    {skip["reason"] for skip in report["skipped"]},
                    {"secret_detected", "missing_artifact"},
                )
                all_output = canonical_json_bytes(
                    {"batches": _post_bodies(stub), "report": report, "stderr": stderr}
                )
                self.assertNotIn(SECRET_RESPONSE.encode(), all_output)
            finally:
                self.assertTrue(stub.close())

    def test_aborted_status_preserved_end_to_end(self):
        root = _aborted_root("obs-acc-aborted-")
        stub = StubLangfuse()
        try:
            code, report, _ = _run_cli(root, stub)
            self.assertEqual(code, 0)
            self.assertEqual(report["counts"]["accepted_items"], 1)
            self.assertEqual(report["counts"]["accepted_events"], 3)
            (trace,) = (
                entry["body"]
                for batch in _post_bodies(stub)
                for entry in batch["batch"]
                if entry["type"] == "trace-create"
            )
            self.assertEqual(trace["metadata"]["status"], "aborted")
            self.assertEqual(trace["metadata"]["outcome"], "safe_abort")
        finally:
            self.assertTrue(stub.close())

    def test_partial_207_batch_reports_honest_accepted_and_error_counters(self):
        """A 207 with one errored event yields honest per-event accounting."""
        root = _aborted_root("obs-acc-partial-")

        def partial_errors(method, path, body, server):
            if method == "GET":
                return 200, {"status": "OK", "version": "3.114.0"}
            batch = json.loads(body)["batch"]
            return 207, {
                "successes": [
                    {"id": event["id"], "status": 201} for event in batch[1:]
                ],
                "errors": [{"id": batch[0]["id"]}],
            }

        stub = StubLangfuse(behavior=partial_errors)
        try:
            code, report, _ = _run_cli(root, stub)
            self.assertEqual(code, 1)
            self.assertEqual(
                report["counts"],
                {
                    "processed": 1,
                    "accepted_items": 0,
                    "accepted_events": 2,
                    "error_events": 1,
                    "failed": 1,
                    "skipped": 1,
                },
            )
            (entry,) = report["exported"]
            self.assertEqual(entry["accepted_events"], 2)
            self.assertEqual(entry["error_events"], 1)
            (skip,) = report["skipped"]
            self.assertEqual(skip["reason"], "ingestion_failed")
        finally:
            self.assertTrue(stub.close())

    def test_usage_error_exits_two_without_processing(self):
        stub = StubLangfuse()
        try:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.assertRaises(SystemExit) as raised:
                cli.main(
                    ["export", "--artifacts-root", ""],
                    environ=dict(_ENV),
                    stdout=stdout,
                    stderr=stderr,
                )
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stub.requests, [])
        finally:
            self.assertTrue(stub.close())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
