"""Append-only, hash-chained JSONL trace (REQ-03, REQ-05, REQ-07).

Every record carries EXACTLY the thirteen REQ-03 top-level fields:
``schema_version, seq, logical_run_id, policy_step, event, operation_key,
tool, arguments, state_before, state_after, payload, prev_event_hash,
event_hash``. (tasks.md prose says "14-field"; the binding spec REQ-03 and
the design both enumerate exactly these 13 — the spec's field list is
authoritative and is frozen by tests/test_golden_trace.py.)

Chain rules: ``seq`` starts at 0 and increments by one; the first
``prev_event_hash`` is ``null``; every later record links the preceding
record's ``event_hash``; ``event_hash = sha256(canonical(record with
event_hash omitted))``; ``trace_digest`` = SHA-256 over all UTF-8 JSONL
bytes with exactly one LF after each record. No ``execution_id``, path, or
timestamp may enter any record (REQ-07); payloads carrying an
``execution_id`` key at any depth are rejected outright.

Closure ordering (locked decision 3): all run events, then ``run_finished``
as the final record, then ``close()`` computes the stable digest.
``verification_finished`` is NEVER a trace record — it lives in the
post-verification receipt block (evidence/receipt.py). Appends after
``run_finished`` or after ``close()`` are rejected; ``digest()`` is
unavailable before close and stable afterwards.

This module knows no runtime, faults, receipt, store, or replay code: it
serializes and hashes the records it is given (its only import is the
shared canonical authority).
"""
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from robot_factory_arena.contracts.outcomes import SUPPORTED_TOOLS
from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes,
    digest_json,
    sha256_digest,
)

TRACE_SCHEMA_VERSION = 1

# Runtime evidence event types (REQ-03) that may appear in the v0 trace.
# ``verification_finished`` is deliberately absent (locked decision 3).
RUNTIME_TRACE_EVENTS = (
    "budget_exhausted",
    "fault_injected",
    "observation_emitted",
    "policy_decision",
    "retry",
    "run_finished",
    "run_started",
    "tool_committed",
    "tool_rejected",
)

# Committed domain transitions also appear as their own trace records.
DOMAIN_TRACE_EVENTS = ("item_picked", "order_delivered", "robot_moved")

TRACE_EVENT_TYPES = RUNTIME_TRACE_EVENTS + DOMAIN_TRACE_EVENTS

# The one REQ-03 event reserved for post-verification auxiliary evidence.
VERIFICATION_FINISHED_EVENT = "verification_finished"

# Exactly the REQ-03 record fields (see module docstring: 13, not 14).
TRACE_RECORD_FIELDS = (
    "schema_version",
    "seq",
    "logical_run_id",
    "policy_step",
    "event",
    "operation_key",
    "tool",
    "arguments",
    "state_before",
    "state_after",
    "payload",
    "prev_event_hash",
    "event_hash",
)

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _validate(value):
    """Structural walk: JSON-ness plus the execution_id ban (REQ-07)."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "execution_id":
                raise ValueError(
                    "execution_id must never enter the trace (REQ-03, REQ-07)"
                )
            _validate(item)
        return
    raise TypeError(f"unsupported trace value: {type(value).__name__}")


@dataclass(frozen=True)
class TraceEvent:
    """The semantic input behind one trace record (pre-chain fields).

    ``policy_step`` is the 1-based reserved attempt number or None for
    lifecycle events; digests are ``sha256:`` strings or None; ``payload``
    carries the audit data for the event. Construction validates the closed
    event vocabulary and rejects ``verification_finished`` outright.
    """

    event: str
    policy_step: int | None = None
    operation_key: str | None = None
    tool: str | None = None
    arguments: Mapping | None = None
    state_before: str | None = None
    state_after: str | None = None
    payload: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if self.event == VERIFICATION_FINISHED_EVENT:
            raise ValueError(
                "verification_finished is never a trace record (locked "
                "decision 3); it belongs to the receipt verification block"
            )
        if self.event not in TRACE_EVENT_TYPES:
            raise ValueError(f"event must be one of {TRACE_EVENT_TYPES}")
        if self.policy_step is not None:
            if isinstance(self.policy_step, bool) or not isinstance(
                self.policy_step, int
            ) or self.policy_step < 1:
                raise ValueError("policy_step must be a positive int or None")
        if self.operation_key is not None and (
            not isinstance(self.operation_key, str) or not self.operation_key
        ):
            raise ValueError("operation_key must be a non-empty str or None")
        if self.tool is not None and self.tool not in SUPPORTED_TOOLS:
            raise ValueError(f"tool must be one of {SUPPORTED_TOOLS} or None")
        if self.arguments is not None and not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping or None")
        for name in ("state_before", "state_after"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None
            ):
                raise ValueError(f"{name} must be a sha256 digest or None")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        _validate(self.payload)
        if self.arguments is not None:
            _validate({"arguments": self.arguments})


@dataclass(frozen=True)
class TraceRecord:
    """One immutable hash-chained trace record (exact REQ-03 shape)."""

    seq: int
    event: TraceEvent
    logical_run_id: str
    prev_event_hash: str | None
    event_hash: str

    def to_mapping(self) -> dict:
        """Exact 13-field mapping; nullable fields are explicit nulls."""
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "seq": self.seq,
            "logical_run_id": self.logical_run_id,
            "policy_step": self.event.policy_step,
            "event": self.event.event,
            "operation_key": self.event.operation_key,
            "tool": self.event.tool,
            "arguments": None
            if self.event.arguments is None
            else dict(self.event.arguments),
            "state_before": self.event.state_before,
            "state_after": self.event.state_after,
            "payload": dict(self.event.payload),
            "prev_event_hash": self.prev_event_hash,
            "event_hash": self.event_hash,
        }


class TraceWriter:
    """Append-only chain authority for one run's trace (REQ-03, REQ-05).

    ``sink`` is an optional callable receiving each LF-terminated canonical
    record line as it is appended (the artifact store streams ``trace.jsonl``
    through it). The writer itself stays in-memory and path-independent.
    """

    def __init__(
        self,
        logical_run_id: str,
        sink: Callable[[bytes], None] | None = None,
    ):
        if (
            not isinstance(logical_run_id, str)
            or _LOGICAL_RUN_ID_RE.fullmatch(logical_run_id) is None
        ):
            raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
        if sink is not None and not callable(sink):
            raise TypeError("sink must be callable or None")
        self._logical_run_id = logical_run_id
        self._sink = sink
        self._records: list[TraceRecord] = []
        self._lines: list[bytes] = []
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def records(self) -> tuple[TraceRecord, ...]:
        return tuple(self._records)

    def append(self, event: TraceEvent) -> TraceRecord:
        """Append one event, extending the hash chain (order enforced)."""
        if self._closed:
            raise RuntimeError("the trace is closed; no further appends")
        if self._records and self._records[-1].event.event == "run_finished":
            raise RuntimeError(
                "run_finished is final: no record may follow it (locked "
                "decision 3)"
            )
        if not isinstance(event, TraceEvent):
            raise TypeError("append accepts only TraceEvent values")
        seq = len(self._records)
        prev_hash = (
            self._records[-1].event_hash if self._records else None
        )
        mapping = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "seq": seq,
            "logical_run_id": self._logical_run_id,
            "policy_step": event.policy_step,
            "event": event.event,
            "operation_key": event.operation_key,
            "tool": event.tool,
            "arguments": None if event.arguments is None else dict(event.arguments),
            "state_before": event.state_before,
            "state_after": event.state_after,
            "payload": dict(event.payload),
            "prev_event_hash": prev_hash,
        }
        record = TraceRecord(
            seq=seq,
            event=event,
            logical_run_id=self._logical_run_id,
            prev_event_hash=prev_hash,
            event_hash=digest_json(mapping),
        )
        line = canonical_json_bytes(record.to_mapping()) + b"\n"
        self._records.append(record)
        self._lines.append(line)
        if self._sink is not None:
            self._sink(line)
        return record

    def close(self) -> None:
        """Seal the trace: run_finished must be the final record (decision 3)."""
        if self._closed:
            return
        if not self._records:
            raise RuntimeError("cannot close an empty trace")
        if self._records[-1].event.event != "run_finished":
            raise RuntimeError(
                "trace closure requires run_finished as the final record "
                "(locked decision 3)"
            )
        self._closed = True

    def jsonl_bytes(self) -> bytes:
        """All record bytes, exactly one LF after each record."""
        if not self._lines:
            raise RuntimeError("the trace has no records")
        return b"".join(self._lines)

    def digest(self) -> str:
        """``trace_digest``: SHA-256 over the full JSONL bytes (post-close)."""
        if not self._closed:
            raise RuntimeError("trace_digest is unavailable before close()")
        return sha256_digest(self.jsonl_bytes())
