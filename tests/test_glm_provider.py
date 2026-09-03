"""Unit tests for the stdlib-only Z.AI GLM provider adapter."""

import json
import os
import unittest
import uuid
from unittest import mock

from robot_factory_arena.policies.llm import TOOL_SCHEMA
from robot_factory_arena.providers.adapter import ProviderRequest
from robot_factory_arena.providers.glm import GLMProvider

DEFAULT_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"


def _request(**overrides):
    values = {
        "provider": "glm",
        "model": "glm-4.5",
        "request_id": "request-1",
        "observation": {"schema_version": 1},
        "prompt": "the frozen prompt",
        "tool_schema": TOOL_SCHEMA,
    }
    values.update(overrides)
    return ProviderRequest(**values)


class _StubTransport:
    def __init__(self, status=200, body=b"{}", raises=None):
        self.calls = []
        self.status = status
        self.body = body
        self.raises = raises

    def __call__(self, url, headers, payload_bytes, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": json.loads(payload_bytes.decode("utf-8")),
                "timeout": timeout,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


def _chat_body(content="moving north", tool_calls=None):
    if tool_calls is None:
        tool_calls = [
            {
                "function": {
                    "name": "move",
                    "arguments": '{"direction": "north"}',
                }
            }
        ]
    return json.dumps(
        {
            "choices": [{"message": {"content": content, "tool_calls": tool_calls}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 6,
                "total_tokens": 10,
            },
        }
    ).encode("utf-8")


class TestGLMProviderRequest(unittest.TestCase):
    def test_identity_default_endpoint_and_request_envelope(self):
        stub = _StubTransport(body=_chat_body())
        provider = GLMProvider("glm-4.5", transport=stub)
        secret = "zai-unit-" + uuid.uuid4().hex
        environment = dict(os.environ)
        environment["ZAI_API_KEY"] = secret
        with mock.patch.dict(os.environ, environment, clear=True):
            result = provider.invoke(_request())

        self.assertEqual(provider.provider, "glm")
        self.assertEqual(provider.model, "glm-4.5")
        self.assertIsNone(result.error)
        self.assertEqual(len(stub.calls), 1)
        call = stub.calls[0]
        self.assertEqual(call["url"], DEFAULT_URL)
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {secret}")
        self.assertEqual(call["payload"]["model"], "glm-4.5")
        self.assertEqual(
            call["payload"]["messages"],
            [{"role": "user", "content": "the frozen prompt"}],
        )
        self.assertEqual(call["payload"]["tool_choice"], "auto")
        self.assertNotIn("parallel_tool_calls", call["payload"])
        self.assertEqual(
            call["payload"]["tools"],
            [{"type": "function", "function": tool} for tool in TOOL_SCHEMA["tools"]],
        )
        self.assertNotIn("observation", call["payload"])
        self.assertNotIn("tool_schema", call["payload"])

    def test_key_is_read_at_call_time_and_not_retained(self):
        stub = _StubTransport(body=_chat_body())
        clean = {
            key: value for key, value in os.environ.items() if key != "ZAI_API_KEY"
        }
        with mock.patch.dict(os.environ, clean, clear=True):
            provider = GLMProvider("glm-4.5", transport=stub)
            secret = "zai-unit-" + uuid.uuid4().hex
            with mock.patch.dict(
                os.environ, {**clean, "ZAI_API_KEY": secret}, clear=True
            ):
                result = provider.invoke(_request())

        self.assertIsNone(result.error)
        self.assertNotIn(secret, repr(vars(provider)))
        self.assertNotIn(secret, repr(provider))

    def test_missing_key_does_not_touch_transport(self):
        stub = _StubTransport()
        provider = GLMProvider("glm-4.5", transport=stub)
        clean = {
            key: value for key, value in os.environ.items() if key != "ZAI_API_KEY"
        }
        with mock.patch.dict(os.environ, clean, clear=True):
            result = provider.invoke(_request())

        assert result.error is not None
        self.assertEqual(result.error.code, "missing_api_key")
        self.assertEqual(stub.calls, [])


class TestGLMProviderResponses(unittest.TestCase):
    def _invoke(self, body=None, raises=None, status=200):
        stub = _StubTransport(
            status=status, body=_chat_body() if body is None else body, raises=raises
        )
        provider = GLMProvider("glm-4.5", transport=stub)
        environment = dict(os.environ)
        environment["ZAI_API_KEY"] = "zai-unit-" + uuid.uuid4().hex
        with mock.patch.dict(os.environ, environment, clear=True):
            return provider.invoke(_request())

    def test_success_parses_usage_content_and_exactly_one_tool_call(self):
        result = self._invoke()

        assert result.error is None
        assert result.tool_call is not None
        self.assertEqual(
            result.usage.to_mapping(),
            {
                "input_tokens": 4,
                "output_tokens": 6,
                "total_tokens": 10,
            },
        )
        self.assertEqual(result.raw_visible_response, "moving north")
        self.assertEqual(
            result.tool_call.to_mapping(),
            {"tool": "move", "arguments": {"direction": "north"}},
        )
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_no_and_multiple_tool_calls_are_rejected_with_usage_contract(self):
        no_call = self._invoke(body=_chat_body(content="plain prose", tool_calls=[]))
        assert no_call.error is not None
        self.assertEqual(no_call.error.code, "no_tool_call")
        self.assertEqual(no_call.usage.total_tokens, 10)

        multiple = self._invoke(
            body=_chat_body(
                tool_calls=[
                    {"function": {"name": "move", "arguments": "{}"}},
                    {"function": {"name": "place", "arguments": "{}"}},
                ]
            )
        )
        assert multiple.error is not None
        self.assertEqual(multiple.error.code, "multiple_tool_calls")

    def test_string_form_arguments_are_parsed_into_the_tool_call(self):
        result = self._invoke(
            body=_chat_body(
                tool_calls=[
                    {
                        "function": {
                            "name": "move",
                            "arguments": '{"direction": "north"}',
                        }
                    }
                ]
            )
        )

        assert result.error is None
        assert result.tool_call is not None
        self.assertEqual(
            result.tool_call.to_mapping(),
            {"tool": "move", "arguments": {"direction": "north"}},
        )

    def test_object_form_arguments_are_accepted_directly(self):
        result = self._invoke(
            body=_chat_body(
                tool_calls=[
                    {
                        "function": {
                            "name": "move",
                            "arguments": {"direction": "north"},
                        }
                    }
                ]
            )
        )

        assert result.error is None
        assert result.tool_call is not None
        self.assertEqual(
            result.tool_call.to_mapping(),
            {"tool": "move", "arguments": {"direction": "north"}},
        )

    def test_malformed_json_arguments_are_invalid_response(self):
        result = self._invoke(
            body=_chat_body(
                tool_calls=[{"function": {"name": "move", "arguments": "{broken"}}]
            )
        )
        assert result.error is not None
        self.assertEqual(result.error.code, "invalid_response")

    def test_non_object_and_non_string_arguments_are_invalid_response(self):
        for arguments in (
            ["north"],
            42,
            None,
            True,
            '["north"]',
            '"north"',
            "42",
        ):
            with self.subTest(arguments=arguments):
                result = self._invoke(
                    body=_chat_body(
                        tool_calls=[
                            {"function": {"name": "move", "arguments": arguments}}
                        ]
                    )
                )
                assert result.error is not None
                self.assertEqual(result.error.code, "invalid_response")

    def test_malformed_response_and_transport_error_are_bounded(self):
        for body in (b"not json", b"{}", b'{"choices": []}'):
            with self.subTest(body=body):
                result = self._invoke(body=body)
                assert result.error is not None
                self.assertEqual(result.error.code, "invalid_response")

        secret = "zai-unit-" + uuid.uuid4().hex
        result = self._invoke(raises=OSError(secret))
        assert result.error is not None
        self.assertEqual(result.error.code, "transport_error")
        self.assertNotIn(secret, result.error.message)

    def test_http_error_detail_is_allow_listed_and_secret_safe(self):
        secret = "zai-unit-" + uuid.uuid4().hex
        body = json.dumps(
            {
                "error": {"code": "invalid_api_key", "message": f"bad {secret}"},
                "internal_echo": secret,
                "request_payload": {"prompt": "the frozen prompt"},
            }
        ).encode("utf-8")
        stub = _StubTransport(status=401, body=body)
        provider = GLMProvider("glm-4.5", transport=stub)
        with mock.patch.dict(os.environ, {"ZAI_API_KEY": secret}, clear=True):
            result = provider.invoke(_request())

        assert result.error is not None
        self.assertEqual(result.error.code, "http_error")
        self.assertIn("HTTP 401", result.error.message)
        self.assertIn("invalid_api_key", result.error.message)
        self.assertNotIn(secret, result.error.message)
        self.assertNotIn("internal_echo", result.error.message)
        self.assertNotIn("request_payload", result.error.message)


if __name__ == "__main__":
    unittest.main()
