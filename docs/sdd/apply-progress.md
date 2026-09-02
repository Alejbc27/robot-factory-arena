# SDD Apply Progress — robot-factory-arena v0

## Batch B1 — T-01..T-06 — COMPLETE, gate green

- **Branch:** `feat/v0-evidence-chain`
- **Code commit:** `51bc7ba` — `feat(core): B1 scaffold, canonical authority, schema locks, domain model, seeded tasks, pure policies` (34 files, +2,526 lines)
- **Delivery path:** single PR with user-accepted `size:exception` (recorded in tasks.md session note); work-unit commit per batch.

### Test gate (REQ-08)

- Command: `python3 -m unittest discover -s tests -v` (Python 3.14.5)
- Final result: **Ran 86 tests — OK — 86 pass / 0 failures / 0 errors, exit 0.**

### TDD Cycle Evidence

| Task | RED (observed) | GREEN (observed) | Notes |
|------|----------------|------------------|-------|
| T-01 | 8 import errors (no package) + discovery gaps | 9 tests OK | smoke asserts 8 packages import + 8 test modules discovered with ≥1 test |
| T-02 | import error `evidence.canonical` | 19 tests OK | sorted keys, compact separators, UTF-8, explicit nulls, float rejection incl. finite, `sha256:` format + known vector, equivalent mappings hash identically, LF-terminated JSONL, no injected metadata |
| T-03 | 15 errors (fixtures absent) | 26 tests OK | 5 canonical schema fixtures; exact key lists vs hardcoded lock; enums/nullables/status enum; run_config `fault_tape_spec` excludes `operation_key` (accepted SPEC-CONFLICT interpretation documented in fixture + test); operation_log valid/rejected/malformed examples |
| T-04 | import error `domain.model` | 44 tests OK | frozen instances, list collections rejected, dimension/coordinate/ID/quantity/schema/step validation, strictly-sorted tuples, unique shelf positions, completed ⊆ orders, exact 8-key serialization, golden canonical bytes, round-trip, fixture states validate (story steps 0→3→4→7→8) |
| T-05 | import error `domain.task` | 58 tests OK | seed normalization (`"017"`→`"17"`; rejects −1, 1.5, True, "abc", "", "+17", None, "0x11"), hand-checked formulas for seeds 17/29, fixed-P slot coverage, unique source shelf per order (seed sweep 0..35), in-grid, byte-identical regeneration, golden task-17/task-29 bytes, locked-schema conformance |
| T-06 | import errors (observation/contracts/policies) | 86 tests OK | observation exact keys/sorted grants/BudgetView consistency; operation-key grammar (leading-zero step, 65-char intent, wrong run id all rejected); ToolCall normalization (`malformed_call`/`unsupported_tool`/`operation_key_invalid`/`invalid_arguments`); immutable argument views; AST import allowlist (policies import only the contract boundary); greedy lexicographic target + N/E/S/W tie order + pick/place flows + step arithmetic; bounded Manhattan minimum (38 vs 6) + order-id tie; determinism/purity/no-mutation |

Triangulation catch during T-05: implementation output exposed two wrong hand-written assertions (P[7] is (5,5), P[9] is (9,5)); tests were corrected to the spec formulas — implementation was already correct.

### Completed tasks

- **T-01 done** — package scaffold (8 `__init__.py`) + `tests/` scaffold; discovery smoke green; gate exits 0.
- **T-02 done** — `evidence/canonical.py` is the single canonical JSON/SHA-256 authority used by all later packages (sorted keys, compact separators, UTF-8, explicit nulls, no floats — finite or not, `sha256:<64 lowercase hex>`, one LF per JSONL record, no path/execution metadata).
- **T-03 done** — five schema-lock fixtures (`task_definition_schema.json`, `run_config_schema.json`, `operation_log_schema.json`, `replay_input_schema.json`, `verifier_violation_schema.json`) are reviewable canonical JSON; tests reject extra/missing/default fields; sorted violation objects per locked decision 5; the accepted two-stage fault-descriptor interpretation (semantic `fault_tape_spec` has no `operation_key`; the resolved tape carries the concrete key) is documented in fixture + tests.
- **T-04 done** — frozen `RobotState`/`ShelfState`/`OrderState`/`WorldState` with validating (never normalizing) constructors; exact REQ-03 serialization (8 top-level keys, tuples→arrays, absent values→null); `tests/fixtures/transition_states.json` hand-checked states all validate.
- **T-05 done** — `domain/task.py` formula-only generator (fixed P, `q=s mod 10`, `n=5+(s mod 6)`, shelf/order item formulas); `generate_task(17)`/`generate_task(29)` byte-match committed golden fixtures; seed 29 labeled plumbing-only in tests (REQ-10); no random/float/global state.
- **T-06 done** — serialized observation boundary (exact REQ-03 keys, sorted grants, no side channels), `ToolCall` normalization with closed-taxonomy rejection reasons and key grammar `op/<lr>/step-<decimal>/intent-<token>`, bounded outcome types (`Outcome`, `ResultStatus`, `BudgetView`, `LastResult`, `Counters`, `VerifierViolation`, `VerificationResult`), and pure `greedy-v0`/`bounded-v0` with frozen intent labels (`goto-base`, `goto-<shelf>`, `pick-<shelf>`, `place-<order>`).

### Task checkbox note

Artifact store is Engram; the materialized `docs/sdd/tasks.md` is outside this phase's allowed edit surfaces. T-01..T-06 completion is recorded here and (best effort) in the Engram apply-progress observation. Per the session instruction ("only `mem_save` at the end, best effort; the file is authoritative"), the Engram tasks observation (6177) was not rewritten — the orchestrator should mark T-01..T-06 `[x]` there.

### Files changed (34, +2,526)

- Package: `robot_factory_arena/__init__.py` + 7 subpackage inits; `evidence/canonical.py`; `domain/{model,task,observation}.py`; `contracts/{tool_call,policy,outcomes}.py`; `policies/{greedy,bounded}.py`
- Tests: `tests/__init__.py`; 8 required test modules; fixtures: 5 schema locks + `transition_states.json` + `task-17.json` + `task-29.json`

### Deviations from design (bounded, documented)

1. `BudgetView` lives in `contracts/outcomes.py` instead of `runtime/budget.py`: B1 may not touch `runtime/` beyond `__init__.py`, and T-06's Observation needs it. T-08's file list includes `contracts/outcomes.py`, so B2's `Budget` (runtime/budget.py) will own behavior and reuse this view.
2. Policy routing helpers are duplicated in `greedy.py`/`bounded.py` (commented as intentional): B1's declared layout has no shared policy helper module; each reference policy stays self-contained.
3. `contracts/tool_call.py` adds `expand_operation_key()` supporting locked decision 4 (two-stage fault-target expansion; grammar identical to the validated key). B3/T-11 will consume it.
4. Discovery placeholders remain in the four test modules whose SDD tasks land in B2+; each is replaced as its task is implemented.

### Workload / PR boundary

Single PR, `size:exception` accepted (session decision). B1 = 2,526 changed lines in one work-unit commit; review order suggestion: schema fixtures + canonical authority → model/task → contracts/policies.

### Structured status / environment notes

- Consumed non-authoritative SDD status (artifact store: `engram`); readiness resolved from the materialized `docs/sdd/{tasks,spec,design}.md` files (byte-faithful Engram copies; provenance headers intact).
- pi-lens static analyzer reports false "import could not be resolved" for repo-root packages (its resolver has no repo-root config; every flagged import executes in each green gate run) and its runner cannot launch (`spawn python ENOENT` — it invokes `python`; REQ-08 locks `python3`). Authoritative verification remains the documented unittest command.
- `__pycache__/` is untracked and excluded from commits via explicit staging; `.gitignore` is outside this phase's allowed surfaces — recommend the orchestrator add `__pycache__/` before B2.
- Test modules load as top-level modules under the locked discover command; keep every test module self-contained (no `tests.` prefix imports).

### Remaining tasks (unchecked — B1 snapshot; T-07..T-09 completed in B2 below)

- [x] T-07 Implement the pure transition authority *(completed in B2 — see Batch B2)*
- [x] T-08 Implement run-scoped grants, outcome types, and budget accounting *(completed in B2 — see Batch B2)*
- [x] T-09 Implement idempotency ledger and ToolBay gateway *(completed in B2 — see Batch B2)*
- [ ] T-10 Implement RunConfig and runner lifecycle without evidence finalization
- [ ] T-11 Derive and freeze all response-loss place targets
- [ ] T-12 Implement declarative fault model, semantic resolver, and injector
- [ ] T-13 Integrate response-loss retry into the runner
- [ ] T-14..T-17 (B4 trace/receipt/store/replay), T-18..T-21 (B5 verifier/adversarial/denylist), T-22..T-25 (B6 report/CLI/golden/final gate)

### Notes / risks for B2

- `BudgetView` (used+remaining==maximum invariant) is already exported from `contracts/outcomes.py`; `Budget` must preserve it and implement reserve-before-parse.
- `expand_operation_key()` is ready for T-11; the intent labels are frozen — `place-<order_id>` is what fault targets must record.
- OperationLog schema examples use sentinel digest `sha256:e3b0c442…b855` (sha256 of empty bytes) and sentinel run id `lr_0123456789abcdef…`; reuse these conventions for hand-built evidence.
- `apply()` (T-07) must construct new frozen states and reject rather than mutate; model constructors strictly validate and never normalize.
- Ledger decisions (T-09): keyed only by `operation_key`; same payload → `idempotent_replay` without invoking `apply`; different payload → `operation_key_conflict`, no effect.
- Grants must be checked before transitions; unsupported tools reject as `unsupported_tool` (ToolBay owns this; `normalize_tool_call` already returns the reason).

## Batch B2 — T-07..T-09 — COMPLETE, gate green

- **Branch:** `feat/v0-evidence-chain`
- **Code commit:** `8e3abaa` — `feat(core): B2 pure transitions, grants, budget, tool bay, and idempotency ledger` (8 files, +2,124/−26 lines)
- **Delivery path:** single PR with user-accepted `size:exception`; one work-unit commit per the batch instruction (code+tests together; docs follow-up commit).

### Test gate (REQ-08)

- Command: `python3 -m unittest discover -s tests -v` (Python 3.14.5)
- Final result: **Ran 153 tests — OK — 153 pass / 0 failures / 0 errors, exit 0.** (B1 baseline 86 → 153: +67 tests)

### TDD Cycle Evidence

| Task | RED (observed) | GREEN (observed) | Notes |
|------|----------------|------------------|-------|
| T-07 | `ModuleNotFoundError: robot_factory_arena.domain.transition` (116 tests, 2 loader errors) | all apply() units ok | legal move/pick/place with exact REQ-03 event data; every closed rejection reason incl. unknown shelf/order folds (`robot_not_at_shelf`, `order_item_mismatch`); documented precedence (pick: at-shelf→inventory→shelf-item→pending; place: at-base→inventory→already-completed→item-match); one step per commit; no effect on rejection; frozen inputs (commit constructs new state, rejection returns the identical frozen input); purity under repetition; `invalid_arguments`/`unsupported_tool` defensive rejects; the fixture story (start→at_shelf→carrying→at_base_carrying→one_completed) is exactly reproducible via apply(); AST independence (imports ⊆ {contracts.outcomes, domain.model}); emitted keys validate against the grammar |
| T-08 | `ModuleNotFoundError: runtime.budget` / `runtime.grants` (116 tests, 3 errors) | all grants/budget units ok | deterministic grant ids (`grant-` + 16 hex over canonical {capability, logical_run_id, scope}); issue_grants sorts/dedupes/rejects unsupported; foreign-run grant → `grant_scope_mismatch`; duck-typed unsupported tool → `unsupported_tool`; AuthorizationResult ok↔reason invariant; reserve-before-parse (rejection never refunds); 99→100 boundary; no 101st reservation (`BudgetExhaustedError`); six exact separate counters; free terminal checks; retry separates tool_attempts from policy_steps; frozen BudgetView/Counters snapshots; view invariant across all 100 reserves (invariants) |
| T-09 | `ModuleNotFoundError: runtime.ledger` (test_runtime_boundaries fails to load; 116 tests, 3 errors) | all ledger/ToolBay units ok (153 OK) | Ledger: first-result-wins; same key+payload → `idempotent_replay` (insertion-order-insensitive via canonical digest); same key+different payload or tool → `operation_key_conflict`; entries validate (replays never stored; committed entries carry snapshot digest); ToolBay: ledger replay proven without re-apply (replayed from a moved world where apply would reject); missing_grant/invalid_arguments first rejections stored and replayed with original reason; conflicts leave the original entry intact; retry=True walks the identical path; submit type-checks its inputs; no mutable authority exposed (no ledger/apply/lookup attributes; frozen ToolResult; committed-flag↔status invariant; reasons ∈ closed taxonomy); place fixture delivers exactly once (one `order_delivered`, replay adds nothing); runtime module AST allowlists forbid policies/faults/verification/runner/cli |

Triangulation catches in T-07: five hand-written test setups were wrong against the spec (south/west from (0,0) are genuinely off-grid; `start` is already at base so `inventory_empty` fired instead of `not_at_base`; story snapshot belongs after move 3; empty-shelf state still held its item; post-place inventory is empty). Implementation was already correct — tests were corrected to spec semantics (same pattern as B1/T-05).

### Completed tasks

- **T-07 done** — `domain/transition.py`: pure `apply(state, operation)` with `Operation(tool, arguments, operation_key)`, `DomainEvent` (exact 4-key REQ-03 shape), `TransitionResult` (committed⇒1+ events/no reason; rejected⇒no events/closed-taxonomy reason). Emits only `robot_moved`/`item_picked`/`order_delivered`; never mutates inputs; knows nothing of grants/ledger/faults/runner/report (AST-enforced). Done-check verified: all unit cases pass.
- **T-08 done** — `runtime/grants.py` (`Grant`/`GrantSet`/`issue_grants`/`authorize`/`AuthorizationResult`, deterministic ids, scope-mismatch proof by id recomputation) + `runtime/budget.py` (`Budget` with `require_available`/`reserve_policy_attempt`/`record_*`/`view()`/`counters()`, `BudgetExhaustedError`; hard maximum default 100). `contracts/outcomes.py` needed NO extension — B1's `BudgetView` reused as-is (invariant preserved by construction). Done-check verified: grant serialization explicit/sorted, counters separate integers, budget primitives cannot be bypassed.
- **T-09 done** — `runtime/ledger.py` (`LedgerEntry`/`LedgerDecision`/`Ledger` keyed only by `operation_key`, first-wins `store_first`, canonical `arguments_digest`) + `runtime/tool_bay.py` (`ToolResult`, `ToolBay.submit` as the only gateway). Ledger replay never invokes `apply` (proven by replaying from a diverged world). Done-check verified: every failure deterministic/auditable; committed place fixture delivers exactly once.

### Files changed (8, +2,124/−26)

- New: `domain/transition.py`, `runtime/grants.py`, `runtime/budget.py`, `runtime/ledger.py`, `runtime/tool_bay.py`
- Tests: `tests/test_transitions.py` (+554), `tests/test_runtime_boundaries.py` (+540), `tests/test_invariants.py` (+141)
- `contracts/outcomes.py`: untouched (BudgetView reused; no extension needed)
- `tests/fixtures/transition_states.json`: untouched (story states already covered the T-07 needs)

### Deviations / interpretations from design (documented in code docstrings)

1. **`Operation` carries `operation_key` as a third field** (design sketch lists only tool+arguments): REQ-03 requires every domain event to carry the producing operation's key, and `apply` is the sole producer of events — there is no other source for the key. Minimal amendment; grammar validation stays in `contracts.tool_call`. Recorded here per the SDD interpretation-documentation rule.
2. **ToolBay check order: envelope → ledger → grants → arguments → apply** (design §3 flow steps 6–7 list grants before the ledger query on the fresh-dispatch path). Rationale: REQ-04 (binding spec) makes replay unconditional — repeating a stored key with the same normalized tool+arguments MUST return the stored result — while REQ-04's grant requirement is only "grant checks before APPLYING a transition", which is preserved (nothing reaches `apply` without passing grants; replays/conflicts never apply). One uniform rule: same key+payload → first result replays; different payload → `operation_key_conflict`. Grant-before-apply tests still green.
3. **Rejection precedence inside `apply`** is fixed and tested (pick: at-shelf → inventory → shelf-item → pending; place: at-base → inventory → already-completed → item-match; unknown shelf folds into `robot_not_at_shelf`, unknown order into `order_item_mismatch`). The taxonomy is closed; no new reasons introduced.
4. **pi-lens/ruff reformatted parts of B1 test code** during this session (import order, `range(0,36)`→`range(36)`, dropped explicit `"utf-8"` in `.encode()` inside a test, merged subTest/assertRaises context managers). Behavior-identical; the gate runs green on the reformatted code.

### Workload / PR boundary

Single PR, `size:exception` accepted (session decision). B2 = 2,150 net changed lines in one work-unit commit (cumulative branch: B1 2,526 + B2 2,150 ≈ 4,676 + docs). Review order suggestion: transition authority + tests (domain semantics, precedence) → grants/budget (boundary primitives) → ledger/tool_bay (idempotency order and replay proofs).

### Structured status / environment notes

- Consumed non-authoritative SDD status (artifact store: `engram`); readiness resolved from the materialized `docs/sdd/{tasks,spec,design}.md` files; B1 constraints honored as hard inputs.
- pi-lens: unchanged false-positive pattern (repo-root import resolution; runner `spawn python ENOENT` vs REQ-08's locked `python3`). Its ruff autofix reformatted B1 test code mid-batch (behavior-identical, noted above); one mid-session indentation corruption it applied to `tests/test_invariants.py` was repaired and is covered by the green gate. Authoritative verification remains the documented unittest command.
- Engram remains the authoritative artifact store; this file is the session-authoritative progress record per batch instruction.

### Remaining tasks (unchecked)

- [ ] T-10 Implement RunConfig and runner lifecycle without evidence finalization
- [ ] T-11 Derive and freeze all response-loss place targets
- [ ] T-12 Implement declarative fault model, semantic resolver, and injector
- [ ] T-13 Integrate response-loss retry into the runner
- [ ] T-14..T-17 (B4 trace/receipt/store/replay), T-18..T-21 (B5 verifier/adversarial/denylist), T-22..T-25 (B6 report/CLI/golden/final gate)

### Notes / risks for B3 (runner integration contract)

- **ToolResult fields (T-10/T-13 consumer contract):** `(status: committed|rejected|idempotent_replay, state: WorldState, events: tuple[DomainEvent, ...], operation_key: str, reason: str|None, committed: bool)`. `committed` is True exactly for `committed`. A committed result carries the NEW state + its domain events + reason None. A rejected result carries the caller's input state unchanged, no events, and the closed-taxonomy reason. An `idempotent_replay` result carries the caller's current state unchanged, no events, and the ORIGINAL reason (None when the first result committed).
- **Retry protocol (T-13):** `ToolBay.submit(state, call, grant_set, retry=False)`. A retry walks the identical ledger/grants path — since the first commit is stored, the retried call returns `idempotent_replay` (reason None) with no second effect and never touches `policy_steps`; the runner increments `tool_attempts` + `internal_retries` and does NOT increment `committed_effects` again. Retry of a first REJECTION replays with the original reason.
- **Persistence-boundary callback shape (T-13 proposal):** B2 deliberately added no speculative parameters. Suggested T-13 integration: extend `ToolBay.submit(..., on_commit_boundary=None)` (or construct ToolBay with the callback); ToolBay invokes `on_commit_boundary(call, tool_result)` after `store_first` of a committed entry and BEFORE returning the result — the runner/evidence layer durably persists ledger entry + snapshot + operation record + events + commit marker there, then the injector's `after_commit_before_response` hook fires (also T-13) before the response is observed. `LedgerEntry.committed_snapshot_digest` (= `digest_json(state.to_mapping())`) is already computed at store time and can be reused by the artifact writer.
- **Ledger visibility:** `Ledger` exposes `__len__`/`__contains__` (audit helpers); entries themselves are not exposed via ToolBay. If T-10/T-16 need entry snapshots for the operation log, add a read-only `snapshot()` on Ledger (B3's surface to extend — not done speculatively in B2).
- **Parse-level rejections stay runner-side:** `malformed_call`/`unsupported_tool`/`operation_key_invalid` never reach ToolBay (no ToolCall is constructible for them), and `invalid_arguments` candidates normally fail `normalize_tool_call` in the runner before dispatch. Per design flow step 6 ("enter the ledger when a valid key exists"), the runner must ledger-store such rejections itself when the candidate carried a valid key — `LedgerEntry` is a public API for exactly that. ToolBay-side storage covers `missing_grant`/`grant_scope_mismatch`/defensive shape rejections/apply rejections/conflicts.
- **Run identity plumbing:** ToolBay derives `logical_run_id` from the operation key grammar (group 1); grants bind via deterministic id recomputation, so a foreign-run key fails `grant_scope_mismatch` on first dispatch. `issue_grants(logical_run_id, ("move","pick","place"))` gives the sorted observation grant list.
- **Budget:** `Budget()` defaults to the locked maximum 100; `counters()` returns the frozen six-integer snapshot for observations/receipts; `BudgetExhaustedError("budget_exhausted")` is the kill-switch signal the runner maps to `safe_abort` (B3/T-10).
- B1 sentinel conventions for hand-built evidence (`sha256:e3b0c442…b855`, sentinel run id) remain the reference for fixtures (T-11/T-16).

## Batch B3 — T-10..T-13 — COMPLETE, gate green

- **Branch:** `feat/v0-evidence-chain`
- **Code commit:** `3059165` — `feat(core): B3 runner lifecycle, fault tapes, and idempotent response-loss retry` (9 files, +2,394/−22)
- **Delivery path:** single PR with user-accepted `size:exception`; one work-unit commit per the batch instruction (code+tests+fixtures together; docs follow-up commit).

### Test gate (REQ-08)

- Command: `python3 -m unittest discover -s tests -v` (Python 3.14.5)
- Final result: **Ran 201 tests — OK — 201 pass / 0 failures / 0 errors, exit 0.** (B2 baseline 153 → 201: +48)
- Per-module counts: test_faults 28, test_runtime_boundaries 52, test_invariants 63, test_golden_trace 9, test_transitions 46, test_rerun_replay / test_adversarial_verifier / test_verifier_dependencies 1 each (B4+ placeholders).

### TDD Cycle Evidence

| Task | RED (observed) | GREEN (observed) | Notes |
|------|----------------|------------------|-------|
| T-10 | `ModuleNotFoundError: robot_factory_arena.runtime.runner` (2 loader errors; 120 tests ran) | 166 OK | RunConfig locked-shape enforcement (budget pinned at 100, canonical seed, sorted grants, digest format); initial terminal check completes with 0 attempts; reserve-before-parse (malformed candidate consumes step + violation); grantless/domain rejections vs hygiene accounting; no 101st observation (decide called exactly 100x); step-100 terminal precedence (Counters(100,1,1,0,99,101) completed); policy-initiated key replay = hygiene violation; serialized-observation-only input (+ mutation cannot corrupt the next observation); evidence stream ordering; logical identity deterministic and execution-id-free; runner input validation |
| T-11 | fixture absent → 6 errors (7 tests ran); then one order-order assertion failure caught during GREEN | 172 OK | `fault_targets.json` frozen in canonical bytes with derivation provenance; re-simulation from SERIALIZED observations only (mirror of the runner loop through `apply`) reproduces all 4 frozen targets; runner-level confirmation via evidence callbacks; determinism; helper-deleted assertion |
| T-12 | `ImportError: faults.model / faults.injector / resolve_fault_tape` (5 errors; 169 ran) | 191 OK | exact REQ-05 resolved shape {fault_id, operation_key, hook, kind}; semantic spec carries NO operation_key (locked decision 4); two-stage expansion validates against the grammar; identity deterministic/execution-independent; unknown kinds/hooks/ids and ambiguous targets (non-place intents, step 0) rejected; one-shot injector; before_commit never fires in v0; no mutable authority; faults-module AST allowlists (no runtime/policies/domain imports) |
| T-13 | `ImportError: ResponseLostError` + boundary/runner tests failing (201 loaded, 2 failures + 25 errors pre-fix) | 201 OK | ToolBay boundary fires after store_first (ledger-visible inside the callback) and before return; only fresh commits invoke it; "response_lost" signal → ResponseLostError (committed response never observed) while the ledger entry stays replayable; boundary ordering evidence (persist → fault → retry); retry reuses the EXACT call/key, returns idempotent_replay with reason None and no events; counters Counters(1,2,1,1,0,3) — policy_steps untouched by the retry; exactly one boundary/delivery/event, `completed_orders.count == 1`; step-100 faulted completion precedence (Counters(100,2,1,1,99,102) completed); boundary durability failure → unresolved_outcome, never success, no retry |

Triangulation catch during T-13 GREEN: an autofix had re-indented tool_bay's fresh-commit block inside the `if not parse.ok:` body — syntactically valid, unreachable after its return, so `submit` returned `None` on every commit. Caught by the gate (25 errors) and by the checker's return-path analysis; fixed and covered by all T-13/B2 gateway tests.

### Completed tasks

- **T-10 done** — `runtime/runner.py`: `RunConfig` (exact locked keys; budget locked at 100), `compute_logical_run_id` (lr_+ 64 hex over canonical semantic identity: task_version, seed, config_identity, policy manifest, budget, sorted grants, semantic fault descriptor — no execution id), `EvidenceCallbacks` (10 optional callables; B4 seam), `RunResult` (+ `fault_tape` in T-13), and the lifecycle: free initial + post-result terminal checks, reserve-before-parse, parse-level ledger duty (design flow step 6), protocol-violation accounting, no 101st observation, step-100 precedence, `safe_abort` kill switch (loop guard + defensive BudgetExhaustedError mapping). Done-check verified: fake policy runs success/rejection/budget cases with exact counters; mutable state never crosses the policy boundary; policy never invoked after exhaustion.
- **T-11 done** — `tests/fixtures/fault_targets.json`: computed constants (step **8**, intent **place-order-00**) for {17, 29} × {greedy-v0, bounded-v0}, derived by temporary script (**deleted**) that fed only serialized observations to the policies and advanced the world via `domain.transition.apply`. Tests re-simulate all four targets and confirm them through the real runner lifecycle. **Discovery: full greedy/bounded runs need 190 policy steps > 100 budget → full-run golden evidence (B4/B6) will be `safe_abort` by design; the faulted delivery at step 8 happens well before exhaustion.** No policy is called by fault resolution (resolver takes only spec + run id; AST-tested).
- **T-12 done** — `faults/model.py` (constants + frozen `Fault`/`FaultTape`/`FaultDecision`/`FaultContext`), `faults/tape.py` (`FaultTarget`, `FaultTapeSpec`, `resolve_fault_tape` — two-stage semantic→concrete resolution via `expand_operation_key`), `faults/injector.py` (`FaultInjector` — one-shot, response-only, `before_commit` never fires in v0). Unknown kinds/hooks/ids and ambiguous targets rejected at construction. Done-check verified.
- **T-13 done** — `runtime/tool_bay.py` extended with `on_commit_boundary` (fresh commits only; after `store_first`, before return; returns None or `"response_lost"` → `ResponseLostError`) and `runtime/runner.py` fault integration (boundary handler ordering, exact retry, recovery from proven boundary evidence, unresolved classification). `contracts/outcomes.py` needed NO extension. Done-check verified: faulted fixture completes with one effect, one retry, no duplicate reward, explicit outcome.

### Files changed (9, +2,394/−22)

- New: `runtime/runner.py`, `faults/{model,tape,injector}.py`, `tests/fixtures/fault_targets.json`
- Extended: `runtime/tool_bay.py` (boundary + ResponseLostError), `tests/{test_faults,test_runtime_boundaries,test_invariants}.py`
- `contracts/outcomes.py`: untouched (no extension needed)

### Deviations / interpretations (documented in code docstrings)

1. **`contract_violations` = protocol violations** (REQ-02): the seven closed parse/grant/conflict reasons, plus policy-initiated ledger replays (key reuse across distinct policy actions = hygiene violation, REQ-03). Domain-legality rejections (e.g. `robot_not_at_shelf`) consume the reserved step but are not violations. Internal retries are exempt.
2. **`committed_effects` counts at the PROVEN commit boundary**: the boundary handler persists evidence first, then records the commit — so a boundary durability failure leaves the counter at 0 and classifies `unresolved_outcome` (never success).
3. **Response-loss model**: ToolBay *raises* `ResponseLostError` — the committed response is genuinely unobservable, not ignored. The post-fault terminal check uses the runner's last observed (pre-commit) state; the retry replays `idempotent_replay` (caller's state, reason None); state recovery adopts the durable boundary record only (design steps 9–10). Every response the runner received stays response-blind; adoption is always evidence-justified.
4. **`FaultTape.identity` is a derived property** (canonical digest of the resolved mapping), not a stored field — cannot drift; design-sketch deviation documented.
5. **`FaultTapeSpec`/`FaultTarget` landed with T-10** (in `faults/tape.py`) because RunConfig's locked schema requires the semantic-descriptor type; resolver/injector followed in T-12.
6. Both fixture seeds coincidentally derive the same target (order-00's source shelf is at (1,2) for q=7 and q=9 alike); the values are genuinely computed per seed and every test re-simulates them.

### B4 handoff — evidence-callback interface shape (trace/artifact writers implement this)

- **Seam:** `EvidenceCallbacks` (frozen dataclass, `runtime/runner.py`), passed as `Runner(evidence=...)`. All fields optional callables; B4 implements the subset it needs. Exact signatures:
  - `on_run_started(logical_run_id: str, config: RunConfig)`
  - `on_observation(policy_step: int, serialized_observation: Mapping)`
  - `on_policy_decision(policy_step: int, candidate: object)` — the raw untrusted candidate
  - `on_parse_rejected(policy_step: int, reason: str, operation_key: str)` — key is `""` when the candidate had no valid key for the run (B4 synthesizes `tool_rejected` with null tool/arguments)
  - `on_tool_result(policy_step: int, call: ToolCall, result: ToolResult)` — every OBSERVED gateway result (committed / rejected / policy-initiated replay). **A response-lost result NEVER arrives here** — its durable data arrives via `on_commit_boundary`.
  - `on_commit_boundary(policy_step: int, call: ToolCall, result: ToolResult)` — the persistence boundary; fires after `store_first`, carries the committed result with its domain events; `result.state` is the post-commit snapshot; `LedgerEntry.committed_snapshot_digest` was precomputed at store time and is reusable.
  - `on_fault_injected(policy_step: int, fault_id: str, operation_key: str)` — fires AFTER the boundary evidence, only for a resolved tape hit
  - `on_retry(policy_step: int, call: ToolCall, result: ToolResult)` — the internal retry's `idempotent_replay` result (same step number as the faulted attempt)
  - `on_budget_exhausted(policy_steps_used: int)`
  - `on_run_finished(outcome: str, counters: Counters, final_state: WorldState)`
- **Emission order:** run_started → (observation → decision → [parse_rejected | tool_result | (commit_boundary → fault_injected → retry)])* → [budget_exhausted] → run_finished. Boundary ordering inside a commit: sink persist → proof → `record_commit()` → injector consult.
- **Identity stability:** `compute_logical_run_id(task, config)` inputs are frozen — {budget, config_identity, fault_tape (semantic mapping or null), grants, policy_digest, policy_id, seed, task_version}; B4 must not change them (digest stability). `RunConfig.to_mapping()` is the locked `config.json` form (semantic spec inside); the RESOLVED tape is `RunResult.fault_tape` (`fault_tape.json` form with concrete keys). The runner already resolves the tape before the first observation.
- **Outcome contract:** runner produces only `completed` / `safe_abort` / `unresolved_outcome`; `duplicate_effect` stays verifier-owned (B5). Golden full-run evidence will be `safe_abort` (190-step requirement vs 100 budget).
- **Module boundaries (AST-tested upper bounds):** runner imports ⊆ {contracts.*, domain.{model,observation,task}, evidence.canonical, faults.*, runtime.{budget,grants,ledger,tool_bay}} and is forbidden from policies/verification/cli/`domain.transition`/evidence.{trace,receipt,replay,artifact_store,report} — B4 wires evidence exclusively through the callbacks. Faults modules import only contracts/evidence.canonical/each other.
- `RESPONSE_LOST` and `ResponseLostError(operation_key, reason)` are exported from `runtime.tool_bay`; B5's `commit_boundary_unproven` verifier classification is foreshadowed by `CommitBoundaryUnprovenError` (runner).

### Workload / PR boundary

Single PR, `size:exception` accepted (session decision). B3 = 2,394 net added lines in one work-unit commit (cumulative branch ≈ B1 2,526 + B2 2,150 + B3 2,394 + docs). Review order suggestion: runner lifecycle + tests → fault fixture + re-simulation → faults modules → boundary/retry integration.

### Structured status / environment notes

- Consumed non-authoritative SDD status (artifact store: `engram`); readiness resolved from the materialized `docs/sdd/{tasks,spec,design}.md` files; B1+B2 handoff constraints honored as hard inputs (ToolResult contract, retry protocol, boundary callback shape, runner-side parse-level ledger duty, BudgetExhaustedError→safe_abort, trace closure deferred to B4).
- pi-lens: recurring false positives (repo-root import resolution — disproven by direct import and green gates; `spawn python ENOENT` — its runner invokes `python`, REQ-08 locks `python3`). One REAL catch this batch: its return-path analysis surfaced the autofix-induced tool_bay indentation defect (see triangulation note). Authoritative verification remains the documented unittest command.
- Helper scripts: the T-11 derivation script lived in /tmp and was deleted after freezing the fixture (asserted by `test_documented_derivation_method`).

### Remaining tasks (unchecked)

- [ ] T-14..T-17 (B4 trace/receipt/store/replay), T-18..T-21 (B5 verifier/adversarial/denylist), T-22..T-25 (B6 report/CLI/golden/final gate)

### Notes / risks for B4

- Full-run golden evidence is `safe_abort` at step 100 (policies need 190 steps): T-24/T-25 golden artifacts and rerun equality must account for this; the response-loss fixture (step 8) completes well inside the budget.
- Observations carry `BudgetView.used` = steps consumed BEFORE the attempt; policies label keys `used+1`. The runner reserves immediately after `policy.decide` returns.
- Parse-level rejections with a valid key are ledger-stored runner-side (design step 6); B4's operation log should treat them like ToolBay-stored rejections (`on_parse_rejected` provides step/reason/key).
- The boundary handler wraps the evidence sink: a sink exception becomes `CommitBoundaryUnprovenError` → `unresolved_outcome`. B4's artifact writer must therefore never raise for expected control flow — only genuine durability failures.
- `Ledger` still exposes only `__len__`/`__contains__`; entry snapshots for the operation log should flow through the boundary/tool-result evidence callbacks (no new Ledger surface added speculatively in B3).

## Batch B4 — T-14..T-17 — COMPLETE, gate green

- **Branch:** `feat/v0-evidence-chain`
- **Code commit:** `a5017e6` — `feat(evidence): B4 hash-chained trace, verified receipt, artifact store, and offline replay` (9 files, +3,452/−3)
- **Delivery path:** single PR with user-accepted `size:exception`; one work-unit commit for code+tests, docs follow-up commit.

### Test gate (REQ-08)

- Command: `python3 -m unittest discover -s tests -v` (Python 3.14.5)
- Final result: **Ran 271 tests — OK — 271 pass / 0 failures / 0 errors, exit 0.** (B3 baseline 201 → 271: +70)
- Per-module counts: test_golden_trace 45, test_invariants 77, test_runtime_boundaries 56, test_faults 32, test_rerun_replay 13, test_transitions 46, test_adversarial_verifier 1 + test_verifier_dependencies 1 (B5 placeholders).

### TDD Cycle Evidence

| Task | RED (observed) | GREEN (observed) | Notes |
|------|----------------|------------------|-------|
| T-14 | `ModuleNotFoundError: evidence.trace` (module loader errors; suite could not load the golden module) | 215→219 OK | Exact 13-field REQ-03 record shape (spec wins over the tasks.md "14-field" prose miscount — see deviations); seq/prev chain; event_hash = digest of record minus event_hash; LF-terminated canonical lines; digest only after close and stable; close requires run_finished final; post-run_finished/post-close appends rejected; `verification_finished` never appendable; recursive `execution_id` guard on payloads/arguments. Runner-side closure proven for completed/safe_abort/unresolved via an in-test trace collector (`_TraceCollector`) through real `EvidenceCallbacks` emissions — run_finished always last, faulted order boundary→fault→retry→finished. |
| T-15 | `ModuleNotFoundError: evidence.receipt` (2 loader errors; 197 ran) | 232 OK | Receipt = exact 19 REQ-03 keys + `verification` block (7 keys); four-outcome enum; distinct logical/execution ids; all digest formats; candidate carries NO score field (runtime claims structurally excluded); released outcome is always the verifier's; invalid verification releases NO receipt (`ReceiptNotReleasedError`); valid-but-scoreless cannot release; float scores rejected at the VerificationResult contract (TypeError); canonical-byte + determinism + execution-id-exclusion invariants; receipt drift (seed/version/schema_version/budget/grants) rejected. |
| T-16 | `ModuleNotFoundError: evidence.artifact_store` (3 loader errors; 182 ran) | 257 OK | Locked `artifacts/runs/<lr>/<exec>/` layout with the 7 pre-final artifacts, all canonical bytes; trace.jsonl ends run_finished with recorded digest binding; receipt candidate digests == persisted artifact digests; op log matches the locked schema exactly (submission order); replay-input carries identity/ops/null tape; boundary file `boundaries/step-1.json` proves the crossing (marker/snapshot/events/op record) and is storage-only (excluded from digests); path/execution-id independence (byte-identical artifacts across roots/executions); faulted run: tape persisted BEFORE execution with concrete key, ops preserve commit→fault→retry evidence, unresolved sink failure leaves NO commit evidence and never upgrades to success. Evidence-module AST allowlists + no-RNG/time imports. |
| T-17 | missing `evidence.replay` (14 errors: rerun_replay loader + invariants boundary tests) | 271 OK | Locked ReplayInput enforced (top/nested/op/event/fault-tape key drift, enums, digest formats, run-id-embedded keys, policy steps 1..100 non-decreasing, committed⇔events/state_after, canonical JSON gate rejects floats); policy-free: raising `_RaisingPolicy` never called (`policy` tripwire parameter) + AST allowlist (no policies/runtime/verification/domain.model imports); replay reproduces recorded final digests for completed (seed 18, 92 ops), faulted (seed 18 tape at step 8, 93 ops incl. the no-op retry), and safe_abort (seed 17 full run, ops reaching the budget); seed/config binding, tampered events → `ReplayInconsistentError`; digest mismatch with a receipt is REPORTED (`matches_receipt False`), never upgraded; replay-result.json written canonically through the artifact store. |

### Completed tasks

- **T-14 done** — `evidence/trace.py`: append-only hash-chained JSONL trace; exactly the 13 REQ-03 fields; closure ordering frozen in the writer (`close()` requires `run_finished` last; appends after run_finished/close rejected; `digest()` unavailable before close, stable after); `verification_finished` rejected outright (locked decision 3). Runner wiring is callback-exclusive — `runtime/runner.py` needed NO changes (B3's `EvidenceCallbacks` emission order already ends run_finished-last for all three classifications; proven by tests). Done-check verified.
- **T-15 done** — `evidence/receipt.py`: frozen scoreless `ReceiptCandidate` (16 validated fields), `ReceiptWriter.write_final` (requires a real `VerificationResult`; invalid ⇒ `ReceiptNotReleasedError`; released outcome = verification's), exact `verification` block with the post-verification `verification_finished` AUXILIARY record — never a trace record. `contracts/outcomes.py` needed NO extension (Counters/VerificationResult/OUTCOMES reused as-is). Done-check verified: no release without a VerificationResult; no trace records after the bound digest.
- **T-16 done** — `evidence/artifact_store.py`: `ArtifactStore`/`RunArtifactWriter` (atomic temp+flush+fsync+replace + directory fsync per file; per-line fsynced trace appends; boundary files path-addressed and digest-excluded) + `RunEvidenceRecorder`, the full 10-callback `EvidenceCallbacks` implementation: config/task/resolved-tape persisted BEFORE execution, boundary proof persisted FIRST at the commit boundary, locked OperationLog + ReplayInput artifacts at finish, scoreless `ReceiptCandidate` after trace closure. The writer never raises for expected control flow (a sink exception propagates and the runner classifies `unresolved_outcome`). Done-check verified: run directory contains all pre-final artifacts and proves the response-loss boundary without exactly-once claims.
- **T-17 done** — `evidence/replay.py`: locked-schema `ReplayInput` parsing (fail-closed) + policy-free `replay()` that regenerates the task from recorded identity and re-applies recorded committed ops through `domain.transition.apply`, verifying recorded events/state digests per step; `ReplayResult` with receipt agreement reporting and canonical `replay-result.json` form; consistency contradictions raise `ReplayInconsistentError`. Done-check verified: consumes only declared artifact data, never imports `policies` or asks for a decision, reproduces the faulted run's final state.

### Files changed (9, +3,452/−3)

- New: `evidence/{trace,receipt,artifact_store,replay}.py`
- Tests: `tests/test_golden_trace.py` (+739), `tests/test_invariants.py` (+296), `tests/test_runtime_boundaries.py` (+216), `tests/test_rerun_replay.py` (+389/−3), `tests/test_faults.py` (+164)
- `runtime/runner.py`: untouched (B3 callbacks sufficient — zero direct evidence imports; AST allowlist unchanged and green). `contracts/outcomes.py`: untouched.

### Deviations / interpretations (documented in code docstrings + tests)

1. **Trace records have 13 fields, not "14"**: tasks.md prose says "14-field record shape"; the binding spec (REQ-03) and design both enumerate exactly 13 (`schema_version…event_hash`). The spec's field list wins; frozen by `test_record_has_exactly_the_thirteen_req03_fields`.
2. **Commits are traced at the persistence boundary** (design flow step 8 ordering): `on_commit_boundary` writes boundary proof → op record → `tool_committed` + domain-event trace records, BEFORE the fault/response. The observed committed `on_tool_result` adds no second record. A response-lost commit therefore has no `tool_committed` trace record — its proof is the boundary file + op record (REQ-05 semantics).
3. **Policy-initiated idempotent replays trace as `tool_rejected`** with payload `{status: "idempotent_replay", reason}`: the closed REQ-03 trace vocabulary has no observed-replay event; the op log carries the exact status. No world effect either way.
4. **The recorder resolves the fault tape independently** (`resolve_fault_tape(spec, logical_run_id)`) — the same pure function the runner uses — so `fault_tape.json` persists the RESOLVED tape before execution without changing the frozen `on_run_started` signature.
5. **Retry ops carry the fault evidence** (`fault_ids=[fault_id]`, `retry=true`): the already-persisted boundary record is never rewritten after the fault fires (append-only durability).
6. **Replay never recomputes scores** (verifier-owned, REQ-06): `receipt_verified_score`/`receipt_outcome` pass through from the receipt; replay reports digest/outcome agreement and classifies `completed`/`safe_abort`/`unresolved_outcome` from recorded evidence only.
7. **Replay tests record real formula-task runs**: replay regenerates the task from the recorded seed, so hand-built tasks correctly fail the `state_before` binding. Completed-run replay tests use **seed 18** (greedy completes in 92 steps ≤ 100; first delivery step 8, `place-order-00`) — consistent with AMEND-01's deterministic selection rule (expected winner 18). B6/T-24 still owns freezing the fixture; nothing new was frozen here.
8. **Receipt verification-block `status`** is `"valid"` for every RELEASED receipt (invalid verification releases nothing at all).

### Notes for B5 (verifier contract inputs)

- **Exact receipt `verification` block** (7 keys, released only for valid verification):
  `{status: "valid", verifier_version, verified_score: int, completed_count, total_count, violations: [{code, location, detail}...] (sorted), verification_finished: {event: "verification_finished", schema_version: 1, payload: {outcome, verified_score, verifier_version, violations_count}}}`. The block is fully derived from `VerificationResult` fields by `ReceiptWriter._verification_block` / `verification_finished_record` — B5 needs no new contract; produce a `VerificationResult(valid=…, outcome∈OUTCOMES, verified_score: int|None (int required when valid), completed_count, total_count, violations: sorted tuple[VerifierViolation], verifier_version)` and call `ReceiptWriter.write_final(candidate, verification)`.
- **`VerificationArtifacts` composition for `Verifier.verify`** — every field comes from the B4 run directory (parsed canonical bytes; re-verify candidate digests first):
  - `task` ← `task.json`; `config` ← `config.json` (semantic RunConfig; `config_digest` binds it);
  - `grants` ← `receipt["grants"]` (sorted capability list) — cross-check `config.json.grants`;
  - `trace_records` ← parsed `trace.jsonl` (verify `trace_digest` against `receipt.trace_digest` and the per-record chain: seq/prev/event_hash rules in `evidence/trace.py`);
  - `final_state` ← `final_state.json` (bound by `receipt.final_state_digest`);
  - `operation_log` ← `operation-log.json` (bound by `receipt.operation_log_digest`; exact locked op shape; `state_before`/`state_after` chain reconstructable independently);
  - `fault_tape` ← parsed `fault_tape.json` or None (bound by `receipt.fault_tape_digest`; RESOLVED form with concrete keys; semantic spec lives inside `config.json.fault_tape_spec`);
  - `replay_input` ← `replay-input.json`; `receipt` ← `receipt.json` (None until the verifier releases it — B6 CLI composes `VerificationArtifacts` twice: verify → write receipt → verify bindings again if desired).
- **Commit-boundary proof for REQ-05 classification**: `boundaries/step-<policy_step>.json` per commit, containing `{schema_version, policy_step, operation_key, tool, arguments, state_snapshot, events, operation_record, commit_marker{commit_proven: true, snapshot_digest}}`. These files are path-addressed storage, excluded from all semantic digests — the verifier should treat a missing/ambiguous boundary for a claimed commit as `commit_boundary_unproven` → `unresolved_outcome`. Note the response-lost commit has NO `tool_committed` trace record by design (see deviation 2) — its proof set is boundary file + op record + the paired `idempotent_replay` retry op with `fault_ids`.
- **Budget/golden expectations**: full greedy/bounded runs on seeds 17/29 are `safe_abort` (190 steps needed vs 100). The completion demonstrator is seed 18 (92 greedy steps; first delivery step 8 `place-order-00`; first delivery for bounded also step 8) — AMEND-01 candidate list confirms 18 wins for greedy; T-24 freezes it.
- **Available for reuse**: `TraceWriter` enforces the chain rules (B5 re-derives, never imports it — denylist stays intact); `ReplayInput.from_mapping` is fail-closed for artifact-only input validation; `evidence.canonical` remains the only shared dependency.

### Remaining tasks (unchecked)

- [ ] T-18 Build the independent verifier model and structural invariants
- [ ] T-19 Implement verifier-owned integer scoring and structured violations
- [ ] T-20 Complete the artifact release gate and fault ambiguity classifications
- [ ] T-21 Add the full adversarial suite and import denylist gate
- [ ] T-22..T-25 (B6 static report, CLI run/rerun/replay, golden fixtures, final gate)

### Workload / PR boundary

Single PR, `size:exception` accepted (session decision). B4 = 3,455 net changed lines in one work-unit commit (cumulative branch ≈ B1 2,526 + B2 2,150 + B3 2,394 + B4 3,455 + docs). Review order suggestion: trace chain + closure tests → receipt release gate + verification block → artifact store layout/recorder ordering + boundary proofs → replay validation/walk + policy-free proofs.

### Structured status / environment notes

- Consumed non-authoritative SDD status (artifact store: `engram`); readiness resolved from the materialized `docs/sdd/{tasks,spec,design}.md` files; B3 handoff constraints honored as hard inputs (callback-exclusive wiring, trace closure, receipt-before-verification, artifact-store non-raising, replay policy-free).
- pi-lens: the established false-positive patterns recurred throughout the batch (repo-root import resolution — disproven by direct `python3 -c` imports and every green gate; `spawn python ENOENT` — its runner invokes `python`, REQ-08 locks `python3`; type-checker flags on intentional negative tests inside `assertRaises`, frozen-dataclass mutation probes, and duck-typed `**callback_map()` wiring). Its static analysis DID catch three real test defects this batch — a missing `FrozenInstanceError` import, a hand-mis-sorted expected key list, and an unused parameter on a stub helper — all fixed and covered by the green gate. Authoritative verification remains the documented unittest command.
- Real triangulation catches at GREEN (tests corrected to spec, implementations already correct): pi-lens's autofix initially reordered imports in closure tests and dropped a helper; one mid-session partial-apply was repaired from the authoritative file content. The runner/fault machinery needed zero implementation changes for B4.
- Engram remains the authoritative artifact store; this file is the session-authoritative progress record per batch instruction. T-14..T-17 completion is recorded here and (best effort) in the Engram apply-progress observation.

### Notes / risks for B5

- `verification/` may import ONLY `evidence.canonical` plus its own modules (denylist: no `runtime`, `policies`, `domain.transition`, runtime scoring); AST denylist tests exist as placeholders (`test_verifier_dependencies`, `test_adversarial_verifier` — 1 discovery test each) and must fail closed once populated.
- Forged-artifact fixtures must NOT construct Runner/ToolBay/policies: build raw artifact mappings (hand-built or tampered copies of recorded shapes) and feed `Verifier.verify` directly.
- `Counters` snapshots arrive via `receipt["counters"]` (six ints); cross-check against the op log (tool_attempts = submissions incl. retries, committed_effects = proven boundaries, internal_retries = retry-flagged ops, policy_steps = max non-retry policy_step, terminal_checks = checks, contract_violations = protocol reasons).
- Tamper vectors to cover per T-20/T-21 map 1:1 to B4's fail-closed paths: edit a trace line (chain/`trace_digest` breaks), swap `final_state.json` (digest binding), flip an op status (replay/verifier inconsistency), drop a boundary file for a committed op (unproven → `unresolved_outcome`), self-report `verified_score` (candidate carries none; only the verifier writes it).
- Keep `docs/sdd/amendments.md` AMEND-01 in mind: T-24 freezes the completion seed entry in `tests/fixtures/fault_targets.json` (B4's replay tests derive seed 18's target in-test and freeze nothing).

### B4 addendum — pi-lens import-resolution fix (orchestrator recommendation)

- Root cause identified for the recurring "Import could not be resolved" false positives: pi-lens's Python support **is pyright** (`docs/lsp-capability-matrix.md`, python → pyright), and this repo has no `pyrightconfig.json`, so pyright runs without a project anchor and cannot resolve repo-root packages (`robot_factory_arena.*` from `tests/`). Every flagged import executes in each green gate run (the suite is fully green), so these are static-resolution gaps, not code defects.
- **Recommended fix (out of B4's allowed edit surfaces — orchestrator call):** add a repo-root `pyrightconfig.json` anchoring the project, e.g. `{"executionEnvironments": [{"root": ".", "extraPaths": ["."]}], "include": ["robot_factory_arena", "tests"]}` (plus `"typeCheckingMode": "standard"` or `"basic"` to keep the negative-test noise down). Precedent: the B1 `.gitignore` recommendation, implemented by the orchestrator in `6f1c509`. This should eliminate most import flags for B5/B6.


## Batch B5 — T-18..T-21 — COMPLETE, gate green (continuation of a crashed run)

- **Branch:** `feat/v0-evidence-chain`
- **Code commit:** `c9fbedc` — `feat(verification): B5 independent verifier, scoring, adversarial suite, and denylist gate` (8 files, +4,414/−150)
- **Delivery path:** single PR with user-accepted `size:exception`; one work-unit commit for code+tests+fixtures, docs follow-up commit.
- **Continuation context:** a previous B5 agent crashed on a connection error leaving UNCOMMITTED work: `verification/{verifier,invariants,scoring}.py` + three modified test files, gate green at 302, no B5 progress section. This run assessed, kept, fixed, and completed that work — nothing was blindly redone.

### Test gate (REQ-08)

- Command: `python3 -m unittest discover -s tests -v` (Python 3.14.5)
- Final result: **Ran 310 tests — OK — 310 pass / 0 failures / 0 errors, exit 0.** (B4 baseline 271 → 310: +39)
- Per-module counts: test_golden_trace 45, test_invariants 86, test_runtime_boundaries 56, test_faults 35, test_rerun_replay 13, test_transitions 46, test_adversarial_verifier 21, test_verifier_dependencies 8.

### Continuation findings (what existed vs what this run completed)

- **Kept as-is (crashed run, verified sound):** `invariants.py` (independent shapes + `independently_apply` REQ-04 semantics), `scoring.py` (integer floor score), `verifier.py` phases 0–7/9/10 (task/config identity, logical-id agreement, trace chain re-derivation, op-log validation, fault-tape two-stage agreement, reconstruction walk with boundary proofs, replay/receipt bindings, trace cross-checks, classification), and the T-18/T-19 tests (reconstruction, scoring table, violation contract).
- **Gaps found and closed this run (the crashed verifier had three real holes):**
  1. `score_mismatch` was documented in the module docstring but NEVER emitted — a forged receipt claiming `verified_score=100` on evidence scoring 20 passed silently. Fixed with phase 8.5 `_check_receipt_claims` (receipt score vs independently recomputed score → `score_mismatch`; receipt outcome vs independent classification → `artifact_mismatch`).
  2. The reconstructed final state was never compared to `final_state.json` — swapping `final_state.json` AND reforging `run_finished.state_after` (re-chained) slipped through every other binding. Fixed with phase 6.5: reconstruction digest vs final_state.json digest → `artifact_mismatch` (trace-versus-final mismatch).
  3. Receipt `outcome` was only enum-checked, never compared to the classification (self-reported success vector). Fixed in the same phase 8.5 check.
- **Missing deliverables created this run:** `tests/fixtures/adversarial_artifacts.json` (T-21: ten frozen named reward-hacking cases with contracted valid/outcome/codes), `TestNamedRewardHackingFixtures` (canonical-bytes gate on the fixture + per-case contract assertions + forged-score-never-official), the synthetic denylist fail-closed test, and the T-20 end-to-end release-gate tests on a REAL recorded formula run.

### TDD / verification evidence (continuation run)

| Task | Evidence (observed this run) |
|------|------------------------------|
| T-18 | Reconstruction suite green (legal prefix verifies honestly: 1/5 orders, score 20, no violations; deterministic re-verify; frozen result); mappings-only inputs (`TypeError` on non-VerificationArtifacts); AST denylist fail-closed incl. NEW synthetic forbidden-import tests (runtime.tool_bay, runtime.scoring, policies.greedy, domain.transition, evidence.trace, runtime.runner each flagged by name; allowlist set clean). |
| T-19 | Score table + raw numerator/denominator + integer-only + float/bool rejection green; violation objects exactly `{code, location, detail}` sorted by the three fields, matching `verifier_violation_schema.json` (enum-locked); NEW: `score_mismatch` proven emitted (partial-credit fixture: 60 claimed vs 20 recomputed) and forged scores never official (`verified_score is None` on invalid evidence). |
| T-20 | NEW end-to-end release gate on a REAL seed-18 formula run (greedy, tape at step 8/place-order-00, faulted delivery, completes all 5 orders): `load_verification_artifacts` → verify → valid/completed/100 → `ReceiptWriter.write_final` releases the receipt with the exact `verification` block (status valid, post-verification auxiliary `verification_finished` record) → persist `receipt.json` canonically → re-verify bindings green (the B6 CLI composition order, proven). Missing `boundaries/` → invalid/`unresolved_outcome`/no score + `commit_boundary_unproven` + `ReceiptNotReleasedError` (an artifact lacking commit proof cannot be labeled completed). Non-canonical artifact bytes rejected at load. |
| T-21 | `adversarial_artifacts.json` frozen canonical; ten named vectors each produce their contracted result: self_reported_success (invalid, score_mismatch+artifact_mismatch), duplicate_reward_key_reuse (valid duplicate_effect, operation_key_conflict+order_already_completed), teleport_illegal_transition (invalid, robot_not_at_shelf), trace_final_mismatch (invalid, artifact_mismatch), budget_bypass (invalid, artifact_mismatch), unauthorized_effect (invalid, missing_grant), seed_config_substitution (invalid, artifact_mismatch), idempotency_failure (invalid, artifact_mismatch), artifact_substitution (invalid, artifact_mismatch), partial_credit_gaming (invalid, score_mismatch). No runtime object is ever constructed (mappings + the pure apply() oracle on the TEST side only). |

Triangulation catch during the continuation: the fixture-driven duplicate-reward builder initially minted a NEW step-9 key — the verifier correctly saw a fresh illegal commit (5 counter violations) instead of a same-key duplicate; fixed by resubmitting the STORED key (real broken-ledger shape). Also caught: the first end-to-end draft used a hand-built task and the verifier correctly rejected it (`task.json does not match the frozen REQ-01 formulas`) — the test was corrected to the real formula run, the verifier was already right.

### Completed tasks

- **T-18 done** — `verification/invariants.py` (re-declared locked shapes, canonical trace digest, `independently_apply`) + `verification/verifier.py` (artifact-only `Verifier.verify` over `VerificationArtifacts` mappings; imports restricted to contracts.outcomes, evidence.canonical, domain.task, siblings — AST-enforced). Done-check verified: mappings only; no runtime/policies/domain.transition/runtime-scoring imports (fail-closed, synthetically proven).
- **T-19 done** — `verification/scoring.py` (`floor(100*completed/total)`, raw counts retained, floats/bools rejected) + structured sorted violations; NEW receipt-claim checks make `score_mismatch` real. Done-check verified: only the verifier creates the verified score; the receipt candidate is scoreless; violation serialization matches the locked fixture.
- **T-20 done** — release gate completed: bindings + reconstruction + classification + receipt claims all pass before `ReceiptWriter.write_final` releases; ambiguity (missing/unprovable boundary) is never upgraded; duplicate effects classify `duplicate_effect`; `unresolved_outcome` carries no score. Done-check verified end-to-end on real artifacts.
- **T-21 done** — frozen named fixtures + full adversarial suite + denylist gate (real-source scans AND synthetic fail-closed proof). Done-check verified: every mandatory adversarial case yields a deterministic violation; the dependency test fails closed.

### Files changed (8, +4,414/−150)

- New: `robot_factory_arena/verification/{verifier,invariants,scoring}.py`, `tests/fixtures/adversarial_artifacts.json`
- Tests: `tests/test_adversarial_verifier.py` (+1,143 net incl. the fixture suite), `tests/test_invariants.py` (+599 net: scoring + violation contract), `tests/test_verifier_dependencies.py` (+317 net: allowlist/denylist + forged-no-runtime + synthetic fail-closed), `tests/test_faults.py` (+118 net: T-20 release-gate class)
- Untouched: `runtime/`, `evidence/`, `policies/`, `domain/`, `contracts/`, `cli.py` (does not exist yet), README — B4 artifacts required zero changes; the verifier consumed them as-is.

### Deviations / interpretations (documented in code docstrings)

1. **Phase ordering inside `verify`**: receipt bindings (phase 7) run before classification (phase 8) and receipt-claim checks (phase 8.5) before trace cross-checks (phase 9) — the receipt score/outcome comparison needs the classification, and the final-state comparison needs the walk. All findings accumulate into one fail-closed result regardless of order.
2. **`final_digest` added to the `_walk` result**: the reconstruction's final state digest is returned for the phase 6.5 binding check; a duplicate-commit chain stop leaves the last good state, so duplicate_effect evidence still binds cleanly.
3. **Grant cross-check source**: `config.grants` is authoritative; the optional `VerificationArtifacts.grants` field exists for callers that want to assert a second declaration; receipt grants are cross-checked against both when a receipt is present.
4. **Payload-key expectations** in the verifier match the B4 recorder exactly (proven end-to-end this batch): run_started {budget,grants,policy_digest,policy_id}, observation_emitted {observation_digest}, tool_committed {status}, tool_rejected/retry {reason,status}, fault_injected {fault_id}, run_finished {counters,outcome}, domain events {data}; policy_decision payloads are unchecked (candidate shape is runtime-side).
5. The locked schemas were NOT changed. `verifier_violation_schema.json` was already correct (its enum already contained `score_mismatch`); the crashed implementation simply never emitted it.

### Workload / PR boundary

Single PR, `size:exception` accepted (session decision). B5 = 4,564 net changed lines in one work-unit commit (cumulative branch ≈ B1 2,526 + B2 2,150 + B3 2,394 + B4 3,455 + B5 4,564 + docs). Review order suggestion: invariants (independent semantics) → verifier phases (identity → chain → walk → bindings → claims → classification) → scoring → adversarial fixtures file + suite → denylist gate → T-20 end-to-end release test.

### Notes for B6 (verified-run flow — what CLI/report must call)

- **The verified-run composition order (proven by `TestVerifierReleaseGate`)**: run via `Runner(evidence=RunEvidenceRecorder(...).callback_map())` → `recorder.close()` → `verification = Verifier().verify(load_verification_artifacts(recorder.directory))` → `receipt = ReceiptWriter.write_final(recorder.receipt_candidate, verification)` (raises `ReceiptNotReleasedError` when invalid) → persist `receipt.json` with `canonical_json_bytes(receipt)` → optionally re-verify with the receipt present (must stay valid). `robot_factory_arena.verification.verifier.load_verification_artifacts(directory)` is the one-call composition for CLI/report.
- **Report should render the VerificationResult**, not the receipt's claims: `verification.{valid, outcome, verified_score, completed_count, total_count, violations, verifier_version}`; the released receipt's `verification` block mirrors these (status is always `"valid"` for a released receipt).
- **Golden-run expectation reminder**: full greedy/bounded runs on seeds 17/29 are `safe_abort` (190 steps needed); the completion demonstrator is **seed 18** (92 greedy steps, faulted first delivery at step 8 → `completed`, score 100). T-24 freezes seed-17 public evidence per AMEND-01's entry list; the verifier classifies `safe_abort` runs validly with the `budget_exhausted` record required.
- `Verifier.verify` requires a `VerificationArtifacts` instance (TypeError otherwise) and `load_verification_artifacts` enforces canonical bytes on every declared file — CLI error paths should surface those ValueErrors verbatim.
- `boundaries/` files are storage-only (digest-excluded); deleting them flips a completed run to `unresolved_outcome` — the CLI must not treat `boundaries/` as optional when copying run directories.

### Remaining tasks (unchecked)

- [ ] T-22 Render the dependency-free static report and honesty boundary
- [ ] T-23 Implement CLI run, rerun, and replay commands
- [ ] T-24 Freeze complete golden run evidence for public seed 17
- [ ] T-25 Add evaluation-seed plumbing, rerun equality, and final v0 gate

### Structured status / environment notes

- Consumed non-authoritative SDD status (artifact store: `engram`); readiness resolved from the materialized `docs/sdd/{tasks,spec,design}.md` files; B4 handoff constraints honored as hard inputs (denylist scope, forged-artifact discipline, receipt `verification` block shape, `VerificationArtifacts` composition, boundary-proof shape, tamper-vector map).
- **Session incident (edit tooling):** the file-edit tool corrupted indentation twice while inserting into `verifier.py` (a racing formatter hook stripped leading whitespace); repaired deterministically via scripted rewrites validated by `py_compile` + the full gate. Subsequent edits were made via scripts. Also one automated ruff pass raced a manual `setattr` fix on the frozen-probe test and was re-applied.
- pi-lens: recurring documented false positives (repo-root import resolution; `spawn python ENOENT` — its runner invokes `python`, REQ-08 locks `python3`; pyright flags on intentional negative tests inside `assertRaises` and frozen-dataclass mutation probes — pre-existing B3-era patterns, accepted through B1–B4). No NEW flagged lines were introduced by B5 code. Authoritative verification remains the documented unittest command (310 green).
- Engram remains the authoritative artifact store; this file is the session-authoritative progress record per batch instruction. T-18..T-21 completion is recorded here and (best effort) in the Engram apply-progress observation; the materialized `docs/sdd/tasks.md` is outside this phase's allowed edit surfaces, so the orchestrator should mark T-18..T-21 `[x]` there or in the Engram tasks observation.
