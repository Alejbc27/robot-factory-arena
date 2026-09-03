"""Minimal stdlib-urllib Z.AI GLM chat-completions adapter.

The adapter is intentionally independent from the OpenAI adapter while using
Z.AI's OpenAI-compatible chat-completions envelope. It performs one request per
``invoke`` call, reads ``ZAI_API_KEY`` only at call time, and maps provider
failures to bounded ``ProviderError`` values. The transport is injectable so
all behavior can be tested without a live API call.
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlsplit

from robot_factory_arena.providers.adapter import (
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ToolCallPayload,
    Usage,
)

Transport = Callable[[str, dict, bytes, float], "tuple[int, bytes]"]

PROVIDER_ID = "glm"
DEFAULT_API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
API_KEY_VARIABLE = "ZAI_API_KEY"

ERROR_MISSING_KEY = "missing_api_key"
ERROR_TRANSPORT = "transport_error"
ERROR_HTTP = "http_error"
ERROR_INVALID_RESPONSE = "invalid_response"
ERROR_NO_TOOL_CALL = "no_tool_call"
ERROR_MULTIPLE_TOOL_CALLS = "multiple_tool_calls"

_MAX_DETAIL_CHARS = 300


def _sanitized_http_detail(body, sensitive=None):
    """Return bounded, allow-listed detail from an upstream error body."""
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
        if sensitive:
            flattened = flattened.replace(sensitive, "[redacted]")
        if flattened:
            parts.append(f"{field}={flattened}")
    if not parts:
        return ""
    return ": " + " ".join(parts)


def _default_transport(url, headers, payload_bytes, timeout):
    """Perform the only real network operation used by this adapter."""
    request = urllib.request.Request(
        url, data=payload_bytes, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        if not isinstance(error.code, int):
            raise
        try:
            body = error.read()
        except Exception:
            body = b""
        if error.fp is not None:
            error.close()
        return error.code, body


class GLMProvider:
    """Pinned live adapter: ``provider='glm'``, one model, one call per ask."""

    def __init__(
        self,
        model: str,
        api_url: str = DEFAULT_API_URL,
        transport: Transport | None = None,
        timeout: float = 60.0,
    ):
        if not isinstance(model, str) or not model:
            raise TypeError("model must be a non-empty string")
        if not isinstance(api_url, str) or not api_url:
            raise TypeError("api_url must be a non-empty string")
        parsed_url = urlsplit(api_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("api_url must be an absolute HTTP(S) URL")
        if transport is not None and not callable(transport):
            raise TypeError("transport must be callable or None")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a number")
        self._model = model
        self._api_url = api_url
        self._transport: Transport = (
            transport if transport is not None else _default_transport
        )
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
        """Make one chat-completions request and return one provider result."""
        if not isinstance(request, ProviderRequest):
            raise TypeError("invoke expects a ProviderRequest")
        if request.provider != PROVIDER_ID or request.model != self._model:
            raise ValueError(
                "request identity does not match the pinned adapter identity"
            )
        started = time.monotonic_ns()
        api_key = os.environ.get(API_KEY_VARIABLE)
        if not api_key:
            return self._error(
                request,
                ERROR_MISSING_KEY,
                f"{API_KEY_VARIABLE} is not set in the environment",
                started,
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
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        try:
            status, body = self._transport(
                self._api_url,
                headers,
                json.dumps(payload).encode("utf-8"),
                self._timeout,
            )
        except Exception as error:
            return self._error(
                request,
                ERROR_TRANSPORT,
                f"transport failure: {type(error).__name__}",
                started,
            )

        if status != 200:
            return self._error(
                request,
                ERROR_HTTP,
                f"upstream returned HTTP {status}"
                f"{_sanitized_http_detail(body, api_key)}",
                started,
            )

        try:
            envelope = json.loads(body.decode("utf-8"))
            choices = envelope["choices"]
            message = choices[0]["message"]
            api_usage = envelope["usage"]
            if not isinstance(choices, list) or not choices:
                raise ValueError
            if not isinstance(message, dict) or not isinstance(api_usage, dict):
                raise TypeError
            input_tokens = api_usage.get("prompt_tokens")
            output_tokens = api_usage.get("completion_tokens")
            if (
                isinstance(input_tokens, bool)
                or not isinstance(input_tokens, int)
                or isinstance(output_tokens, bool)
                or not isinstance(output_tokens, int)
            ):
                return self._error(
                    request,
                    ERROR_INVALID_RESPONSE,
                    "usage counts are missing or not integers",
                    started,
                )
            usage = Usage(input_tokens, output_tokens, input_tokens + output_tokens)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return self._error(
                request,
                ERROR_INVALID_RESPONSE,
                "response is not a usable chat-completions envelope",
                started,
            )

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            return self._error(
                request,
                ERROR_INVALID_RESPONSE,
                "message content must be a string or null",
                started,
            )
        tool_calls = message.get("tool_calls")
        if tool_calls is None:
            tool_calls = []
        if not isinstance(tool_calls, list):
            return self._error(
                request,
                ERROR_INVALID_RESPONSE,
                "tool_calls must be a list",
                started,
            )
        if len(tool_calls) > 1:
            return self._error(
                request,
                ERROR_MULTIPLE_TOOL_CALLS,
                "the model attempted parallel/multiple tool calls",
                started,
            )
        if not tool_calls:
            return self._error(
                request,
                ERROR_NO_TOOL_CALL,
                "the model answered without a tool call",
                started,
                usage,
            )

        try:
            function = tool_calls[0]["function"]
            name = function["name"]
            arguments_text = function["arguments"]
        except (KeyError, TypeError, IndexError):
            return self._error(
                request,
                ERROR_INVALID_RESPONSE,
                "tool call is missing its function name or arguments",
                started,
            )
        if not isinstance(name, str) or not name:
            return self._error(
                request,
                ERROR_INVALID_RESPONSE,
                "tool call name must be a non-empty string",
                started,
            )
        try:
            arguments = json.loads(arguments_text)
        except (ValueError, TypeError):
            return self._error(
                request,
                ERROR_INVALID_RESPONSE,
                "tool call arguments are not valid JSON",
                started,
            )
        if not isinstance(arguments, dict):
            return self._error(
                request,
                ERROR_INVALID_RESPONSE,
                "tool call arguments must be a JSON object",
                started,
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
