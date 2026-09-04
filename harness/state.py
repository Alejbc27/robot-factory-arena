"""Typed orchestration state with append-only reducers (design §7).

Pure ``TypedDict`` + reducer functions; no langgraph import in the foundation
(graph wiring is Unit 3+). Nodes own writes; edges only inspect and route.
Artifact generation sorts reducer records by their declared deterministic
identity, never completion timing.
"""

from typing import TypedDict


class HarnessState(TypedDict, total=False):
    historial: list  # append-only: launch/receipt/recovery records
    descubrimientos: list  # append-only: discovery records
    decision_log: list  # append-only: decisions and routing events
    intentos: int | None  # replace: current attempt ordinal/work item
    semilla: str | None  # replace: current branch seed
    condition: str | None  # replace: current route condition
    agent_id: str | None  # replace: current route agent
    context_snapshot_ref: dict | None  # replace: current frozen launch input
    status: str | None  # replace: current node/route status


APPEND_FIELDS = ("historial", "descubrimientos", "decision_log")
REPLACE_FIELDS = (
    "intentos",
    "semilla",
    "condition",
    "agent_id",
    "context_snapshot_ref",
    "status",
)
SORT_KEYS = {
    "historial": ("attempt_ordinal", "seq"),
    "descubrimientos": ("source_attempt_id", "explorer_id", "discovery_ordinal"),
    "decision_log": ("attempt_ordinal", "seq"),
}


def initial_state():
    return {field: [] for field in APPEND_FIELDS}


def _append(current, field, record):
    if field not in APPEND_FIELDS:
        raise ValueError(f"not an append-only field: {field}")
    return {**current, field: [*current.get(field, []), record]}


def append_historial(current, record):
    return _append(current, "historial", record)


def append_descubrimientos(current, record):
    return _append(current, "descubrimientos", record)


def append_decision_log(current, record):
    return _append(current, "decision_log", record)


def set_current(current, **fields):
    unknown = set(fields) - set(REPLACE_FIELDS)
    if unknown:
        raise ValueError(f"not replace fields: {sorted(unknown)}")
    return {**current, **fields}


def ordered_records(current, field):
    keys = SORT_KEYS[field]
    return sorted(current.get(field, []), key=lambda r: tuple(r[k] for k in keys))
