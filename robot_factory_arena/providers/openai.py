"""Minimal stdlib-urllib OpenAI chat-completions adapter (AMEND-4A-1).

A permanent, dependency-free live adapter: one chat-completions call per
``invoke``, the frozen common tool schema translated ONLY into the envelope
the API requires, ``OPENAI_API_KEY`` read from the environment AT CALL TIME
(never stored on the instance, never logged, never embedded in an error), and
every failure mapped to a deterministic ``ProviderError`` — no retries beyond
one transport attempt, no silent fallback, no second provider. The HTTP layer
is injectable (``transport``) so unit tests run with a stub and the default
path is the only place ``urllib`` is used.

Envelope translation (P4A-REQ-04 permits envelope-only translation): the
frozen ``tools`` array becomes OpenAI ``{"type": "function", "function": …}``
entries and ``parallel_tool_calls: false`` is asserted; the model, user
message (the frozen rendered prompt), and single-call discipline are carried
unchanged.
"""
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from robot_factory_arena.providers.adapter import (
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ToolCallPayload,
    Usage,
)

Transport = Callable[[str, dict, bytes, float], "tuple[int, bytes]"]

PROVIDER_ID = "openai"
DEFAULT_API_URL = "https://api.openai.com/v1/chat/completions"
API_KEY_VARIABLE = "OPENAI_API_KEY"

ERROR_MISSING_KEY = "missing_api_key"
ERROR_TRANSPORT = "transport_error"
ERROR_HTTP = "http_error"
ERROR_INVALID_RESPONSE = "invalid_response"
ERROR_NO_TOOL_CALL = "no_tool_call"
ERROR_MULTIPLE_TOOL_CALLS = "multiple_tool_calls"

# Upper bound for each sanitized upstream error field in diagnostics.
_MAX_DETAIL_CHARS = 300


def _sanitized_http_detail(body):
    """Bounded ``code=… message=…`` detail from an upstream error body.

    ONLY the JSON ``error.code`` and ``error.message`` scalar fields are
    surfaced, whitespace-flattened and truncated. The raw body, headers,
    the request payload, and any credential material are never included,
    so an oversized or hostile body cannot leak into diagnostics.
    """
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (ValueError, AttributeError, TypeError):
        return ""
    if not isinstance(envelope, dict):
        return ""
    error = envelope.get("error")
    if not isinstance(error, dict):
        return ""
    parts = []
    for field in ("code", "message"):
        value = error.get(field)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        flattened = " ".join(str(value).split())[:_MAX_DETAIL_CHARS]
        if flattened:
            parts.append(f"{field}={flattened}")
    if not parts:
        return ""
    return ": " + " ".join(parts)


def _default_transport(url, headers, payload_bytes, timeout):
    """The real stdlib transport; the ONLY network touchpoint in the package.

    An ``HTTPError`` is a NORMAL upstream answer (a 4xx/5xx response with a
    body), so it is unwrapped into a plain ``(status, body)`` result instead
    of collapsing into an opaque transport exception. Without a usable status
    it propagates to the adapter's deterministic transport-error path.
    """
    request = urllib.request.Request(
        url, data=payload_bytes, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        if not isinstance(error.code, int):
            raise  # no usable status: keep the transport-failure path
        try:
            body = error.read()
        except Exception:
            body = b""  # the diagnostic body is best-effort; status is enough
        if error.fp is not None:  # close() needs a body stream to close
            error.close()
        return error.code, body


class OpenAIProvider:
    """Pinned live adapter: ``provider='openai'``, one model, one call per ask."""

    def __init__(self, model: str, api_url: str = DEFAULT_API_URL,
                 transport: Transport | None = None, timeout: float = 60.0):
        if not isinstance(model, str) or not model:
            raise TypeError("model must be a non-empty string")
        if not isinstance(api_url, str) or not api_url:
            raise TypeError("api_url must be a non-empty string")
        if transport is not None and not callable(transport):
            raise TypeError("transport must be callable or None")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a number")
        self._model = model
        self._api_url = api_url
        self._transport: Transport = (
            transport if transport is not None else _default_transport
        )
        # int and float are both valid urlopen timeout seconds; no coercion.
        self._timeout = timeout

    @property
    def provider(self) -> str:
        return PROVIDER_ID

    @property
    def model(self) -> str:
        return self._model

    def _error(self, request, code, message, started, usage=None):
        latency_ms = (time.monotonic_ns() - started) // 1_000_000
        return ProviderResult(
            provider=PROVIDER_ID,
            model=request.model,
            request_id=request.request_id,
            usage=usage if usage is not None else Usage(0, 0, 0),
            latency_ms=latency_ms,
            raw_visible_response=None,
            tool_call=None,
            error=ProviderError(code, message),
        )

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        """One chat-completions call; every failure becomes a ProviderError."""
        if not isinstance(request, ProviderRequest):
            raise TypeError("invoke expects a ProviderRequest")
        if request.provider != PROVIDER_ID or request.model != self._model:
            raise ValueError(
                "request identity does not match the pinned adapter identity"
            )
        started = time.monotonic_ns()
        api_key = os.environ.get(API_KEY_VARIABLE)  # read at call time only
        if not api_key:
            return self._error(
                request, ERROR_MISSING_KEY,
                f"{API_KEY_VARIABLE} is not set in the environment", started,
            )
        payload = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    },
                }
                for tool in request.tool_schema.get("tools", [])
            ],
            "parallel_tool_calls": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",  # header only, never stored
        }
        try:
            status, body = self._transport(
                self._api_url, headers,
                json.dumps(payload).encode("utf-8"), self._timeout,
            )
        except Exception as error:  # transport failures are deterministic
            return self._error(
                request, ERROR_TRANSPORT,
                f"transport failure: {type(error).__name__}", started,
            )
        if status != 200:
            return self._error(
                request, ERROR_HTTP,
                f"upstream returned HTTP {status}"
                f"{_sanitized_http_detail(body)}",
                started,
            )
        try:
            envelope = json.loads(body.decode("utf-8"))
            message = envelope["choices"][0]["message"]
            api_usage = envelope["usage"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return self._error(
                request, ERROR_INVALID_RESPONSE,
                "response is not a usable chat-completions envelope", started,
            )
        raw_tokens = (
            api_usage.get("prompt_tokens"),
            api_usage.get("completion_tokens"),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in raw_tokens
        ):
            return self._error(
                request, ERROR_INVALID_RESPONSE,
                "usage counts are missing or not integers", started,
            )
        usage = Usage(raw_tokens[0], raw_tokens[1], raw_tokens[0] + raw_tokens[1])
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            return self._error(
                request, ERROR_INVALID_RESPONSE,
                "message content must be a string or null", started,
            )
        tool_calls = message.get("tool_calls") or []
        if len(tool_calls) > 1:
            return self._error(
                request, ERROR_MULTIPLE_TOOL_CALLS,
                "the model attempted parallel/multiple tool calls", started,
            )
        if not tool_calls:
            return self._error(
                request, ERROR_NO_TOOL_CALL,
                "the model answered without a tool call", started, usage
            )
        try:
            function = tool_calls[0]["function"]
            name = function["name"]
            arguments_text = function["arguments"]
        except (KeyError, TypeError, IndexError):
            return self._error(
                request, ERROR_INVALID_RESPONSE,
                "tool call is missing its function name or arguments",
                started,
            )
        if not isinstance(name, str) or not name:
            return self._error(
                request, ERROR_INVALID_RESPONSE,
                "tool call name must be a non-empty string", started,
            )
        try:
            arguments = json.loads(arguments_text)
        except (ValueError, TypeError):
            return self._error(
                request, ERROR_INVALID_RESPONSE,
                "tool call arguments are not valid JSON", started,
            )
        if not isinstance(arguments, dict):
            return self._error(
                request, ERROR_INVALID_RESPONSE,
                "tool call arguments must be a JSON object", started,
            )
        latency_ms = (time.monotonic_ns() - started) // 1_000_000
        return ProviderResult(
            provider=PROVIDER_ID,
            model=request.model,
            request_id=request.request_id,
            usage=usage,
            latency_ms=latency_ms,
            raw_visible_response=content,
            tool_call=ToolCallPayload(name, arguments),
            error=None,
        )
