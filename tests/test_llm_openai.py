"""Unit tests for the stdlib-urllib OpenAI adapter (AMEND-4A-1).

Every test uses a STUBBED transport — no network, no real API call. The
adapter must read ``OPENAI_API_KEY`` at call time, never store or leak it,
translate only the envelope, recompute the usage total, and map every
failure (missing key, transport, HTTP status, malformed body, zero/multiple
tool calls, bad arguments) to a deterministic ProviderError.
"""
import json
import os
import unittest
import uuid
from unittest import mock

from robot_factory_arena.policies.llm import TOOL_SCHEMA
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


def _chat_body(content="moving north", tool_calls=None, prompt_tokens=4,
               completion_tokens=6):
    if tool_calls is None:
        tool_calls = [
            {
                "function": {
                    "name": "move",
                    "arguments": "{\"direction\": \"north\"}",
                }
            }
        ]
    return json.dumps(
        {
            "choices": [{"message": {"content": content,
                                     "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": prompt_tokens,
                      "completion_tokens": completion_tokens},
        }
    ).encode("utf-8")


class TestOpenAIProviderIdentityAndKeyHandling(unittest.TestCase):
    def test_pinned_identity_and_request_echo(self):
        provider = OpenAIProvider("gpt-test",
                                  transport=_StubTransport(body=_chat_body()))
        self.assertEqual(provider.provider, "openai")
        self.assertEqual(provider.model, "gpt-test")
        environment = dict(os.environ)
        environment["OPENAI_API_KEY"] = "sk-unit-" + uuid.uuid4().hex
        with mock.patch.dict(os.environ, environment, clear=True):
            result = provider.invoke(_request())
        assert result.error is None
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "gpt-test")
        self.assertEqual(result.request_id, "request-1")

    def test_invoke_rejects_mismatching_request_identity(self):
        provider = OpenAIProvider("gpt-test", transport=_StubTransport())
        with self.assertRaises(ValueError):
            provider.invoke(_request(provider="fake"))
        with self.assertRaises(ValueError):
            provider.invoke(_request(model="other-model"))

    def test_missing_key_fails_closed_without_touching_the_transport(self):
        stub = _StubTransport()
        provider = OpenAIProvider("gpt-test", transport=stub)
        clean = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, clean, clear=True):
            result = provider.invoke(_request())
        assert result.error is not None
        self.assertIsNone(result.tool_call)
        self.assertEqual(result.error.code, "missing_api_key")
        self.assertEqual(stub.calls, [])

    def test_key_is_read_at_call_time_and_sent_only_in_the_header(self):
        secret = "sk-unit-" + uuid.uuid4().hex
        stub = _StubTransport(body=_chat_body())
        # The key is ABSENT at construction time and appears only afterwards.
        clean = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, clean, clear=True):
            provider = OpenAIProvider("gpt-test", transport=stub)
            environment = dict(os.environ)
            environment["OPENAI_API_KEY"] = secret
            with mock.patch.dict(os.environ, environment, clear=True):
                result = provider.invoke(_request())
        assert result.error is None
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(
            stub.calls[0]["headers"]["Authorization"], f"Bearer {secret}"
        )
        # The instance must not retain the key anywhere.
        self.assertNotIn(secret, repr(vars(provider)))
        self.assertNotIn(secret, repr(provider))

    def test_payload_translates_only_the_envelope(self):
        stub = _StubTransport(body=_chat_body())
        provider = OpenAIProvider("gpt-test", transport=stub)
        environment = dict(os.environ)
        environment["OPENAI_API_KEY"] = "sk-unit-x"
        with mock.patch.dict(os.environ, environment, clear=True):
            provider.invoke(_request())
        payload = stub.calls[0]["payload"]
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["messages"],
                         [{"role": "user", "content": "the frozen prompt"}])
        self.assertIs(payload["parallel_tool_calls"], False)
        self.assertEqual(len(payload["tools"]), 3)
        for sent, frozen in zip(payload["tools"], TOOL_SCHEMA["tools"]):
            self.assertEqual(sent["type"], "function")
            self.assertEqual(sent["function"]["name"], frozen["name"])
            self.assertEqual(sent["function"]["description"],
                             frozen["description"])
            self.assertEqual(sent["function"]["parameters"],
                             frozen["parameters"])
        self.assertNotIn("tool_schema", payload)
        self.assertNotIn("observation", payload)


class TestOpenAIProviderResponses(unittest.TestCase):
    def _invoke(self, status=200, body=None, raises=None):
        stub = _StubTransport(
            status=status,
            body=_chat_body() if body is None else body,
            raises=raises,
        )
        provider = OpenAIProvider("gpt-test", transport=stub)
        environment = dict(os.environ)
        environment["OPENAI_API_KEY"] = "sk-unit-x"
        with mock.patch.dict(os.environ, environment, clear=True):
            return provider.invoke(_request())

    def test_success_result_recomputes_total_and_carries_the_tool_call(self):
        result = self._invoke()
        assert result.error is None and result.tool_call is not None
        self.assertEqual(result.usage.input_tokens, 4)
        self.assertEqual(result.usage.output_tokens, 6)
        self.assertEqual(result.usage.total_tokens, 10)
        self.assertEqual(result.raw_visible_response, "moving north")
        self.assertEqual(result.tool_call.to_mapping(),
                         {"tool": "move", "arguments": {"direction": "north"}})
        self.assertIsInstance(result.latency_ms, int)
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_transport_failure_becomes_a_deterministic_error(self):
        result = self._invoke(raises=OSError("network unreachable"))
        assert result.error is not None
        self.assertIsNone(result.tool_call)
        self.assertEqual(result.error.code, "transport_error")
        self.assertNotIn("network unreachable", result.error.message)

    def test_http_status_becomes_http_error(self):
        result = self._invoke(status=503)
        assert result.error is not None
        self.assertEqual(result.error.code, "http_error")
        self.assertIn("503", result.error.message)

    def test_malformed_bodies_become_invalid_response(self):
        for body in (b"not json", b"{}", b'{"choices": []}',
                     b'{"choices": [{"message": {}}]}',
                     b'{"choices": [{"message": {"content": "x", '
                     b'"tool_calls": []}}]}'):
            with self.subTest(body=body):
                result = self._invoke(body=body)
                assert result.error is not None
                self.assertEqual(result.error.code, "invalid_response")

    def test_zero_tool_calls_become_no_tool_call_with_usage(self):
        result = self._invoke(body=_chat_body(tool_calls=[],
                                              content="plain prose"))
        assert result.error is not None
        self.assertEqual(result.error.code, "no_tool_call")
        self.assertEqual(result.usage.total_tokens, 10)

    def test_multiple_tool_calls_are_refused(self):
        two = [
            {"function": {"name": "move",
                          "arguments": "{\"direction\": \"north\"}"}},
            {"function": {"name": "place",
                          "arguments": "{\"order_id\": \"order-00\"}"}},
        ]
        result = self._invoke(body=_chat_body(tool_calls=two))
        assert result.error is not None
        self.assertEqual(result.error.code, "multiple_tool_calls")

    def test_non_json_arguments_become_invalid_response(self):
        broken = [{"function": {"name": "move", "arguments": "{direction"}}]
        result = self._invoke(body=_chat_body(tool_calls=broken))
        assert result.error is not None
        self.assertEqual(result.error.code, "invalid_response")

    def test_non_object_arguments_become_invalid_response(self):
        broken = [{"function": {"name": "move", "arguments": "[1, 2]"}}]
        result = self._invoke(body=_chat_body(tool_calls=broken))
        assert result.error is not None
        self.assertEqual(result.error.code, "invalid_response")

    def test_unusable_usage_becomes_invalid_response(self):
        body = json.dumps(
            {
                "choices": [{"message": {"content": None, "tool_calls": []}}],
                "usage": {"prompt_tokens": 1.5, "completion_tokens": 2},
            }
        ).encode("utf-8")
        result = self._invoke(body=body)
        assert result.error is not None
        self.assertEqual(result.error.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
