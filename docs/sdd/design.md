# SDD Design — robot-factory-arena v0 (working copy)

> **Provenance**: byte-faithful copy of Engram observation 6172 (`sdd/robot-factory-arena/design`, 2026-09-01 17:49). Materialized to disk because the Engram HTTP tool path is intermittently failing; Engram remains the authoritative store.

---

# SDD Design — Robot Factory & Arena deterministic evidence-chain v0

## 1. Design outcome and boundaries

Implement v0 as a stdlib-only Python 3.14.5 package centered on immutable warehouse domain state, a runner-owned policy boundary, an authorized ToolBay, append-only evidence, and an independent artifact-only verifier. The core has no LLM/provider/framework/Arena/frontier/discovery concepts. The only accepted v0 tools are `move`, `pick`, and `place`; the only executed fault is the resolved `response_lost_after_commit` tape.

The operational truth is a canonical `WorldState` snapshot plus an append-only operation/domain-event log. The verifier is authoritative for official score and does not import policy, runner, ToolBay, runtime scoring, or the transition implementation. Tamper evidence is not authentication; policy outputs are semantically untrusted, but hostile arbitrary Python is outside the v0 threat model.

### Requirements ownership

| Requirement | Owning modules | Verification evidence |
|---|---|---|
| REQ-01 seeded task and pure reference policies | `domain/task.py`, `domain/model.py`, `policies/*` | seed 17/29 golden task and policy tests |
| REQ-02 budget/accounting/termination | `runtime/budget.py`, `runtime/runner.py` | boundary, rejection, retry, terminal-check tests |
| REQ-03 schemas/canonical trace/receipt | `evidence/canonical.py`, `domain/model.py`, `contracts/*`, `evidence/*` | schema and golden bytes |
| REQ-04 transitions/grants/idempotency | `domain/transition.py`, `runtime/tool_bay.py`, `runtime/grants.py`, `runtime/ledger.py` | transition and ledger units |
| REQ-05 resolved response-loss tape | `faults/*`, `runtime/runner.py`, evidence persistence | fault tape fixture and replay |
| REQ-06 independent verification/score | `verification/*` | adversarial forged-artifact suite and denylist |
| REQ-07 determinism/rerun/replay | all deterministic layers, `evidence/replay.py` | byte-identical rerun and policy-free replay |
| REQ-08 unittest harness | `tests/` | one documented command |
| REQ-09 artifacts/report/CLI | `evidence/artifact_store.py`, `evidence/report.py`, `cli.py` | artifact inspection and offline CLI test |
| REQ-10 honesty boundary | `evidence/report.py`, `README` implementation notes | report limitation assertions |

## 2. Package/module design

All package value objects are `@dataclass(frozen=True)`. Collections crossing a module boundary are tuples in semantic order; mappings are normalized before serialization and never semantically iterated in insertion order. Runtime owners may keep private mutable cursors/dictionaries, but never expose them to policy or domain state.

```text
robot_factory_arena/
  __init__.py
  domain/
    __init__.py
    model.py
    task.py
    observation.py
    transition.py
  contracts/
    __init__.py
    tool_call.py
    policy.py
    outcomes.py
  runtime/
    __init__.py
    runner.py
    tool_bay.py
    grants.py
    budget.py
    ledger.py
  policies/
    __init__.py
    greedy.py
    bounded.py
  faults/
    __init__.py
    model.py
    tape.py
    injector.py
  evidence/
    __init__.py
    canonical.py
    trace.py
    receipt.py
    replay.py
    artifact_store.py
    report.py
  verification/
    __init__.py
    verifier.py
    invariants.py
    scoring.py
cli.py

tests/
  fixtures/ (canonical seed/config/tape and golden bytes)
  test_golden_trace.py
  test_transitions.py
  test_invariants.py
  test_adversarial_verifier.py
  test_runtime_boundaries.py
  test_faults.py
  test_rerun_replay.py
  test_verifier_dependencies.py
artifacts/runs/<logical_run_id>/<execution_id>/
```

### `domain/model.py` — immutable warehouse model

Owns only serializable warehouse values and their validation. It knows no runner, capabilities, faults, reports, providers, Arena, or verifier.

```text
RobotState(position: tuple[int,int], inventory_item_id: str | None,
           base_position: tuple[int,int])
ShelfState(shelf_id: str, position: tuple[int,int], item_id: str | None)
OrderState(order_id: str, item_id: str, quantity: int)
WorldState(schema_version: int, width: int, height: int,
           robot: RobotState, shelves: tuple[ShelfState, ...],
           orders: tuple[OrderState, ...], completed_orders: tuple[str, ...],
           step: int)
```

`WorldState` is exactly the REQ-03 top-level shape. Shelves are sorted by `shelf_id`, orders by `order_id`, completed IDs lexicographically. `step` advances only on a committed move/pick/place, never on rejection, terminal check, or retry. Constructors validate dimensions, coordinates, IDs, uniqueness, and tuple order; no hidden mutable state is permitted.

### `domain/task.py` — seeded task generation

```text
TaskDefinition(task_version: str, schema_version: int, seed: str,
               width: int, height: int, slot_positions: tuple[tuple[int,int],...],
               shelves: tuple[ShelfState,...], orders: tuple[OrderState,...],
               initial_state: WorldState, config_identity: Mapping)
generate_task(seed: int | str) -> TaskDefinition
serialize_task(task: TaskDefinition) -> Mapping
```

Normalize a non-negative decimal seed to its canonical decimal string. Use only REQ-01 formulas: fixed `P`, `q=s mod 10`, `n=5+(s mod 6)`, shelf positions/items, and order items. Serialization explicitly exposes seed, version, geometry, positions, items, orders, and config identity. Public fixture is 17; evaluation fixture is 29 and is plumbing-only.

### `domain/observation.py` — policy view

```text
Observation(world_state: WorldState, logical_run_id: str,
            grants: tuple[str,...], policy_steps: BudgetView,
            last_result: LastResult | None)
serialize_observation(...) -> Mapping
```

The serialized observation has exactly the REQ-03 keys. It contains capability names `("move","pick","place")` in sorted order, not execution ID, timestamps, host information, object references, or hidden state. The runner constructs it from immutable state and counters immediately before each policy call.

### `domain/transition.py` — sole domain transition authority

```text
Operation(tool: ToolName, arguments: NormalizedArguments)
DomainEvent(schema_version: int, event_type: str,
            operation_key: str, data: Mapping)
TransitionResult(state: WorldState, status: "committed" | "rejected",
                 events: tuple[DomainEvent,...], reason: str | None)
apply(state: WorldState, operation: Operation) -> TransitionResult
```

`apply` is pure and functional: it creates a new frozen state, never mutates its input, and emits only `robot_moved`, `item_picked`, and `order_delivered`. It enforces all move/pick/place preconditions as deterministic rejections. It does not know grants, ledgers, faults, or trace writing; ToolBay performs those outer concerns before calling it.

### `contracts/tool_call.py` — exact policy request

```text
ToolCall(schema_version: int, tool: Literal["move","pick","place"],
         arguments: Mapping, operation_key: str)
normalize_tool_call(candidate: object) -> ParseResult
validate_operation_key(key: str, logical_run_id: str) -> bool
```

A valid call has exactly the four fields and the operation-key regex `op/<logical_run_id>/step-<decimal>/intent-<1..64 ASCII [A-Za-z0-9._-]>`. Arguments are normalized into the exact tool shapes before dispatch. Malformed candidates are represented by an internal parse result, not by adding fields to `ToolCall`.

### `contracts/policy.py` — pure policy boundary

```text
class Policy(Protocol):
    policy_id: str
    policy_digest: str
    def decide(self, observation: Mapping) -> object: ...
```

The accepted output is one candidate intended to be one `ToolCall` per policy step. The runner treats the output as untrusted data. Built-in policies receive only the serialized observation and return no side effects. `policy_digest` is a digest of a stable canonical policy manifest (`policy_id`, implementation version, contract version), not a path or execution metadata.

### `contracts/outcomes.py` — bounded enums/results

```text
Outcome = Literal["completed", "duplicate_effect", "safe_abort", "unresolved_outcome"]
ResultStatus = Literal["committed", "rejected", "idempotent_replay"]
LastResult(status, operation_key, reason)
Counters(policy_steps, tool_attempts, committed_effects,
         internal_retries, contract_violations, terminal_checks)
VerificationResult(valid: bool, outcome: Outcome, verified_score: int | None,
                   completed_count: int, total_count: int,
                   violations: tuple[str,...], verifier_version: str)
```

Runtime result statuses and final receipt outcomes remain distinct. An invalid/tampered artifact is not released as a valid receipt; an ambiguity is represented as `unresolved_outcome`.

### `runtime/grants.py` — run-scoped capability issuance

```text
Grant(grant_id: str, capability: str, scope: Literal["run"])
GrantSet(grants: tuple[Grant,...])
issue_grants(logical_run_id: str, capabilities: tuple[str,...]) -> GrantSet
authorize(call: ToolCall, grant_set: GrantSet, logical_run_id: str) -> AuthorizationResult
```

Grant IDs are deterministic from logical run ID and capability; the serialized observation and receipt expose the required sorted capability list. Authorization precedes domain transition and rejects unsupported tools or scope/run mismatches.

### `runtime/budget.py` — runner-owned hard kill switch

```text
Budget(maximum: int = 100)
BudgetView(used: int, remaining: int, maximum: int)
Budget.require_available() -> None
Budget.reserve_policy_attempt() -> BudgetView
Budget.record_tool_attempt()/record_commit()/record_retry()/record_violation()/record_terminal_check()
```

The runner preflights availability before observation, invokes policy once, and immediately reserves one policy step before parse, validation, or dispatch. Malformed, invalid, unauthorized, and key-conflict outputs consume the reserved step. Retries consume tool attempts and internal retries only.

### `runtime/ledger.py` — idempotency authority

```text
LedgerEntry(operation_key: str, tool: str, arguments_digest: str,
           status: ResultStatus, result: ToolResult, committed_snapshot_digest: str | None)
Ledger.lookup(key: str, tool: str, arguments: Mapping) -> LedgerDecision
Ledger.store_first(entry: LedgerEntry) -> None
```

The ledger is keyed only by `operation_key`, and stores the first committed or rejected normalized result. Same key/same payload returns the stored result as `idempotent_replay` without invoking `apply`; same key/different normalized payload returns deterministic `operation_key_conflict` rejection with no effect. Runner records reuse across distinct policy actions as a hygiene violation; an internal retry is explicitly exempt from that hygiene count.

### `runtime/tool_bay.py` — only runtime gateway

```text
ToolResult(status: ResultStatus, state: WorldState,
           events: tuple[DomainEvent,...], operation_key: str,
           reason: str | None, committed: bool)
ToolBay.submit(state: WorldState, call: ToolCall,
               grant_set: GrantSet, retry: bool = False) -> ToolResult
```

ToolBay validates schema, grants, normalized arguments, and ledger before calling `apply`. It does not expose state, ledger, or transition authority to policy. It coordinates persistence-boundary callbacks supplied by the runner/evidence layer, but the domain remains unaware of them.

### `runtime/runner.py` — lifecycle/orchestration owner

```text
RunConfig(task_version, seed, policy_id, policy_digest, budget,
          grants, fault_tape_spec)
RunResult(logical_run_id: str, execution_id: str, receipt: Mapping,
          verification: VerificationResult, artifact_directory: Path)
Runner.run(task: TaskDefinition, policy: Policy, config: RunConfig,
           execution_id: str) -> RunResult
Runner.rerun(...) -> RunResult
```

Runner owns lifecycle, observation, policy invocation, budget reservation, terminal checks, ToolBay dispatch, retry loop, fault consumption, counters, and evidence sequencing. It only ever passes serialized observations to policy. It never delegates scoring to policy/runtime.

### `policies/greedy.py` and `policies/bounded.py` — reference pure policies

```text
GreedyPolicy.decide(observation: Mapping) -> ToolCall
BoundedPolicy.decide(observation: Mapping) -> ToolCall
```

Both reconstruct decisions solely from serialized observation. Greedy selects lexicographically first pending order; bounded minimizes `Manhattan(robot,shelf)+Manhattan(shelf,base)+2`, ties by order ID. Both route with move tie order north/east/south/west and perform the required pick/place behavior. No filesystem, randomness, mutable runtime, verifier, seed label, or execution ID is read.

### `faults/model.py`, `faults/tape.py`, `faults/injector.py` — declarative semantic faults

```text
Fault(fault_id: str, operation_key: str, hook: FaultHook,
      kind: FaultKind)
FaultTape(schema_version: int, faults: tuple[Fault,...], identity: str)
resolve_fault_tape(spec: FaultTapeSpec, logical_run_id: str) -> FaultTape
FaultInjector(tape: FaultTape)
FaultInjector.before_commit(context) -> FaultDecision
FaultInjector.after_commit_before_response(context) -> FaultDecision
```

The v0 resolver accepts exactly one demonstrator: `response_lost_after_commit`, represented as `hook="after_commit_before_response"`, `kind="response_lost"`, `fault_id`, and the concrete operation key. It persists the fully resolved tape before execution; no execution RNG, wall-clock scheduling, or state mutation exists. Matching is by semantic hook plus concrete operation key and is consumed once. The injector returns a response fault only after the persistence-boundary marker; it never edits WorldState or grants.

To avoid an identity cycle, the run config's fault-tape identity is the canonical semantic descriptor (fault kind/hook/target intent and version) before the concrete key is expanded. Derive `logical_run_id` from that descriptor, then resolve the operation key from the ID and configured target step, and digest/persist the resolved tape. This keeps logical ID deterministic and excludes execution ID. A fixture must configure a target step that deterministically reaches a delivery `place`; no policy is called to resolve the tape.

### `evidence/canonical.py` — one shared canonicalization authority

```text
canonical_json(value: JSONValue) -> str
canonical_json_bytes(value: JSONValue) -> bytes
sha256_digest(data: bytes) -> str  # sha256:<64 lowercase hex>
digest_json(value: JSONValue) -> str
canonical_jsonl_line(record: Mapping) -> bytes  # record bytes + b"\n"
```

Every package, including the verifier, uses this module for schema serialization and SHA-256. Use sorted object keys, compact separators, finite numbers only, UTF-8, and explicit nulls. Semantic arrays retain specified order. JSON artifact files are exact canonical JSON bytes; trace is UTF-8 JSONL with one LF after every record. This utility is the only intentionally shared verifier dependency; semantic transition and score code are not shared.

### `evidence/trace.py` — append-only hash chain

```text
TraceWriter(path: Path, logical_run_id: str)
TraceWriter.append(event: TraceEvent) -> TraceRecord
TraceWriter.digest() -> str
```

Every record has exactly the required top-level fields: `schema_version`, `seq`, `logical_run_id`, `policy_step`, `event`, `operation_key`, `tool`, `arguments`, `state_before`, `state_after`, `payload`, `prev_event_hash`, `event_hash`. Nullable fields are explicit `null`; arguments are exact normalized received arguments. `event_hash` is SHA-256 of canonical record without `event_hash`, including `prev_event_hash`; trace digest hashes all LF-terminated record bytes. No execution ID/path/timestamp enters the trace.

### `evidence/receipt.py` — verified binding, not runtime score

```text
ReceiptCandidate.from_run(...) -> ReceiptCandidate
ReceiptWriter.write_final(candidate, verification: VerificationResult) -> Mapping
```

After terminal state/evidence collection, create an in-memory candidate containing logical/execution identity, task/config/policy/budget/grants/fault identity and all artifact digests. The verifier consumes the candidate plus declared artifacts. Only after verification does `ReceiptWriter` persist `receipt.json`, filling verifier version, verified score, counters, and one supported outcome. A runtime claim is never accepted as `verified_score`; invalid/tampered artifacts receive no valid released receipt.

### `evidence/replay.py` — operation-only offline replay

```text
ReplayInput(schema_version: int, logical_run_id: str, task_identity: Mapping,
            grants: tuple[str,...], budget: int, fault_tape: Mapping | None,
            operations: tuple[ReplayOperation,...])
ReplayResult(final_state_digest: str, verified_score: int,
             outcome: Outcome, matches_receipt: bool)
replay(artifacts: ReplayArtifacts) -> ReplayResult
```

Replay input records normalized committed/rejected submissions, policy steps, operation keys, retry/idempotent results, and fault evidence. Replay regenerates the task from the declared seed and consumes the recorded operation sequence without importing or invoking any policy/provider. It verifies final-state digest, score, and outcome against receipt and writes `replay-result.json`.

### `evidence/artifact_store.py` — physical run boundary

```text
ArtifactStore(root: Path)
ArtifactStore.begin(logical_run_id, execution_id) -> RunArtifactWriter
RunArtifactWriter.write_json(name, value)
RunArtifactWriter.append_trace_bytes(line)
RunArtifactWriter.persist_commit_boundary(record) -> None
RunArtifactWriter.finalize() -> Path
```

Each run uses `artifacts/runs/<logical_run_id>/<execution_id>/`. At start write canonical `config.json`, `task.json`, and resolved `fault_tape.json` or canonical `null`. During execution append trace records and update operation/replay material. At the simulated persistence boundary, persist ledger entry, committed state snapshot, operation record, domain events, and a commit marker before invoking `after_commit_before_response`; use temp-file/flush/fsync/replace for individual files, but make no distributed exactly-once claim. End with `operation-log.json`, `final_state.json`, `replay-input.json`, verified `receipt.json`, `replay-result.json`, and `report.html`. The deterministic bytes/digests are computed from values, never directory paths or execution IDs. Optional internal commit snapshots/markers are path-addressed only as storage and are excluded from semantic digests.

### `evidence/report.py` — static, dependency-free report

```text
render_report(receipt: Mapping, verification: Mapping,
              artifact_digests: Mapping) -> str
```

Generate offline HTML with stdlib string templating and escaped values; no CSS/JS/network dependency is required. Render four separate labeled sections: task quality, efficiency, robustness, and contract hygiene. Show raw counters, completed/total, actions per delivery, budget/outcome, all digests, verifier status/version, fault kind/result/outcome, violations, audit completeness, and the required limitations. No composite normalization or authoritative combined score is emitted.

### `verification/verifier.py`, `invariants.py`, `scoring.py` — independent release gate

```text
VerificationArtifacts(task: Mapping, config: Mapping, grants: Mapping,
                      trace_records: tuple[Mapping,...], final_state: Mapping,
                      operation_log: Mapping, fault_tape: Mapping | None,
                      replay_input: Mapping, receipt: Mapping | None)
Verifier.verify(artifacts: VerificationArtifacts) -> VerificationResult
```

The verifier consumes parsed declared artifacts only, with no runtime objects or policy. It rebuilds a minimal warehouse model from seed and independently checks operation legality, grants, unique order completion, budget/counters, idempotency, state/trace consistency, event-chain integrity, artifact bindings, seed/config identity, and the fault persistence proof. `invariants.py` owns independent structural checks. `scoring.py` computes only `floor(100 * unique_valid_completed_orders / total_orders)` and retains numerator/denominator. It may share only the low-risk canonical JSON/hash helper; it must not import `runtime`, `policies`, `domain.transition`, or runtime scoring.

## 3. End-to-end data flow

### Normal run

1. CLI/runner normalizes seed/config, creates the task, computes canonical task/config/policy manifest digests, and computes `logical_run_id = "lr_" + hex(SHA256(canonical identity))`. Identity includes task/config/policy/budget/sorted grants/fault-tape semantic identity and excludes execution ID.
2. Generate `execution_id` for the physical run only. Create the artifact directory. Resolve and persist the fault tape before any observation. Write `config.json`, `task.json`, and `fault_tape.json`/`null`.
3. Initialize immutable state, empty ledger, counters, trace writer, and append `run_started`.
4. Perform a free initial terminal check and trace it. If all orders are already complete, finish without a policy attempt.
5. If non-terminal, preflight budget availability, emit a complete serialized observation, call `Policy.decide(observation)`, and immediately reserve one `policy_steps` unit before parsing/validation/dispatch. Record observation and decision in trace; no execution ID appears in either.
6. Parse/normalize the candidate into a `ToolCall`. Malformed, unsupported, invalid, unauthorized, or key-conflict requests are rejected deterministically, consume the reserved policy step, increment contract violations where applicable, enter the ledger when a valid key exists, and are traced. No domain effect occurs.
7. For a valid authorized call, increment `tool_attempts`, query ledger, and either return the stored result (`idempotent_replay`) or call `apply(state, operation)`. For a commit, compute state-before/state-after digests and domain-event payloads.
8. Persist the operation result. For a committed result, the boundary writer durably records ledger entry, new snapshot, operation record, domain event, and commit marker; then update the runner's immutable state and `committed_effects` exactly once. For rejection, persist its first result and no state effect. Append `tool_committed`/`tool_rejected` and domain events to the trace.
9. Run a terminal check after every rejected, committed, retry, and fault result. A response-loss fault's post-fault check uses the runner's last observed state, so it cannot silently infer the hidden response; the runner then retries the exact original call/key. The persisted boundary proof is available to verifier/recovery, not a second world mutation.
10. For `after_commit_before_response`, append `fault_injected`, return an unavailable response to runner, append the required `retry`, and submit the same call/key internally. Ledger returns `idempotent_replay`; increment `tool_attempts` and `internal_retries`, not `policy_steps` or `committed_effects`. Final terminal check sees completion and takes precedence over budget exhaustion.
11. If step 100 is non-terminal after its post-result check, append `budget_exhausted`, stop before observation/decision/tool dispatch for step 101, and classify the known non-ambiguous abort as `safe_abort`. An unresolved fault remains `unresolved_outcome` and is never upgraded.
12. Write final snapshot and operation log, compute their canonical digests and the trace digest, and create the in-memory receipt candidate. Verify candidate plus artifacts. Only the verifier fills/releases `verified_score` in canonical `receipt.json`.
13. Build `replay-input.json`, run operation-only offline replay, write `replay-result.json`, and render static `report.html`. Append/record `verification_finished` and `run_finished` with deterministic payloads before final trace digest/receipt binding is finalized; implementation must define this ordering so trace digest is computed only after all trace records are present.

The final trace ordering must be fixed in the implementation fixture; receipt/verification events that bind the trace cannot be appended after the receipt's trace digest is computed. The simplest ordering is: all run events → `run_finished` → close trace/digest → verify → write receipt → replay/report; `verification_finished` is then represented in a separate verification result or included before close. `NEEDS-TASKS`: choose and freeze whether `verification_finished` is in the run trace before receipt verification or is emitted into a post-verification auxiliary trace, because REQ-03 permits the event but receipt binding requires a closed trace.

### Rerun path

`rerun` is a separate CLI/runtime operation that regenerates task/config/policy and executes a fresh physical `execution_id` under the same logical identity. It invokes the policy again, unlike replay. Task bytes, serialized observations/decisions, trace JSONL, operation log, final state, deterministic digests, and verifier score must match byte-for-byte; physical receipt shell/path and execution ID may differ. Any differing deterministic bytes fail the rerun test.

### Offline replay path

`replay` reads only the selected run directory's declared artifacts, never imports/calls a policy or provider, and consumes recorded normalized operations/rejections/retries. It regenerates the task from recorded identity, applies only recorded operation effects, checks bindings and the receipt, and writes/reports whether final-state digest, verified score, and outcome match exactly. It is not rerun and never seeks a fresh decision.

## 4. Key technical decisions

### Canonical JSON and SHA-256

One helper module, `evidence/canonical.py`, is the mandatory serialization/digest authority for domain, runtime, evidence, and verifier. It sorts object keys lexicographically, uses compact separators, UTF-8, finite-number checks, explicit nulls, no default injection, and prescribed array order. JSON files are canonical bytes; JSONL adds exactly one LF per record. Every state/artifact digest is over the exact bytes. Hash-chain records omit only `event_hash` while retaining `prev_event_hash`. This rejects ad hoc `repr`, `str(dict)`, per-module JSON options, and path/execution metadata.

### Frozen data and functional transitions

Frozen dataclasses and tuple collections make state snapshots immutable and diffable. `apply` returns a new state and event tuple. Mutable shared state and event-sourcing-only alternatives are rejected: shared mutation breaks isolation; pure event sourcing adds reducer/version/fault complexity and risks snapshot/log divergence. Snapshot plus operation log retains current state and audit history.

### Ledger idempotency

Use `operation_key -> LedgerEntry` with normalized tool/arguments digest, first result status, result payload, and committed snapshot digest. Same payload replays stored result; conflict rejects. Ledger replay is auditable and not an exactly-once claim. The runner, not ledger alone, identifies semantic key reuse across distinct policy actions for hygiene.

### Fault hooks and runner consumption

The resolved tape is an ordered immutable tuple. The injector matches concrete operation key and semantic hook at `before_commit` or `after_commit_before_response`; v0 only enables the latter response-loss kind. It has no RNG and cannot mutate state. Runner invokes hooks around the persistence boundary; domain receives neither tape nor injector. A response loss is a transport/observation fault after commit, so retry tests ledger idempotency rather than fabricating a duplicate reward.

### Verifier independence

`verification/` accepts only `VerificationArtifacts` composed of artifact data. It reconstructs its own minimal model and score. An AST/import denylist test must fail if verifier code imports `runtime`, `policies`, `domain.transition`, or runtime scoring; tests must verify forged artifacts without constructing Runner/ToolBay/policy. Canonical serialization/hash is the only shared implementation permitted, because artifact byte identity must be interpreted identically.

### Artifact store and path independence

Run paths are physical organization only. Canonical task/config/state/operation/fault/trace/replay contents never include path or execution ID. Write individual files through temp + flush/fsync + atomic replace; use a deterministic commit marker to prove the simulated persistence boundary. Do not describe this as a transaction or exactly-once system.

### Operation key and logical ID

`logical_run_id` is `lr_` plus 64 lowercase hex characters over a canonical identity object containing `task_version`, canonical seed/config identity, policy manifest identity, budget 100, sorted grant capabilities, and semantic fault-tape identity; execution ID is excluded. A ToolCall key is `op/<logical_run_id>/step-<decimal>/intent-<token>`. `step` is the policy step and `intent` is a deterministic policy intent label; retries reuse it. Concrete fault target keys are expanded after logical ID calculation from a predeclared target step/intent, avoiding a circular digest.

### Static reporting

Use stdlib-only escaped string templates. Four signal sections remain separate: task quality (verified integer score plus raw completion ratio), efficiency (policy steps and actions per delivery), robustness (fault kind/response result/retry/outcome), and contract hygiene (violations/audit completeness). Include all counters/digests/verifier status/limitations. No composite score, float, JavaScript, CDN, or dependency is authoritative.

## 5. Extension seams (design only; not v0 implementation)

### Phase 4 LLM seam

`LLMPolicy` implements the existing `Policy` protocol and is constructed outside the core. It converts the serialized observation to a provider request, calls a `ProviderAdapter.invoke(ProviderRequest) -> ProviderResult`, then normalizes exactly one common `ToolCall`. OpenAI/GLM adapters and provider-specific evidence attach beside `policies/` (for example a later `providers/` package), not in domain/runtime. Pinned provider/model/config is included in run identity and receipt; no silent fallback or parallel calls. Replay starts from recorded normalized ToolCalls and never calls the provider. Langfuse is an observer-only side projection; LangGraph coordinates attempts/conditions outside Runner, never robot moves.

### Phase 3 Arena seam

A later `arena/` package owns `DiscoveryStore`, immutable discovery snapshots/digests, frontier, conditions, and attempt scheduling. R1/R2/R3 are searches across bounded runs, not WorldState modes. Arena creates/fixes a per-run StrategyContext and passes it to a Policy wrapper; core remains `Policy.decide(observation) -> ToolCall`. Discovery publication occurs only after independent verification, and frontier updates only after verified score; v0 does not implement or name these runtime concepts.

## 6. Testing strategy

The single supported command is `python3 -m unittest discover -s tests -v` under Python 3.14.5. No pytest, network, or external dependency is permitted.

1. `tests/test_golden_trace.py`: fixture seeds 17 and 29; canonical task bytes; greedy/bounded observations and decisions; complete artifact schemas; golden trace JSONL and digest; same-run rerun byte equality.
2. `tests/test_transitions.py`: pure `apply` units for legal move/pick/place, every rejection reason, shelf/order/inventory updates, event shapes, step increments, and no input mutation. `tests/fixtures/` holds hand-checked states including place of `order-03`.
3. `tests/test_invariants.py`: generated source uniqueness, in-grid route satisfiability, sorted tuples, schema versions, counter accounting, terminal-check placement, policy-step 100 boundary, no 101st observation, and no effect on rejection/retry.
4. `tests/test_adversarial_verifier.py`: forged self-reported success, duplicate reward/key reuse, teleport/illegal transition, trace/final mismatch, budget bypass, unauthorized effect, seed/config substitution, idempotency failure, artifact substitution, and partial-credit gaming. Verify without Runner/ToolBay/policy instantiation.
5. `tests/test_runtime_boundaries.py`: budget at 99 with malformed/unauthorized output; terminal completion on step 100; exact counters for initial/retry/rejected submissions and free terminal checks.
6. `tests/test_faults.py`: resolved tape before run, exact hook/kind shape, persistence proof, response-loss trace, one retry, one committed effect, final `completed`; malformed/incomplete boundary proof is rejected as unresolved rather than success.
7. `tests/test_rerun_replay.py`: distinct execution IDs with same logical ID, deterministic artifact bytes/digests, replay with a policy that would raise if called, and exact final-state/score/outcome agreement.
8. `tests/test_verifier_dependencies.py`: AST/import denylist for runtime/policy/transition/scoring imports and a forged-artifact verifier test that never constructs runtime components.

Use public seed 17 for hand-checkable golden evidence and evaluation seed 29 only as plumbing evidence; never tune policy behavior to the evaluation label.

## 7. Implementation notes and explicit open items

Prescribe stable lowercase rejection reasons for trace/report/verifier interoperability: `malformed_call`, `unsupported_tool`, `operation_key_invalid`, `missing_grant`, `grant_scope_mismatch`, `operation_key_conflict`, `invalid_arguments`, `destination_out_of_grid`, `robot_not_at_shelf`, `inventory_not_empty`, `shelf_item_mismatch`, `item_not_pending`, `not_at_base`, `inventory_empty`, `order_item_mismatch`, `order_already_completed`, `budget_exhausted`, `response_lost`, `commit_boundary_unproven`, `artifact_mismatch`, `trace_chain_invalid`, and `score_mismatch`. Keep the taxonomy closed for v0; unknown internal failures are invalid evidence, not new public outcomes.

Prescribe report skeleton: title/honesty banner; run identity; four signal cards; raw counters; completion and actions/delivery; artifact digest table; verifier status/version/violations; fault/retry details; limitations. Escape all values and state that tamper evidence is not authentication, no exactly-once/hostile-Python sandbox guarantee exists, evaluation seed is plumbing-only, and no scientific REFUGIO conclusion is claimed.

`NEEDS-TASKS` — freeze the exact fault fixture target step/intent for each reference policy, since REQ-05 names the fixture delivery key but does not provide its decimal step.

`NEEDS-TASKS` — freeze the exact final trace event ordering around `verification_finished`, trace closure, receipt verification, replay, and report. The trace digest must be computed only after its final record; do not append after receipt binding.

`NEEDS-TASKS` — define the exact serialized `TaskDefinition`, `RunConfig`, `OperationLog`, `ReplayInput`, and verifier violation object schemas, because REQ-03 fixes state/call/event/trace/receipt fields but leaves these auxiliary shapes open. This design prescribes their roles and fields but implementation tasks must lock golden schemas before coding.

`SPEC-CONFLICT` candidate to review during tasks: REQ-05 requires the resolved fault tape to contain a concrete operation key, while REQ-03 makes that key contain `logical_run_id` and REQ-03 also derives logical ID from fault-tape identity. The two-stage semantic-descriptor then concrete-key resolution above removes the cycle without changing externally required shapes; tasks should confirm this interpretation or amend the spec.

## 8. Delivery risks

- Snapshot, operation log, commit markers, and trace can diverge; boundary markers and adversarial mismatch tests are mandatory.
- A policy-step reservation in the wrong place can allow malformed calls or a 101st decision to bypass the hard kill switch.
- A response-loss fault can be accidentally implemented as a direct duplicate mutation; injector must remain response-only after persisted commit.
- Shared transition/scoring code can silently invalidate verifier independence; denylist tests are a release gate.
- Physical receipts differ on execution ID by design; deterministic comparisons must compare the declared deterministic artifacts and fields, not whole receipt bytes.
- Static report or auxiliary schemas may accidentally add defaults/floats/path metadata; canonical fixtures and schema tests must reject this.
- CodeGraph was available as a marker (`.codegraph/`) but no CodeGraph query tool/CLI was exposed in this session; design was based on the required reviewed documents and memory artifacts rather than broad source exploration.
