"""Physical run artifact store and the run evidence recorder (T-16).

Layout (REQ-09): every physical run owns ``<root>/runs/<logical_run_id>/
<execution_id>/`` and receives, at minimum, ``config.json`` (the locked
semantic RunConfig), ``task.json``, ``fault_tape.json`` (the RESOLVED tape
with concrete operation keys, persisted BEFORE execution per design flow
step 2 — or the explicit canonical ``null`` artifact), an append-only
``trace.jsonl``, ``final_state.json``, ``operation-log.json`` (locked
OperationLog schema, submission order), ``replay-input.json`` (locked
ReplayInput schema), plus ``boundaries/step-<n>.json`` commit-boundary
proofs. ``receipt.json``/``replay-result.json``/``report.html`` are released
by B5/B6 and are intentionally absent here.

Path independence (REQ-07): every deterministic byte is computed from
values, never from the directory path or execution id. Boundary files are
path-addressed storage proving the simulated persistence boundary (REQ-05);
they are excluded from all semantic digests. Individual files are written
temp + flush + fsync + atomic replace (+ directory fsync) — a durability
helper, NOT a transaction or exactly-once claim (REQ-05, REQ-10).

``RunEvidenceRecorder`` is the B3 ``EvidenceCallbacks`` implementation: the
runner wires it exclusively through ``EvidenceCallbacks(**recorder.
callback_map())`` — no runtime module imports evidence writers (AST-tested).
At the commit boundary it persists the boundary proof FIRST (durability
before bookkeeping); a raised exception surfaces to the runner, which
classifies the run ``unresolved_outcome`` — the writer itself never raises
for expected control flow. After ``run_finished`` it closes the trace
(locked decision 3: run events -> run_finished -> close/digest) and builds
the scoreless ``ReceiptCandidate`` for B5's verifier.
"""
import os
import re
from collections.abc import Mapping
from pathlib import Path

from robot_factory_arena.contracts.outcomes import SUPPORTED_TOOLS
from robot_factory_arena.contracts.tool_call import validate_operation_key
from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes,
    digest_json,
)
from robot_factory_arena.evidence.receipt import ReceiptCandidate
from robot_factory_arena.evidence.trace import TraceEvent, TraceWriter
from robot_factory_arena.faults.tape import resolve_fault_tape

SCHEMA_VERSION = 1
RUNS_SUBDIR = "runs"
BOUNDARY_DIRNAME = "boundaries"

# The pre-final declared artifacts (REQ-09); receipt/replay-result/report
# arrive in B5/B6 and are deliberately not written by B4.
REQUIRED_ARTIFACT_NAMES = (
    "config.json",
    "final_state.json",
    "fault_tape.json",
    "operation-log.json",
    "replay-input.json",
    "task.json",
    "trace.jsonl",
)

_OPERATION_KEYS = (
    "arguments", "events", "fault_ids", "operation_key", "policy_step",
    "reason", "retry", "state_after", "state_before", "status", "tool",
)
_BOUNDARY_KEYS = (
    "arguments", "commit_marker", "events", "operation_key", "operation_record",
    "policy_step", "schema_version", "state_snapshot", "tool",
)
_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")

# Trace event names are the closed vocabulary; domain event records reuse
# the transition event types as their trace ``event`` value.
_DOMAIN_EVENT_TYPES = ("item_picked", "order_delivered", "robot_moved")


def _validate_logical_run_id(value) -> str:
    if (
        not isinstance(value, str)
        or _LOGICAL_RUN_ID_RE.fullmatch(value) is None
    ):
        raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
    return value


def _validate_execution_id(value) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("execution_id must be a non-empty str")
    return value


def _fsync_directory(directory: Path) -> None:
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _atomic_write(path: Path, data: bytes) -> None:
    """temp + flush + fsync + atomic replace; durability, not a transaction."""
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as error:
        # Fail FAST with context: durability failures must surface to the
        # runner (CommitBoundaryUnprovenError -> unresolved_outcome),
        # never be swallowed (REQ-05).
        raise OSError(f"atomic write failed for {path}: {error}") from error
    os.replace(temp, path)
    _fsync_directory(path.parent)


def atomic_write_bytes(path, data: bytes) -> None:
    """Durably write bytes into an existing run directory (B6/T-23 wiring).

    Minimal CLI seam: exposes the recorder's own durability helper (temp +
    flush + fsync + atomic replace + directory fsync) so the CLI can release
    ``receipt.json`` / ``replay-result.json`` / ``report.html`` into a closed
    run directory with identical durability semantics. A durability helper,
    NOT a transaction or exactly-once claim (REQ-05, REQ-10).
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("atomic_write_bytes requires bytes")
    _atomic_write(Path(path), bytes(data))


class ArtifactStore:
    """Root for physical run directories: ``<root>/runs/<lr>/<exec>/``."""

    def __init__(self, root):
        self._root = Path(root)
        if self._root.exists() and not self._root.is_dir():
            raise ValueError(f"artifact root is not a directory: {self._root}")

    @property
    def root(self) -> Path:
        return self._root

    def begin(self, logical_run_id, execution_id) -> "RunArtifactWriter":
        """Create the per-run directory and return its writer."""
        _validate_logical_run_id(logical_run_id)
        _validate_execution_id(execution_id)
        directory = (
            self._root / RUNS_SUBDIR / logical_run_id / execution_id
        )
        directory.mkdir(parents=True, exist_ok=False)
        _fsync_directory(directory.parent)
        return RunArtifactWriter(directory, logical_run_id, execution_id)


class RunArtifactWriter:
    """Atomic canonical-file writer for exactly one physical run directory."""

    def __init__(self, directory: Path, logical_run_id: str, execution_id: str):
        self._directory = directory
        self._logical_run_id = logical_run_id
        self._execution_id = execution_id
        self._finalized = False

    @property
    def directory(self) -> Path:
        return self._directory

    def write_json(self, name, value) -> None:
        """Write exact canonical JSON bytes atomically under the run root."""
        if (
            not isinstance(name, str)
            or not name.endswith(".json")
            or "/" in name
            or "\\" in name
            or ".." in name
        ):
            raise ValueError(f"invalid artifact name: {name!r}")
        _atomic_write(self._directory / name, canonical_json_bytes(value))

    def append_trace_bytes(self, line: bytes) -> None:
        """Append one LF-terminated trace record; flushed and fsynced."""
        if not isinstance(line, (bytes, bytearray)) or not line.endswith(b"\n"):
            raise ValueError("trace lines must be LF-terminated bytes")
        try:
            with open(self._directory / "trace.jsonl", "ab") as handle:
                handle.write(bytes(line))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            # Fail FAST with context: trace-append durability failures must
            # surface to the runner, never be swallowed (REQ-05).
            raise OSError(
                f"trace append failed for {self._directory}: {error}"
            ) from error

    def persist_commit_boundary(self, record: Mapping) -> None:
        """Durably record one proven persistence boundary (path-only storage).

        The record bundles the committed operation record, the state
        snapshot, the domain events, and the commit marker. Boundary files
        are storage addressed by policy step and are excluded from every
        semantic digest.
        """
        if not isinstance(record, Mapping):
            raise TypeError("boundary record must be a mapping")
        if sorted(record) != sorted(_BOUNDARY_KEYS):
            raise ValueError("boundary record key drift")
        marker = record["commit_marker"]
        proven = marker.get("commit_proven") if isinstance(marker, Mapping) else None
        if not isinstance(proven, bool) or not proven:
            raise ValueError("a boundary record must carry a proven marker")
        step = record["policy_step"]
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            raise ValueError("boundary policy_step must be a positive int")
        boundaries = self._directory / BOUNDARY_DIRNAME
        boundaries.mkdir(exist_ok=True)
        _atomic_write(
            boundaries / f"step-{step}.json", canonical_json_bytes(record)
        )

    def finalize(self) -> Path:
        """Flush the directory entry and return the run path (idempotent)."""
        if not self._finalized:
            _fsync_directory(self._directory)
            self._finalized = True
        return self._directory


class RunEvidenceRecorder:
    """The ``EvidenceCallbacks`` implementation over one run directory.

    Constructed by the driver (test/CLI) with the task, the config, and the
    pre-computed logical identity; wire it with ``EvidenceCallbacks(
    **recorder.callback_map())``. ``receipt_candidate`` is available only
    after the run finished; ``close()`` finalizes the directory.
    """

    def __init__(self, root, task, config, logical_run_id, execution_id):
        _validate_logical_run_id(logical_run_id)
        _validate_execution_id(execution_id)
        if not callable(getattr(task, "to_mapping", None)):
            raise TypeError("task must expose to_mapping()")
        if not hasattr(task, "initial_state"):
            raise TypeError("task must expose initial_state")
        if not callable(getattr(config, "to_mapping", None)):
            raise TypeError("config must expose to_mapping()")
        if not hasattr(config, "fault_tape_spec"):
            raise TypeError("config must expose fault_tape_spec")

        self._logical_run_id = logical_run_id
        self._execution_id = execution_id
        self._config = dict(config.to_mapping())
        self._task_mapping = dict(task.to_mapping())
        if self._config.get("seed") != self._task_mapping.get("seed"):
            raise ValueError("config and task identity disagree")
        if self._config.get("logical_run_id") is not None:
            raise ValueError("config must not pin a logical run id")

        self._writer = ArtifactStore(root).begin(logical_run_id, execution_id)
        # The tape resolves as the same pure function the runner uses; the
        # RESOLVED form (concrete keys) is persisted before any observation.
        spec = config.fault_tape_spec
        self._fault_tape = (
            resolve_fault_tape(spec, logical_run_id)
            if spec is not None
            else None
        )
        self._writer.write_json("config.json", self._config)
        self._writer.write_json("task.json", self._task_mapping)
        self._writer.write_json(
            "fault_tape.json",
            None if self._fault_tape is None else self._fault_tape.to_mapping(),
        )
        self._config_digest = digest_json(self._config)

        self._trace = TraceWriter(
            logical_run_id, sink=self._writer.append_trace_bytes
        )
        self._state_digest = digest_json(task.initial_state.to_mapping())
        self._operations: list[dict] = []
        self._boundary_keys: set[str] = set()
        self._pending_faults: dict[str, str] = {}
        self._run_started_seen = False
        self._candidate: ReceiptCandidate | None = None
        self._closed = False

    # -- driver-facing surface -------------------------------------------------

    @property
    def directory(self) -> Path:
        return self._writer.directory

    @property
    def receipt_candidate(self) -> ReceiptCandidate:
        if self._candidate is None:
            raise RuntimeError("the run has not finished; no candidate exists")
        return self._candidate

    def callback_map(self) -> dict:
        """All ten EvidenceCallbacks entries, ready for keyword splatting."""
        return {
            "on_run_started": self._on_run_started,
            "on_observation": self._on_observation,
            "on_policy_decision": self._on_policy_decision,
            "on_parse_rejected": self._on_parse_rejected,
            "on_tool_result": self._on_tool_result,
            "on_commit_boundary": self._on_commit_boundary,
            "on_fault_injected": self._on_fault_injected,
            "on_retry": self._on_retry,
            "on_budget_exhausted": self._on_budget_exhausted,
            "on_run_finished": self._on_run_finished,
        }

    def close(self) -> Path:
        """Finalize the run directory; requires a finished run."""
        if self._candidate is None:
            raise RuntimeError("close() requires a finished run")
        if not self._closed:
            self._writer.finalize()
            self._closed = True
        return self._writer.directory

    # -- operation records -------------------------------------------------

    def _operation_record(
        self, step, call, status, reason, state_after, events, retry,
        fault_ids,
    ) -> dict:
        return {
            "policy_step": step,
            "operation_key": call.operation_key,
            "tool": call.tool,
            "arguments": dict(call.arguments),
            "status": status,
            "reason": reason,
            "state_before": self._state_digest,
            "state_after": state_after,
            "events": list(events),
            "retry": retry,
            "fault_ids": list(fault_ids),
        }

    def _trace_tool(self, call):
        return call.tool if getattr(call, "tool", None) in SUPPORTED_TOOLS \
            else None

    # -- EvidenceCallbacks implementation (exact emission order) ----------

    def _on_run_started(self, logical_run_id, config):
        if logical_run_id != self._logical_run_id:
            raise ValueError("run_started logical id disagrees with the run")
        if self._run_started_seen:
            raise RuntimeError("run_started may fire only once per run")
        self._run_started_seen = True
        self._trace.append(TraceEvent(
            "run_started",
            state_after=self._state_digest,
            payload={
                "policy_id": self._config["policy_id"],
                "policy_digest": self._config["policy_digest"],
                "budget": self._config["budget"],
                "grants": list(self._config["grants"]),
            },
        ))

    def _on_observation(self, policy_step, serialized_observation):
        self._trace.append(TraceEvent(
            "observation_emitted",
            policy_step=policy_step,
            state_before=self._state_digest,
            payload={
                "observation_digest": digest_json(serialized_observation)
            },
        ))

    def _decision_fields(self, candidate):
        """(key, tool, payload) from the untrusted candidate; deterministic."""
        key = None
        tool = None
        if isinstance(candidate, Mapping):
            raw_key = candidate.get("operation_key")
            if isinstance(raw_key, str) and validate_operation_key(
                raw_key, self._logical_run_id
            ):
                key = raw_key
            if candidate.get("tool") in SUPPORTED_TOOLS:
                tool = candidate["tool"]
            try:
                canonical_json_bytes(candidate)
                payload: dict = {"candidate": dict(candidate)}
            except (TypeError, ValueError):
                payload = {
                    "candidate_unserializable": type(candidate).__name__
                }
            if _contains_execution_id(payload):
                payload = {"candidate_unrecordable": "execution_id"}
        elif hasattr(candidate, "to_mapping"):
            payload = {"candidate": dict(candidate.to_mapping())}
            raw_key = getattr(candidate, "operation_key", None)
            if isinstance(raw_key, str) and validate_operation_key(
                raw_key, self._logical_run_id
            ):
                key = raw_key
            if getattr(candidate, "tool", None) in SUPPORTED_TOOLS:
                tool = candidate.tool
        else:
            payload = {"candidate_type": type(candidate).__name__}
        return key, tool, payload

    def _on_policy_decision(self, policy_step, candidate):
        key, tool, payload = self._decision_fields(candidate)
        self._trace.append(TraceEvent(
            "policy_decision",
            policy_step=policy_step,
            operation_key=key,
            tool=tool,
            payload=payload,
        ))

    def _on_parse_rejected(self, policy_step, reason, operation_key):
        record = {
            "policy_step": policy_step,
            "operation_key": operation_key or None,
            "tool": None,
            "arguments": None,
            "status": "rejected",
            "reason": reason,
            "state_before": self._state_digest,
            "state_after": None,
            "events": [],
            "retry": False,
            "fault_ids": [],
        }
        self._operations.append(record)
        self._trace.append(TraceEvent(
            "tool_rejected",
            policy_step=policy_step,
            operation_key=operation_key or None,
            state_before=self._state_digest,
            payload={"status": "rejected", "reason": reason},
        ))

    def _on_commit_boundary(self, policy_step, call, result):
        """Persist the boundary proof FIRST (durability before bookkeeping)."""
        snapshot_digest = digest_json(result.state.to_mapping())
        events = [event.to_mapping() for event in result.events]
        record = self._operation_record(
            policy_step, call, "committed", None, snapshot_digest, events,
            retry=False, fault_ids=[],
        )
        self._writer.persist_commit_boundary({
            "schema_version": SCHEMA_VERSION,
            "policy_step": policy_step,
            "operation_key": call.operation_key,
            "tool": call.tool,
            "arguments": dict(call.arguments),
            "state_snapshot": result.state.to_mapping(),
            "events": events,
            "operation_record": record,
            "commit_marker": {
                "commit_proven": True,
                "snapshot_digest": snapshot_digest,
            },
        })
        self._operations.append(record)
        before = self._state_digest
        self._trace.append(TraceEvent(
            "tool_committed",
            policy_step=policy_step,
            operation_key=call.operation_key,
            tool=call.tool,
            arguments=dict(call.arguments),
            state_before=before,
            state_after=snapshot_digest,
            payload={"status": "committed"},
        ))
        for event in result.events:
            self._trace.append(TraceEvent(
                event.event_type,
                policy_step=policy_step,
                operation_key=event.operation_key,
                state_before=before,
                state_after=snapshot_digest,
                payload={"data": dict(event.data)},
            ))
        self._state_digest = snapshot_digest
        self._boundary_keys.add(call.operation_key)

    def _on_tool_result(self, policy_step, call, result):
        if result.committed:
            # The observed commit was already recorded at its persistence
            # boundary; recording it twice would fake a second submission.
            if call.operation_key not in self._boundary_keys:
                raise RuntimeError(
                    "a committed result arrived without a proven boundary"
                )
            return
        record = self._operation_record(
            policy_step, call, result.status, result.reason, None, [],
            retry=False, fault_ids=[],
        )
        self._operations.append(record)
        self._trace.append(TraceEvent(
            "tool_rejected",
            policy_step=policy_step,
            operation_key=call.operation_key,
            tool=self._trace_tool(call),
            arguments=dict(call.arguments),
            state_before=self._state_digest,
            payload={"status": result.status, "reason": result.reason},
        ))

    def _on_fault_injected(self, policy_step, fault_id, operation_key):
        # The retry record (next callback) carries the fault evidence so the
        # already-persisted boundary record is never rewritten.
        self._pending_faults[operation_key] = fault_id
        self._trace.append(TraceEvent(
            "fault_injected",
            policy_step=policy_step,
            operation_key=operation_key,
            payload={"fault_id": fault_id},
        ))

    def _on_retry(self, policy_step, call, result):
        fault_ids = []
        if call.operation_key in self._pending_faults:
            fault_ids.append(self._pending_faults[call.operation_key])
        record = self._operation_record(
            policy_step, call, result.status, result.reason, None, [],
            retry=True, fault_ids=fault_ids,
        )
        self._operations.append(record)
        self._trace.append(TraceEvent(
            "retry",
            policy_step=policy_step,
            operation_key=call.operation_key,
            tool=call.tool,
            arguments=dict(call.arguments),
            state_before=self._state_digest,
            payload={"status": result.status, "reason": result.reason},
        ))

    def _on_budget_exhausted(self, policy_steps_used):
        self._trace.append(TraceEvent(
            "budget_exhausted",
            payload={"policy_steps_used": policy_steps_used},
        ))

    def _on_run_finished(self, outcome, counters, final_state):
        final_digest = digest_json(final_state.to_mapping())
        self._trace.append(TraceEvent(
            "run_finished",
            state_after=final_digest,
            payload={
                "outcome": outcome,
                "counters": {
                    "policy_steps": counters.policy_steps,
                    "tool_attempts": counters.tool_attempts,
                    "committed_effects": counters.committed_effects,
                    "internal_retries": counters.internal_retries,
                    "contract_violations": counters.contract_violations,
                    "terminal_checks": counters.terminal_checks,
                },
            },
        ))
        # Locked decision 3: run events -> run_finished -> close/digest.
        self._trace.close()

        self._writer.write_json(
            "final_state.json", final_state.to_mapping()
        )
        operation_log = {
            "schema_version": SCHEMA_VERSION,
            "logical_run_id": self._logical_run_id,
            "operations": list(self._operations),
        }
        self._writer.write_json("operation-log.json", operation_log)
        replay_input = {
            "schema_version": SCHEMA_VERSION,
            "logical_run_id": self._logical_run_id,
            "task_identity": {
                "task_version": self._config["task_version"],
                "seed": self._config["seed"],
                "config_digest": self._config_digest,
            },
            "grants": list(self._config["grants"]),
            "budget": self._config["budget"],
            "fault_tape": (
                None
                if self._fault_tape is None
                else self._fault_tape.to_mapping()
            ),
            "operations": list(self._operations),
        }
        self._writer.write_json("replay-input.json", replay_input)

        self._candidate = ReceiptCandidate(
            schema_version=SCHEMA_VERSION,
            task_version=self._config["task_version"],
            logical_run_id=self._logical_run_id,
            execution_id=self._execution_id,
            seed=self._config["seed"],
            config_digest=self._config_digest,
            policy_id=self._config["policy_id"],
            policy_digest=self._config["policy_digest"],
            budget=self._config["budget"],
            grants=tuple(self._config["grants"]),
            fault_tape_digest=(
                None
                if self._fault_tape is None
                else self._fault_tape.identity
            ),
            trace_digest=self._trace.digest(),
            final_state_digest=final_digest,
            operation_log_digest=digest_json(operation_log),
            counters=counters,
            outcome=outcome,
        )


def _contains_execution_id(value) -> bool:
    """Depth-first execution_id guard for untrusted candidate payloads."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "execution_id" or _contains_execution_id(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_execution_id(item) for item in value)
    return False
