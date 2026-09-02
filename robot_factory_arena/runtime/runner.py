"""Runner-owned run lifecycle: RunConfig, logical identity, policy loop.

The Runner is the lifecycle/orchestration owner (design §2): it issues the
run-scoped grants, owns the budget/counters, performs the free terminal
checks, emits strictly serialized observations, reserves one policy step
BEFORE parse/validation/dispatch, and dispatches every valid call through the
ToolBay gateway. It never passes mutable state to a policy, never scores a
run, and never invokes the policy after the budget is exhausted — the kill
switch stops before any 101st observation/decision/dispatch and classifies
the known, non-ambiguous abort as ``safe_abort`` (REQ-02).

Documented interpretations (recorded per the SDD interpretation rule):

- ``contract_violations`` counts PROTOCOL violations (REQ-02): malformed,
  unsupported, invalid-key, invalid-argument candidates; missing/foreign
  grants; operation-key conflicts; and policy-initiated ledger replays (key
  reuse across distinct policy actions is a hygiene violation, REQ-03).
  Domain-legality rejections (e.g. ``robot_not_at_shelf``) are legal tool
  calls the world refuses: they consume the reserved step but are not
  protocol violations. Internal retries are exempt (REQ-02).
- Parse-level rejections (``malformed_call``/``unsupported_tool``/
  ``operation_key_invalid``/normally ``invalid_arguments``) never reach the
  ToolBay because no ToolCall is constructible for them; per design flow
  step 6 the runner ledger-stores such first results itself when the
  candidate carried a valid operation key for THIS run.
- The observation handed to the policy carries the budget view with
  ``used`` = steps consumed BEFORE this attempt; policies label their key
  with ``used + 1``. Reservation happens immediately after the policy
  returns, before any validation.
- A terminal check runs before the first policy attempt and after every
  result. Completion at the post-result check of step 100 takes precedence
  over ``budget_exhausted`` (REQ-02).
- Any escaping ``BudgetExhaustedError`` still maps to ``safe_abort``; the
  loop guard makes this defensive, not load-bearing.

Evidence is injectable: ``EvidenceCallbacks`` carries optional callables that
B4's trace writer/artifact store implement (B3 itself writes no evidence).
The emission order for one run is exactly::

    on_run_started
    -> (on_observation -> on_policy_decision
        -> [on_parse_rejected | on_tool_result
            | (on_commit_boundary -> [on_fault_injected -> on_retry])])*
    -> [on_budget_exhausted]
    -> on_run_finished

``on_commit_boundary`` fires inside the ToolBay commit path after the ledger
``store_first`` and before the response is returned (T-13); the fault/retry
callbacks only fire for a resolved response-loss tape.
"""
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from robot_factory_arena.contracts.outcomes import (
    SUPPORTED_TOOLS,
    Counters,
    LastResult,
)
from robot_factory_arena.contracts.tool_call import (
    ToolCall,
    normalize_tool_call,
    validate_operation_key,
)
from robot_factory_arena.domain.model import WorldState
from robot_factory_arena.domain.observation import Observation, serialize_observation
from robot_factory_arena.domain.task import TASK_VERSION, TaskDefinition
from robot_factory_arena.evidence.canonical import canonical_json_bytes, sha256_digest
from robot_factory_arena.faults.injector import FaultInjector
from robot_factory_arena.faults.model import FaultContext, FaultTape
from robot_factory_arena.faults.tape import FaultTapeSpec, resolve_fault_tape
from robot_factory_arena.runtime.budget import Budget, BudgetExhaustedError
from robot_factory_arena.runtime.grants import issue_grants
from robot_factory_arena.runtime.ledger import Ledger, LedgerEntry, arguments_digest
from robot_factory_arena.runtime.tool_bay import (
    COMMITTED,
    IDEMPOTENT_REPLAY_STATUS,
    REJECTED,
    RESPONSE_LOST,
    ResponseLostError,
    ToolBay,
    ToolResult,
)

SCHEMA_VERSION = 1
DEFAULT_BUDGET = 100

OUTCOME_COMPLETED = "completed"
OUTCOME_SAFE_ABORT = "safe_abort"
OUTCOME_UNRESOLVED = "unresolved_outcome"

# Runner-producible outcomes (REQ-02, REQ-05). ``duplicate_effect`` is a
# verifier-owned classification (REQ-06) and is never claimed by the runtime.
RUN_OUTCOMES = (OUTCOME_COMPLETED, OUTCOME_SAFE_ABORT, OUTCOME_UNRESOLVED)

# Locked RunConfig artifact keys (tasks.md exact auxiliary schema lock).
RUN_CONFIG_KEYS = (
    "budget", "fault_tape_spec", "grants", "policy_digest", "policy_id",
    "schema_version", "seed", "task_version",
)

# Closed set of protocol-violation reasons (see module docstring).
PROTOCOL_VIOLATION_REASONS = frozenset(
    {
        "grant_scope_mismatch",
        "invalid_arguments",
        "malformed_call",
        "missing_grant",
        "operation_key_conflict",
        "operation_key_invalid",
        "unsupported_tool",
    }
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SEED_TEXT_RE = re.compile(r"(0|[1-9][0-9]*)")
_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")


class CommitBoundaryUnprovenError(RuntimeError):
    """The evidence layer failed to record the commit boundary (REQ-05).

    Whether the commit crossed the persistence boundary is unprovable, so the
    run is classified ``unresolved_outcome`` and never upgraded to success.
    """

    def __init__(self, operation_key: str):
        self.operation_key = operation_key
        super().__init__(f"commit boundary unproven: {operation_key}")


def _is_valid_execution_id(value) -> bool:
    return isinstance(value, str) and bool(value)


@dataclass(frozen=True)
class RunConfig:
    """The locked RunConfig artifact (tasks.md exact auxiliary schema lock).

    Exactly {schema_version, task_version, seed, policy_id, policy_digest,
    budget, grants, fault_tape_spec}. ``fault_tape_spec`` is the SEMANTIC
    descriptor (no concrete operation key — locked decision 4) or None. The
    budget is locked at the v0 maximum 100 and the runner constructs the
    Budget primitive at exactly this value.
    """

    schema_version: int
    task_version: str
    seed: str
    policy_id: str
    policy_digest: str
    budget: int
    grants: tuple[str, ...]
    fault_tape_spec: FaultTapeSpec | None

    def __post_init__(self):
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValueError("schema_version must be 1")
        if self.task_version != TASK_VERSION:
            raise ValueError(f"task_version must be {TASK_VERSION!r}")
        if (
            not isinstance(self.seed, str)
            or _SEED_TEXT_RE.fullmatch(self.seed) is None
        ):
            raise ValueError("seed must be canonical decimal text")
        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise ValueError("policy_id must be a non-empty str")
        if (
            not isinstance(self.policy_digest, str)
            or _DIGEST_RE.fullmatch(self.policy_digest) is None
        ):
            raise ValueError("policy_digest must be a sha256:<64 hex> digest")
        if (
            isinstance(self.budget, bool)
            or not isinstance(self.budget, int)
            or self.budget != DEFAULT_BUDGET
        ):
            raise ValueError(f"budget is locked at {DEFAULT_BUDGET} in v0")
        if not isinstance(self.grants, tuple) or not self.grants:
            raise ValueError("grants must be a non-empty tuple of capabilities")
        for capability in self.grants:
            if capability not in SUPPORTED_TOOLS:
                raise ValueError(f"unsupported capability: {capability!r}")
        if any(a >= b for a, b in zip(self.grants, self.grants[1:])):
            raise ValueError(f"grants must be strictly ascending: {self.grants!r}")
        if self.fault_tape_spec is not None and not isinstance(
            self.fault_tape_spec, FaultTapeSpec
        ):
            raise TypeError("fault_tape_spec must be a FaultTapeSpec or None")

    def to_mapping(self):
        """Exact locked RunConfig mapping; null spec stays an explicit null."""
        return {
            "schema_version": self.schema_version,
            "task_version": self.task_version,
            "seed": self.seed,
            "policy_id": self.policy_id,
            "policy_digest": self.policy_digest,
            "budget": self.budget,
            "grants": list(self.grants),
            "fault_tape_spec": None
            if self.fault_tape_spec is None
            else self.fault_tape_spec.to_mapping(),
        }


def compute_logical_run_id(task: TaskDefinition, config: RunConfig) -> str:
    """Deterministic logical identity: ``lr_`` + 64 hex (REQ-03, REQ-07).

    Identity covers task/config identity, policy manifest identity, budget,
    sorted grants, and the SEMANTIC fault-tape descriptor (locked decision
    4), and excludes the execution id by construction. The task and config
    must agree on seed and task version.
    """
    if not isinstance(task, TaskDefinition):
        raise TypeError("task must be a TaskDefinition")
    if not isinstance(config, RunConfig):
        raise TypeError("config must be a RunConfig")
    if task.seed != config.seed or task.task_version != config.task_version:
        raise ValueError("task and config identity disagree")
    identity = {
        "budget": config.budget,
        "config_identity": dict(task.config_identity),
        "fault_tape": None
        if config.fault_tape_spec is None
        else config.fault_tape_spec.to_mapping(),
        "grants": list(config.grants),
        "policy_digest": config.policy_digest,
        "policy_id": config.policy_id,
        "seed": config.seed,
        "task_version": config.task_version,
    }
    return "lr_" + sha256_digest(canonical_json_bytes(identity)).removeprefix(
        "sha256:"
    )


@dataclass(frozen=True)
class EvidenceCallbacks:
    """Injectable evidence seam; B4's trace/artifact writers implement it.

    B3 writes no evidence. Every field is an optional callable; the shapes
    below are the B4 contract. ``policy_step`` is the 1-based reserved step
    of the attempt. ``on_tool_result`` fires for every gateway result
    (committed, rejected, policy-initiated replay); parse-level rejections
    are reported through ``on_parse_rejected`` (operation key is "" when the
    candidate carried no valid key for this run).
    """

    on_run_started: Callable[[str, RunConfig], None] | None = None
    on_observation: Callable[[int, Mapping], None] | None = None
    on_policy_decision: Callable[[int, object], None] | None = None
    on_parse_rejected: Callable[[int, str, str], None] | None = None
    on_tool_result: Callable[[int, ToolCall, ToolResult], None] | None = None
    on_commit_boundary: Callable[[int, ToolCall, ToolResult], None] | None = None
    on_fault_injected: Callable[[int, str, str], None] | None = None
    on_retry: Callable[[int, ToolCall, ToolResult], None] | None = None
    on_budget_exhausted: Callable[[int], None] | None = None
    on_run_finished: Callable[[str, Counters, WorldState], None] | None = None

    def __post_init__(self):
        for field_name in (
            "on_run_started",
            "on_observation",
            "on_policy_decision",
            "on_parse_rejected",
            "on_tool_result",
            "on_commit_boundary",
            "on_fault_injected",
            "on_retry",
            "on_budget_exhausted",
            "on_run_finished",
        ):
            value = getattr(self, field_name)
            if value is not None and not callable(value):
                raise TypeError(f"{field_name} must be callable or None")


@dataclass(frozen=True)
class RunResult:
    """B3 run result; B4 extends it with receipt/verification/artifact paths.

    ``outcome`` is restricted to the runner-producible subset of the
    outcome enum: ``completed``, ``safe_abort``, ``unresolved_outcome``.
    ``fault_tape`` is the RESOLVED tape (concrete operation keys) when the
    config declared a semantic fault-tape spec, else None.
    """

    logical_run_id: str
    execution_id: str
    outcome: str
    final_state: WorldState
    counters: Counters
    fault_tape: FaultTape | None = None

    def __post_init__(self):
        if (
            not isinstance(self.logical_run_id, str)
            or _LOGICAL_RUN_ID_RE.fullmatch(self.logical_run_id) is None
        ):
            raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
        if not _is_valid_execution_id(self.execution_id):
            raise ValueError("execution_id must be a non-empty str")
        if self.outcome not in RUN_OUTCOMES:
            raise ValueError(f"outcome must be one of {RUN_OUTCOMES}")
        if not isinstance(self.final_state, WorldState):
            raise TypeError("final_state must be a WorldState")
        if not isinstance(self.counters, Counters):
            raise TypeError("counters must be a Counters snapshot")
        if self.fault_tape is not None and not isinstance(self.fault_tape, FaultTape):
            raise TypeError("fault_tape must be a resolved FaultTape or None")


def _all_orders_complete(state: WorldState) -> bool:
    """The terminal predicate: every order id appears in completed_orders."""
    return set(state.completed_orders) >= {order.order_id for order in state.orders}


def _candidate_key(candidate, logical_run_id: str) -> str:
    """The candidate's operation key when valid for THIS run, else ""."""
    if isinstance(candidate, Mapping):
        key = candidate.get("operation_key")
    elif isinstance(candidate, ToolCall):
        key = candidate.operation_key
    else:
        return ""
    if isinstance(key, str) and validate_operation_key(key, logical_run_id):
        return key
    return ""


class Runner:
    """Lifecycle owner: observations, budget, terminal checks, dispatch."""

    def __init__(self, evidence: EvidenceCallbacks | None = None):
        if evidence is not None and not isinstance(evidence, EvidenceCallbacks):
            raise TypeError("evidence must be EvidenceCallbacks or None")
        self._evidence = evidence

    def run(self, task, policy, config, execution_id: str) -> RunResult:
        """Execute one bounded in-memory run to a terminal classification."""
        task = self._require_task(task)
        policy = self._require_policy(policy)
        config = self._require_config(config, task)
        if not _is_valid_execution_id(execution_id):
            raise ValueError("execution_id must be a non-empty str")

        logical_run_id = compute_logical_run_id(task, config)
        grants = issue_grants(logical_run_id, config.grants)
        budget = Budget(config.budget)
        ledger = Ledger()
        tool_bay = ToolBay(ledger)
        state = task.initial_state
        capabilities = tuple(config.grants)
        last_result = None
        outcome = None
        # The tape is fully resolved BEFORE any observation (REQ-05); the
        # injector matches the concrete operation key and is one-shot.
        fault_tape = (
            resolve_fault_tape(config.fault_tape_spec, logical_run_id)
            if config.fault_tape_spec is not None
            else None
        )
        injector = FaultInjector(fault_tape) if fault_tape is not None else None
        # Recovery proofs captured at the persistence boundary, keyed by
        # operation key: the ONLY source for post-fault state recovery.
        proofs = {}

        self._emit("on_run_started", logical_run_id, config)

        # Free initial terminal check (REQ-02): a complete task never
        # reaches the policy.
        budget.record_terminal_check()
        if _all_orders_complete(state):
            return self._finish(
                OUTCOME_COMPLETED,
                logical_run_id,
                execution_id,
                state,
                budget,
                fault_tape,
            )

        try:
            while not budget.exhausted:
                step = budget.view().used + 1
                observation = Observation(
                    world_state=state,
                    logical_run_id=logical_run_id,
                    grants=capabilities,
                    policy_steps=budget.view(),
                    last_result=last_result,
                )
                serialized = serialize_observation(observation)
                self._emit("on_observation", step, serialized)
                candidate = policy.decide(serialized)
                self._emit("on_policy_decision", step, candidate)
                # Reserve BEFORE parse/validation/dispatch (REQ-02): every
                # policy action attempt consumes exactly one step.
                budget.reserve_policy_attempt()

                parse = normalize_tool_call(candidate)
                if not parse.ok:
                    reason = parse.reason
                    key = _candidate_key(candidate, logical_run_id)
                    self._store_parse_rejection(
                        ledger, state, candidate, key, reason
                    )
                    self._emit("on_parse_rejected", step, reason, key)
                    if reason in PROTOCOL_VIOLATION_REASONS:
                        budget.record_violation()
                    last_result = LastResult(REJECTED, key, reason)
                else:
                    call = parse.tool_call
                    if call is None:  # fail-closed; ok implies a normalized call
                        raise TypeError("parse reported ok without a ToolCall")
                    budget.record_tool_attempt()
                    handler = self._commit_boundary_handler(
                        budget, injector, proofs, step
                    )
                    try:
                        result = tool_bay.submit(
                            state, call, grants, on_commit_boundary=handler
                        )
                    except CommitBoundaryUnprovenError:
                        # The boundary could not be durably recorded: whether
                        # the commit crossed it is unprovable. REQ-05 forbids
                        # success without proof; ambiguity is never success.
                        outcome = OUTCOME_UNRESOLVED
                        break
                    except ResponseLostError as lost:
                        if lost.operation_key not in proofs:
                            # Defensive: a loss without a proven boundary is
                            # unprovable and is never upgraded to success.
                            outcome = OUTCOME_UNRESOLVED
                            break
                        # Post-fault terminal check on the runner's last
                        # OBSERVED state: the committed response was lost, so
                        # this check cannot infer it (design flow step 9).
                        budget.record_terminal_check()
                        if _all_orders_complete(state):
                            outcome = OUTCOME_COMPLETED
                            break
                        # Internal retry of the EXACT original call and key,
                        # through the same gateway path: tool attempt + retry,
                        # never a policy step (REQ-02).
                        budget.record_tool_attempt()
                        retry_result = tool_bay.submit(
                            state, call, grants, retry=True
                        )
                        budget.record_retry()
                        self._emit("on_retry", step, call, retry_result)
                        # Recover the world from the PROVEN boundary record —
                        # never from the lost response, and never as a second
                        # world mutation (design flow steps 9-10).
                        state = proofs[lost.operation_key].state
                        last_result = LastResult(
                            retry_result.status,
                            lost.operation_key,
                            retry_result.reason,
                        )
                    else:
                        self._emit("on_tool_result", step, call, result)
                        if result.status == COMMITTED:
                            state = result.state
                            last_result = LastResult(
                                COMMITTED, result.operation_key, None
                            )
                        elif result.status == REJECTED:
                            if result.reason in PROTOCOL_VIOLATION_REASONS:
                                budget.record_violation()
                            last_result = LastResult(
                                REJECTED, result.operation_key, result.reason
                            )
                        else:
                            # Policy-initiated replay: the key was reused
                            # across distinct policy actions (hygiene
                            # violation); internal retries take the dedicated
                            # fault path above and are exempt (REQ-02).
                            budget.record_violation()
                            last_result = LastResult(
                                IDEMPOTENT_REPLAY_STATUS,
                                result.operation_key,
                                result.reason,
                            )

                # Free post-result terminal check (REQ-02). Completion here
                # takes precedence over budget exhaustion.
                budget.record_terminal_check()
                if _all_orders_complete(state):
                    outcome = OUTCOME_COMPLETED
                    break
        except BudgetExhaustedError:
            # Defensive: the loop guard is the kill switch; any escape from
            # the budget primitive still classifies as the known abort.
            outcome = OUTCOME_SAFE_ABORT

        if outcome is None:
            # The 100th policy step did not produce a terminal state: emit
            # budget_exhausted and stop before any 101st observation,
            # decision, or tool dispatch (REQ-02).
            outcome = OUTCOME_SAFE_ABORT
            self._emit("on_budget_exhausted", budget.view().used)

        return self._finish(
            outcome, logical_run_id, execution_id, state, budget, fault_tape
        )

    def _commit_boundary_handler(self, budget, injector, proofs, step):
        """The runner-owned ToolBay persistence-boundary callback (T-13).

        Fixed ordering: (1) the evidence layer persists the boundary first —
        durability before any bookkeeping, and a sink failure raises
        ``CommitBoundaryUnprovenError`` so the run can never claim success
        without proof; (2) the runner captures the recovery proof; (3) the
        effect is counted exactly once; (4) the injector consults
        ``after_commit_before_response`` and may consume the response. The
        committed result reaches the loop only when the hook stays quiet.
        """

        def handler(call, tool_result):
            try:
                self._emit("on_commit_boundary", step, call, tool_result)
            except Exception as exc:
                raise CommitBoundaryUnprovenError(call.operation_key) from exc
            proofs[call.operation_key] = tool_result
            budget.record_commit()
            if injector is not None:
                decision = injector.after_commit_before_response(
                    FaultContext(call.operation_key, call.tool)
                )
                if decision.fired:
                    self._emit(
                        "on_fault_injected",
                        step,
                        decision.fault.fault_id,
                        call.operation_key,
                    )
                    return RESPONSE_LOST
            return None

        return handler

    def _finish(
        self, outcome, logical_run_id, execution_id, state, budget, fault_tape
    ):
        result = RunResult(
            logical_run_id=logical_run_id,
            execution_id=execution_id,
            outcome=outcome,
            final_state=state,
            counters=budget.counters(),
            fault_tape=fault_tape,
        )
        self._emit("on_run_finished", outcome, result.counters, state)
        return result

    def _store_parse_rejection(self, ledger, state, candidate, key, reason):
        """Runner-side ledger duty (design flow step 6).

        Parse-level rejections never reach ToolBay, so the runner persists
        their first result itself when the candidate carried a valid
        operation key for THIS run and a storable tool/arguments pair.
        ``store_first`` keeps any earlier entry for the key.
        """
        if key == "":
            return
        tool = candidate.get("tool") if isinstance(candidate, Mapping) else None
        arguments = (
            candidate.get("arguments") if isinstance(candidate, Mapping) else None
        )
        if tool not in SUPPORTED_TOOLS or not isinstance(arguments, Mapping):
            return
        result = ToolResult(REJECTED, state, (), key, reason, False)
        ledger.store_first(
            LedgerEntry(
                key, tool, arguments_digest(arguments), REJECTED, result, None
            )
        )

    def _emit(self, name, *arguments):
        callback = (
            getattr(self._evidence, name) if self._evidence is not None else None
        )
        if callback is not None:
            callback(*arguments)

    @staticmethod
    def _require_task(task) -> TaskDefinition:
        if not isinstance(task, TaskDefinition):
            raise TypeError("task must be a TaskDefinition")
        return task

    @staticmethod
    def _require_policy(policy):
        policy_id = getattr(policy, "policy_id", None)
        digest = getattr(policy, "policy_digest", None)
        if not isinstance(policy_id, str) or not policy_id:
            raise TypeError("policy must expose a non-empty policy_id")
        if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
            raise TypeError("policy must expose a sha256 policy_digest")
        if not callable(getattr(policy, "decide", None)):
            raise TypeError("policy must expose a callable decide()")
        return policy

    @staticmethod
    def _require_config(config, task) -> RunConfig:
        if not isinstance(config, RunConfig):
            raise TypeError("config must be a RunConfig")
        if config.seed != task.seed or config.task_version != task.task_version:
            raise ValueError("config identity disagrees with the task")
        return config
