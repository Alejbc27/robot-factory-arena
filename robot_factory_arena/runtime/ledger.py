"""Idempotency ledger keyed only by operation_key (REQ-03, REQ-04).

The ledger persists the FIRST committed or rejected result per operation key.
The same key with the same normalized tool and arguments replays the stored
result as an audit-visible ``idempotent_replay`` WITHOUT invoking the
transition authority again; the same key with a different normalized payload
returns a deterministic ``operation_key_conflict`` rejection with no effect.
Ledger replay is evidence of a durable first result — it is NOT an
exactly-once guarantee claim (REQ-05, REQ-10). The in-memory dict is a
runtime-owned private cursor (design §2) and is never handed to policy code.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from robot_factory_arena.contracts.outcomes import SUPPORTED_TOOLS
from robot_factory_arena.evidence.canonical import digest_json

if TYPE_CHECKING:  # pragma: no cover - typing-only import (avoids a cycle)
    from robot_factory_arena.runtime.tool_bay import ToolResult

NEW_KEY = "new_key"
IDEMPOTENT_REPLAY = "idempotent_replay"
OPERATION_KEY_CONFLICT = "operation_key_conflict"

LEDGER_DECISION_KINDS = (NEW_KEY, IDEMPOTENT_REPLAY, OPERATION_KEY_CONFLICT)

# Only first results are stored; replays are produced by lookup, never stored.
ENTRY_STATUSES = ("committed", "rejected")

_DIGEST_PREFIX = "sha256:"
_DIGEST_LENGTH = len(_DIGEST_PREFIX) + 64


def arguments_digest(arguments) -> str:
    """Canonical payload digest; equivalent mappings always hash identically."""
    if not isinstance(arguments, Mapping):
        raise TypeError("arguments must be a mapping")
    return digest_json(dict(arguments))


def _is_digest(value) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_DIGEST_PREFIX)
        and len(value) == _DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value[len(_DIGEST_PREFIX):])
    )


@dataclass(frozen=True)
class LedgerEntry:
    """The durable first result stored under one operation key.

    A committed entry carries the post-commit state snapshot digest; a
    rejected entry carries none. The stored status is exactly the stored
    result's status — replays are a lookup-time status, never an entry.
    """

    operation_key: str
    tool: str
    arguments_digest: str
    status: str
    result: "ToolResult"
    committed_snapshot_digest: str | None

    def __post_init__(self):
        if not isinstance(self.operation_key, str) or not self.operation_key:
            raise ValueError("operation_key must be a non-empty str")
        if self.tool not in SUPPORTED_TOOLS:
            raise ValueError(f"tool must be one of {SUPPORTED_TOOLS}")
        if not _is_digest(self.arguments_digest):
            raise ValueError("arguments_digest must be a sha256: digest")
        if self.status not in ENTRY_STATUSES:
            raise ValueError(
                f"status must be one of {ENTRY_STATUSES}; replays are never "
                "stored"
            )
        if self.status == "committed":
            if not _is_digest(self.committed_snapshot_digest):
                raise ValueError(
                    "a committed entry carries its snapshot digest"
                )
        elif self.committed_snapshot_digest is not None:
            raise ValueError("a rejected entry carries no snapshot digest")
        if getattr(self.result, "status", None) != self.status:
            raise ValueError("the stored result must carry the entry status")

    def payload_matches(self, tool: str, arguments) -> bool:
        """Payload identity: normalized tool plus canonical arguments digest."""
        return (
            self.tool == tool
            and self.arguments_digest == arguments_digest(arguments)
        )


@dataclass(frozen=True)
class LedgerDecision:
    """The outcome of one ledger lookup before any dispatch."""

    kind: str
    stored: LedgerEntry | None
    reason: str | None

    def __post_init__(self):
        if self.kind not in LEDGER_DECISION_KINDS:
            raise ValueError(f"kind must be one of {LEDGER_DECISION_KINDS}")
        if self.kind == NEW_KEY:
            if self.stored is not None or self.reason is not None:
                raise ValueError("a new-key decision carries nothing")
        elif self.kind == IDEMPOTENT_REPLAY:
            if not isinstance(self.stored, LedgerEntry) or self.reason is not None:
                raise ValueError(
                    "a replay decision carries the stored entry and no reason"
                )
        elif self.reason != OPERATION_KEY_CONFLICT or self.stored is not None:
            raise ValueError(
                "a conflict decision carries only the conflict reason"
            )


class Ledger:
    """First-result store keyed only by operation_key; first store always wins."""

    def __init__(self):
        self._entries: dict[str, LedgerEntry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, operation_key) -> bool:
        return operation_key in self._entries

    def lookup(self, key, tool, arguments) -> LedgerDecision:
        """Classify one submission against the stored first result."""
        if not isinstance(key, str) or not key:
            raise TypeError("operation key must be a non-empty str")
        entry = self._entries.get(key)
        if entry is None:
            return LedgerDecision(NEW_KEY, None, None)
        if entry.payload_matches(tool, arguments):
            return LedgerDecision(IDEMPOTENT_REPLAY, entry, None)
        return LedgerDecision(OPERATION_KEY_CONFLICT, None, OPERATION_KEY_CONFLICT)

    def store_first(self, entry: LedgerEntry) -> None:
        """Store the first result for a key; later stores never overwrite it."""
        if not isinstance(entry, LedgerEntry):
            raise TypeError("store_first accepts only LedgerEntry values")
        self._entries.setdefault(entry.operation_key, entry)
