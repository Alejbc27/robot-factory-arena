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
        """Pinned v3 envelope: 201 for a new object id, 202 for a replay."""
        if method == "GET":
            return 200, {"success": True}
        successes = []
        for event in json.loads(body)["batch"]:
            key = (event["type"], event["body"]["id"])
            first = key not in server.objects
            server.objects.setdefault(key, 201)
            successes.append({"id": event["id"], "status": 201 if first else 202})
        return 200, {"successes": successes, "failures": []}

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
    def test_single_atomic_post_per_batch(self):
        stub = StubLangfuse()
        try:
            batch = [
                _event("trace-create", "tr_one"),
                _event("generation-create", "gen_one"),
                _event("score-create", "score_one"),
            ]
            result = _client(stub).ingest(batch)
            self.assertTrue(result.accepted)
            self.assertEqual(result.event_outcomes, ("created",) * 3)
            self.assertEqual(len(stub.requests), 1)
            request = stub.requests[0]
            self.assertEqual(request["method"], "POST")
            self.assertEqual(request["path"], langfuse_http.INGESTION_PATH)
            self.assertTrue(request["has_auth"])
            self.assertEqual(request["content_type"], "application/json")
            self.assertEqual(json.loads(request["body"])["batch"], batch)
        finally:
            self.assertTrue(stub.close())

    def test_replay_reports_updates_with_zero_new_objects(self):
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
            self.assertEqual(first.event_outcomes, ("created",) * 2)
            self.assertTrue(second.accepted)
            self.assertEqual(second.event_outcomes, ("updated_or_replayed",) * 2)
            self.assertEqual(len(stub.objects), objects_after_first)
        finally:
            self.assertTrue(stub.close())

    def test_unsupported_envelopes_are_rejected_safely(self):
        envelopes = {
            "not_json": b"not-json",
            "missing_successes": {},
            "successes_not_list": {"successes": "nope"},
            "success_entry_missing_status": {"successes": [{"id": "e1"}]},
            "success_entry_bad_status": {"successes": [{"id": "e1", "status": 400}]},
            "unknown_signal": {"successes": [], "ingest_version": "other"},
        }
        for label, payload in envelopes.items():
            with self.subTest(envelope=label):
                stub = StubLangfuse(_script((200, payload)))
                try:
                    result = _client(stub).ingest([_event("trace-create", "tr_x")])
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "unsupported_response")
                finally:
                    self.assertTrue(stub.close())

    def test_non_mapping_success_entries_fail_closed(self):
        """C1: malformed entry shapes classify safely, never raise."""
        envelopes = {
            "int_entry": {"successes": [1], "failures": []},
            "bool_entry": {"successes": [True], "failures": []},
            "null_entry": {"successes": [None], "failures": []},
            "string_entry": {"successes": ["e1"], "failures": []},
            "list_entry": {"successes": [["e1"]], "failures": []},
        }
        for label, payload in envelopes.items():
            with self.subTest(envelope=label):
                stub = StubLangfuse(_script((200, payload)))
                try:
                    result = _client(stub).ingest([_event("trace-create", "tr_x")])
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "unsupported_response")
                finally:
                    self.assertTrue(stub.close())

    def test_non_mapping_failure_entries_fail_closed(self):
        """C1: malformed failure entries classify as unsupported, never raise."""
        envelopes = {
            "int_entry": {"successes": [], "failures": [1]},
            "bool_entry": {"successes": [], "failures": [True]},
            "null_entry": {"successes": [], "failures": [None]},
            "string_entry": {"successes": [], "failures": ["e2"]},
            "list_entry": {"successes": [], "failures": [["e2"]]},
        }
        for label, payload in envelopes.items():
            with self.subTest(envelope=label):
                stub = StubLangfuse(_script((200, payload)))
                try:
                    result = _client(stub).ingest([_event("trace-create", "tr_x")])
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "unsupported_response")
                finally:
                    self.assertTrue(stub.close())

    def test_response_cardinality_must_match_the_submitted_batch(self):
        """C2: acceptance requires one outcome per submitted event, exactly."""
        batch = [_event("trace-create", "tr_x"), _event("score-create", "sc_x")]
        envelopes = {
            "empty_successes": {"successes": [], "failures": []},
            "too_few_successes": {
                "successes": [{"id": "e1", "status": 201}],
                "failures": [],
            },
            "too_many_successes": {
                "successes": [
                    {"id": "e1", "status": 201},
                    {"id": "e2", "status": 201},
                    {"id": "e3", "status": 201},
                ],
                "failures": [],
            },
        }
        for label, payload in envelopes.items():
            with self.subTest(envelope=label):
                stub = StubLangfuse(_script((200, payload)))
                try:
                    result = _client(stub).ingest(batch)
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.reason, "unsupported_response")
                finally:
                    self.assertTrue(stub.close())

    def test_event_failures_fail_the_whole_batch(self):
        payload = {
            "successes": [{"id": "e1", "status": 201}],
            "failures": [{"id": "e2", "status": 400, "error": "boom-body"}],
        }
        stub = StubLangfuse(_script((200, payload)))
        try:
            result = _client(stub).ingest([_event("trace-create", "tr_x")])
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "ingestion_failed")
            self.assertNotIn("boom-body", str(result))
            self.assertNotIn("boom-body", repr(result))
        finally:
            self.assertTrue(stub.close())

    def test_mixed_outcomes_classify_per_event(self):
        payload = {
            "successes": [
                {"id": "e1", "status": 201},
                {"id": "e2", "status": 202},
                {"id": "e3", "status": 200},
            ],
            "failures": [],
        }
        stub = StubLangfuse(_script((200, payload)))
        try:
            result = _client(stub).ingest(
                [
                    _event("trace-create", "tr_x"),
                    _event("score-create", "sc_x"),
                    _event("generation-create", "gn_x"),
                ]
            )
            self.assertTrue(result.accepted)
            self.assertEqual(
                result.event_outcomes,
                ("created", "updated_or_replayed", "unchanged"),
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
                    200,
                    {
                        "successes": [{"id": "e1", "status": 201}],
                        "failures": [],
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
                            200,
                            {
                                "successes": [{"id": "e1", "status": 201}],
                                "failures": [],
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
            return 200, {"successes": [], "failures": []}

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
