# SDD Spec — robot-factory-arena v0 (working copy)

> **Provenance**: byte-faithful copy of Engram observation 6170 (`sdd/robot-factory-arena/spec`, 2026-09-01 17:40). Materialized to disk because the Engram HTTP tool path is intermittently failing; Engram remains the authoritative store.

---

# Specification: Robot Factory & Arena deterministic evidence-chain v0

## Contract and scope

This specification resolves proposal questions Q1–Q8 for v0. It does not add LLMs, frameworks, `assemble`, frontier/leaderboard behavior, discovery sharing, hostile-code isolation, or scientific arena claims. Q9 (evaluation-seed count and holdout policy) and Q10 (discovery categories and evidence thresholds) are explicitly deferred to the Phase 3 arena protocol.

RFC 2119 terms are normative. All identifiers below are ASCII strings unless stated otherwise. `sha256:<64 lowercase hexadecimal characters>` is the digest representation. A deterministic digest excludes wall-clock, host, process, filesystem-path, and unique-execution metadata.

## REQ-01 — Seeded task generation and reference policies

The system MUST generate one hand-checkable 10×10 warehouse task from a non-negative decimal integer seed.

1. Coordinates are `(x,y)` with `x,y ∈ {0,…,9}`, origin `(0,0)`, north `(0,+1)`, east `(+1,0)`, south `(0,-1)`, and west `(-1,0)`. The base is `(0,0)`. Every in-grid cell, including a shelf cell, is traversable; shelves are storage locations, not walls.
2. Define the fixed slot list, in order, as `P = [(1,2),(3,2),(5,2),(7,2),(9,2),(1,5),(3,5),(5,5),(7,5),(9,5)]`.
3. For seed `s`, let `q = s mod 10` and `n = 5 + (s mod 6)`, so `n ∈ {5,…,10}`. Shelf `shelf-00` through `shelf-09` has position `P[(i+q) mod 10]` and initially stores `item-((i+2q) mod 10)`, with the item number zero-padded to two digits. Each shelf stores at most one item.
4. Order `order-00` through `order-(n-1)` requests exactly one unit of `item-((3k+q) mod 10)`. The stride 3 is coprime to 10, so selected item IDs are unique. Every requested item has exactly one generated source shelf. Orders may be delivered in any order, but each order may be completed only once.
5. The initial robot is at the base with empty inventory and world `step = 0`.
6. The task version is `warehouse-v0`; the generated state and task serialization MUST expose all positions, items, orders, and the seed/config identity needed for hand checking.

The v0 fixtures MUST include public seed `17` and plumbing-only evaluation seed `29`. The evaluation seed MUST NOT be used to tune policy behavior or support an experimental claim.

The `greedy-v0` baseline MUST be a pure observation-only function: target the lexicographically first pending order; if carrying an item, navigate to base and place it; otherwise navigate to that order's source shelf and pick it; when choosing a shortest cardinal move, use tie order north, east, south, west. The `bounded-v0` policy MUST also be pure and deterministic: choose the pending order minimizing `Manhattan(robot,shelf)+Manhattan(shelf,base)+2`, tie by order ID, and follow the same pick/place routing. Neither policy may access mutable runtime state, filesystem, randomness, or the verifier.

#### Scenario: Same seed is hand-reproducible

- GIVEN seed `17`
- WHEN a task is generated twice
- THEN both tasks have the listed 10×10 geometry, `n = 10`, identical shelf positions/items and orders, and byte-identical canonical task serialization.

#### Scenario: Generated delivery invariant is satisfiable

- GIVEN any accepted seed
- WHEN the source shelf for each generated order is looked up using the stated formulas
- THEN exactly one shelf contains that item, every source is in-grid, and a legal route exists from base to source and back to base.

#### Scenario: Baseline is not evaluation-tuned

- GIVEN either fixture seed and the same policy/configuration
- WHEN `greedy-v0` or `bounded-v0` is called with the same serialized observation
- THEN it emits the same decision regardless of seed label, execution ID, wall clock, or prior runs, and the evaluation run is reported as plumbing evidence only.

## REQ-02 — `policy_steps` budget and accounting

The hard budget MUST be named `policy_steps` and have maximum `100`. The runner MUST own it and reserve one step for each policy action attempt before validation or dispatch. A policy action attempt is exactly one policy output intended as one `ToolCall`; malformed, invalid, and unauthorized outputs still consume the reserved step.

The runner MUST maintain these separate integer counters:

- `policy_steps`: reserved policy action attempts; hard limit is 100.
- `tool_attempts`: each initial ToolBay submission and each internal retry submission, including rejected submissions.
- `committed_effects`: each successful world effect, counted once at the commit boundary.
- `internal_retries`: each retry after the initial submission of the same operation.
- `contract_violations`: invalid, malformed, unauthorized, operation-key conflict, or other protocol violations.
- `terminal_checks`: every initial and post-result terminal-state check; terminal checks consume no policy step and do not increment `tool_attempts`, `committed_effects`, or `internal_retries`.

The runner MUST perform a terminal check before the first policy attempt and after every rejected result, committed result, retry result, and fault result. If all orders are complete at a check, the run terminates successfully. If the 100th policy step does not produce a terminal state, the runner MUST emit `budget_exhausted`, stop before any 101st observation/decision/tool dispatch, and classify the known, non-ambiguous abort as `safe_abort`. A terminal completion produced by step 100 takes precedence over `budget_exhausted`. An unresolved fault remains `unresolved_outcome` rather than being upgraded by the budget path.

A rejected or unauthorized call MUST produce no world effect, MUST be visible in the trace, and MUST consume exactly one `policy_steps` unit. An internal retry MUST reuse the original ToolCall and operation key, MUST increment `tool_attempts` and `internal_retries`, and MUST NOT increment `policy_steps`.

#### Scenario: Invalid calls cannot bypass the budget

- GIVEN a non-terminal run with `policy_steps = 99`
- WHEN the policy emits an unauthorized or malformed tool call
- THEN `policy_steps` becomes 100, the call is rejected and traced, and the runner terminates without requesting another policy action.

#### Scenario: Internal retry is not a second policy step

- GIVEN a valid place call at policy step 8 whose response is lost after commit
- WHEN the runner retries that same call internally
- THEN `policy_steps` remains 8, `tool_attempts` increases by one, `internal_retries` increases by one, and no second world effect is applied.

#### Scenario: Terminal check is free

- GIVEN a run whose final order is committed by policy step 100
- WHEN the runner performs the post-result terminal check
- THEN the run is `completed`, `terminal_checks` increases, and no `budget_exhausted` event is emitted.

## REQ-03 — Canonical state, observation, call, event, trace, and receipt schemas

All v0 evidence MUST use schema version `1`, canonical JSON, UTF-8, compact separators, lexicographically sorted object keys, arrays in their specified order, finite numeric values only, and no implicit/default fields. State and artifact digests MUST be SHA-256 over the exact canonical JSON bytes.

### WorldState

`WorldState` MUST be an immutable/frozen dataclass with this exact top-level shape and no hidden mutable state:

```text
WorldState(
  schema_version: int,
  width: int,                 # always 10
  height: int,                # always 10
  robot: RobotState,
  shelves: tuple[ShelfState, ...],
  orders: tuple[OrderState, ...],
  completed_orders: tuple[str, ...],
  step: int                   # committed domain-transition count
)
RobotState(position: tuple[int,int], inventory_item_id: str | None,
           base_position: tuple[int,int])
ShelfState(shelf_id: str, position: tuple[int,int], item_id: str | None)
OrderState(order_id: str, item_id: str, quantity: int)  # quantity is 1 in v0
```

`shelves` MUST be sorted by `shelf_id`, `orders` by `order_id`, and `completed_orders` lexicographically. `step` starts at zero and increases once only for each committed move, pick, or place transition. Rejections, terminal checks, and internal retries do not change state.

The canonical serialized state MUST be an object with keys `schema_version`, `width`, `height`, `robot`, `shelves`, `orders`, `completed_orders`, and `step`; tuples serialize as arrays and absent inventory/item values serialize as JSON `null`.

### Serialized observation

A policy MUST receive exactly a serialized observation object with keys:

```json
{
  "schema_version": 1,
  "logical_run_id": "lr_<64 hex>",
  "world_state": {"...": "canonical WorldState"},
  "grants": ["move", "pick", "place"],
  "policy_steps": {"used": 0, "remaining": 100, "maximum": 100},
  "last_result": null
}
```

After a result, `last_result` MUST be `{ "status": "committed" | "rejected" | "idempotent_replay", "operation_key": string, "reason": string | null }`. Observation content MUST be sufficient to decide a legal action and MUST contain no execution ID, timestamp, host data, mutable object reference, or hidden side channel.

### ToolCall and grants

`ToolCall` MUST have exactly `schema_version: int`, `tool: "move" | "pick" | "place"`, `arguments: object`, and `operation_key: string`. The key MUST match `op/<logical_run_id>/step-<decimal>/intent-<1..64 ASCII letters, digits, '.', '_', or '-'>`; it identifies the logical intent, not the trace position. A retry uses the same key. Reuse across distinct policy actions is allowed to reach the ledger and MUST be recorded as a hygiene violation; a payload conflict is rejected without effect.

A grant MUST be an immutable run-scoped capability `{ "grant_id": string, "capability": "move" | "pick" | "place", "scope": "run" }`. The runner issues an explicit sorted grant set. A ToolCall is authorized only when its tool has the matching active grant for the logical run. `assemble` and every other tool are unsupported and unauthorized in v0.

### Domain events

A committed transition MUST emit one or more deterministic domain events, each shaped as `{ "schema_version": 1, "event_type": string, "operation_key": string, "data": object }`, with only these v0 event types:

- `robot_moved`: `data = {"from":[x,y],"to":[x,y],"direction":...}`.
- `item_picked`: `data = {"shelf_id":...,"item_id":...}`.
- `order_delivered`: `data = {"order_id":...,"item_id":...}`.

Runtime evidence events MAY be `run_started`, `observation_emitted`, `policy_decision`, `tool_rejected`, `tool_committed`, `fault_injected`, `retry`, `budget_exhausted`, `run_finished`, and `verification_finished`. Runtime events MUST carry the data needed to audit the operation and MUST NOT be treated as domain transitions.

### JSONL trace

The trace MUST be append-only JSONL. Every record MUST have these top-level fields:

```text
schema_version, seq, logical_run_id, policy_step, event,
operation_key, tool, arguments, state_before, state_after, payload,
prev_event_hash, event_hash
```

`seq` starts at 0 and increments by one. Nullable fields are explicit `null`. `state_before` and `state_after` are state digests when applicable and `null` otherwise. `arguments` are the exact normalized arguments received. The first `prev_event_hash` is `null`; every later record uses the preceding record's `event_hash`. `event_hash = sha256(canonical_json(record with event_hash omitted))`, including `prev_event_hash`. `trace_digest` is SHA-256 of the UTF-8 JSONL bytes including one LF after each record. The trace MUST contain no unique `execution_id` so identical reruns can be byte-identical.

### Receipt

The receipt MUST be one canonical JSON object containing at least:

```json
{
  "schema_version": 1,
  "logical_run_id": "...",
  "execution_id": "unique physical execution id",
  "task_version": "warehouse-v0",
  "seed": "17",
  "config_digest": "sha256:...",
  "policy_id": "greedy-v0",
  "policy_digest": "sha256:...",
  "budget": 100,
  "grants": ["move", "pick", "place"],
  "fault_tape_digest": "sha256:... or null",
  "trace_digest": "sha256:...",
  "final_state_digest": "sha256:...",
  "operation_log_digest": "sha256:...",
  "verifier_version": "...",
  "verified_score": 0,
  "outcome": "completed | duplicate_effect | safe_abort | unresolved_outcome",
  "counters": {"policy_steps": 0, "tool_attempts": 0, "committed_effects": 0,
               "internal_retries": 0, "contract_violations": 0, "terminal_checks": 0}
}
```

`logical_run_id` MUST be deterministic from task/config/policy/budget/grants/fault-tape identity and MUST exclude `execution_id`. `execution_id` MUST distinguish physical reruns and MUST NOT enter deterministic state, trace, or score digests. `verified_score` is produced by the verifier, never trusted from a policy or runtime claim. Outcomes are exactly the four strings shown above.

#### Scenario: A receipt binds the evidence set

- GIVEN a valid run
- WHEN its receipt is serialized
- THEN it contains distinct logical/execution IDs, task/policy/budget identity, all required digests, counters, verifier-owned score, and exactly one supported outcome; changing the final snapshot or trace causes verification failure.

#### Scenario: Rejected calls remain auditable

- GIVEN an unauthorized place call
- WHEN the run emits evidence
- THEN the trace contains a `tool_rejected` record with the exact call/key and no committed domain event, while the receipt counter records the consumed policy step and contract violation.

## REQ-04 — Tool bay transitions, grants, and idempotency

The ToolBay MUST be the only runtime gateway to the transition authority. It MUST accept these exact argument shapes:

- `move`: `{ "direction": "north" | "east" | "south" | "west" }`; it succeeds only for an in-grid destination.
- `pick`: `{ "shelf_id": "shelf-00".."shelf-09", "item_id": "item-00".."item-09" }`; it succeeds only when the robot is at that shelf, inventory is empty, the named shelf contains the named item, and the item is the source for at least one incomplete order.
- `place`: `{ "order_id": "order-00".., }`; it succeeds only at base, with a non-empty inventory whose item matches that incomplete order, and then clears inventory and adds the order exactly once to `completed_orders`.

Every failed precondition is a deterministic rejection with a reason, no state effect, and a trace record. The ToolBay MUST enforce grant checks before applying a transition and MUST not expose mutable state or transition authority to policy code.

The idempotency ledger MUST be keyed by `operation_key` and persist the first committed or rejected result. Repeating the same key with the same normalized tool and arguments MUST return the stored result and MUST NOT invoke the transition a second time. Repeating it with different payload MUST return a deterministic key-conflict rejection and MUST NOT apply an effect. Ledger replay is an audit-visible `idempotent_replay` result, not an exactly-once guarantee claim.

#### Scenario: Place completes one order

- GIVEN the robot is at base carrying the exact item for pending `order-03` and has a place grant
- WHEN `place({"order_id":"order-03"})` commits
- THEN one `order_delivered` event is emitted, `order-03` appears once in sorted `completed_orders`, inventory becomes empty, and `committed_effects` increases by one.

#### Scenario: Reused operation key cannot duplicate a reward

- GIVEN a committed place result stored under operation key K
- WHEN the same K and identical payload are submitted again
- THEN the ledger returns the original result, `completed_orders` and `committed_effects` do not change, and no second delivery event is emitted.

## REQ-05 — One v0 response-loss fault tape

V0 MUST implement exactly one demonstrator tape: `response_lost_after_commit` on a `place` operation that delivers an order. The resolved, persisted tape MUST contain a declarative fault with `{fault_id, operation_key, hook:"after_commit_before_response", kind:"response_lost"}` and MUST be fully resolved before execution; no runtime RNG or wall-clock scheduling is permitted.

The simulated persistence boundary is the point at which the ledger entry, committed WorldState snapshot, operation record, and domain event are durably present in the run artifact store. The hook fires after that boundary and before the ToolBay response is returned. Thus the system can prove that the delivery committed even though the first response was not observed. The runner MUST retry the exact original ToolCall and operation key internally. The correct v0 fixture MUST finish with one effect and outcome `completed`; a broken non-idempotent ledger would be classified `duplicate_effect`, and an artifact set that cannot prove whether the boundary commit occurred MUST be classified `unresolved_outcome`, never success. A run that proves no commit and aborts without uncertainty is `safe_abort`. The latter classifications are part of the verifier contract even though only the one response-loss tape is executed in v0.

#### Scenario: Faulted delivery is retried safely

- GIVEN the resolved v0 tape targets the fixture's delivery place key
- WHEN the place transition commits and the response-loss hook fires
- THEN the trace records the fault and one retry, the retry uses the same key without a second effect, the final state and trace prove one delivery, and the receipt outcome is `completed`.

#### Scenario: Ambiguous evidence is not success

- GIVEN a tampered or incomplete fault artifact that omits proof of whether the targeted commit crossed the persistence boundary
- WHEN the verifier evaluates the receipt
- THEN it rejects `completed` and returns `unresolved_outcome` (or invalid verification), rather than accepting a claimed success.

## REQ-06 — Independent adversarial verifier and score ownership

The verifier MUST consume only declared artifacts: task/config identity, seed, grants, trace, final snapshot, fault tape, receipt, and replay material. It MUST reconstruct a minimal model independently from the seed and verified committed operations, then validate legal transitions, grants, unique order completion, budget, idempotency, final-state consistency, trace-chain integrity, receipt/artifact bindings, and seed/config identity.

The verifier MUST NOT import or call runtime policy, runner, ToolBay, runtime scoring, or transition implementation. Independence MUST be enforced by a dependency test/denylist and by keeping verifier tests able to run against forged artifacts without instantiating the runtime. Only the verifier MAY produce `verified_score`; runtime/self-reported scores are non-authoritative. V0 MUST include adversarial fixtures for all of these cases: self-reported success, duplicate reward through operation-key reuse, illegal transition/teleport, trace-versus-final-state mismatch, budget bypass, and unauthorized tool effect. Seed/config substitution, idempotency failure, artifact substitution, and partial-credit gaming SHOULD also have fixtures because they are named proposal threats.

The official task-quality score MUST be an integer `floor(100 * unique_valid_completed_orders / total_orders)`, with raw numerator/denominator retained. No receipt score is accepted unless independently recomputed.

#### Scenario: Verifier rejects plausible cheats

- GIVEN a forged artifact claiming all orders complete while the trace contains only a self-reported success or a teleport
- WHEN the independent verifier reconstructs from seed plus committed operations
- THEN verification is invalid, the forged score is not official, and the violation identifies the illegal or unsupported evidence.

#### Scenario: Verifier owns the score

- GIVEN a receipt whose `verified_score` differs from the independently recomputed integer score
- WHEN verification runs
- THEN the receipt is rejected as mismatched and no claimed runtime score is released as official.

## REQ-07 — Determinism, replay, and rerun

The implementation MUST satisfy this determinism checklist as testable behavior:

- no module-global random state; task generation uses only the explicit seed formulas in REQ-01;
- no floating-point values in scoring or digest inputs;
- no set iteration, unordered filesystem iteration, object identity, timestamps, PIDs, hostnames, or wall-clock values in semantics or deterministic hashes;
- canonical JSON and LF-terminated JSONL exactly as REQ-03;
- all semantic collections sorted in their specified order;
- explicit schema versions on state, calls, events, trace, receipt, and replay inputs;
- fault tapes are resolved and persisted before execution;
- execution IDs and artifact paths are excluded from deterministic trace/state/score digests.

A rerun MUST execute the same seed/config/policy and produce byte-identical canonical task, observations/decisions, trace JSONL, final-state serialization, deterministic digests, and verifier score. A replay MUST consume recorded committed/rejected operations from replay material, MUST NOT invoke or import the policy, and MUST reproduce the final-state digest, official score, and outcome. Replay and rerun MUST be exposed as distinct operations.

#### Scenario: Byte-identical rerun

- GIVEN public seed `17`, a fixed config, and `greedy-v0`
- WHEN the run is executed twice as separate physical executions
- THEN the execution IDs may differ, but task/evidence bytes covered by deterministic artifacts, trace digest, final-state digest, and verifier score are identical.

#### Scenario: Replay without policy

- GIVEN a recorded valid run
- WHEN offline replay is run with the replay input and no policy/provider available
- THEN no policy decision is requested and replay reproduces the recorded final-state digest, verified score, and outcome exactly.

## REQ-08 — Stdlib test harness and required test classes

The repository MUST use Python standard-library `unittest` under the active Python 3.14.5 interpreter. Pytest via the separate Python 3.12 executable is rejected because it is a mismatched interpreter and is not a project dependency. The single documented test command MUST be:

```text
python3 -m unittest discover -s tests -v
```

Implementation MUST establish tests under that command for golden traces, transition units, invariants, and adversarial verifier fixtures; all four classes are mandatory. The suite MUST exercise public and evaluation plumbing fixtures, budget boundary behavior, rejected/unauthorized calls, the response-loss tape, rerun identity, and replay without policy.

#### Scenario: One command covers the v0 gate

- GIVEN the repository has been implemented with the required test classes
- WHEN a reviewer runs `python3 -m unittest discover -s tests -v`
- THEN the golden-trace, transition-unit, invariant, adversarial-verifier, budget, fault, rerun, and replay tests execute under the project interpreter without pytest or network access.

## REQ-09 — Report, artifacts, and offline replay

Each physical run MUST use `artifacts/runs/<logical_run_id>/<execution_id>/` and MUST contain at least `config.json`, `task.json`, `fault_tape.json` (or an explicit null artifact), `trace.jsonl`, `final_state.json`, `receipt.json`, `replay-input.json`, `replay-result.json`, and static `report.html`. Deterministic contents MUST not depend on the directory path or execution ID.

`report.html` MUST be static/offline and show four separate labeled signals—task quality, efficiency, robustness, and contract hygiene—without a composite score. It MUST show raw counters (`policy_steps`, `tool_attempts`, `committed_effects`, `internal_retries`, `contract_violations`, `terminal_checks`), completed/total orders, actions per delivery, budget/outcome, all artifact digests, verifier status/version, and a limitations section stating tamper evidence is not authentication, no exactly-once or hostile-Python sandbox guarantee is made, the evaluation seed is plumbing-only, and no scientific REFUGIO conclusion is claimed. Efficiency MUST include raw `policy_steps` and actions per delivery; robustness MUST include the fault kind/result/outcome; hygiene MUST include violations and audit completeness. No v0 composite normalization is required or authoritative.

The documented offline replay instruction MUST be equivalent to:

```text
python3 -m robot_factory_arena.cli replay artifacts/runs/<logical_run_id>/<execution_id>
```

It MUST read only the run artifacts, never call a policy or network provider, and report whether final-state digest, verified score, and outcome match the receipt.

#### Scenario: Reviewer can inspect and replay offline

- GIVEN a completed public or evaluation run directory copied to an offline machine with Python 3.14.5
- WHEN the reviewer opens `report.html` and runs the documented replay command
- THEN the four signals, raw counters, limitations, and artifact bindings are visible, and replay reports exact receipt agreement without network or policy execution.

## REQ-10 — v0 honesty boundary and acceptance claims

The implementation and report MUST present v0 as a deterministic warehouse evidence-chain demonstrator. It MUST state that policies/tool requests are semantically untrusted but policy source is not treated as hostile malware, and MUST not claim production security, hostile arbitrary-Python isolation, exactly-once effects, a scientific REFUGIO reproduction, a frontier/leaderboard, or R1/R2/R3 arena results. The baseline MUST be measurable on the same task/configuration as the bounded policy.

#### Scenario: Report claims remain proportional

- GIVEN a v0 report with public and evaluation results
- WHEN a reviewer reads the report
- THEN it labels the evaluation run as plumbing-only, reports baseline and bounded-policy evidence on the same configuration, and includes the required limitations instead of making a scientific or security claim.

## Deferred decisions

- **Q9 — Deferred to Phase 3 arena protocol:** the number of evaluation seeds, holdout policy, total attempt-budget equalization, and anti-adaptive-oracle protocol.
- **Q10 — Deferred to Phase 3 arena protocol:** discovery categories, evidence thresholds, evaluation-instance leakage rules, and shared-memory admission policy.

## Acceptance mapping

The proposal's 11 checkable acceptance criteria map to the requirements as follows:

| Proposal criterion | Covered by |
|---|---|
| 1. Same seed/config/policy gives byte-identical trace and score | REQ-01, REQ-07 |
| 2. Public/evaluation runs emit complete schema-versioned evidence | REQ-03, REQ-09 |
| 3. Replay without policy reproduces final digest and score | REQ-07, REQ-09 |
| 4. Verifier rejects a plausible cheat | REQ-06 |
| 5. Exceeding 100 steps kills/aborts reproducibly | REQ-02, REQ-08 |
| 6. Invalid/unauthorized calls reject, consume steps, trace, affect hygiene | REQ-02, REQ-03, REQ-04, REQ-09 |
| 7. Reused operation key under response-loss fault has no double effect | REQ-04, REQ-05 |
| 8. Fault outcome uses supported enum and ambiguity is not success | REQ-05, REQ-06 |
| 9. Receipt/trace linkage and SHA-256 chaining detect mismatch; chain is tamper evidence only | REQ-03, REQ-06, REQ-09 |
| 10. Static report separates four signals with raw counters/limitations | REQ-09 |
| 11. Baseline is measurable and no scientific REFUGIO conclusion is presented | REQ-01, REQ-08, REQ-10 |

No Q9 or Q10 behavior is specified in this v0 artifact.
