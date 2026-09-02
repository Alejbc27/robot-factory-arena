"""Deterministic, network-free, credential-free fake provider (P4A-REQ-01).

Scriptable with a finite sequence of ``ProviderResult`` values: results are
returned strictly in sequence (no fallback, no second result per invocation),
every received request is retained in order for assertions, and exhaustion
produces the deterministic ``script_exhausted`` error result — never a retry,
a default action, or a different provider. The module imports only the
adapter contract and the standard library; a subprocess tripwire test proves
that importing and invoking it loads no core module and reads no credential.
"""
from collections.abc import Mapping

from robot_factory_arena.providers.adapter import (
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ToolCallPayload,
    Usage,
)

PROVIDER_ID = "fake"
MODEL_ID = "fake-v1"
SCRIPT_EXHAUSTED = "script_exhausted"

_RESULT_KEYS = {
    "schema_version", "provider", "model", "request_id", "usage",
    "latency_ms", "raw_visible_response", "tool_call", "error",
}


def result_from_mapping(mapping) -> ProviderResult:
    """Load one scripted result from its exact ProviderResult envelope.

    Used by the CLI ``--script`` path: the file form of a fake script is a
    canonical JSON array of these envelopes. Construction validates every
    field; extra/missing keys and wrong types raise ValueError/TypeError.
    """
    if not isinstance(mapping, Mapping) or set(mapping) != _RESULT_KEYS:
        raise ValueError(
            "script entry must have exactly the ProviderResult envelope keys"
        )
    if mapping["schema_version"] != 1:
        raise ValueError("script entry schema_version must be 1")
    usage = mapping["usage"]
    if not isinstance(usage, Mapping) or set(usage) != {
        "input_tokens", "output_tokens", "total_tokens"
    }:
        raise ValueError("script entry usage must have the exact usage keys")
    tool_call = None
    if mapping["tool_call"] is not None:
        payload = mapping["tool_call"]
        if not isinstance(payload, Mapping) or set(payload) != {
            "tool", "arguments"
        }:
            raise ValueError("script entry tool_call must have exact keys")
        tool_call = ToolCallPayload(payload["tool"], dict(payload["arguments"]))
    error = None
    if mapping["error"] is not None:
        fields = mapping["error"]
        if not isinstance(fields, Mapping) or set(fields) != {
            "code", "message"
        }:
            raise ValueError("script entry error must have exact keys")
        error = ProviderError(fields["code"], fields["message"])
    raw = mapping["raw_visible_response"]
    if raw is not None and not isinstance(raw, str):
        raise ValueError("script entry raw_visible_response must be str/null")
    return ProviderResult(
        provider=mapping["provider"],
        model=mapping["model"],
        request_id=mapping["request_id"],
        usage=Usage(
            usage["input_tokens"], usage["output_tokens"],
            usage["total_tokens"],
        ),
        latency_ms=mapping["latency_ms"],
        raw_visible_response=raw,
        tool_call=tool_call,
        error=error,
    )


class FakeProvider:
    """Pinned fake adapter: one scripted ``ProviderResult`` per ``invoke``."""

    def __init__(self, provider: str = PROVIDER_ID, model: str = MODEL_ID,
                 script=()):
        if not isinstance(provider, str) or not provider:
            raise TypeError("provider must be a non-empty string")
        if not isinstance(model, str) or not model:
            raise TypeError("model must be a non-empty string")
        for entry in script:
            if not isinstance(entry, ProviderResult):
                raise TypeError("script entries must be ProviderResult values")
            if entry.provider != provider or entry.model != model:
                raise ValueError(
                    "scripted result identity does not match the pinned adapter"
                )
        self._provider = provider
        self._model = model
        self._script = tuple(script)
        self._received = []

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def requests(self):
        """The retained requests, in invocation order (frozen value objects)."""
        return tuple(self._received)

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        """Return the next scripted result; exhaust deterministically."""
        if not isinstance(request, ProviderRequest):
            raise TypeError("invoke expects a ProviderRequest")
        if request.provider != self._provider or request.model != self._model:
            raise ValueError(
                "request identity does not match the pinned adapter identity"
            )
        self._received.append(request)
        if not self._script:
            return ProviderResult(
                provider=self._provider,
                model=self._model,
                request_id=request.request_id,
                usage=Usage(0, 0, 0),
                latency_ms=0,
                raw_visible_response=None,
                tool_call=None,
                error=ProviderError(
                    SCRIPT_EXHAUSTED,
                    "the scripted result sequence is exhausted",
                ),
            )
        result = self._script[0]
        self._script = self._script[1:]
        if result.request_id != request.request_id:
            raise ValueError(
                "scripted result request_id does not match the request"
            )
        return result
