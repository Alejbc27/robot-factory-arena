"""Independent artifact-only verifier and release gate (REQ-05, REQ-06, T-18..T-20).

The verifier consumes ONLY declared artifact data (parsed canonical mappings —
no runtime objects, no policy, no provider) and reconstructs a minimal
warehouse model from the seed's frozen REQ-01 formulas plus independently
re-applied committed operations (verification/invariants.py). It owns the
official integer score (verification/scoring.py): no claimed runtime score is
ever trusted, and a receipt whose ``verified_score`` disagrees with the
recomputed score is rejected (``score_mismatch``).

Independence (design §4): imports are restricted to the shared canonical
authority, the shared frozen outcome value objects, the golden-frozen seed
formulas, and sibling verification modules — ``runtime``, ``policies``,
``domain.transition``, runtime scoring, and every evidence writer are
forbidden and AST-denylist-enforced
(tests/test_verifier_dependencies.py). The trace-chain rules, the 13-field
record shape, and every auxiliary schema are re-declared in invariants, never
imported from the writers.

Outcome classification (REQ-05/REQ-06, never upgrading ambiguity):
- any ``commit_boundary_unproven`` or unprovable-fault finding -> invalid,
  ``unresolved_outcome`` (never success);
- any other fatal inconsistency (tampered digests, broken chain, illegal
  transition, unauthorized effect, mismatched bindings, contradicted
  counters) -> invalid, ``unresolved_outcome``;
- provable duplicate effects (double commit under one key, repeated delivery)
  -> valid, ``duplicate_effect`` (the artifacts are trustworthy; the run
  misbehaved — REQ-05's broken-ledger classification);
- otherwise the evidence classifies itself: all orders delivered ->
  ``completed``; steps reaching the locked budget -> ``safe_abort``;
  anything else -> ``unresolved_outcome``.
A valid result carries the integer ``floor(100 * unique_valid_completed /
total_orders)``; an invalid one carries no official score (None).

``load_verification_artifacts`` is the one-call composition for the B6
CLI/report flow: it reads a run directory's declared artifacts (canonical
bytes enforced) and returns the VerificationArtifacts for
``Verifier.verify``. The verified-run flow is: run -> recorder candidate ->
verify (receipt absent) -> ``ReceiptWriter.write_final`` -> persist
``receipt.json`` -> re-verify bindings (receipt present).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from robot_factory_arena.contracts.outcomes import (
    CLOSED_REASONS,
    OUTCOMES,
    Counters,
    VerificationResult,
    VerifierViolation,
    sort_violations,
)
from robot_factory_arena.domain.task import TASK_VERSION, generate_task
from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes,
    canonical_jsonl_line,
    digest_json,
)
from robot_factory_arena.verification.invariants import (
    BOUNDARY_KEYS,
    BUDGET_MAXIMUM,
    COMMIT_MARKER_KEYS,
    CONFIG_KEYS,
    EVENT_KEYS,
    FAULT_HOOK,
    FAULT_ID,
    FAULT_KEYS,
    FAULT_KIND,
    FAULT_SPEC_KEYS,
    FAULT_TAPE_KEYS,
    LOG_KEYS,
    OPERATION_KEYS,
    OPERATION_STATUSES,
    PROTOCOL_REASONS,
    REPLAY_KEYS,
    SUPPORTED_TOOLS,
    TARGET_KEYS,
    TASK_IDENTITY_KEYS,
    TASK_KEYS,
    TRACE_EVENT_TYPES,
    TRACE_RECORD_FIELDS,
    canonical_trace_digest,
    independently_apply,
    is_canonical_seed,
    is_digest,
    is_valid_logical_run_id,
    stitched_event,
    validate_operation_key,
)
from robot_factory_arena.verification.scoring import compute_official_score

VERIFIER_VERSION = "verifier-v0"

TRACE_SCHEMA_VERSION = 1
COUNTER_NAMES = (
    "policy_steps",
    "tool_attempts",
    "committed_effects",
    "internal_retries",
    "contract_violations",
    "terminal_checks",
)
RUN_STARTED_PAYLOAD_KEYS = ("budget", "grants", "policy_digest", "policy_id")
RUN_FINISHED_PAYLOAD_KEYS = ("counters", "outcome")
OBSERVATION_PAYLOAD_KEYS = ("observation_digest",)
COMMITTED_PAYLOAD_KEYS = ("status",)
RESULT_PAYLOAD_KEYS = ("reason", "status")
FAULT_PAYLOAD_KEYS = ("fault_id",)
BUDGET_PAYLOAD_KEYS = ("policy_steps_used",)
DOMAIN_PAYLOAD_KEYS = ("data",)

# Receipt keys the verifier binds against (presence check; a released receipt
# additionally carries the ``verification`` block, checked lightly).
RECEIPT_REQUIRED_KEYS = (
    "budget",
    "config_digest",
    "counters",
    "fault_tape_digest",
    "final_state_digest",
    "grants",
    "logical_run_id",
    "operation_log_digest",
    "outcome",
    "schema_version",
    "seed",
    "trace_digest",
    "verified_score",
)


@dataclass(frozen=True)
class VerificationArtifacts:
    """The declared artifact set the verifier consumes (design §2).

    ``grants`` may be omitted (``None``) — the config's sorted grant list is
    authoritative and cross-checked against the receipt when present.
    ``boundaries`` maps integer policy steps to parsed ``boundaries/step-<n>.json``
    proof records. ``trace_jsonl`` is the exact raw JSONL bytes; when given,
    the canonical re-serialization of ``trace_records`` must reproduce it.
    """

    task: Mapping
    config: Mapping
    trace_records: tuple
    final_state: Mapping
    operation_log: Mapping
    replay_input: Mapping
    grants: Mapping | None = None
    fault_tape: Mapping | None = None
    receipt: Mapping | None = None
    boundaries: Mapping | None = None
    trace_jsonl: bytes | None = None


def load_verification_artifacts(directory) -> VerificationArtifacts:
    """Compose VerificationArtifacts from one run directory (REQ-09 layout).

    Every declared artifact is read and canonical-bytes enforced (REQ-03/07):
    a file that is valid JSON but not exact canonical bytes fails here, and a
    semantic tamper fails inside ``Verifier.verify``. ``receipt.json`` is
    optional (absent before the verifier releases it).
    """
    directory = Path(directory)

    def read_canonical(name):
        raw = (directory / name).read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"{name} is not valid JSON: {error}") from error
        if raw != canonical_json_bytes(value):
            raise ValueError(f"{name} is not exact canonical JSON bytes")
        return value

    trace_raw = (directory / "trace.jsonl").read_bytes()
    if not trace_raw.endswith(b"\n"):
        raise ValueError("trace.jsonl must end with exactly one LF")
    records = []
    for raw_line in trace_raw.split(b"\n")[:-1]:
        if not raw_line:
            raise ValueError("trace.jsonl contains a blank line")
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError(
                f"trace.jsonl record is not valid JSON: {error}"
            ) from error
        if raw_line != canonical_json_bytes(record):
            raise ValueError("trace.jsonl contains a non-canonical record line")
        records.append(record)

    boundaries = {}
    boundary_dir = directory / "boundaries"
    if boundary_dir.is_dir():
        for path in sorted(boundary_dir.glob("step-*.json")):
            try:
                step = int(path.stem[len("step-") :])
            except ValueError as error:
                raise ValueError(
                    f"boundary file name {path.name!r} is not step-<n>.json"
                ) from error
            boundaries[step] = read_canonical(str(path.relative_to(directory)))

    receipt = None
    if (directory / "receipt.json").exists():
        receipt = read_canonical("receipt.json")

    return VerificationArtifacts(
        task=read_canonical("task.json"),
        config=read_canonical("config.json"),
        trace_records=tuple(records),
        trace_jsonl=trace_raw,
        final_state=read_canonical("final_state.json"),
        operation_log=read_canonical("operation-log.json"),
        fault_tape=read_canonical("fault_tape.json"),
        replay_input=read_canonical("replay-input.json"),
        receipt=receipt,
        boundaries=boundaries,
    )


class Verifier:
    """The independent release gate: ``verify(artifacts) -> VerificationResult``."""

    def verify(self, artifacts) -> VerificationResult:
        if not isinstance(artifacts, VerificationArtifacts):
            raise TypeError("verify requires a VerificationArtifacts value")
        problems = []  # (fatal, code, location, detail)

        def add(fatal, code, location, detail):
            problems.append((fatal, code, location, detail))

        records = tuple(artifacts.trace_records)
        boundaries = self._normalize_boundaries(artifacts.boundaries, add)

        # -- phase 0: canonical representability of every artifact ----------
        early = [
            ("task", artifacts.task),
            ("config", artifacts.config),
            ("final_state", artifacts.final_state),
            ("operation_log", artifacts.operation_log),
            ("replay_input", artifacts.replay_input),
        ]
        if artifacts.fault_tape is not None:
            early.append(("fault_tape", artifacts.fault_tape))
        if artifacts.receipt is not None:
            early.append(("receipt", artifacts.receipt))
        for name, value in early:
            if not isinstance(value, Mapping):
                add(True, "artifact_mismatch", name, f"{name} must be a mapping")
                return self._finish(problems, 0, 0, "unresolved_outcome")
            try:
                canonical_json_bytes(value)
            except (TypeError, ValueError) as error:
                add(
                    True,
                    "artifact_mismatch",
                    name,
                    f"{name} is not canonical-JSON representable: {error}",
                )
                return self._finish(problems, 0, 0, "unresolved_outcome")
        for record in records:
            if not isinstance(record, Mapping):
                add(
                    True,
                    "trace_chain_invalid",
                    "trace",
                    "trace records must be mappings",
                )
                return self._finish(problems, 0, 0, "unresolved_outcome")
        for step, boundary in boundaries.items():
            try:
                canonical_json_bytes(boundary)
            except (TypeError, ValueError) as error:
                add(
                    True,
                    "artifact_mismatch",
                    f"boundaries/step-{step}",
                    f"boundary proof is not canonical-JSON representable: {error}",
                )
                return self._finish(problems, 0, 0, "unresolved_outcome")

        # -- phase 1: task and config identity ------------------------------
        task_ok = self._check_task(artifacts.task, add)
        config_ok, budget, grants = self._check_config(
            artifacts.config, artifacts.task, task_ok, add
        )
        total_count = len(artifacts.task["orders"]) if task_ok else 0

        # -- phase 2: logical run identity agreement ------------------------
        logical_run_id = self._check_identity(artifacts, records, add)

        # -- phase 3: trace structure, chain, canonical bytes ---------------
        trace_ok = self._check_trace(records, artifacts, logical_run_id, budget, add)
        trace_digest = canonical_trace_digest(records) if records else None

        # -- phase 4: operation log structure -------------------------------
        ops_ok, operations = self._check_operation_log(
            artifacts.operation_log, logical_run_id, budget, add
        )

        # -- phase 5: fault tape structure and spec agreement ---------------
        faults = self._check_fault_tape(artifacts, logical_run_id, add)

        # -- phase 6: independent reconstruction walk ------------------------
        walk = self._walk(
            artifacts,
            operations,
            ops_ok,
            task_ok,
            grants,
            boundaries,
            faults,
            add,
        )

        # -- phase 7: replay-input and receipt bindings ----------------------
        self._check_replay_input(artifacts, budget, add)
        self._check_receipt(
            artifacts, trace_digest, budget, grants, walk["derived"], add
        )

        # -- phase 6.5: final state binds the independent reconstruction -----
        if walk["final_digest"] is not None and ops_ok:
            if digest_json(artifacts.final_state) != walk["final_digest"]:
                add(
                    True,
                    "artifact_mismatch",
                    "final_state",
                    "final_state.json does not match the state independently "
                    "reconstructed from the committed operations (trace-versus-"
                    "final-state mismatch)",
                )

        # -- phase 8: classification (needed by the trace cross-checks) ------
        completed_count = len(walk["delivered"])
        classification = self._classify(walk, completed_count, total_count, budget)

        # -- phase 8.5: receipt claims are non-authoritative (REQ-06) --------
        self._check_receipt_claims(
            artifacts, classification, completed_count, total_count, add
        )

        # -- phase 9: trace cross-checks -------------------------------------
        self._cross_check_trace(
            artifacts,
            records,
            trace_ok,
            ops_ok,
            operations,
            faults,
            walk,
            classification,
            task_ok,
            add,
        )

        # -- phase 10: result -------------------------------------------------
        return self._finish(problems, completed_count, total_count, classification)

    # -- artifact checks ------------------------------------------------------

    def _check_task(self, task, add) -> bool:
        if sorted(task) != sorted(TASK_KEYS):
            add(True, "artifact_mismatch", "task", "task.json key drift")
            return False
        if not is_canonical_seed(task["seed"]):
            add(
                True,
                "artifact_mismatch",
                "task.seed",
                "task seed is not canonical decimal text",
            )
            return False
        try:
            expected = generate_task(task["seed"]).to_mapping()
        except (TypeError, ValueError) as error:
            add(
                True,
                "artifact_mismatch",
                "task.seed",
                f"task seed is not reproducible: {error}",
            )
            return False
        if dict(task) != expected:
            add(
                True,
                "artifact_mismatch",
                "task",
                "task.json does not match the frozen REQ-01 formulas for its "
                "seed (tampering or artifact substitution)",
            )
            return False
        return True

    def _check_config(self, config, task, task_ok, add):
        if sorted(config) != sorted(CONFIG_KEYS):
            add(True, "artifact_mismatch", "config", "config.json key drift")
            return False, BUDGET_MAXIMUM, None
        if config["schema_version"] != 1:
            add(
                True,
                "artifact_mismatch",
                "config.schema_version",
                "config schema_version must be 1",
            )
        if config["task_version"] != TASK_VERSION:
            add(
                True,
                "artifact_mismatch",
                "config.task_version",
                f"config task_version must be {TASK_VERSION!r}",
            )
        if not is_canonical_seed(config["seed"]):
            add(
                True,
                "artifact_mismatch",
                "config.seed",
                "config seed is not canonical decimal text",
            )
        if not isinstance(config["policy_id"], str) or not config["policy_id"]:
            add(
                True,
                "artifact_mismatch",
                "config.policy_id",
                "policy_id must be a non-empty string",
            )
        if not is_digest(config["policy_digest"]):
            add(
                True,
                "artifact_mismatch",
                "config.policy_digest",
                "policy_digest must be a sha256 digest",
            )
        budget = config["budget"]
        if (
            isinstance(budget, bool)
            or not isinstance(budget, int)
            or budget != BUDGET_MAXIMUM
        ):
            add(
                True,
                "artifact_mismatch",
                "config.budget",
                f"budget is locked at {BUDGET_MAXIMUM}",
            )
            budget = BUDGET_MAXIMUM
        grants = None
        raw_grants = config["grants"]
        if (
            not isinstance(raw_grants, list)
            or not raw_grants
            or any(capability not in SUPPORTED_TOOLS for capability in raw_grants)
            or any(a >= b for a, b in zip(raw_grants, raw_grants[1:]))
        ):
            add(
                True,
                "artifact_mismatch",
                "config.grants",
                "grants must be a non-empty ascending list of supported tools",
            )
        else:
            grants = tuple(raw_grants)
        spec = config["fault_tape_spec"]
        if spec is not None:
            self._check_fault_spec(spec, add)
        if task_ok and config["seed"] != task["seed"]:
            add(
                True,
                "artifact_mismatch",
                "config.seed",
                "config seed disagrees with the task seed (substitution)",
            )
        if task_ok and config["task_version"] != task["task_version"]:
            add(
                True,
                "artifact_mismatch",
                "config.task_version",
                "config task_version disagrees with the task",
            )
        return True, budget, grants

    def _check_fault_spec(self, spec, add) -> None:
        if not isinstance(spec, Mapping) or sorted(spec) != sorted(FAULT_SPEC_KEYS):
            add(
                True,
                "artifact_mismatch",
                "config.fault_tape_spec",
                "semantic fault_tape_spec key drift",
            )
            return
        if spec["schema_version"] != 1:
            add(
                True,
                "artifact_mismatch",
                "config.fault_tape_spec.schema_version",
                "fault_tape_spec schema_version must be 1",
            )
        if (
            spec["fault_id"] != FAULT_ID
            or spec["hook"] != FAULT_HOOK
            or spec["kind"] != FAULT_KIND
        ):
            add(
                True,
                "artifact_mismatch",
                "config.fault_tape_spec",
                "v0 admits exactly the response-loss demonstrator tape",
            )
        target = spec["target"]
        if not isinstance(target, Mapping) or sorted(target) != sorted(TARGET_KEYS):
            add(
                True,
                "artifact_mismatch",
                "config.fault_tape_spec.target",
                "fault target key drift",
            )
            return
        step = target["policy_step"]
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            add(
                True,
                "artifact_mismatch",
                "config.fault_tape_spec.target",
                "fault target policy_step must be a positive decimal integer",
            )
        if not isinstance(target["intent"], str) or not target["intent"]:
            add(
                True,
                "artifact_mismatch",
                "config.fault_tape_spec.target",
                "fault target intent must be a non-empty string",
            )

    def _check_identity(self, artifacts, records, add):
        """All logical_run_id sources must agree (artifact substitution proof)."""
        candidates = {}
        log_id = artifacts.operation_log.get("logical_run_id")
        if log_id is not None:
            candidates["operation_log"] = log_id
        replay_id = artifacts.replay_input.get("logical_run_id")
        if replay_id is not None:
            candidates["replay_input"] = replay_id
        if artifacts.receipt is not None:
            receipt_id = artifacts.receipt.get("logical_run_id")
            if receipt_id is not None:
                candidates["receipt"] = receipt_id
        trace_ids = {
            record.get("logical_run_id")
            for record in records
            if record.get("logical_run_id") is not None
        }
        if len(trace_ids) > 1:
            add(
                True,
                "artifact_mismatch",
                "trace",
                "trace records disagree on the logical run id",
            )
        if trace_ids:
            candidates["trace"] = next(iter(trace_ids))
        validated = {
            source: value
            for source, value in candidates.items()
            if is_valid_logical_run_id(value)
        }
        for source, value in candidates.items():
            if not is_valid_logical_run_id(value):
                add(
                    True,
                    "artifact_mismatch",
                    source,
                    f"logical_run_id {value!r} is not lr_ plus 64 hex characters",
                )
        distinct = set(validated.values())
        if len(distinct) > 1:
            add(
                True,
                "artifact_mismatch",
                "logical_run_id",
                "artifacts disagree on the logical run id (substitution)",
            )
        if validated:
            return next(iter(distinct))
        return "lr_" + "0" * 64  # already invalid; keeps later checks total

    def _check_trace(self, records, artifacts, logical_run_id, budget, add) -> bool:
        """Structural + hash-chain validation (rules re-derived, REQ-03)."""
        if not records:
            add(True, "trace_chain_invalid", "trace", "the trace has no records")
            return False
        usable = True
        previous_hash = None
        for index, record in enumerate(records):
            location = f"trace[{index}]"
            if sorted(record) != sorted(TRACE_RECORD_FIELDS):
                add(True, "trace_chain_invalid", location, "trace record field drift")
                usable = False
                continue
            problem = self._trace_record_problem(
                record, index, previous_hash, logical_run_id, budget
            )
            if problem is not None:
                add(True, "trace_chain_invalid", location, problem)
                usable = False
            previous_hash = record["event_hash"]
        if records[-1]["event"] != "run_finished":
            add(
                True,
                "trace_chain_invalid",
                "trace",
                "run_finished must be the final record (locked decision 3)",
            )
            usable = False
        if artifacts.trace_jsonl is not None:
            canonical = b"".join(canonical_jsonl_line(record) for record in records)
            if canonical != artifacts.trace_jsonl:
                add(
                    True,
                    "trace_chain_invalid",
                    "trace",
                    "raw trace bytes are not the canonical serialization of "
                    "the parsed records",
                )
                usable = False
        return usable

    def _trace_record_problem(
        self, record, index, previous_hash, logical_run_id, budget
    ):
        """One structural problem string, or None when the record is sound."""
        if record["schema_version"] != TRACE_SCHEMA_VERSION:
            return "schema_version must be 1"
        if record["seq"] != index:
            return "seq must equal the record position"
        if not is_valid_logical_run_id(record["logical_run_id"]):
            return "logical_run_id is not lr_ plus 64 hex characters"
        if record["logical_run_id"] != logical_run_id:
            return "logical_run_id disagrees with the operation log"
        event = record["event"]
        if event not in TRACE_EVENT_TYPES:
            return f"event {event!r} is outside the closed trace vocabulary"
        step = record["policy_step"]
        if step is not None and (
            isinstance(step, bool)
            or not isinstance(step, int)
            or not 1 <= step <= budget
        ):
            return "policy_step must be a positive int within the budget"
        key = record["operation_key"]
        if key is not None and not validate_operation_key(key, logical_run_id):
            return "operation_key is not valid for this run"
        if record["tool"] is not None and record["tool"] not in SUPPORTED_TOOLS:
            return "tool is not a supported tool"
        if record["arguments"] is not None and not isinstance(
            record["arguments"], Mapping
        ):
            return "arguments must be a mapping or null"
        for name in ("state_before", "state_after"):
            value = record[name]
            if value is not None and not is_digest(value):
                return f"{name} must be a sha256 digest or null"
        payload = record["payload"]
        if not isinstance(payload, Mapping):
            return "payload must be a mapping"
        try:
            canonical_json_bytes(payload)
        except (TypeError, ValueError) as error:
            return f"payload is not canonical-JSON representable: {error}"
        expected_payload = self._expected_payload_keys(event)
        if expected_payload is not None and sorted(payload) != list(expected_payload):
            return f"payload keys for {event} must be {list(expected_payload)}"
        if record["prev_event_hash"] != previous_hash:
            return "prev_event_hash does not link the preceding record"
        body = {k: v for k, v in record.items() if k != "event_hash"}
        if record["event_hash"] != digest_json(body):
            return "event_hash does not match the record body"
        return None

    @staticmethod
    def _expected_payload_keys(event):
        return {
            "run_started": RUN_STARTED_PAYLOAD_KEYS,
            "observation_emitted": OBSERVATION_PAYLOAD_KEYS,
            "tool_committed": COMMITTED_PAYLOAD_KEYS,
            "tool_rejected": RESULT_PAYLOAD_KEYS,
            "retry": RESULT_PAYLOAD_KEYS,
            "fault_injected": FAULT_PAYLOAD_KEYS,
            "budget_exhausted": BUDGET_PAYLOAD_KEYS,
            "run_finished": RUN_FINISHED_PAYLOAD_KEYS,
            "item_picked": DOMAIN_PAYLOAD_KEYS,
            "order_delivered": DOMAIN_PAYLOAD_KEYS,
            "robot_moved": DOMAIN_PAYLOAD_KEYS,
        }.get(event)

    def _check_operation_log(self, operation_log, logical_run_id, budget, add):
        """Locked OperationLog structure (fail-closed, re-declared shapes)."""
        if sorted(operation_log) != sorted(LOG_KEYS):
            add(True, "artifact_mismatch", "operation_log", "operation log key drift")
            return False, []
        if operation_log["schema_version"] != 1:
            add(
                True,
                "artifact_mismatch",
                "operation_log.schema_version",
                "operation log schema_version must be 1",
            )
        operations = operation_log["operations"]
        if not isinstance(operations, list):
            add(
                True,
                "artifact_mismatch",
                "operation_log.operations",
                "operations must be a list",
            )
            return False, []
        usable = True
        previous_step = None
        for index, operation in enumerate(operations):
            location = f"operation_log[{index}]"
            if not isinstance(operation, Mapping) or sorted(operation) != sorted(
                OPERATION_KEYS
            ):
                add(True, "artifact_mismatch", location, "operation record key drift")
                usable = False
                continue
            step = operation["policy_step"]
            if (
                isinstance(step, bool)
                or not isinstance(step, int)
                or not 1 <= step <= budget
            ):
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.policy_step",
                    f"policy_step must be an int in 1..{budget}",
                )
                usable = False
            elif previous_step is not None and step < previous_step:
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.policy_step",
                    "operations must be in submission order (steps never decrease)",
                )
                usable = False
            else:
                previous_step = step
            key = operation["operation_key"]
            if key is not None and not validate_operation_key(key, logical_run_id):
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.operation_key",
                    "operation_key is not valid for this run",
                )
                usable = False
            tool = operation["tool"]
            if tool is not None and tool not in SUPPORTED_TOOLS:
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.tool",
                    "tool is not a supported tool",
                )
                usable = False
            if operation["arguments"] is not None and not isinstance(
                operation["arguments"], Mapping
            ):
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.arguments",
                    "arguments must be a mapping or null",
                )
                usable = False
            status = operation["status"]
            if status not in OPERATION_STATUSES:
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.status",
                    "status is outside the closed operation statuses",
                )
                usable = False
            reason = operation["reason"]
            if reason is not None and reason not in CLOSED_REASONS:
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.reason",
                    "reason is outside the closed taxonomy",
                )
                usable = False
            for name in ("state_before", "state_after"):
                value = operation[name]
                if value is not None and not is_digest(value):
                    add(
                        True,
                        "artifact_mismatch",
                        f"{location}.{name}",
                        "state digest must be a sha256 digest or null",
                    )
                    usable = False
            if (operation["state_after"] is not None) != (status == "committed"):
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.state_after",
                    "state_after is non-null exactly for committed operations",
                )
                usable = False
            if status == "committed" and (
                tool is None or operation["arguments"] is None
            ):
                add(
                    True,
                    "artifact_mismatch",
                    location,
                    "a committed operation must carry tool and arguments",
                )
                usable = False
            events = operation["events"]
            if not isinstance(events, list) or (
                (status == "committed") != bool(events)
            ):
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.events",
                    "events are non-empty exactly for committed operations",
                )
                usable = False
            for position, event in enumerate(events):
                event_location = f"{location}.events[{position}]"
                if not isinstance(event, Mapping) or sorted(event) != sorted(
                    EVENT_KEYS
                ):
                    add(True, "artifact_mismatch", event_location, "event key drift")
                    usable = False
                    continue
                if event["schema_version"] != 1:
                    add(
                        True,
                        "artifact_mismatch",
                        event_location,
                        "event schema_version must be 1",
                    )
                    usable = False
                if event["event_type"] not in (
                    "item_picked",
                    "order_delivered",
                    "robot_moved",
                ):
                    add(
                        True,
                        "artifact_mismatch",
                        event_location,
                        "event_type is outside the closed domain vocabulary",
                    )
                    usable = False
                if not isinstance(event["data"], Mapping):
                    add(
                        True,
                        "artifact_mismatch",
                        event_location,
                        "event data must be a mapping",
                    )
                    usable = False
                if event["operation_key"] != key:
                    add(
                        True,
                        "artifact_mismatch",
                        event_location,
                        "event belongs to a different operation key",
                    )
                    usable = False
            retry = operation["retry"]
            if not isinstance(retry, bool):
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.retry",
                    "retry must be a boolean",
                )
                usable = False
            elif retry and status != "idempotent_replay":
                add(
                    True,
                    "artifact_mismatch",
                    location,
                    "an internal retry records an idempotent replay result",
                )
                usable = False
            fault_ids = operation["fault_ids"]
            if not isinstance(fault_ids, list) or any(
                not isinstance(f, str) or not f for f in fault_ids
            ):
                add(
                    True,
                    "artifact_mismatch",
                    f"{location}.fault_ids",
                    "fault_ids must be a list of non-empty strings",
                )
                usable = False
        return usable, list(operations)

    def _check_fault_tape(self, artifacts, logical_run_id, add):
        """Resolved-tape structure plus two-stage spec agreement (decision 4)."""
        faults = []
        tape = artifacts.fault_tape
        spec = artifacts.config.get("fault_tape_spec")
        if tape is not None:
            if sorted(tape) != sorted(FAULT_TAPE_KEYS):
                add(True, "artifact_mismatch", "fault_tape", "fault tape key drift")
                return faults
            if tape["schema_version"] != 1:
                add(
                    True,
                    "artifact_mismatch",
                    "fault_tape.schema_version",
                    "fault tape schema_version must be 1",
                )
            entries = tape["faults"]
            if not isinstance(entries, list) or not entries:
                add(
                    True,
                    "artifact_mismatch",
                    "fault_tape.faults",
                    "fault tape must carry at least one resolved fault",
                )
                return faults
            if spec is None:
                add(
                    True,
                    "artifact_mismatch",
                    "fault_tape",
                    "resolved tape present but the config declares no "
                    "semantic fault_tape_spec",
                )
            for position, fault in enumerate(entries):
                location = f"fault_tape.faults[{position}]"
                if not isinstance(fault, Mapping) or sorted(fault) != sorted(
                    FAULT_KEYS
                ):
                    add(True, "artifact_mismatch", location, "resolved fault key drift")
                    continue
                if any(
                    not isinstance(fault[name], str) or not fault[name]
                    for name in FAULT_KEYS
                ):
                    add(
                        True,
                        "artifact_mismatch",
                        location,
                        "resolved fault fields must be non-empty strings",
                    )
                    continue
                if (
                    fault["fault_id"] != FAULT_ID
                    or fault["hook"] != FAULT_HOOK
                    or fault["kind"] != FAULT_KIND
                ):
                    add(
                        True,
                        "artifact_mismatch",
                        location,
                        "v0 admits exactly the response-loss demonstrator",
                    )
                    continue
                if not validate_operation_key(fault["operation_key"], logical_run_id):
                    add(
                        True,
                        "artifact_mismatch",
                        location,
                        "resolved operation key is not valid for this run",
                    )
                    continue
                if (
                    spec is not None
                    and isinstance(spec, Mapping)
                    and sorted(spec) == sorted(FAULT_SPEC_KEYS)
                ):
                    target = spec.get("target")
                    if isinstance(target, Mapping) and sorted(target) == sorted(
                        TARGET_KEYS
                    ):
                        expected_key = (
                            f"op/{logical_run_id}"
                            f"/step-{target['policy_step']}"
                            f"/intent-{target['intent']}"
                        )
                        if fault["operation_key"] != expected_key:
                            add(
                                True,
                                "artifact_mismatch",
                                location,
                                "resolved key disagrees with the two-stage "
                                "expansion of the semantic spec target",
                            )
                        if (
                            fault["fault_id"] != spec.get("fault_id")
                            or fault["hook"] != spec.get("hook")
                            or fault["kind"] != spec.get("kind")
                        ):
                            add(
                                True,
                                "artifact_mismatch",
                                location,
                                "resolved fault disagrees with the semantic spec",
                            )
                faults.append(fault)
        elif spec is not None:
            add(
                True,
                "artifact_mismatch",
                "config.fault_tape_spec",
                "semantic fault spec declared but no resolved tape artifact",
            )
        return faults

    # -- reconstruction walk ---------------------------------------------------

    def _walk(
        self, artifacts, operations, ops_ok, task_ok, grants, boundaries, faults, add
    ):
        """Independently re-apply committed operations and prove every commit."""
        delivered = {}
        committed_keys = {}
        committed_steps = set()
        seen_keys = {}
        commit_count = 0  # every committed operation, duplicates included
        duplicate_effect = False
        chain_ok = True
        max_nonretry_step = 0
        state = dict(artifacts.task["initial_state"]) if task_ok else None
        current = digest_json(state) if state is not None else None

        if ops_ok:
            fault_keys = {fault["operation_key"]: fault["fault_id"] for fault in faults}
            proven_fault_retries = set()
            for index, operation in enumerate(operations):
                location = f"operation_log[{index}]"
                key = operation["operation_key"]
                step = operation["policy_step"]
                status = operation["status"]
                tool = operation["tool"]
                retry = operation["retry"]
                args_digest = (
                    digest_json(operation["arguments"])
                    if operation["arguments"] is not None
                    else None
                )
                if not retry:
                    max_nonretry_step = max(max_nonretry_step, step)
                first = seen_keys.get(key) if key is not None else "absent"

                if status == "committed":
                    if grants is not None and tool not in grants:
                        add(
                            True,
                            "missing_grant",
                            location,
                            f"committed {tool} effect without a {tool} grant",
                        )
                    duplicate_commit = key is not None and key in committed_keys
                    if duplicate_commit:
                        add(
                            False,
                            "operation_key_conflict",
                            location,
                            "operation key committed more than once (first "
                            f"at index {committed_keys[key]}); ledger "
                            "idempotency broken",
                        )
                        duplicate_effect = True
                    elif key is not None and first is not None and first != "absent":
                        add(
                            True,
                            "artifact_mismatch",
                            location,
                            "a commit over an already-stored operation key "
                            "must have been replayed or conflicted",
                        )
                    if key is not None:
                        committed_keys.setdefault(key, index)
                        seen_keys.setdefault(key, (args_digest, None))
                    committed_steps.add(step)
                    commit_count += 1
                    boundary = boundaries.get(step)
                    if boundary is None:
                        add(
                            True,
                            "commit_boundary_unproven",
                            f"boundaries/step-{step}",
                            "no persistence-boundary proof for the committed "
                            f"operation {key}",
                        )
                    elif not _boundary_matches(boundary, operation):
                        add(
                            True,
                            "artifact_mismatch",
                            f"boundaries/step-{step}",
                            "boundary proof contradicts the operation record",
                        )
                    # A duplicate commit is itself the proven broken-ledger
                    # effect; the state chain cannot continue past it (the
                    # engine would reject what the ledger already accepted),
                    # so the deep reconstruction is skipped for that record.
                    if not duplicate_commit and chain_ok and state is not None:
                        if operation["state_before"] != current:
                            add(
                                True,
                                "artifact_mismatch",
                                f"{location}.state_before",
                                "records a state before that disagrees with "
                                "the independent reconstruction",
                            )
                            chain_ok = False
                        else:
                            new_state, engine_events, reason = independently_apply(
                                state, tool, operation["arguments"]
                            )
                            if reason is not None:
                                add(
                                    True,
                                    reason,
                                    location,
                                    "claimed committed but the independent "
                                    f"reconstruction rejects: {reason}",
                                )
                                chain_ok = False
                            else:
                                expected = [
                                    stitched_event(event, key)
                                    for event in engine_events
                                ]
                                if list(operation["events"]) != expected:
                                    add(
                                        True,
                                        "artifact_mismatch",
                                        f"{location}.events",
                                        "independent reconstruction emits "
                                        "different events (illegal transition "
                                        "or forged effect)",
                                    )
                                    chain_ok = False
                                elif operation["state_after"] != digest_json(new_state):
                                    add(
                                        True,
                                        "artifact_mismatch",
                                        f"{location}.state_after",
                                        "records a state after that disagrees "
                                        "with the independent reconstruction",
                                    )
                                    chain_ok = False
                                else:
                                    state = new_state
                                    current = digest_json(state)
                    for position, event in enumerate(operation["events"]):
                        if event["event_type"] == "order_delivered":
                            order_id = event["data"].get("order_id")
                            if order_id in delivered:
                                add(
                                    False,
                                    "order_already_completed",
                                    f"{location}.events[{position}]",
                                    f"order {order_id!r} delivered more than "
                                    "once (duplicate reward)",
                                )
                                duplicate_effect = True
                            else:
                                delivered[order_id] = index
                    if retry and key in fault_keys:
                        proven_fault_retries.add(key)

                elif status == "idempotent_replay":
                    if key is None or first is None or first == "absent":
                        add(
                            True,
                            "artifact_mismatch",
                            location,
                            "a replay is recorded for a key with no stored "
                            "first result",
                        )
                    elif first != (args_digest, operation["reason"]):
                        add(
                            True,
                            "artifact_mismatch",
                            location,
                            "idempotent replay disagrees with the stored "
                            "first result (payload or reason)",
                        )
                    if key is not None:
                        seen_keys.setdefault(key, (args_digest, operation["reason"]))

                else:  # rejected
                    if key is not None:
                        if first is None or first == "absent":
                            seen_keys.setdefault(
                                key, (args_digest, operation["reason"])
                            )
                        elif first[0] == args_digest:
                            add(
                                True,
                                "artifact_mismatch",
                                location,
                                "same key and payload rejected; the ledger "
                                "must replay the stored first result",
                            )
                        elif operation["reason"] != "operation_key_conflict":
                            add(
                                True,
                                "artifact_mismatch",
                                location,
                                "a different payload under a stored key must "
                                "reject as operation_key_conflict",
                            )
                    if (
                        operation["state_before"] is not None
                        and chain_ok
                        and current is not None
                        and operation["state_before"] != current
                    ):
                        add(
                            True,
                            "artifact_mismatch",
                            f"{location}.state_before",
                            "records a state before that disagrees with the "
                            "independent reconstruction",
                        )
                        chain_ok = False

            # fault protocol: committed target + the paired fault-evidence retry
            committed_key_set = set(committed_keys)
            for fault in faults:
                key = fault["operation_key"]
                if key not in committed_key_set:
                    add(
                        True,
                        "response_lost",
                        "fault_tape",
                        f"fault {key!r} targets an operation with no "
                        "committed record; whether it crossed the persistence "
                        "boundary is unprovable",
                    )
                    continue
                retries = [
                    operation
                    for operation in operations
                    if operation["retry"]
                    and operation["operation_key"] == key
                    and fault["fault_id"] in operation["fault_ids"]
                ]
                if len(retries) != 1:
                    add(
                        True,
                        "response_lost",
                        "fault_tape",
                        f"fault {key!r} has no matching retry operation "
                        "carrying the fault evidence",
                    )
            del proven_fault_retries, fault_keys

            for step in sorted(boundaries):
                if step not in committed_steps:
                    add(
                        True,
                        "artifact_mismatch",
                        f"boundaries/step-{step}",
                        "boundary proof for a step with no committed operation",
                    )

        derived = {
            "policy_steps": max_nonretry_step,
            "tool_attempts": len(operations) if ops_ok else 0,
            "committed_effects": commit_count if ops_ok else 0,
            "internal_retries": (
                sum(1 for op in operations if op["retry"]) if ops_ok else 0
            ),
            "contract_violations": (
                sum(
                    1
                    for op in operations
                    if (op["status"] == "rejected" and op["reason"] in PROTOCOL_REASONS)
                    or (op["status"] == "idempotent_replay" and not op["retry"])
                )
                if ops_ok
                else 0
            ),
        }
        return {
            "delivered": delivered,
            "duplicate_effect": duplicate_effect,
            "derived": derived,
            "max_nonretry_step": max_nonretry_step,
            "committed_operations": list(committed_keys.items()),
            "final_digest": digest_json(state) if state is not None else None,
        }

    def _normalize_boundaries(self, boundaries, add):
        normalized = {}
        if not boundaries:
            return normalized
        if not isinstance(boundaries, Mapping):
            add(
                True,
                "artifact_mismatch",
                "boundaries",
                "boundaries must be a mapping of steps to proof records",
            )
            return normalized
        for key, record in boundaries.items():
            if isinstance(key, bool) or not isinstance(key, int):
                if (
                    isinstance(key, str)
                    and key.startswith("step-")
                    and key[5:].isdigit()
                ):
                    try:
                        key = int(key[5:])
                    except ValueError as error:  # guarded by isdigit above
                        add(
                            True,
                            "artifact_mismatch",
                            "boundaries",
                            f"boundary key {key!r} is not a policy step: {error}",
                        )
                        continue
                else:
                    add(
                        True,
                        "artifact_mismatch",
                        "boundaries",
                        f"boundary key {key!r} is not a policy step",
                    )
                    continue
            normalized[key] = record
        return normalized

    # -- bindings --------------------------------------------------------------

    def _check_replay_input(self, artifacts, budget, add) -> None:
        replay_input = artifacts.replay_input
        if sorted(replay_input) != sorted(REPLAY_KEYS):
            add(True, "artifact_mismatch", "replay_input", "replay input key drift")
            return
        if replay_input["schema_version"] != 1:
            add(
                True,
                "artifact_mismatch",
                "replay_input.schema_version",
                "replay input schema_version must be 1",
            )
        identity = replay_input["task_identity"]
        if not isinstance(identity, Mapping) or sorted(identity) != sorted(
            TASK_IDENTITY_KEYS
        ):
            add(
                True,
                "artifact_mismatch",
                "replay_input.task_identity",
                "task identity key drift",
            )
            return
        if identity["task_version"] != TASK_VERSION:
            add(
                True,
                "artifact_mismatch",
                "replay_input.task_identity",
                f"task identity version must be {TASK_VERSION!r}",
            )
        if identity["seed"] != artifacts.config["seed"]:
            add(
                True,
                "artifact_mismatch",
                "replay_input.task_identity",
                "replay seed disagrees with the config",
            )
        if identity["config_digest"] != digest_json(artifacts.config):
            add(
                True,
                "artifact_mismatch",
                "replay_input.task_identity.config_digest",
                "config digest does not bind the declared config.json",
            )
        if replay_input["grants"] != artifacts.config["grants"]:
            add(
                True,
                "artifact_mismatch",
                "replay_input.grants",
                "replay grants disagree with the config",
            )
        if replay_input["budget"] != budget:
            add(
                True,
                "artifact_mismatch",
                "replay_input.budget",
                "replay budget disagrees with the config",
            )
        if replay_input["fault_tape"] != artifacts.fault_tape:
            add(
                True,
                "artifact_mismatch",
                "replay_input.fault_tape",
                "replay fault tape disagrees with the resolved tape artifact",
            )
        operations = artifacts.operation_log.get("operations")
        if operations is not None and replay_input["operations"] != operations:
            add(
                True,
                "artifact_mismatch",
                "replay_input.operations",
                "replay operations disagree with the operation log",
            )

    def _check_receipt(
        self, artifacts, trace_digest, budget, grants, derived, add
    ) -> None:
        receipt = artifacts.receipt
        if receipt is None:
            return
        if sorted(receipt) == sorted(RECEIPT_REQUIRED_KEYS) or all(
            name in receipt for name in RECEIPT_REQUIRED_KEYS
        ):
            missing = [name for name in RECEIPT_REQUIRED_KEYS if name not in receipt]
            if missing:
                add(
                    True,
                    "artifact_mismatch",
                    "receipt",
                    f"receipt is missing required keys: {sorted(missing)}",
                )
                return
        else:
            missing = [name for name in RECEIPT_REQUIRED_KEYS if name not in receipt]
            add(
                True,
                "artifact_mismatch",
                "receipt",
                f"receipt is missing required keys: {sorted(missing)}",
            )
            return
        if receipt["schema_version"] != 1:
            add(
                True,
                "artifact_mismatch",
                "receipt.schema_version",
                "receipt schema_version must be 1",
            )
        if receipt["seed"] != artifacts.config["seed"]:
            add(
                True,
                "artifact_mismatch",
                "receipt.seed",
                "receipt seed disagrees with the config",
            )
        if receipt["budget"] != budget:
            add(
                True,
                "artifact_mismatch",
                "receipt.budget",
                "receipt budget disagrees with the config",
            )
        if receipt["grants"] != artifacts.config["grants"]:
            add(
                True,
                "artifact_mismatch",
                "receipt.grants",
                "receipt grants disagree with the config",
            )
        if grants is not None and list(receipt["grants"]) != list(grants):
            add(
                True,
                "artifact_mismatch",
                "receipt.grants",
                "receipt grants disagree with the declared grant set",
            )
        bindings = (
            ("config_digest", digest_json(artifacts.config)),
            ("trace_digest", trace_digest),
            ("final_state_digest", digest_json(artifacts.final_state)),
            ("operation_log_digest", digest_json(artifacts.operation_log)),
            (
                "fault_tape_digest",
                None
                if artifacts.fault_tape is None
                else digest_json(artifacts.fault_tape),
            ),
        )
        for name, expected in bindings:
            if receipt[name] != expected:
                add(
                    True,
                    "artifact_mismatch",
                    f"receipt.{name}",
                    "receipt binding does not match the independently "
                    "recomputed digest",
                )
        counters = receipt["counters"]
        if not isinstance(counters, Mapping) or sorted(counters) != sorted(
            COUNTER_NAMES
        ):
            add(
                True,
                "artifact_mismatch",
                "receipt.counters",
                "receipt counters key drift",
            )
        else:
            try:
                Counters(
                    policy_steps=counters["policy_steps"],
                    tool_attempts=counters["tool_attempts"],
                    committed_effects=counters["committed_effects"],
                    internal_retries=counters["internal_retries"],
                    contract_violations=counters["contract_violations"],
                    terminal_checks=counters["terminal_checks"],
                )
            except (TypeError, ValueError) as error:
                add(
                    True,
                    "artifact_mismatch",
                    "receipt.counters",
                    f"receipt counters are not six non-negative ints: {error}",
                )
        if receipt["outcome"] not in OUTCOMES:
            add(
                True,
                "artifact_mismatch",
                "receipt.outcome",
                "receipt outcome is outside the closed enum",
            )
        score = receipt["verified_score"]
        if isinstance(score, bool) or not isinstance(score, int):
            add(
                True,
                "artifact_mismatch",
                "receipt.verified_score",
                "receipt verified_score must be an integer",
            )
        block = receipt.get("verification")
        if isinstance(block, Mapping):
            if block.get("status") != "valid":
                add(
                    True,
                    "artifact_mismatch",
                    "receipt.verification",
                    "a released receipt carries a valid verification block",
                )
            if block.get("verified_score") != score:
                add(
                    True,
                    "artifact_mismatch",
                    "receipt.verification",
                    "verification block score disagrees with the receipt",
                )

    @staticmethod
    def _check_receipt_claims(
        artifacts, classification, completed_count, total_count, add
    ) -> None:
        """A receipt's score/outcome claims never outrank the verifier.

        REQ-06: no receipt score is accepted unless independently recomputed.
        A released receipt carries the verifier's own values, so any
        disagreement between ``receipt.json`` and the independent result is
        itself tamper evidence (``score_mismatch`` / outcome mismatch).
        """
        receipt = artifacts.receipt
        if receipt is None:
            return
        score = receipt.get("verified_score")
        if total_count > 0:
            try:
                recomputed = compute_official_score(
                    completed_count, total_count
                ).score
            except (TypeError, ValueError):
                recomputed = None
            if (
                recomputed is not None
                and not isinstance(score, bool)
                and isinstance(score, int)
                and score != recomputed
            ):
                add(
                    True,
                    "score_mismatch",
                    "receipt.verified_score",
                    f"receipt claims verified_score {score} but the "
                    f"independent reconstruction scores {recomputed}",
                )
        claimed_outcome = receipt.get("outcome")
        if claimed_outcome in OUTCOMES and claimed_outcome != classification:
            add(
                True,
                "artifact_mismatch",
                "receipt.outcome",
                f"receipt claims outcome {claimed_outcome!r} but the "
                f"independent classification is {classification!r}",
            )

    # -- classification and cross-checks ----------------------------------------

    @staticmethod
    def _classify(walk, completed_count, total_count, budget):
        if walk["duplicate_effect"]:
            return "duplicate_effect"
        if total_count > 0 and completed_count >= total_count:
            return "completed"
        if walk["max_nonretry_step"] >= budget:
            return "safe_abort"
        return "unresolved_outcome"

    def _cross_check_trace(
        self,
        artifacts,
        records,
        trace_ok,
        ops_ok,
        operations,
        faults,
        walk,
        classification,
        task_ok,
        add,
    ) -> None:
        if not records:
            return
        run_started = records[0]
        run_finished = records[-1]
        if run_finished["event"] != "run_finished":
            return  # already reported structurally
        if run_started["event"] == "run_started" and task_ok:
            if run_started["state_after"] != digest_json(
                artifacts.task["initial_state"]
            ):
                add(
                    True,
                    "artifact_mismatch",
                    "trace[0]",
                    "run_started does not bind the task's initial state",
                )
        if run_started["event"] == "run_started":
            payload = run_started["payload"]
            if sorted(payload) == sorted(RUN_STARTED_PAYLOAD_KEYS):
                expected = {
                    "policy_id": artifacts.config["policy_id"],
                    "policy_digest": artifacts.config["policy_digest"],
                    "budget": artifacts.config["budget"],
                    "grants": artifacts.config["grants"],
                }
                if payload != expected:
                    add(
                        True,
                        "artifact_mismatch",
                        "trace[0]",
                        "run_started payload disagrees with the config",
                    )
        payload = run_finished["payload"]
        if sorted(payload) != sorted(RUN_FINISHED_PAYLOAD_KEYS):
            add(
                True,
                "artifact_mismatch",
                f"trace[{len(records) - 1}]",
                "run_finished payload key drift",
            )
            return
        claim = payload["counters"]
        counters_ok = isinstance(claim, Mapping) and sorted(claim) == sorted(
            COUNTER_NAMES
        )
        if not counters_ok:
            add(
                True,
                "artifact_mismatch",
                f"trace[{len(records) - 1}]",
                "run_finished counters key drift",
            )
            return
        if run_finished["state_after"] != digest_json(artifacts.final_state):
            add(
                True,
                "artifact_mismatch",
                f"trace[{len(records) - 1}]",
                "run_finished does not bind final_state.json",
            )
        if payload["outcome"] != classification:
            add(
                True,
                "artifact_mismatch",
                f"trace[{len(records) - 1}].outcome",
                f"the claimed outcome {payload['outcome']!r} disagrees with "
                f"the independent classification {classification!r}",
            )

        derived = walk["derived"]
        for name, expected_value in derived.items():
            if claim.get(name) != expected_value:
                add(
                    True,
                    "artifact_mismatch",
                    f"counters.{name}",
                    f"the run claims {claim.get(name)!r} but the operation "
                    f"log derives {expected_value!r}",
                )
        receipt = artifacts.receipt
        if receipt is not None and isinstance(receipt.get("counters"), Mapping):
            for name in COUNTER_NAMES:
                if receipt["counters"].get(name) != claim.get(name):
                    add(
                        True,
                        "artifact_mismatch",
                        "receipt.counters",
                        f"receipt counter {name} disagrees with the "
                        "run_finished evidence",
                    )
        if not (trace_ok and ops_ok):
            return

        # observation/decision coverage: one per reserved policy step
        observations = sum(
            1 for record in records if record["event"] == "observation_emitted"
        )
        decisions = sum(1 for record in records if record["event"] == "policy_decision")
        if observations != claim.get("policy_steps"):
            add(
                True,
                "artifact_mismatch",
                "trace",
                f"{observations} observations for a claimed "
                f"{claim.get('policy_steps')} reserved policy steps",
            )
        if decisions != claim.get("policy_steps"):
            add(
                True,
                "artifact_mismatch",
                "trace",
                f"{decisions} policy decisions for a claimed "
                f"{claim.get('policy_steps')} reserved policy steps",
            )

        # tool_committed records bind every committed operation (the recorder
        # writes the record at the persistence boundary, before any fault).
        committed = [
            (operation["policy_step"], operation["operation_key"], operation)
            for operation in operations
            if operation["status"] == "committed"
        ]
        committed_multiset = sorted((step, key) for step, key, _ in committed)
        trace_commits = {}
        for position, record in enumerate(records):
            if record["event"] == "tool_committed":
                trace_commits.setdefault(
                    (record["policy_step"], record["operation_key"]), []
                ).append((position, record))
        if sorted(trace_commits_elements(trace_commits)) != committed_multiset:
            add(
                True,
                "artifact_mismatch",
                "trace",
                "tool_committed records and committed operations disagree",
            )
        else:
            for step, key, operation in committed:
                for _, record in trace_commits.get((step, key), []):
                    if (
                        record["tool"] != operation["tool"]
                        or record["arguments"] != operation["arguments"]
                        or record["state_before"] != operation["state_before"]
                        or record["state_after"] != operation["state_after"]
                    ):
                        add(
                            True,
                            "artifact_mismatch",
                            "trace",
                            "a tool_committed record contradicts its operation record",
                        )

        # domain-event records bind every emitted domain event
        expected_events = sorted(
            (
                operation["policy_step"],
                operation["operation_key"],
                event["event_type"],
                canonical_json_bytes(event["data"]),
                operation["state_before"],
                operation["state_after"],
            )
            for operation in operations
            if operation["status"] == "committed"
            for event in operation["events"]
        )
        actual_events = sorted(
            (
                record["policy_step"],
                record["operation_key"],
                record["event"],
                canonical_json_bytes(record["payload"].get("data", {})),
                record["state_before"],
                record["state_after"],
            )
            for record in records
            if record["event"] in ("item_picked", "order_delivered", "robot_moved")
        )
        if actual_events != expected_events:
            add(
                True,
                "artifact_mismatch",
                "trace",
                "domain-event trace records and committed operation events disagree",
            )

        # fault and retry records bind the resolved tape and the retry ops
        fault_records = sorted(
            (
                record["policy_step"],
                record["operation_key"],
                record["payload"].get("fault_id"),
            )
            for record in records
            if record["event"] == "fault_injected"
        )
        expected_fault_records = sorted(
            (
                next(
                    (
                        operation["policy_step"]
                        for operation in operations
                        if operation["status"] == "committed"
                        and operation["operation_key"] == fault["operation_key"]
                    ),
                    None,
                ),
                fault["operation_key"],
                fault["fault_id"],
            )
            for fault in faults
        )
        if fault_records != expected_fault_records:
            add(
                True,
                "artifact_mismatch",
                "trace",
                "fault_injected records disagree with the resolved tape",
            )
        retry_ops = sorted(
            (operation["policy_step"], operation["operation_key"])
            for operation in operations
            if operation["retry"]
        )
        retry_records = sorted(
            (record["policy_step"], record["operation_key"])
            for record in records
            if record["event"] == "retry"
        )
        if retry_ops != retry_records:
            add(
                True,
                "artifact_mismatch",
                "trace",
                "retry trace records and retry operations disagree",
            )

        # the kill-switch record belongs exactly to safe_abort classifications
        has_budget_record = any(
            record["event"] == "budget_exhausted" for record in records
        )
        if classification == "safe_abort" and not has_budget_record:
            add(
                True,
                "artifact_mismatch",
                "trace",
                "a safe_abort classification requires the budget_exhausted record",
            )
        if classification != "safe_abort" and has_budget_record:
            add(
                True,
                "artifact_mismatch",
                "trace",
                "budget_exhausted recorded without a safe_abort classification",
            )

    # -- result composition ------------------------------------------------------

    def _finish(self, problems, completed_count, total_count, classification):
        """Compose the VerificationResult (fatal evidence never releases)."""
        violations = sort_violations(
            tuple(
                VerifierViolation(code=code, location=location, detail=detail)
                for _, code, location, detail in problems
            )
        )
        if any(fatal for fatal, _, _, _ in problems):
            # Invalid evidence carries no official score (REQ-06): the forged
            # score is not official and ambiguity is never upgraded.
            valid = False
            outcome = "unresolved_outcome"
            score = None
        else:
            valid = True
            outcome = classification
            score = (
                compute_official_score(completed_count, total_count).score
                if total_count > 0
                else None
            )
        return VerificationResult(
            valid=valid,
            outcome=outcome,
            verified_score=score,
            completed_count=completed_count,
            total_count=total_count,
            violations=violations,
            verifier_version=VERIFIER_VERSION,
        )


def trace_commits_elements(trace_commits):
    """Flatten the trace-commit index into a comparable (step, key) list."""
    return [
        (step, key)
        for (step, key), occurrences in trace_commits.items()
        for _ in occurrences
    ]


def _boundary_matches(boundary, operation) -> bool:
    """A boundary proof proves exactly this committed operation (REQ-05)."""
    if not isinstance(boundary, Mapping) or sorted(boundary) != sorted(BOUNDARY_KEYS):
        return False
    marker = boundary["commit_marker"]
    if not isinstance(marker, Mapping) or sorted(marker) != sorted(COMMIT_MARKER_KEYS):
        return False
    proven = marker["commit_proven"]
    if not isinstance(proven, bool) or not proven:
        return False
    if boundary["schema_version"] != 1:
        return False
    if boundary["policy_step"] != operation["policy_step"]:
        return False
    if boundary["operation_key"] != operation["operation_key"]:
        return False
    if boundary["tool"] != operation["tool"]:
        return False
    if boundary["arguments"] != operation["arguments"]:
        return False
    if boundary["events"] != operation["events"]:
        return False
    if boundary["operation_record"] != operation:
        return False
    if not is_digest(marker["snapshot_digest"]):
        return False
    if marker["snapshot_digest"] != operation["state_after"]:
        return False
    try:
        return digest_json(boundary["state_snapshot"]) == operation["state_after"]
    except (TypeError, ValueError):
        return False
