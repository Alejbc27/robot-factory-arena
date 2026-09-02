# SDD Verification Report — robot-factory-arena v0

## Status

**FAIL — archive-blocked, with the implementation evidence otherwise largely passing.**

The v0 runtime, evidence chain, independent verifier, CLI flows, replay, fault handling, and golden artifacts were exercised successfully. Archive is blocked by two CRITICAL findings: an authoritative Engram tasks snapshot still contains an unchecked B6 implementation marker, and two discovery-only tests contain assertions that do not exercise production behavior under the strict-TDD assertion-quality rules. A WARNING also remains for the report's mislabeled actions-per-delivery denominator.

## Executive summary

- Full gate passed twice: **354 tests, 0 failures, 0 errors, exit 0** on Python 3.14.5.
- Public seed 17 `run` and `rerun` produced `safe_abort`, verified score 50, identical logical identity, distinct execution IDs, and deterministic agreement.
- Offline replay reproduced the final-state digest, score, and outcome with `receipt_agreement: match`.
- AMEND-01 seed 18 response-loss evidence completed with score 100, one retry, one fault, and one committed effect per transition; the counters correctly show 92 committed world effects and 5 completed orders.
- Two independent tamper vectors failed closed: edited trace → `artifact_mismatch`/`trace_chain_invalid`; deleted commit boundary → `commit_boundary_unproven`, `unresolved_outcome`, and no score.
- The report is static, escaped, offline, and separates the four signals. Its displayed “Actions per delivery” value is actually `tool_attempts / committed_effects`, not actions per completed delivery.

## Structured status and action context

| Field | Finding |
|---|---|
| Active change | `robot-factory-arena`, proven by `docs/sdd/*` and Engram topic keys `sdd/robot-factory-arena/*`; no competing active change found. |
| Artifact store | `engram`; native status is explicitly non-authoritative with `nextRecommended: resolve-via-engram`. |
| Required artifacts | Spec confirmed from Engram observation 6170; tasks from observation 6177; apply progress from observation 6191. The materialized files were also inspected. |
| Action context | `repo-local`; workspace `/Users/alejandrobosch/Documents/GitHub/robot-factory-arena`; allowed edit root is the repository root. Implementation ownership and target files are inside that root. |
| Dependencies/blockers | Native `dependencies`/`blockedReasons` were not treated as authoritative. Readiness was resolved from the required Engram artifacts. |
| OpenSpec config | `openspec/config.yaml` is absent; config-specific strict-TDD settings were skipped. Strict-TDD verification was nevertheless applied because the parent/session and apply-progress enabled it. |

## Specification coverage

| Requirement | Result | Evidence |
|---|---|---|
| REQ-01 | PASS | Formula-generated 10×10 tasks, seeds 17/18/29, pure greedy/bounded policies, frozen target derivation, and plumbing-only seed-29 labeling. |
| REQ-02 | PASS | Reserve-before-parse, 100-step ceiling, step-100 terminal precedence, separate counters, rejection accounting, and retry accounting are covered by tests and golden receipts. |
| REQ-03 | PASS | Canonical JSON/JSONL, schema-versioned artifacts, exact trace fields, SHA-256 chain/digests, explicit IDs, and receipt bindings pass. |
| REQ-04 | PASS | Pure transitions, grants, ToolBay gateway, rejection taxonomy, first-result ledger, replay, and key-conflict behavior pass. |
| REQ-05 | PASS | Seed-18 response-loss fault resolves before execution, persists boundary proof, retries the exact key, produces one delivery/effect, and verifies as completed. Missing proof fails closed. |
| REQ-06 | PASS | Verifier AST denylist and synthetic fail-closed tests pass; adversarial fixtures cover self-reporting, teleport, duplicate reward, artifact substitution, budget bypass, unauthorized effects, and score ownership. |
| REQ-07 | PASS | Two physical seed-17 executions have equal deterministic artifact bytes and receipt bytes after removing `execution_id`; replay is distinct and policy-free. |
| REQ-08 | PASS | `python3 -m unittest discover -s tests -v` passes twice under Python 3.14.5 with stdlib unittest. |
| REQ-09 | WARN | Required artifacts, static report, replay instruction, raw counters, digests, and limitations are present. The actions-per-delivery display is semantically mislabeled; see WARNING W-01. |
| REQ-10 | PASS | README and report preserve the honesty boundary: no authentication, exactly-once, hostile-Python sandbox, scientific REFUGIO, leaderboard, or arena claims. |

## Acceptance criteria

| # | Criterion | Result | Verification evidence |
|---:|---|---|---|
| 1 | Same seed/config/policy gives byte-identical trace and score | PASS | Seed-17 primary/rerun: same logical ID; nine deterministic artifacts byte-equal; receipt equal minus `execution_id`; agreement true. |
| 2 | Public/evaluation runs emit complete schema-versioned evidence | PASS | Seed-17 and seed-29 goldens plus generated run directories contain the required canonical artifacts and released receipts. |
| 3 | Replay without policy reproduces final digest and score | PASS | Documented replay flow returned `digest_match: true`, `outcome_match: true`, `receipt_agreement: match`; policy-import tripwire tests pass. |
| 4 | Verifier rejects a plausible cheat | PASS | Named adversarial fixtures and independent verifier reject forged score, teleport, unauthorized effect, trace/final mismatch, and substitutions. |
| 5 | Exceeding 100 steps kills/aborts reproducibly | PASS | Seed-17/29 safe-abort goldens reach exactly 100 observations/decisions and emit `budget_exhausted` without a 101st action. |
| 6 | Invalid/unauthorized calls reject, consume steps, trace, and affect hygiene | PASS | Runtime boundary, trace, receipt, and verifier tests cover malformed, invalid, unauthorized, and protocol-violation paths. |
| 7 | Reused operation key under response-loss fault has no double effect | PASS | Seed-18 faulted run records one fault and one idempotent retry; one delivery event and no duplicate effect. |
| 8 | Fault outcome uses supported enum and ambiguity is not success | PASS | Seed-18 is `completed`; deleted boundary is `unresolved_outcome` with no score; supported outcome taxonomy is enforced. |
| 9 | Receipt/trace linkage and SHA-256 chaining detect mismatch; chain is tamper evidence only | PASS | Trace mutation failed with `artifact_mismatch` and `trace_chain_invalid`; report/README state tamper evidence is not authentication. |
| 10 | Static report separates four signals with raw counters/limitations | WARN | All four sections, counters, digests, verifier status, fault details, and limitations are present; “actions per delivery” is mislabeled as attempts/effects. |
| 11 | Baseline is measurable and no scientific REFUGIO conclusion is presented | PASS | Greedy and bounded policies are frozen and deterministic; seed 29 is labeled plumbing-only; README/report disclaim scientific REFUGIO results. |

## Test and validation commands

### Full gate

Command, run 1:

```text
python3 -m unittest discover -s tests -v
```

Result: **exit 0; Ran 354 tests; OK; 0 failures; 0 errors.**

Command, run 2:

```text
python3 -m unittest discover -s tests -v
```

Result: **exit 0; Ran 354 tests; OK; 0 failures; 0 errors.**

### CLI evidence

Public run:

```text
python3 -m robot_factory_arena.cli run --seed 17 --policy greedy-v0 --artifacts-root /tmp/rfa-cli-verify --execution-id execution-primary
```

Result: exit 0; `safe_abort`; verified score 50; logical run `lr_f24e293774cf3b2b81ca7f241a5122e5cb8e245672d2eca220eec9a39ea3faff`.

Public rerun:

```text
python3 -m robot_factory_arena.cli rerun --seed 17 --policy greedy-v0 --artifacts-root /tmp/rfa-cli-verify --execution-id execution-rerun
```

Result: exit 0; same logical run; distinct execution ID; `deterministic agreement: true`.

Resolved documented replay path:

```text
python3 -m robot_factory_arena.cli replay /tmp/rfa-cli-verify/runs/lr_f24e293774cf3b2b81ca7f241a5122e5cb8e245672d2eca220eec9a39ea3faff/execution-primary
```

Result: exit 0; final-state digest matched; score 50; outcome `safe_abort`; `receipt_agreement: match`.

The README template command is equivalent after substituting the real `<logical_run_id>` and `<execution_id>` values. The literal angle-bracket placeholder was not expected to resolve to a directory and returned the documented artifact/usage error exit 2.

Faulted completion:

```text
python3 -m robot_factory_arena.cli run --seed 18 --policy greedy-v0 --artifacts-root /tmp/rfa-cli-fault-verify --execution-id execution-faulted --fault-step 8 --fault-intent place-order-00
```

Result: exit 0; `completed`; verified score 100; counters: `policy_steps=92`, `tool_attempts=93`, `committed_effects=92`, `internal_retries=1`, `contract_violations=0`, `terminal_checks=94`.

## Report integrity

Inspected generated public and faulted `report.html` files:

- `<!DOCTYPE html>` static documents; no `<script>`, `javascript:`, CDN, or URL references.
- Four separately labeled signal sections are present.
- All six raw counters, completed/total orders, budget/outcome, artifact digests, verifier status/version, fault details, violations/audit completeness, and required limitations are present.
- No numerical composite score is calculated or rendered; the required explanatory “never collapsed” limitation is present.
- Values are escaped and reports are execution-ID-independent.

### Report metric discrepancy

**WARNING W-01:** `robot_factory_arena/evidence/report.py:_ratio_text` renders “Actions per delivery (tool attempts / committed effects)” and computes `tool_attempts/committed_effects`. REQ-02 defines `committed_effects` as every successful move, pick, or place, not completed deliveries. Therefore the seed-18 displayed `93/92` is actions per committed world effect, not actions per delivery across 5 completed orders. The raw counters remain correct and the discrepancy does not compromise verifier authority, but the label/value does not faithfully satisfy the metric wording in REQ-09.

## Tamper evidence

Tampering was performed on copies outside the repository; implementation files were not modified.

1. **Trace payload mutation** — changed `trace.jsonl` record 1 `observation_digest` while leaving the chain/receipt unchanged. Direct `Verifier().verify(load_verification_artifacts(...))` returned:
   - `valid=False`
   - `outcome=unresolved_outcome`
   - `verified_score=None`
   - violations: `artifact_mismatch` at `receipt.trace_digest`, `trace_chain_invalid` at `trace[1]`.

2. **Missing boundary proof** — deleted `boundaries/step-1.json` from a copied completed faulted run. Direct verifier returned:
   - `valid=False`
   - `outcome=unresolved_outcome`
   - `verified_score=None`
   - violation: `commit_boundary_unproven` at `boundaries/step-1`.

These checks demonstrate fail-closed tamper detection, not authentication or transaction/exactly-once guarantees.

## Strict-TDD verification

Strict-TDD support guidance was loaded from `/Users/alejandrobosch/.pi/agent/gentle-ai/support/strict-tdd-verify.md`. The project-local override path `.pi/gentle-ai/support/strict-tdd-verify.md` does not exist. `apply-progress.md` contains TDD Cycle Evidence tables for B1 through B6, covering all 25 tasks. Every reported test file exists, and the two current full-gate runs remain GREEN.

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | PASS | RED/GREEN evidence is present for T-01..T-25 across all six batches. |
| Test files cross-referenced | PASS | Eight declared test modules exist and are discovered. |
| GREEN revalidated | PASS | 354/354 passed in both full runs. |
| Triangulation | PASS | Apply-progress records concrete triangulation catches and distinct adversarial/fault/report vectors. |
| Safety-net evidence | WARN | The narrative records prior gates, but a uniform per-file safety-net field is not reported for every changed file. |
| Coverage | INFO | Skipped; no coverage tool detected. |
| Quality metrics | WARN | `ruff check robot_factory_arena tests` exited 1 with three E402 errors in `tests/test_faults.py` (lines 786, 943, and 949). `pyright` was not installed. The repository documents prior pi-lens/pyright false positives; no type-check result was available in this phase. |

### Test layer distribution

All 354 tests execute through stdlib `unittest` across eight test modules. They are predominantly unit and in-process integration tests; no browser E2E, network, live provider, or pytest test layer is present. This is informational and not a gate failure.

### Assertion quality

| File | Line | Assertion | Issue | Severity |
|---|---:|---|---|---|
| `tests/test_adversarial_verifier.py` | 331 | `self.assertIsNotNone(__doc__)` | T-01 discovery placeholder only checks the test module docstring; it calls no production code and does not exercise verifier behavior. | CRITICAL |
| `tests/test_rerun_replay.py` | 88 | `self.assertIsNotNone(__doc__)` | T-01 discovery placeholder only checks the test module docstring; it calls no production code and does not exercise replay behavior. | CRITICAL |

The other tests reviewed contain production calls or meaningful contract/value assertions; no tautology, ghost-loop, or smoke-only implementation test was found beyond these two placeholders. The full functional suites still exercise the affected verifier/replay behaviors, but these two assertions do not count as strict-TDD behavior coverage.

## Findings

### CRITICAL

- **C-01 — Stale authoritative task checkbox blocks archive.** Engram tasks observation 6177 still contains the exact unchecked implementation marker: `- [ ] B6 — T-22..T-25 — remaining (static report, CLI run/rerun/replay, golden seed-17 evidence, final gate)`. The current apply-progress evidence (observation 6191 and materialized `docs/sdd/apply-progress.md`) plus the green gate prove B6 is implemented, so this appears to be stale-checkbox state rather than missing implementation; nevertheless the authoritative tasks artifact must be reconciled before archive. The unchecked marker is an archive blocker under the task-checkbox contract.
- **C-02 — Strict-TDD assertion-quality violations.** `tests/test_adversarial_verifier.py:331` and `tests/test_rerun_replay.py:88` assert only `__doc__` and never call production code. The strict-TDD guidance classifies assertions that exercise nothing as CRITICAL. Do not count these placeholders as meaningful T-01 coverage.

### WARNING

- **W-01 — Actions-per-delivery metric is mislabeled.** The report displays `tool_attempts/committed_effects` (seed 18: `93/92`) while `committed_effects` includes all moves, picks, and places. Rename the metric or compute it against completed deliveries in a follow-up; no implementation fix was made during verification.
- **W-02 — Strict-TDD evidence format is narrative rather than the support template's literal `✅ Written`/`✅ Passed` labels, and safety-net status is not uniform per changed file. The underlying RED/GREEN evidence and current test execution are present, so this is a traceability warning rather than an unverified implementation claim.**
- **W-03 — `ruff check robot_factory_arena tests` failed with three E402 errors in `tests/test_faults.py` at lines 786, 943, and 949. These are test import-order findings and did not affect the unittest gate.**

### SUGGESTION

- **S-01 — Add a small formula-level delivery denominator field or explicit metric name to prevent readers from confusing committed world effects with completed deliveries.**
- **S-02 — Add a machine-readable final task-artifact reconciliation/update step so Engram task snapshots cannot retain a stale aggregate unchecked marker after B6 completion.**

## Review workload and PR boundary

The task forecast recommended chained PRs because the estimated change exceeded the canonical 400-line review budget. The apply-progress and tasks artifacts explicitly record the user's accepted single-PR `size:exception` on branch `feat/v0-evidence-chain`, with one work-unit commit per batch. Verification found no scope creep beyond T-01..T-25 and no evidence that a chained slice was partially implemented. The returned work boundary is the explicitly accepted single PR; no additional chain strategy was invented.

## Honesty and claims audit

README and generated reports correctly state:

- tamper evidence is not authentication;
- no exactly-once guarantee;
- no hostile-Python sandbox or arbitrary-Python isolation;
- evaluation seed 29 is plumbing-only;
- no scientific REFUGIO conclusion or reproduction;
- no frontier/leaderboard or R1/R2/R3 arena results;
- four signals remain separate and no composite score is authoritative.

No forbidden claim was found in the inspected v0 implementation/report path.

## Archive decision

**Not ready for archive.** Reconcile the stale Engram B6 checkbox and address the two strict-TDD assertion-quality blockers before declaring a clean verification pass. The report-metric warning and ruff findings are non-archive-blocking but should remain visible to reviewers.
