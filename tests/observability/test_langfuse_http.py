"""Transport-contract tests for the direct ingestion client (Unit B slice B1).

The stub is an in-process ``http.server`` on an ephemeral localhost port: no
live Langfuse, no external network, no retained background process (every test
shuts its stub down and asserts the server thread ended). Authorization is
recorded as presence only — the stub never retains credential material.

Retry timing is asserted through an injected sleep recorder, so the exact
bounded schedule (250 ms, 500 ms, Retry-After capped at 2 s) is verified
deterministically without real waiting.
"""

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# tests/observability/__init__.py loads the runtime package under a private
# alias; Unit B modules that the shim does not yet forward bind through it.
# The alias package exists only at runtime, so static import resolution is
# impossible by design; execution (python3 -m unittest) is the authority.
import observability_runtime.langfuse_http as langfuse_http  # pyright: ignore[reportMissingImports]

from observability import ids


def _event(event_type, object_id):
    """Minimal synthetic ingestion event (transport tests need no artifacts)."""
    return {
        "id": ids.event_id(event_type, object_id),
        "timestamp": "2026-07-08T12:34:56+00:00",
        "metadata": None,
        "type": event_type,
        "body": {"id": object_id},
    }


def _script(*responses):
    """Stub behavior yielding queued ``(status, payload[, headers])`` tuples."""
    queue = list(responses)

    def behavior(method, path, body, server):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return behavior


class _StubHandler(BaseHTTPRequestHandler):
    server: "StubLangfuse"

    def log_message(self, fmt, *args):
        pass

    def _respond(self, status, payload, headers):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, body):
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "body": body,
                "has_auth": self.headers.get("Authorization") is not None,
                "content_type": self.headers.get("Content-Type"),
            }
        )
        outcome = self.server.behavior(self.command, self.path, body, self.server)
        headers = outcome[2] if len(outcome) > 2 else {}
        self._respond(outcome[0], outcome[1], headers)

    def do_GET(self):
        self._dispatch(b"")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self._dispatch(self.rfile.read(length))


class StubLangfuse(ThreadingHTTPServer):
    """In-process stub recording request facts and object (type, id) identities."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # timed-out client sockets are expected in retry tests

    def __init__(self, behavior=None):
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.requests = []
        self.objects = {}
        self.behavior = behavior or self.upsert_ok
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self):
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def upsert_ok(self, method, path, body, server):
        """Pinned v3 envelope; independently retain object IDs to verify replay."""
        if method == "GET":
            return 200, {"status": "OK", "version": "3.114.0"}
        successes = []
        for event in json.loads(body)["batch"]:
            key = (event["type"], event["body"]["id"])
            server.objects.setdefault(key, 201)
            successes.append({"id": event["id"], "status": 201})
        return 207, {"successes": successes, "errors": []}

    def close(self):
        """One-shot teardown: no background thread survives the test."""
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)
        return not self._thread.is_alive()


class _Recorder:
    """Injected sleep capturing the retry schedule instead of real waiting."""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


def _client(stub, **overrides):
    options = {"sleep": _Recorder()}
    options.update(overrides)
    return langfuse_http.LangfuseClient(stub.url, "pub-test", "sec-test", **options)


def _closed_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class TestHealthGuard(unittest.TestCase):
    def test_healthy_endpoint_answers_public_health(self):
        stub = StubLangfuse()
        try:
            result = _client(stub).check_health()
            self.assertTrue(result.healthy)
            self.assertIsNone(result.reason)
            (request,) = stub.requests
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["path"], langfuse_http.HEALTH_PATH)
            self.assertTrue(request["has_auth"])
        finally:
            self.assertTrue(stub.close())

    def test_health_requires_exact_200_ok_shape(self):
        cases = (
            (201, {"status": "OK", "version": "3.114.0"}),
            (200, {"status": "ok", "version": "3.114.0"}),
            (200, {"status": "OK"}),
            (200, {"status": "OK", "version": 3}),
        )
        for status, payload in cases:
            with self.subTest(status=status, payload=payload):
                stub = StubLangfuse(_script((status, payload)))
                try:
                    self.assertFalse(_client(stub).check_health().healthy)
                finally:
                    self.assertTrue(stub.close())

    def test_unhealthy_endpoints_fail_with_reason_codes(self):
        cases = {
            "transient_exhausted": (_script((503, {})), 3),
            "auth_rejected": (_script((401, {})), 1),
            "not_json": (_script((200, b"<html>gateway error</html>")), 1),
        }
        for label, (behavior, attempts) in cases.items():
            with self.subTest(case=label):
                stub = StubLangfuse(behavior)
                try:
                    result = _client(stub).check_health()
                    self.assertFalse(result.healthy)
                    self.assertEqual(result.reason, "endpoint_unhealthy")
                    self.assertEqual(len(stub.requests), attempts)
                finally:
                    self.assertTrue(stub.close())


class TestIngestContract(unittest.TestCase):
    def test_single_request_per_item_batch(self):
        stub = StubLangfuse()
        try:
            batch = [
                _event("trace-create", "tr_one"),
                _event("generation-create", "gen_one"),
                _event("score-create", "score_one"),
            ]
            result = _client(stub).ingest(batch)
            self.assertTrue(result.accepted)
            self.assertEqual(result.accepted_events, 3)
            self.assertEqual(result.error_events, 0)
            self.assertEqual(len(stub.requests), 1)
            request = stub.requests[0]
            self.assertEqual(request["method"], "POST")
            self.assertEqual(request["path"], langfuse_http.INGESTION_PATH)
            self.assertEqual(json.loads(request["body"])["batch"], batch)
        finally:
            self.assertTrue(stub.close())

    def test_replay_has_same_acceptance_without_novelty_claims(self):
        stub = StubLangfuse()
        try:
            client = _client(stub)
            batch = [
                _event("trace-create", "tr_same"),
                _event("score-create", "sc_same"),
            ]
            first = client.ingest(batch)
            objects_after_first = len(stub.objects)
            second = client.ingest(batch)
            self.assertTrue(first.accepted)
            self.assertEqual(first.accepted_events, 2)
            self.assertTrue(second.accepted)
            self.assertEqual(second.accepted_events, 2)
            self.assertEqual(len(stub.objects), objects_after_first)
        finally:
            self.assertTrue(stub.close())

    def test_unsupported_envelopes_are_rejected_safely(self):
        event = _event("trace-create", "tr_x")
        envelopes = {
            "not_json": b"not-json",
            "missing_successes": {"errors": []},
            "successes_not_list": {"successes": "nope", "errors": []},
            "success_entry_missing_status": {
                "successes": [{"id": event["id"]}],
                "errors": [],
            },
            "success_entry_bad_status": {
                "successes": [{"id": event["id"], "status": 202}],
                "errors": [],
            },
            "unknown_signal": {
                "successes": [],
                "errors": [],
                "ingest_version": "other",
            },
        }
        for label, payload in envelopes.items():
            with self.subTest(envelope=label):
                stub = StubLangfuse(_script((207, payload)))
                try:
                    result = _client(stub).ingest([event])
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "unsupported_response")
                finally:
                    self.assertTrue(stub.close())

    def test_non_mapping_entries_fail_closed(self):
        for key in ("successes", "errors"):
            for entry in (1, True, None, "e1", ["e1"]):
                payload = {"successes": [], "errors": []}
                payload[key] = [entry]
                stub = StubLangfuse(_script((207, payload)))
                try:
                    self.assertEqual(
                        _client(stub).ingest([_event("trace-create", "tr_x")]).reason,
                        "unsupported_response",
                    )
                finally:
                    self.assertTrue(stub.close())

    def test_malformed_utf8_response_bytes_fail_closed(self):
        """Invalid UTF-8 response bytes classify safely; no body ever escapes."""
        for surface, status, payload in (
            ("health", 200, b"\xff"),
            ("ingest", 207, b"\xff\xfe"),
        ):
            with self.subTest(surface=surface):
                stub = StubLangfuse(_script((status, payload)))
                try:
                    client = _client(stub)
                    if surface == "health":
                        result = client.check_health()
                        self.assertFalse(result.healthy)
                        self.assertEqual(result.reason, "endpoint_unhealthy")
                    else:
                        result = client.ingest([_event("trace-create", "tr_x")])
                        self.assertFalse(result.accepted)
                        self.assertEqual(result.reason, "unsupported_response")
                    self.assertNotIn("\\xff", repr(result))
                finally:
                    self.assertTrue(stub.close())

    def test_malformed_submitted_ids_fail_closed_before_transport(self):
        """Event shape/ID validity gates hashing, accounting, and transport."""
        for value in (7, True, None, ["u1"], {"k": 1}, ""):
            with self.subTest(id=repr(value)):
                stub = StubLangfuse()
                try:
                    event = _event("trace-create", "tr_x")
                    event["id"] = value
                    result = _client(stub).ingest([event])
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "unsupported_response")
                    self.assertIsNone(result.status)
                    self.assertEqual(stub.requests, [])
                finally:
                    self.assertTrue(stub.close())

    def test_empty_batch_and_non_mapping_events_fail_closed(self):
        stub = StubLangfuse()
        try:
            self.assertEqual(_client(stub).ingest([]).reason, "unsupported_response")
            for event in (42, "event", None, ["id"]):
                with self.subTest(event=repr(event)):
                    result = _client(stub).ingest([event])
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "unsupported_response")
            self.assertEqual(stub.requests, [])
        finally:
            self.assertTrue(stub.close())

    def test_event_errors_report_partial_counts_without_message_leak(self):
        batch = [_event("trace-create", "tr_x"), _event("score-create", "sc_x")]
        payload = {
            "successes": [{"id": batch[0]["id"], "status": 201}],
            "errors": [{"id": batch[1]["id"], "status": 400, "message": "boom-body"}],
        }
        stub = StubLangfuse(_script((207, payload)))
        try:
            result = _client(stub).ingest(batch)
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "ingestion_failed")
            self.assertEqual(result.accepted_events, 1)
            self.assertEqual(result.error_events, 1)
            self.assertNotIn("boom-body", repr(result))
        finally:
            self.assertTrue(stub.close())

    def test_result_ids_require_exact_unique_complete_accounting(self):
        batch = [_event("trace-create", "tr_x"), _event("score-create", "sc_x")]
        first, second = (event["id"] for event in batch)
        envelopes = {
            "duplicate": {
                "successes": [{"id": first, "status": 201}] * 2,
                "errors": [],
            },
            "missing": {"successes": [{"id": first, "status": 201}], "errors": []},
            "unknown": {
                "successes": [
                    {"id": first, "status": 201},
                    {"id": "unknown", "status": 201},
                ],
                "errors": [],
            },
            "overlap": {
                "successes": [{"id": first, "status": 201}],
                "errors": [{"id": first, "status": 400}],
            },
            "bad_error_id": {
                "successes": [{"id": first, "status": 201}],
                "errors": [{"id": 7, "status": 400}],
            },
            "duplicate_errors": {
                "successes": [],
                "errors": [{"id": first}, {"id": first}],
            },
            "valid_control": {
                "successes": [
                    {"id": first, "status": 201},
                    {"id": second, "status": 201},
                ],
                "errors": [],
            },
        }
        for label, payload in envelopes.items():
            stub = StubLangfuse(_script((207, payload)))
            try:
                result = _client(stub).ingest(batch)
                if label == "valid_control":
                    self.assertTrue(result.accepted)
                else:
                    self.assertEqual(result.reason, "unsupported_response")
            finally:
                self.assertTrue(stub.close())

    def test_exact_http_status_and_top_level_keys_are_required(self):
        event = _event("trace-create", "tr_x")
        valid = {"successes": [{"id": event["id"], "status": 201}], "errors": []}
        for status, payload in (
            (200, valid),
            (201, valid),
            (207, {**valid, "extra": []}),
        ):
            stub = StubLangfuse(_script((status, payload)))
            try:
                self.assertEqual(
                    _client(stub).ingest([event]).reason, "unsupported_response"
                )
            finally:
                self.assertTrue(stub.close())


class TestBoundedRetry(unittest.TestCase):
    def test_transient_5xx_retries_with_scheduled_backoff(self):
        stub = StubLangfuse(
            _script(
                (503, {}),
                (503, {}),
                (
                    207,
                    {
                        "successes": [
                            {"id": _event("trace-create", "tr_x")["id"], "status": 201}
                        ],
                        "errors": [],
                    },
                ),
            )
        )
        try:
            client = _client(stub)
            result = client.ingest([_event("trace-create", "tr_x")])
            self.assertTrue(result.accepted)
            self.assertEqual(len(stub.requests), 3)
            self.assertEqual(client.sleep.delays, [0.25, 0.5])
        finally:
            self.assertTrue(stub.close())

    def test_retry_after_header_is_bounded(self):
        for label, headers, expected in (
            ("capped", {"Retry-After": "5"}, [2.0]),
            ("honored_under_cap", {"Retry-After": "1.5"}, [1.5]),
            ("invalid_falls_back", {"Retry-After": "soon"}, [0.25]),
            ("negative_falls_back", {"Retry-After": "-1"}, [0.25]),
            ("nan_falls_back", {"Retry-After": "nan"}, [0.25]),
            ("infinity_falls_back", {"Retry-After": "inf"}, [0.25]),
        ):
            with self.subTest(case=label):
                stub = StubLangfuse(
                    _script(
                        (429, {}, headers),
                        (
                            207,
                            {
                                "successes": [
                                    {
                                        "id": _event("trace-create", "tr_x")["id"],
                                        "status": 201,
                                    }
                                ],
                                "errors": [],
                            },
                        ),
                    )
                )
                try:
                    client = _client(stub)
                    result = client.ingest([_event("trace-create", "tr_x")])
                    self.assertTrue(result.accepted)
                    self.assertEqual(client.sleep.delays, expected)
                finally:
                    self.assertTrue(stub.close())

    def test_permanent_4xx_is_never_retried(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                stub = StubLangfuse(_script((status, {})))
                try:
                    result = _client(stub).ingest([_event("trace-create", "tr_x")])
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "ingestion_failed")
                    self.assertEqual(result.status, status)
                    self.assertEqual(len(stub.requests), 1)
                finally:
                    self.assertTrue(stub.close())

    def test_5xx_retry_exhaustion_reports_failure(self):
        stub = StubLangfuse(_script((500, {})))
        try:
            client = _client(stub)
            result = client.ingest([_event("trace-create", "tr_x")])
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "ingestion_failed")
            self.assertEqual(result.status, 500)
            self.assertEqual(len(stub.requests), 3)
            self.assertEqual(client.sleep.delays, [0.25, 0.5])
        finally:
            self.assertTrue(stub.close())

    def test_network_errors_retry_bounded_then_unreachable(self):
        client = langfuse_http.LangfuseClient(
            f"http://127.0.0.1:{_closed_port()}", "pub", "sec", sleep=_Recorder()
        )
        result = client.ingest([_event("trace-create", "tr_x")])
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "ingestion_unreachable")
        self.assertIsNone(result.status)
        self.assertEqual(client.sleep.delays, [0.25, 0.5])

    def test_timeout_is_retried_bounded(self):
        def slow(method, path, body, server):
            threading.Event().wait(0.4)
            return 207, {"successes": [], "errors": []}

        stub = StubLangfuse(slow)
        try:
            client = _client(stub, timeout=0.15)
            result = client.ingest([_event("trace-create", "tr_x")])
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "ingestion_unreachable")
            self.assertEqual(len(stub.requests), 3)
        finally:
            self.assertTrue(stub.close())


class TestDiagnosticSafety(unittest.TestCase):
    def test_credentials_and_bodies_never_escape_the_client(self):
        stub = StubLangfuse(_script((200, b"not-json"), (400, {})))
        try:
            client = _client(stub)
            first = client.ingest([_event("trace-create", "tr_secret_marker")])
            second = client.ingest([_event("trace-create", "tr_y")])
            for result in (first, second):
                rendered = f"{result!r} {result} {result.reason}"
                self.assertNotIn("pub-test", rendered)
                self.assertNotIn("sec-test", rendered)
                self.assertNotIn("not-json", rendered)
                self.assertNotIn("tr_secret_marker", rendered)
            self.assertNotIn("sec-test", repr(client))
            self.assertNotIn("pub-test", repr(client))
        finally:
            self.assertTrue(stub.close())
