"""Phase 4A provider-adapter contract tests (T4A-01..T4A-03, P4A-REQ-01).

The envelope is provider evidence, never a core ToolCall: exact top-level
keys, exact nested usage/error/tool-call shapes, pinned provider/model echo,
deterministic usage/latency invariants, success/error exclusivity, and no
operation_key anywhere. The fake provider is network-free, credential-free,
and scriptable with a finite ProviderResult sequence.
"""
import unittest

from robot_factory_arena.providers.adapter import (
    ProviderAdapter,
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ToolCallPayload,
    Usage,
)
from robot_factory_arena.providers.fake import FakeProvider

SCHEMA_VERSION = 1

REQUEST_KEYS = {
    "schema_version",
    "provider",
    "model",
    "request_id",
    "observation",
    "prompt",
    "tool_schema",
}
RESULT_KEYS = {
    "schema_version",
    "provider",
    "model",
    "request_id",
    "usage",
    "latency_ms",
    "raw_visible_response",
    "tool_call",
    "error",
}
USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens"}
TOOL_CALL_KEYS = {"tool", "arguments"}
ERROR_KEYS = {"code", "message"}


def _request(**overrides):
    values = {
        "provider": "fake",
        "model": "fake-v1",
        "request_id": "request-1",
        "observation": {"schema_version": 1},
        "prompt": "prompt text",
        "tool_schema": {"schema_version": 1},
    }
    values.update(overrides)
    return ProviderRequest(**values)


def _usage(**overrides):
    values = {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
    values.update(overrides)
    return Usage(**values)


def _tool_call(**overrides):
    values = {"tool": "move", "arguments": {"direction": "north"}}
    values.update(overrides)
    return ToolCallPayload(**values)


def _error(**overrides):
    values = {"code": "provider_error", "message": "upstream failure"}
    values.update(overrides)
    return ProviderError(**values)


def _result(**overrides):
    values = {
        "provider": "fake",
        "model": "fake-v1",
        "request_id": "request-1",
        "usage": _usage(),
        "latency_ms": 12,
        "raw_visible_response": "moving north",
        "tool_call": _tool_call(),
        "error": None,
    }
    values.update(overrides)
    return ProviderResult(**values)


class TestProviderRequestShape(unittest.TestCase):
    """T4A-01: the request contract is exact and stdlib-only."""

    def test_to_mapping_has_exactly_the_spec_top_level_keys(self):
        mapping = _request().to_mapping()
        self.assertEqual(set(mapping), REQUEST_KEYS)
        self.assertEqual(mapping["schema_version"], SCHEMA_VERSION)

    def test_request_fields_are_exposed_unchanged(self):
        observation = {"schema_version": 1, "world_state": {}}
        schema = {"schema_version": 1, "tools": []}
        request = _request(observation=observation, tool_schema=schema)
        self.assertEqual(request.provider, "fake")
        self.assertEqual(request.model, "fake-v1")
        self.assertEqual(request.request_id, "request-1")
        self.assertEqual(request.observation, observation)
        self.assertEqual(request.prompt, "prompt text")
        self.assertEqual(request.tool_schema, schema)

    def test_empty_or_non_string_identity_fields_are_rejected(self):
        for field in ("provider", "model", "request_id"):
            with self.subTest(field=field):
                for bad in ("", None, 7):
                    with self.assertRaises((TypeError, ValueError)):
                        _request(**{field: bad})

    def test_observation_prompt_and_schema_types_are_enforced(self):
        with self.assertRaises(TypeError):
            _request(observation="not-a-mapping")
        with self.assertRaises(TypeError):
            _request(tool_schema="not-a-mapping")
        with self.assertRaises(TypeError):
            _request(prompt=None)


class TestProviderResultShape(unittest.TestCase):
    """T4A-01: the result contract is exact; usage/latency invariants hold."""

    def test_to_mapping_has_exactly_the_spec_top_level_keys(self):
        mapping = _result().to_mapping()
        self.assertEqual(set(mapping), RESULT_KEYS)
        self.assertEqual(mapping["schema_version"], SCHEMA_VERSION)
        self.assertEqual(set(mapping["usage"]), USAGE_KEYS)
        self.assertEqual(set(mapping["tool_call"]), TOOL_CALL_KEYS)

    def test_error_mapping_shape(self):
        mapping = _result(
            tool_call=None, error=_error()
        ).to_mapping()
        self.assertEqual(set(mapping["error"]), ERROR_KEYS)

    def test_request_id_must_be_a_non_empty_string(self):
        for bad in ("", None, 5):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    _result(request_id=bad)

    def test_usage_must_be_non_negative_integers_summing_exactly(self):
        with self.assertRaises(ValueError):
            _usage(total_tokens=9)  # != input + output
        for bad in (-1, True, 1.5, "2"):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    _usage(input_tokens=bad)
        with self.assertRaises(ValueError):
            _usage(output_tokens=-5, total_tokens=-2)

    def test_latency_must_be_a_non_negative_integer(self):
        for bad in (-1, True, 2.5, None, "7"):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    _result(latency_ms=bad)

    def test_success_and_error_are_exclusive(self):
        # Success: non-null tool_call and null error (both missing fails too).
        with self.assertRaises(ValueError):
            _result(tool_call=None, error=None)
        # Unsuccessful: null tool_call and non-null error.
        with self.assertRaises(ValueError):
            _result(tool_call=_tool_call(), error=_error())

    def test_raw_visible_response_may_be_null_but_not_arbitrary(self):
        self.assertIsNone(_result(raw_visible_response=None).raw_visible_response)
        for bad in (5, [], {}):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                _result(raw_visible_response=bad)

    def test_error_code_is_a_non_empty_ascii_string(self):
        for bad in ("", "café", None, 3):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    _result(tool_call=None, error=_error(code=bad))
        with self.assertRaises((TypeError, ValueError)):
            _result(tool_call=None, error=_error(message=""))

    def test_no_operation_key_can_enter_the_provider_envelope(self):
        request_mapping = _request().to_mapping()
        result_mapping = _result().to_mapping()
        self.assertNotIn("operation_key", request_mapping)
        self.assertNotIn("operation_key", result_mapping)
        self.assertNotIn("operation_key", result_mapping["tool_call"])
        self.assertNotIn("operation_key", result_mapping["usage"])

    def test_tool_call_payload_requires_a_mapping_of_arguments(self):
        with self.assertRaises(TypeError):
            ToolCallPayload(tool="move", arguments="north")
        with self.assertRaises(TypeError):
            ToolCallPayload(tool="", arguments={})


class TestProviderAdapterContract(unittest.TestCase):
    """T4A-01: one operation, invoke(request) -> result, no parallel calls."""

    def test_fake_satisfies_the_provider_adapter_protocol(self):
        provider = FakeProvider()
        self.assertIsInstance(provider, ProviderAdapter)
        self.assertEqual(provider.provider, "fake")
        self.assertEqual(provider.model, "fake-v1")


class TestFakeProviderRoundTrip(unittest.TestCase):
    """T4A-02: deterministic scripted results, request retention, no fallback."""

    def test_one_exact_request_result_round_trip(self):
        scripted = _result()
        provider = FakeProvider(script=[scripted])
        request = _request()
        returned = provider.invoke(request)
        self.assertEqual(returned, scripted)
        self.assertEqual(provider.requests, (request,))

    def test_finite_sequence_order_and_exhaustion_behavior(self):
        first = _result(request_id="request-1", usage=_usage(total_tokens=1, input_tokens=1, output_tokens=0))
        second = _result(
            request_id="request-2",
            tool_call=None,
            error=_error(code="no_tool_call", message="model answered in prose"),
        )
        provider = FakeProvider(script=[first, second])
        self.assertEqual(provider.invoke(_request(request_id="request-1")), first)
        self.assertEqual(provider.invoke(_request(request_id="request-2")), second)
        exhausted = provider.invoke(_request(request_id="request-3"))
        self.assertIsNone(exhausted.tool_call)
        self.assertIsNotNone(exhausted.error)
        self.assertEqual(exhausted.error.code, "script_exhausted")

    def test_requests_are_retained_in_order_and_exposed_immutable(self):
        provider = FakeProvider(script=[_result()])
        request = _request()
        provider.invoke(request)
        recorded = provider.requests
        self.assertEqual(recorded, (request,))
        with self.assertRaises((AttributeError, TypeError)):
            recorded[0].provider = "mutated"  # frozen value object
        self.assertEqual(len(provider.requests), 1)

    def test_invoke_rejects_requests_mismatching_the_pinned_identity(self):
        provider = FakeProvider()
        with self.assertRaises(ValueError):
            provider.invoke(_request(provider="openai"))
        with self.assertRaises(ValueError):
            provider.invoke(_request(model="gpt-x"))

    def test_scripted_results_must_match_the_pinned_identity(self):
        with self.assertRaises(ValueError):
            FakeProvider(script=[_result(provider="openai")])
        with self.assertRaises(ValueError):
            FakeProvider(script=[_result(model="gpt-x")])
        with self.assertRaises(TypeError):
            FakeProvider(script=[{"tool_call": None}])

    def test_no_fallback_or_second_result_per_invocation(self):
        provider = FakeProvider(script=[_result()])
        returned = provider.invoke(_request())
        self.assertEqual(provider.requests, (_request(),))
        self.assertEqual(provider._script, ())
        self.assertEqual(returned.provider, "fake")
        self.assertEqual(returned.model, "fake-v1")


if __name__ == "__main__":
    unittest.main()
