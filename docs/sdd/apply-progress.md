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

### Remaining tasks (unchecked)

- [ ] T-07 Implement the pure transition authority
- [ ] T-08 Implement run-scoped grants, outcome types, and budget accounting
- [ ] T-09 Implement idempotency ledger and ToolBay gateway
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
