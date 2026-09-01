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

