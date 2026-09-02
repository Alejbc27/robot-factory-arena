"""Provider-neutral adapter contract: ProviderRequest/ProviderResult (P4A-REQ-01).

One operation per adapter: ``invoke(request) -> result`` — at most one provider
request per call, no fallback to another provider/model, no parallel tool
calls, and no import of the v0 core. Provider and model are pinned on the
adapter instance and every result echoes them exactly.

The result is PROVIDER EVIDENCE, never a core ``ToolCall``: it carries no
``operation_key``, and ``ToolCallPayload.tool`` is untrusted provider output —
the ``move|pick|place`` restriction and exact argument shapes are enforced by
LLMPolicy normalization (P4A-REQ-02), mirroring how the runner treats policy
candidates as untrusted data. A successful result has a non-null ``tool_call``
and null ``error``; an unsuccessful result has a null ``tool_call`` and a
non-null ``error`` (the two are mutually exclusive by construction).
``total_tokens`` always equals ``input_tokens + output_tokens``.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

SCHEMA_VERSION = 1


def _require_non_empty_str(value, label):
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _require_mapping(value, label):
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return value


def _require_non_negative_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an int")
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


@dataclass(frozen=True)
class Usage:
    """Token usage; ``total_tokens == input_tokens + output_tokens`` exactly."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self):
        _require_non_negative_int(self.input_tokens, "input_tokens")
        _require_non_negative_int(self.output_tokens, "output_tokens")
        _require_non_negative_int(self.total_tokens, "total_tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")

    def to_mapping(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class ToolCallPayload:
    """The provider-reported tool call as raw (untrusted) evidence."""

    tool: str
    arguments: Mapping

    def __post_init__(self):
        _require_non_empty_str(self.tool, "tool")
        _require_mapping(self.arguments, "arguments")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

    def to_mapping(self):
        return {"tool": self.tool, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class ProviderError:
    """Deterministic adapter-level error; the code is non-empty ASCII."""

    code: str
    message: str

    def __post_init__(self):
        _require_non_empty_str(self.code, "code")
        if any(ord(character) >= 128 for character in self.code):
            raise ValueError("code must be ASCII")
        _require_non_empty_str(self.message, "message")

    def to_mapping(self):
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ProviderRequest:
    """The exact P4A-REQ-01 request envelope (schema_version pinned at 1)."""

    provider: str
    model: str
    request_id: str
    observation: Mapping
    prompt: str
    tool_schema: Mapping

    def __post_init__(self):
        _require_non_empty_str(self.provider, "provider")
        _require_non_empty_str(self.model, "model")
        _require_non_empty_str(self.request_id, "request_id")
        _require_mapping(self.observation, "observation")
        _require_non_empty_str(self.prompt, "prompt")
        _require_mapping(self.tool_schema, "tool_schema")

    def to_mapping(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "observation": self.observation,
            "prompt": self.prompt,
            "tool_schema": self.tool_schema,
        }


@dataclass(frozen=True)
class ProviderResult:
    """The exact P4A-REQ-01 result envelope; tool_call/error are exclusive."""

    provider: str
    model: str
    request_id: str
    usage: Usage
    latency_ms: int
    raw_visible_response: str | None
    tool_call: ToolCallPayload | None
    error: ProviderError | None

    def __post_init__(self):
        _require_non_empty_str(self.provider, "provider")
        _require_non_empty_str(self.model, "model")
        _require_non_empty_str(self.request_id, "request_id")
        if not isinstance(self.usage, Usage):
            raise TypeError("usage must be a Usage value")
        _require_non_negative_int(self.latency_ms, "latency_ms")
        if self.raw_visible_response is not None and not isinstance(
            self.raw_visible_response, str
        ):
            raise TypeError("raw_visible_response must be a string or None")
        if self.tool_call is not None and not isinstance(self.tool_call, ToolCallPayload):
            raise TypeError("tool_call must be a ToolCallPayload or None")
        if self.error is not None and not isinstance(self.error, ProviderError):
            raise TypeError("error must be a ProviderError or None")
        if (self.tool_call is None) == (self.error is None):
            raise ValueError(
                "exactly one of tool_call (success) or error (failure) must be set"
            )

    def to_mapping(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "usage": self.usage.to_mapping(),
            "latency_ms": self.latency_ms,
            "raw_visible_response": self.raw_visible_response,
            "tool_call": None if self.tool_call is None else self.tool_call.to_mapping(),
            "error": None if self.error is None else self.error.to_mapping(),
        }


@runtime_checkable
class ProviderAdapter(Protocol):
    """The one-operation adapter contract every provider implements."""

    provider: str
    model: str

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        """Perform at most one provider request and return one result."""
        ...
