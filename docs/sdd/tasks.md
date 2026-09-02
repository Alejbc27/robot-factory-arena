# SDD Tasks — robot-factory-arena v0 (working copy)

> **Provenance**: byte-faithful copy of Engram observation 6177 (`sdd/robot-factory-arena/tasks`, 2026-09-01 17:58). Materialized to disk because the Engram HTTP tool path is intermittently failing; Engram remains the authoritative store.

---

# SDD Tasks — robot-factory-arena v0

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 3,000–3,500 (implementation, tests, golden fixtures, and README replay notes) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: B1 → B2; PR 2: B3; PR 3: B4; PR 4: B5; PR 5: B6 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

> **Session note (2026-09-01, after user decision)**: the user explicitly chose **single PR with `size:exception`** — delivery branch `feat/v0-evidence-chain`, work-unit commits per batch, one final PR. The suggested 5-PR split above is superseded.

The estimate is high because the repository has no implementation or test suite and v0 adds the complete stdlib package, eight test modules, canonical/golden artifacts, an independent verifier, and CLI/report integration. Each batch below is a self-contained review unit; the apply phase must not begin until the user chooses a chain strategy or explicitly accepts another delivery plan.

## Locked implementation decisions

1. **Auxiliary schemas are locked first.** B1/T-03 creates golden fixtures and schema tests for `TaskDefinition`, `RunConfig`, `OperationLog`, `ReplayInput`, and verifier violation objects. Later writers may not add fields, defaults, or alternate shapes without a new SDD decision.
2. **Fault target derivation is deterministic, not policy-driven at runtime.** After the reference policies exist, implementation deterministically simulates each policy from the serialized observation and records the first delivery `place` policy step and emitted intent. For public seed 17 and every evaluation-fixture variant (seed 29, and each reference policy used by a fixture), freeze the resulting decimal step and intent in `tests/fixtures/fault_targets.json`; derive it by hand or with a temporary helper script, then delete the helper unless it belongs to the declared layout. The fault tape resolver consumes these constants and never calls a policy.
3. **Trace closure is fixed.** The final run sequence is: all run events and domain/runtime records → `run_finished` → close trace and compute `trace_digest` → verify artifacts → write verified receipt → replay → report. `verification_finished` is a post-verification auxiliary record in the verification result and the receipt `verification` block; it is not appended to the closed trace. Golden trace tests must assert both the final `run_finished` record and absence of a `verification_finished` trace record.
4. **Fault-key/spec conflict is accepted by interpretation.** The semantic fault-tape descriptor (version, fault id, hook, kind, and predeclared target policy step/intent) is included in logical-run identity. Compute `logical_run_id` from that descriptor first; then expand the concrete `operation_key` as `op/<logical_run_id>/step-<decimal>/intent-<token>` and persist the resolved tape. The resolved tape has the required concrete key, while the pre-resolution `RunConfig.fault_tape_spec` has no concrete operation key. This is an accepted interpretation of REQ-03/REQ-05 and must be documented in implementation comments/tests, not silently changed.
5. **Verifier violations are structured.** Resolve the loose design sketch of `VerificationResult.violations` as an ordered tuple of frozen violation objects serialized as `{code, location, detail}`. Sort them by `(code, location, detail)` and use the closed lower-case reason taxonomy from the design. Runtime claims remain non-authoritative.
6. **Testing gate.** Python stdlib `unittest` only; every batch ends green under `python3 -m unittest discover -s tests -v`. No pytest, network, framework, or live provider is introduced.

## Exact auxiliary schema lock (B1/T-03)

These are the exact canonical JSON object shapes. All keys are present; arrays preserve semantic order; object keys are canonicalized lexicographically for bytes. Digest values use `sha256:<64 lowercase hex>` and examples in fixtures use fixed valid sentinel digests.

- **TaskDefinition:** `{schema_version, task_version, seed, width, height, slot_positions, shelves, orders, initial_state, config_identity}`. `config_identity` is exactly `{generator, generator_version, slot_layout}`. `task_version="warehouse-v0"`; `seed` is canonical decimal text; `width=height=10`; shelf/order/state shapes are the REQ-03 shapes; shelves/orders are sorted.
- **RunConfig:** `{schema_version, task_version, seed, policy_id, policy_digest, budget, grants, fault_tape_spec}`. `fault_tape_spec` is `null` or exactly `{schema_version, fault_id, hook, kind, target}` where `target` is exactly `{policy_step, intent}`. `target.policy_step` is a decimal integer and `target.intent` is the emitted deterministic intent token. The semantic spec contains no `operation_key`.
- **OperationLog:** `{schema_version, logical_run_id, operations}`. Each operation has exactly `{policy_step, operation_key, tool, arguments, status, reason, state_before, state_after, events, retry, fault_ids}`. Nullable `operation_key`, `tool`, `arguments`, `state_before`, and `state_after` support malformed/rejected submissions; `status` is one of `committed`, `rejected`, or `idempotent_replay`; `events` and `fault_ids` are arrays; `retry` is boolean. Entries are submission order, including internal retries.
- **ReplayInput:** `{schema_version, logical_run_id, task_identity, grants, budget, fault_tape, operations}`. `task_identity` is exactly `{task_version, seed, config_digest}`. `fault_tape` is the resolved tape object or `null`. `operations` use the exact OperationLog operation shape, preserving rejection, retry, idempotent replay, policy step, and fault evidence. Replay never carries a policy/provider reference.
- **Verifier violation:** exactly `{code, location, detail}` with non-empty ASCII strings; `code` is from the closed verifier/rejection taxonomy, `location` identifies the artifact or reconstructed field, and `detail` is deterministic explanatory text. Violations are sorted by the three fields.

## Ordered implementation batches

### B1 — scaffolding, canonical primitives, schema lock, immutable model, task generation, and pure policies

**Start boundary:** empty repository with charter README and no configured test suite. **Finish boundary:** package imports, canonical JSON/digest behavior, frozen domain values, seeded tasks, observation contract, and both pure policies work for seeds 17 and 29; schema fixtures are locked. **Verification:** `python3 -m unittest discover -s tests -v` is green. **Rollback:** revert only B1 paths; no runtime/evidence writer should exist yet, and the schema fixtures remain the review baseline if later batches are reverted.

#### T-01 — Establish the stdlib package and unittest discovery gate

- **Goal:** Create the declared package/test layout and a minimal green discovery harness without adding runtime behavior.
- **Files:** `robot_factory_arena/__init__.py`; `robot_factory_arena/domain/__init__.py`; `robot_factory_arena/contracts/__init__.py`; `robot_factory_arena/runtime/__init__.py`; `robot_factory_arena/policies/__init__.py`; `robot_factory_arena/faults/__init__.py`; `robot_factory_arena/evidence/__init__.py`; `robot_factory_arena/verification/__init__.py`; `tests/__init__.py`; `tests/test_golden_trace.py`; `tests/test_transitions.py`; `tests/test_invariants.py`; `tests/test_adversarial_verifier.py`; `tests/test_runtime_boundaries.py`; `tests/test_faults.py`; `tests/test_rerun_replay.py`; `tests/test_verifier_dependencies.py`.
- **REQs/ADRs:** REQ-08; stdlib-only and one-command test gate; package boundaries from the design.
- **Tests:** RED/GREEN: add a smoke discovery test and assert the documented command discovers all named modules; keep the suite green while later tests are filled in.
- **Depends on:** none.
- **Estimate:** ~50 lines.
- **Done-check:** Every declared package imports under Python 3.14.5, discovery finds the eight required test modules, and the exact command exits 0.

#### T-02 — Implement the single canonical JSON and digest authority

- **Goal:** Provide deterministic JSON, JSONL, and SHA-256 helpers used by every later package, including the verifier.
- **Files:** `robot_factory_arena/evidence/canonical.py`; `tests/test_invariants.py`; `tests/test_golden_trace.py`.
- **REQs/ADRs:** REQ-03, REQ-07; canonical JSON/SHA-256 as the only shared verifier dependency.
- **Tests:** RED then GREEN for sorted object keys, compact separators, UTF-8, finite-number rejection, explicit nulls, `sha256:` format, and LF-terminated JSONL; triangulate that equivalent mappings hash identically and path/execution metadata is not injected.
- **Depends on:** T-01.
- **Estimate:** ~90 lines.
- **Done-check:** Golden canonical bytes and digest vectors pass, no float/non-finite or implicit-default behavior is accepted, and no other serializer is needed by the planned interfaces.

#### T-03 — Lock all auxiliary artifact schemas before writer modules

- **Goal:** Freeze exact golden JSON shapes for TaskDefinition, RunConfig, OperationLog, ReplayInput, and verifier violations before any module that writes them is implemented.
- **Files:** `tests/fixtures/task_definition_schema.json`; `tests/fixtures/run_config_schema.json`; `tests/fixtures/operation_log_schema.json`; `tests/fixtures/replay_input_schema.json`; `tests/fixtures/verifier_violation_schema.json`; `tests/test_golden_trace.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-03, REQ-06, REQ-07, REQ-09; schema-first evidence contract; structured violations decision.
- **Tests:** RED/GREEN: tests assert exact top-level/nested keys, schema version 1, nullability, enum values, sorted semantic arrays, absence of `operation_key` from semantic fault descriptors, and canonical fixture bytes. Include one valid and one malformed/rejected OperationLog operation shape.
- **Depends on:** T-02.
- **Estimate:** ~140 lines.
- **Done-check:** Fixtures are reviewable canonical JSON, tests reject extra/missing/default fields, and later task references use these shapes verbatim.

#### T-04 — Implement frozen warehouse state values and serialization

- **Goal:** Implement immutable RobotState, ShelfState, OrderState, and WorldState with validation and exact REQ-03 serialization.
- **Files:** `robot_factory_arena/domain/model.py`; `tests/fixtures/transition_states.json`; `tests/test_transitions.py`; `tests/test_invariants.py`; `tests/test_golden_trace.py`.
- **REQs/ADRs:** REQ-01, REQ-03, REQ-04; frozen dataclasses, tuple collections, snapshot plus operation-log source of truth.
- **Tests:** RED then GREEN for frozen instances, dimension/coordinate/ID/order validation, sorted shelves/orders/completed IDs, null inventory/items, step semantics, canonical state bytes, and no input mutation.
- **Depends on:** T-02, T-03.
- **Estimate:** ~130 lines.
- **Done-check:** State constructors cannot expose mutable collections, serialize to exactly the required eight top-level keys, and all fixture states pass validation.

#### T-05 — Generate canonical seed-17 and seed-29 task definitions

- **Goal:** Implement the formula-only warehouse generator and TaskDefinition serialization for public and plumbing-only evaluation fixtures.
- **Files:** `robot_factory_arena/domain/task.py`; `tests/fixtures/task-17.json`; `tests/fixtures/task-29.json`; `tests/test_golden_trace.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-01, REQ-03, REQ-07, REQ-10; explicit seed formulas and evaluation-honesty boundary.
- **Tests:** RED then GREEN for canonical decimal seed normalization, exact slot/item/order formulas, source uniqueness, in-grid positions/routes, byte-identical repeated generation, and seed-29 plumbing-only labeling. Assert TaskDefinition matches the locked schema fixture.
- **Depends on:** T-03, T-04.
- **Estimate:** ~130 lines.
- **Done-check:** `generate_task(17)` and `generate_task(29)` match committed golden bytes, reject negative/non-decimal-invalid seeds, and use no random/global/runtime state.

#### T-06 — Implement observation, ToolCall contract, outcomes, and reference policies

- **Goal:** Establish the pure serialized policy boundary and deterministic greedy-v0/bounded-v0 policies.
- **Files:** `robot_factory_arena/domain/observation.py`; `robot_factory_arena/contracts/tool_call.py`; `robot_factory_arena/contracts/policy.py`; `robot_factory_arena/contracts/outcomes.py`; `robot_factory_arena/policies/greedy.py`; `robot_factory_arena/policies/bounded.py`; `tests/test_golden_trace.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-01, REQ-02, REQ-03, REQ-04, REQ-07; pure observation-only policy boundary; exact operation-key grammar; bounded outcome enums.
- **Tests:** RED then GREEN for exact observation keys/no side channels, ToolCall normalization and malformed candidates, operation-key regex, sorted grants in observations, greedy lexicographic target, bounded Manhattan target/tie, cardinal tie order north/east/south/west, and policy purity under repeated calls.
- **Depends on:** T-04, T-05.
- **Estimate:** ~100 lines.
- **Done-check:** Both policies accept only serialized observations, emit one normalized call per decision with deterministic intent labels, and never import runtime, filesystem, randomness, or verifier code.

### B2 — transitions, grants, budget, ToolBay, and idempotency ledger

**Start boundary:** B1 green and schema fixtures immutable. **Finish boundary:** all three tools are pure-transition-tested and runtime-gateway-tested with grants, operation keys, idempotency, and counters/budget primitives. **Verification:** exact unittest command is green, including transition and runtime-boundary suites. **Rollback:** revert B2 modules/tests only; retain B1 schemas and fixtures as the contract.

#### T-07 — Implement the pure transition authority

- **Goal:** Implement `apply(state, operation)` for legal move/pick/place and every closed rejection reason.
- **Files:** `robot_factory_arena/domain/transition.py`; `tests/fixtures/transition_states.json`; `tests/test_transitions.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-01, REQ-03, REQ-04; single functional transition authority with domain-only events.
- **Tests:** RED then GREEN for legal move/pick/place, every listed precondition rejection, event shapes, one step per commit, no step/effect on rejection, sorted completion, inventory/shelf updates, and frozen input preservation.
- **Depends on:** T-04, T-06.
- **Estimate:** ~150 lines.
- **Done-check:** `apply` knows no grants/ledger/fault/runner/report concepts, emits only the three allowed domain event types, and all unit cases pass.

#### T-08 — Implement run-scoped grants, outcome types, and budget accounting

- **Goal:** Implement deterministic grants and the runner-owned counters/hard `policy_steps=100` budget primitive.
- **Files:** `robot_factory_arena/runtime/grants.py`; `robot_factory_arena/runtime/budget.py`; `robot_factory_arena/contracts/outcomes.py`; `tests/test_runtime_boundaries.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-02, REQ-03, REQ-04; least privilege, free terminal checks, retries outside policy budget.
- **Tests:** RED then GREEN for deterministic grant IDs, capability/run/scope mismatch, unsupported tool authorization, reserve-before-parse semantics, 99→100 boundary, no 101st reservation, exact counter increments, retry/tool-attempt separation, and terminal-check non-effects.
- **Depends on:** T-02, T-06.
- **Estimate:** ~100 lines.
- **Done-check:** Grant serialization is explicit/sorted, all counters are separate integers, and budget primitives cannot be bypassed by malformed or unauthorized actions.

#### T-09 — Implement idempotency ledger and ToolBay gateway

- **Goal:** Make ToolBay the only runtime gateway and persist first-result ledger decisions without duplicate effects.
- **Files:** `robot_factory_arena/runtime/ledger.py`; `robot_factory_arena/runtime/tool_bay.py`; `tests/test_transitions.py`; `tests/test_runtime_boundaries.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-03, REQ-04, REQ-06; ledger keyed by operation key, authorization before transition, no exactly-once claim.
- **Tests:** RED then GREEN for grant-before-apply, normalized argument validation, first committed/rejected storage, same-key/same-payload `idempotent_replay`, same-key/different-payload `operation_key_conflict`, no second event/effect, and no mutable authority exposure to policy.
- **Depends on:** T-07, T-08.
- **Estimate:** ~150 lines.
- **Done-check:** Every ToolBay failure is deterministic/auditable, ledger replay never invokes `apply`, and the committed place fixture delivers exactly once.

### B3 — runner lifecycle, deterministic fault fixtures, and response-loss retry

**Start boundary:** B2 green. **Finish boundary:** a runner can execute a bounded in-memory run, enforce lifecycle/counters, resolve the one declarative fault, and safely retry a committed place. **Verification:** exact unittest command green; fault and budget boundary tests pass. **Rollback:** revert B3 runner/fault paths and fault fixtures; B2 gateway remains independently green.

#### T-10 — Implement RunConfig and runner lifecycle without evidence finalization

- **Goal:** Implement runner-owned observations, policy-step reservation, terminal checks, dispatch, counters, and supported outcomes behind injectable evidence callbacks.
- **Files:** `robot_factory_arena/runtime/runner.py`; `tests/test_runtime_boundaries.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-02, REQ-03, REQ-04, REQ-07; runner as lifecycle owner and no runtime score authority.
- **Tests:** RED then GREEN for initial/post-result terminal checks, reserve-before-validation, malformed/invalid/unauthorized rejection, no 101st observation, terminal step-100 precedence, counters, serialized-only policy input, and deterministic logical identity excluding execution ID.
- **Depends on:** T-08, T-09.
- **Estimate:** ~180 lines.
- **Done-check:** A fake policy can run success/rejection/budget cases with exact counters; runner never passes mutable state or invokes policy after budget exhaustion.

#### T-11 — Derive and freeze all response-loss place targets

- **Goal:** Determine the first delivery `place` step and intent deterministically, then freeze fault target constants before resolving concrete keys.
- **Files:** `tests/fixtures/fault_targets.json`; `tests/test_faults.py`; `tests/test_golden_trace.py`.
- **REQs/ADRs:** REQ-01, REQ-05, REQ-07; deterministic declarative fault tape; accepted two-stage fault-key interpretation.
- **Tests:** RED then GREEN for a helper/test simulation that feeds only serialized observations to `greedy-v0` and `bounded-v0`, confirms the selected operation is a delivery `place`, and verifies frozen entries for seed 17 plus every seed-29 evaluation-fixture variant. The committed fixture must contain the computed decimal `policy_step` and exact emitted `intent`, not a runtime-discovered value.
- **Depends on:** T-05, T-06, T-10.
- **Estimate:** ~80 lines.
- **Done-check:** The implementation records the actual computed constants in the fixture, documents whether the derivation was hand or temporary-script based, and no policy is called by fault resolution/execution to discover a target.

#### T-12 — Implement declarative fault model, semantic resolver, and injector

- **Goal:** Implement the single `response_lost_after_commit` tape and two-stage semantic-to-concrete operation-key resolution.
- **Files:** `robot_factory_arena/faults/model.py`; `robot_factory_arena/faults/tape.py`; `robot_factory_arena/faults/injector.py`; `tests/test_faults.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-03, REQ-05, REQ-07; response-only injector; no RNG/time/state mutation; spec-conflict interpretation.
- **Tests:** RED then GREEN for pre-execution resolution/persistence, exact `{fault_id, operation_key, hook, kind}` resolved shape, logical ID before key expansion, one-shot hook matching, no before-commit v0 fault, and rejection of unknown fault kinds/ambiguous target constants.
- **Depends on:** T-02, T-03, T-11.
- **Estimate:** ~130 lines.
- **Done-check:** Resolved tapes contain concrete keys, semantic config does not, identity is deterministic/execution-independent, and injector can only produce a response loss after the persistence marker.

#### T-13 — Integrate response-loss retry into the runner

- **Goal:** Execute the resolved place fault after commit, record one internal retry, and preserve one committed effect and `completed` outcome.
- **Files:** `robot_factory_arena/runtime/runner.py`; `robot_factory_arena/runtime/tool_bay.py`; `tests/test_faults.py`; `tests/test_runtime_boundaries.py`.
- **REQs/ADRs:** REQ-02, REQ-04, REQ-05; retry reuses exact ToolCall/key and does not consume policy step.
- **Tests:** RED then GREEN for persistence-boundary callback ordering, response-loss/fault/retry signals, unchanged policy steps, incremented tool attempts/retries, one delivery event/effect, terminal completion precedence, and unresolved boundary proof not upgraded to success.
- **Depends on:** T-10, T-12.
- **Estimate:** ~140 lines.
- **Done-check:** The faulted fixture completes with `committed_effects=1`, one retry, no duplicate reward, and explicit outcome classification; no exactly-once claim is introduced.

### B4 — trace, receipt, artifact store, operation log, and replay

**Start boundary:** B3 green with in-memory fault evidence. **Finish boundary:** a run has deterministic closed trace, physical artifact directory, locked auxiliary artifacts, receipt candidate, and policy-free operation replay. **Verification:** exact unittest command green; golden trace/evidence tests pass. **Rollback:** revert B4 evidence modules and integration while preserving B3 lifecycle behavior.

#### T-14 — Implement append-only trace and freeze closure ordering

- **Goal:** Write exact hash-chained trace records and enforce the adopted final event order.
- **Files:** `robot_factory_arena/evidence/trace.py`; `robot_factory_arena/runtime/runner.py`; `tests/test_golden_trace.py`; `tests/test_runtime_boundaries.py`.
- **REQs/ADRs:** REQ-03, REQ-05, REQ-07; trace hash chain and closed-trace binding.
- **Tests:** RED then GREEN for exact 14-field record shape, seq/prev hash chain, normalized arguments, state digests, LF bytes, no execution ID/path, and final ordering: all run events → `run_finished` → close/digest. Assert `verification_finished` is absent from trace and reserved for the later verification/receipt block.
- **Depends on:** T-02, T-10, T-13.
- **Estimate:** ~130 lines.
- **Done-check:** `TraceWriter.digest()` is unavailable or stable after close, appends after close are rejected, and golden trace bytes are deterministic.

#### T-15 — Implement receipt candidate and verified receipt serialization

- **Goal:** Bind declared artifact digests and verifier-owned fields into the exact receipt schema without trusting runtime score claims.
- **Files:** `robot_factory_arena/evidence/receipt.py`; `robot_factory_arena/contracts/outcomes.py`; `tests/test_golden_trace.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-03, REQ-06, REQ-09; receipt as verified binding and separate logical/physical identity.
- **Tests:** RED then GREEN for required receipt fields, four-outcome enum, distinct IDs, digest formats, counters, score rejection/mismatch, and exact `verification` block containing `status`, `verifier_version`, `verified_score`, counts, sorted violation objects, and post-verification `verification_finished` auxiliary record.
- **Depends on:** T-03, T-08, T-14.
- **Estimate:** ~110 lines.
- **Done-check:** ReceiptWriter cannot release a verified score without a VerificationResult and never adds trace records after the bound trace digest.

#### T-16 — Implement physical artifact store and operation-log persistence boundary

- **Goal:** Persist canonical config/task/fault/trace/state/operation artifacts with a simulated commit boundary and path-independent semantic bytes.
- **Files:** `robot_factory_arena/evidence/artifact_store.py`; `robot_factory_arena/runtime/runner.py`; `tests/test_faults.py`; `tests/test_golden_trace.py`; `tests/test_invariants.py`.
- **REQs/ADRs:** REQ-03, REQ-05, REQ-07, REQ-09; per-run path convention, atomic individual files, no distributed transaction claim.
- **Tests:** RED then GREEN for required directory/file names, canonical JSON bytes, operation log exact schema, resolved fault persisted before execution, commit marker after ledger/state/event/operation record, fsync/replace helper behavior, and digest independence from root/execution ID.
- **Depends on:** T-03, T-13, T-14, T-15.
- **Estimate:** ~170 lines.
- **Done-check:** `artifacts/runs/<logical_run_id>/<execution_id>/` contains all pre-final artifacts and proves the response-loss boundary without treating it as exactly-once storage.

#### T-17 — Implement operation-only offline replay

- **Goal:** Reconstruct state from task identity and recorded operations without importing or invoking a policy/provider.
- **Files:** `robot_factory_arena/evidence/replay.py`; `tests/test_rerun_replay.py`; `tests/test_golden_trace.py`; `tests/fixtures/replay_input_schema.json`.
- **REQs/ADRs:** REQ-06, REQ-07, REQ-09; replay distinct from rerun and policy-free.
- **Tests:** RED then GREEN with a policy object that raises if called, committed/rejected/idempotent retry sequences, seed/config binding, final-state digest, outcome/score handoff, and malformed replay input rejection. Validate exact ReplayInput schema.
- **Depends on:** T-07, T-09, T-16.
- **Estimate:** ~150 lines.
- **Done-check:** Replay consumes only declared artifact data, never imports `policies` or asks for a decision, and reproduces the B3 faulted final state.

### B5 — independent verifier, score, adversarial fixtures, and dependency denylist

**Start boundary:** B4 artifacts are emitted and replayable. **Finish boundary:** artifact-only verification independently reconstructs legality/score and rejects all required plausible cheats. **Verification:** exact unittest command green, including adversarial and dependency tests. **Rollback:** revert only B5 verification paths/tests; B4 remains capable of producing inspectable artifacts.

#### T-18 — Build the independent verifier model and structural invariants

- **Goal:** Reconstruct a minimal warehouse model from seed plus declared committed operations without runtime transition imports.
- **Files:** `robot_factory_arena/verification/invariants.py`; `robot_factory_arena/verification/verifier.py`; `tests/test_adversarial_verifier.py`; `tests/test_verifier_dependencies.py`.
- **REQs/ADRs:** REQ-06, REQ-07; artifact-only independent reconstruction and canonical helper as sole shared implementation.
- **Tests:** RED then GREEN for legal operation replay, grants, unique completion, budget/counter checks, state/trace consistency, idempotency, seed/config identity, trace-chain integrity, and forged artifacts without Runner/ToolBay/policy construction.
- **Depends on:** T-03, T-07, T-16, T-17.
- **Estimate:** ~170 lines.
- **Done-check:** Verification code accepts mappings/artifact values only and does not import/call `runtime`, `policies`, `domain.transition`, or runtime scoring.

#### T-19 — Implement verifier-owned integer scoring and structured violations

- **Goal:** Compute the official floor score and stable violation objects from independently validated evidence.
- **Files:** `robot_factory_arena/verification/scoring.py`; `robot_factory_arena/contracts/outcomes.py`; `tests/test_adversarial_verifier.py`; `tests/test_invariants.py`; `tests/fixtures/verifier_violation_schema.json`.
- **REQs/ADRs:** REQ-06, REQ-09; no floats/composite score; structured violation decision.
- **Tests:** RED then GREEN for `floor(100 * unique_valid_completed_orders / total_orders)`, raw numerator/denominator, duplicate/invalid exclusion, sorted `{code,location,detail}` violations, and receipt score mismatch rejection.
- **Depends on:** T-03, T-18.
- **Estimate:** ~100 lines.
- **Done-check:** Only verification code creates verified score, no claimed runtime score is accepted, and violation serialization exactly matches the locked fixture.

#### T-20 — Complete the artifact release gate and fault ambiguity classifications

- **Goal:** Verify receipt/artifact bindings and emit valid, `safe_abort`, `duplicate_effect`, or `unresolved_outcome` results with no false success.
- **Files:** `robot_factory_arena/verification/verifier.py`; `robot_factory_arena/verification/invariants.py`; `robot_factory_arena/verification/scoring.py`; `tests/test_adversarial_verifier.py`; `tests/test_faults.py`.
- **REQs/ADRs:** REQ-05, REQ-06, REQ-09; verifier-authoritative receipt and ambiguity handling.
- **Tests:** RED then GREEN for tampered final state/trace/receipt, missing commit-boundary proof, response-loss completed fixture, duplicate effect classification, unresolved ambiguity, artifact substitution, and verifier status/version plus post-verification auxiliary record.
- **Depends on:** T-18, T-19.
- **Estimate:** ~180 lines.
- **Done-check:** Valid receipt is released only after all bindings pass; an artifact lacking commit proof cannot be labeled completed.

#### T-21 — Add the full adversarial suite and import denylist gate

- **Goal:** Freeze named reward-hacking fixtures and enforce verifier dependency independence structurally.
- **Files:** `tests/test_adversarial_verifier.py`; `tests/test_verifier_dependencies.py`; `tests/fixtures/adversarial_artifacts.json`.
- **REQs/ADRs:** REQ-06, REQ-08; adversarial verifier and denylist release gate.
- **Tests:** RED then GREEN for self-reported success, duplicate reward/key reuse, teleport/illegal transition, trace/final mismatch, budget bypass, unauthorized effect, seed/config substitution, idempotency failure, artifact substitution, and partial-credit gaming. AST/import denylist must reject runtime, policies, domain.transition, and runtime scoring imports; tests must never instantiate runtime for forged artifacts.
- **Depends on:** T-20.
- **Estimate:** ~170 lines.
- **Done-check:** Every mandatory adversarial case has a deterministic violation and the dependency test fails closed if a forbidden import appears.

### B6 — static report, CLI, public/evaluation fixtures, golden rerun, and replay integration

**Start boundary:** B5 verifier release gate is green. **Finish boundary:** CLI run/rerun/replay produces inspectable complete artifacts for seeds 17 and 29, static report, exact verifier result, and offline replay. **Verification:** exact unittest command green and documented replay command works. **Rollback:** revert B6 integration/report/CLI and retain B1–B5 core/evidence contracts.

#### T-22 — Render the dependency-free static report and honesty boundary

- **Goal:** Render four separate labeled signals and all required raw evidence/limitations without composite scoring or network assets.
- **Files:** `robot_factory_arena/evidence/report.py`; `README.md`; `tests/test_golden_trace.py`; `tests/test_rerun_replay.py`.
- **REQs/ADRs:** REQ-09, REQ-10; static escaped HTML; proportional claims and limitations.
- **Tests:** RED then GREEN for task quality, efficiency, robustness, contract hygiene sections; raw counters, completion/actions per delivery, budget/outcome, all digests, verifier status/version, fault details, violations/audit completeness, and exact limitations including no authentication/exactly-once/hostile-Python/scientific REFUGIO claim. Assert no JS/CDN/composite score.
- **Depends on:** T-15, T-20.
- **Estimate:** ~130 lines.
- **Done-check:** `report.html` opens offline as static escaped HTML and README documents the v0 boundary and replay command exactly.

#### T-23 — Implement CLI run, rerun, and replay commands

- **Goal:** Expose distinct CLI operations and connect runner, artifact store, verifier, receipt, replay, and report.
- **Files:** `robot_factory_arena/cli.py`; `robot_factory_arena/runtime/runner.py`; `robot_factory_arena/evidence/artifact_store.py`; `robot_factory_arena/evidence/replay.py`; `tests/test_rerun_replay.py`; `tests/test_golden_trace.py`.
- **REQs/ADRs:** REQ-07, REQ-09, REQ-10; CLI command boundaries and offline replay.
- **Tests:** RED then GREEN for `run`, `rerun`, and `replay` argument validation, seed/config/policy selection, distinct execution IDs, same logical ID, artifact completeness, replay path-only reads, and a policy-forbidden replay process.
- **Depends on:** T-16, T-17, T-20, T-22.
- **Estimate:** ~160 lines.
- **Done-check:** `python3 -m robot_factory_arena.cli replay artifacts/runs/<logical_run_id>/<execution_id>` reads no provider/policy and reports exact final digest, score, and outcome agreement.

#### T-24 — Freeze complete golden run evidence for public seed 17

- **Goal:** Produce and test the canonical public greedy-v0 and bounded-v0 run artifacts, including the response-loss fixture.
- **Files:** `tests/fixtures/task-17.json`; `tests/fixtures/fault_targets.json`; `tests/fixtures/golden-trace-17.jsonl`; `tests/fixtures/golden-receipt-17.json`; `tests/test_golden_trace.py`; `tests/test_faults.py`; `tests/test_rerun_replay.py`.
- **REQs/ADRs:** REQ-01 through REQ-09; public hand-checkability and golden evidence.
- **Tests:** RED then GREEN for task/observation/decision bytes, complete required artifact files, trace digest/hash chain, response-loss one-effect retry, verifier-owned score, report fields, and receipt verification block/trace closure ordering.
- **Depends on:** T-11, T-14, T-16, T-20, T-23.
- **Estimate:** ~220 lines.
- **Done-check:** A reviewer can inspect seed-17 artifacts, reproduce the frozen place target, verify the receipt, and replay without policy with byte-identical deterministic artifacts.

#### T-25 — Add evaluation-seed plumbing, rerun equality, and final v0 gate

- **Goal:** Exercise seed 29 as plumbing-only and prove rerun-vs-replay distinctions plus the full one-command acceptance gate.
- **Files:** `tests/fixtures/task-29.json`; `tests/fixtures/golden-trace-29.jsonl`; `tests/fixtures/golden-receipt-29.json`; `tests/test_golden_trace.py`; `tests/test_invariants.py`; `tests/test_rerun_replay.py`; `README.md`.
- **REQs/ADRs:** REQ-01, REQ-07, REQ-08, REQ-09, REQ-10; evaluation honesty and no tuning claim.
- **Tests:** RED then GREEN for seed-29 task/config/artifact schema, plumbing-only label, two physical reruns with same deterministic bytes/digests/score but distinct execution IDs, replay with a raising policy, no 101st observation, and all mandatory test classes under the exact command.
- **Depends on:** T-23, T-24.
- **Estimate:** ~120 lines.
- **Done-check:** Full suite is green; public and evaluation runs are clearly separated, rerun invokes policy while replay does not, and README makes no scientific, leaderboard, security-sandbox, or exactly-once claim.

## Apply-phase sequencing and review gate

- Apply one batch at a time; each batch must end with `python3 -m unittest discover -s tests -v` green before starting the next.
- Keep tests and fixtures in the same work unit as the code they validate. Do not defer golden schemas, adversarial fixtures, or denylist checks to a cleanup PR.
- If a batch exceeds its review slice or changes a locked schema, stop and ask rather than silently expanding scope.
- Because the forecast is above the 400-line canonical threshold and the session delivery strategy is `ask-on-risk`, obtain the user's delivery decision before applying: choose a stacked chain, a feature-branch chain, or another explicitly accepted strategy. `exception-ok` is not inferred. **[Resolved: single PR, size:exception accepted; branch `feat/v0-evidence-chain`.]**
- Out of scope: LLM/provider/framework integrations, `assemble`, Arena/frontier/discovery/R1/R2/R3, extra fault kinds, broad task libraries, network services, hostile-code sandboxing, and scientific claims.
