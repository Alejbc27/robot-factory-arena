"""Deterministic, network-free, credential-free fake provider (P4A-REQ-01).

Scriptable with a finite sequence of ``ProviderResult`` values: results are
returned strictly in sequence (no fallback, no second result per invocation),
every received request is retained in order for assertions, and exhaustion
produces the deterministic ``script_exhausted`` error result — never a retry,
a default action, or a different provider. The module imports only the
adapter contract and the standard library; a subprocess tripwire test proves
that importing and invoking it loads no core module and reads no credential.
"""
from robot_factory_arena.providers.adapter import (
    ProviderError,
    ProviderRequest,
    ProviderResult,
    Usage,
)

PROVIDER_ID = "fake"
MODEL_ID = "fake-v1"
SCRIPT_EXHAUSTED = "script_exhausted"


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
