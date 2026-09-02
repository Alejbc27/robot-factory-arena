"""Focused HTTP-failure diagnostics tests for the OpenAI adapter.

Every test uses a STUBBED transport — or a patched
``urllib.request.urlopen`` for the default transport — no network, no real
API call. These tests pin the diagnostic contract: an upstream HTTP error
must surface its status code plus ONLY a sanitized ``error.code`` and
``error.message`` pair, and must never leak the API key, Authorization
headers, request payloads, or the raw response body.
"""
import email.message
import io
import json
import os
import unittest
import urllib.error
import uuid
from unittest import mock

from robot_factory_arena.policies.llm import TOOL_SCHEMA
from robot_factory_arena.providers import openai as openai_module
from robot_factory_arena.providers.adapter import ProviderRequest
from robot_factory_arena.providers.openai import OpenAIProvider


def _request(**overrides):
    values = {
        "provider": "openai",
        "model": "gpt-test",
        "request_id": "request-1",
        "observation": {"schema_version": 1},
        "prompt": "the frozen prompt",
        "tool_schema": TOOL_SCHEMA,
    }
    values.update(overrides)
    return ProviderRequest(**values)


class _StubTransport:
    """Records calls; returns a canned (status, body) or raises."""

    def __init__(self, status: int = 200, body: bytes = b"{}",
                 raises: Exception | None = None):
        self.calls = []
        self.status = status
        self.body = body
        self.raises = raises

    def __call__(self, url, headers, payload_bytes, timeout):
        self.calls.append(
            {"url": url, "headers": dict(headers),
             "payload": json.loads(payload_bytes.decode("utf-8"))}
        )
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


def _http_error(status, body_bytes, reason="Error"):
    """A real ``urllib.error.HTTPError`` carrying a body — no network."""
    return urllib.error.HTTPError(
        "https://api.openai.com/v1/chat/completions",
        status, reason, email.message.Message(), io.BytesIO(body_bytes),
    )


class TestHTTPErrorDiagnosticsViaInjectableTransport(unittest.TestCase):
    """Non-200 stubbed responses must be diagnosable and never leak."""

    def _invoke(self, status, body):
        stub = _StubTransport(status=status, body=body)
        provider = OpenAIProvider("gpt-test", transport=stub)
        environment = dict(os.environ)
        environment["OPENAI_API_KEY"] = "sk-unit-" + uuid.uuid4().hex
        with mock.patch.dict(os.environ, environment, clear=True):
            return provider.invoke(_request())

    def test_upstream_error_body_exposes_status_and_sanitized_fields(self):
        body = json.dumps(
            {
                "error": {
                    "message": "The model 'gpt-test' does not exist",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            }
        ).encode("utf-8")
        result = self._invoke(status=404, body=body)
        assert result.error is not None
        self.assertEqual(result.error.code, "http_error")
        # The exact diagnostic format: status plus ONLY code and message.
        self.assertEqual(
            result.error.message,
            "upstream returned HTTP 404: code=model_not_found"
            " message=The model 'gpt-test' does not exist",
        )
        # The un-sanctioned ``type`` field is deliberately excluded.
        self.assertNotIn("invalid_request_error", result.error.message)

    def test_error_detail_never_leaks_key_headers_or_arbitrary_body(self):
        secret = "sk-unit-" + uuid.uuid4().hex
        body = json.dumps(
            {
                "error": {"message": "quota exceeded",
                          "code": "insufficient_quota"},
                "internal_echo": secret,
                "authorization": "Bearer " + secret,
                "request_payload": {"prompt": "the frozen prompt"},
            }
        ).encode("utf-8")
        stub = _StubTransport(status=429, body=body)
        provider = OpenAIProvider("gpt-test", transport=stub)
        environment = dict(os.environ)
        environment["OPENAI_API_KEY"] = secret
        with mock.patch.dict(os.environ, environment, clear=True):
            result = provider.invoke(_request())
        assert result.error is not None
        message = result.error.message
        self.assertNotIn(secret, message)
        self.assertNotIn("Bearer", message)
        self.assertNotIn("authorization", message.lower())
        self.assertNotIn("internal_echo", message)
        self.assertNotIn("request_payload", message)
        self.assertNotIn("the frozen prompt", message)
        self.assertIn("429", message)
        self.assertIn("insufficient_quota", message)

    def test_non_json_error_body_yields_status_only(self):
        body = b"<html>bad gateway SECRET-BODY-MARKER</html>"
        result = self._invoke(status=502, body=body)
        assert result.error is not None
        self.assertEqual(result.error.code, "http_error")
        self.assertIn("502", result.error.message)
        self.assertNotIn("SECRET-BODY-MARKER", result.error.message)
        self.assertNotIn("html", result.error.message)

    def test_oversized_error_message_is_truncated(self):
        body = json.dumps(
            {"error": {"message": "x" * 2000, "code": "overflow"}}
        ).encode("utf-8")
        result = self._invoke(status=400, body=body)
        assert result.error is not None
        self.assertLess(len(result.error.message), 400)
        self.assertIn("x" * 300, result.error.message)
        self.assertNotIn("x" * 301, result.error.message)

    def test_non_scalar_error_fields_are_ignored(self):
        for error_value in (
            {"message": {"nested": "object"}, "code": ["not", "a", "list"]},
            "a plain string, not a mapping",
            {"message": None, "code": True},
        ):
            with self.subTest(error_value=error_value):
                body = json.dumps({"error": error_value}).encode("utf-8")
                result = self._invoke(status=403, body=body)
                assert result.error is not None
                self.assertEqual(result.error.code, "http_error")
                self.assertEqual(result.error.message,
                                 "upstream returned HTTP 403")

    def test_error_message_newlines_are_flattened(self):
        body = json.dumps(
            {"error": {"message": "line one\nline two\ttabbed",
                       "code": "multi\nline"}}
        ).encode("utf-8")
        result = self._invoke(status=400, body=body)
        assert result.error is not None
        self.assertNotIn("\n", result.error.message)
        self.assertNotIn("\t", result.error.message)
        self.assertIn("line one line two tabbed", result.error.message)


class TestDefaultTransportHTTPErrorUnwrapping(unittest.TestCase):
    """``_default_transport`` must turn HTTPError into (status, body)."""

    def test_http_error_is_unwrapped_to_status_and_body(self):
        error = _http_error(
            429, b'{"error": {"message": "rate limited",'
                 b' "code": "rate_limit_exceeded"}}',
            reason="Too Many Requests",
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            status, body = openai_module._default_transport(
                "https://api.openai.com/v1/chat/completions",
                {}, b"{}", 1.0,
            )
        self.assertEqual(status, 429)
        self.assertEqual(
            json.loads(body.decode("utf-8"))["error"]["code"],
            "rate_limit_exceeded",
        )

    def test_http_error_flows_through_invoke_as_http_error(self):
        # THE regression test for the reported bug: before the fix this
        # path collapsed into transport_error "transport failure: HTTPError".
        error = _http_error(
            401, b'{"error": {"message": "Incorrect API key provided",'
                 b' "code": "invalid_api_key"}}',
            reason="Unauthorized",
        )
        provider = OpenAIProvider("gpt-test")  # DEFAULT urllib transport
        environment = dict(os.environ)
        environment["OPENAI_API_KEY"] = "sk-unit-" + uuid.uuid4().hex
        with (mock.patch.dict(os.environ, environment, clear=True),
              mock.patch("urllib.request.urlopen", side_effect=error)):
            result = provider.invoke(_request())
        assert result.error is not None
        self.assertEqual(result.error.code, "http_error")
        self.assertIn("401", result.error.message)
        self.assertIn("invalid_api_key", result.error.message)
        self.assertIn("Incorrect API key provided", result.error.message)
        self.assertIsNone(result.tool_call)

    def test_http_error_without_a_usable_status_stays_a_transport_failure(
            self):
        exotic = urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions",
            500, "no usable status", email.message.Message(), None,
        )
        object.__setattr__(exotic, "code", None)  # exotic: no status code
        stub = _StubTransport(raises=exotic)
        provider = OpenAIProvider("gpt-test", transport=stub)
        environment = dict(os.environ)
        environment["OPENAI_API_KEY"] = "sk-unit-x"
        with mock.patch.dict(os.environ, environment, clear=True):
            result = provider.invoke(_request())
        assert result.error is not None
        self.assertEqual(result.error.code, "transport_error")


if __name__ == "__main__":
    unittest.main()
