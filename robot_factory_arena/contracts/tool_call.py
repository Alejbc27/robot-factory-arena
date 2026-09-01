"""Exact policy request contract: ToolCall shape, key grammar, normalization (REQ-03, REQ-04).

A valid call has exactly the four fields and the operation-key grammar
``op/<logical_run_id>/step-<decimal>/intent-<1..64 ASCII [A-Za-z0-9._-]>``.
The key identifies the logical intent, not the trace position; retries reuse
it. Untrusted candidates normalize into exact per-tool argument shapes or a
deterministic closed-taxonomy rejection (``malformed_call``,
``unsupported_tool``, ``operation_key_invalid``, ``invalid_arguments``).
Malformed candidates are represented by ParseResult, never by extra ToolCall
fields.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from robot_factory_arena.contracts.outcomes import DIRECTIONS, SUPPORTED_TOOLS

SCHEMA_VERSION = 1

_OPERATION_KEY_RE = re.compile(
    r"op/(lr_[0-9a-f]{64})/step-(0|[1-9][0-9]*)/intent-([A-Za-z0-9._-]{1,64})"
)
_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")
_SHELF_ID_RE = re.compile(r"shelf-(0[0-9])")
_ITEM_ID_RE = re.compile(r"item-(0[0-9])")
_ORDER_ID_RE = re.compile(r"order-(0[0-9])")


def is_valid_logical_run_id(value) -> bool:
    """True iff value is ``lr_`` plus 64 lowercase hex characters."""
    return (
        isinstance(value, str)
        and _LOGICAL_RUN_ID_RE.fullmatch(value) is not None
    )


def validate_operation_key(key, logical_run_id) -> bool:
    """True iff key matches the grammar and embeds exactly this run id."""
    if not is_valid_logical_run_id(logical_run_id):
        return False
    if not isinstance(key, str):
        return False
    match = _OPERATION_KEY_RE.fullmatch(key)
    return match is not None and match.group(1) == logical_run_id


def expand_operation_key(logical_run_id: str, policy_step: int, intent: str) -> str:
    """Two-stage identity support (locked decision 4): expand the concrete
    key only after logical_run_id derivation, e.g. for fault-tape targets."""
    if not is_valid_logical_run_id(logical_run_id):
        raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
    if isinstance(policy_step, bool) or not isinstance(policy_step, int):
        raise TypeError("policy_step must be an int")
    if policy_step < 0:
        raise ValueError("policy_step must be non-negative")
    return f"op/{logical_run_id}/step-{policy_step}/intent-{intent}"


@dataclass(frozen=True)
class ToolCall:
    """Exactly schema_version, tool, arguments, operation_key (REQ-03)."""

    schema_version: int
    tool: str
    arguments: Mapping
    operation_key: str

    def __post_init__(self):
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise TypeError("schema_version must be an int")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if self.tool not in SUPPORTED_TOOLS:
            raise ValueError(f"tool must be one of {SUPPORTED_TOOLS}")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        if not isinstance(self.operation_key, str) or (
            _OPERATION_KEY_RE.fullmatch(self.operation_key) is None
        ):
            raise ValueError("operation_key does not match the v0 grammar")
        # Freeze the exposed mapping so the value object carries no mutable data.
        object.__setattr__(
            self, "arguments", MappingProxyType(dict(self.arguments))
        )

    def to_mapping(self):
        return {
            "schema_version": self.schema_version,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "operation_key": self.operation_key,
        }


@dataclass(frozen=True)
class ParseResult:
    """Normalization outcome: normalized call or a deterministic reason."""

    ok: bool
    tool_call: ToolCall | None
    reason: str | None


def _check_arguments(tool: str, arguments) -> str | None:
    """Exact per-tool shapes (REQ-04); None when valid, else a reason."""
    if not isinstance(arguments, Mapping):
        return "invalid_arguments"
    if tool == "move":
        if set(arguments) != {"direction"}:
            return "invalid_arguments"
        if arguments["direction"] not in DIRECTIONS:
            return "invalid_arguments"
        return None
    if tool == "pick":
        if set(arguments) != {"shelf_id", "item_id"}:
            return "invalid_arguments"
        shelf_id = arguments["shelf_id"]
        item_id = arguments["item_id"]
        if not isinstance(shelf_id, str) or _SHELF_ID_RE.fullmatch(shelf_id) is None:
            return "invalid_arguments"
        if not isinstance(item_id, str) or _ITEM_ID_RE.fullmatch(item_id) is None:
            return "invalid_arguments"
        return None
    if set(arguments) != {"order_id"}:
        return "invalid_arguments"
    order_id = arguments["order_id"]
    if not isinstance(order_id, str) or _ORDER_ID_RE.fullmatch(order_id) is None:
        return "invalid_arguments"
    return None


def normalize_tool_call(candidate) -> ParseResult:
    """Normalize one untrusted policy candidate into a ToolCall."""
    if isinstance(candidate, ToolCall):
        reason = _check_arguments(candidate.tool, candidate.arguments)
        if reason is not None:
            return ParseResult(False, None, reason)
        return ParseResult(True, candidate, None)
    if not isinstance(candidate, Mapping):
        return ParseResult(False, None, "malformed_call")
    if set(candidate) != {"schema_version", "tool", "arguments", "operation_key"}:
        return ParseResult(False, None, "malformed_call")
    schema_version = candidate["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SCHEMA_VERSION
    ):
        return ParseResult(False, None, "malformed_call")
    tool = candidate["tool"]
    if not isinstance(tool, str) or tool not in SUPPORTED_TOOLS:
        return ParseResult(False, None, "unsupported_tool")
    operation_key = candidate["operation_key"]
    if not isinstance(operation_key, str) or (
        _OPERATION_KEY_RE.fullmatch(operation_key) is None
    ):
        return ParseResult(False, None, "operation_key_invalid")
    reason = _check_arguments(tool, candidate["arguments"])
    if reason is not None:
        return ParseResult(False, None, reason)
    call = ToolCall(
        SCHEMA_VERSION, tool, dict(candidate["arguments"]), operation_key
    )
    return ParseResult(True, call, None)
