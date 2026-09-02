# SDD Archive Report — robot-factory-arena v0 evidence chain

## Status: CLOSED

The `robot-factory-arena` v0 evidence-chain change is **CLOSED**. This archive record is documentation and persistence only; no functional code, refactor, or foundational implementation change was made during closure.

## Change summary

The change delivered a deterministic, stdlib-only warehouse evidence chain: seeded tasks, immutable world state, pure reference policies, runner-owned budgets, grants and ToolBay idempotency, one declarative response-loss fault, canonical trace and receipts, independent adversarial verification, offline replay, static reporting, and CLI run/rerun/replay flows. The implementation deliberately excludes live providers, LangGraph, Langfuse, Arena/R1/R2/R3 behavior, frontier/leaderboard behavior, hostile-code isolation, exactly-once claims, and scientific REFUGIO claims.

## Lifecycle and final evidence chain

| Phase | Final state and evidence |
|---|---|
| Explore | Repository and reference spike were examined; the narrow deterministic evidence-chain MVP boundary was selected. Engram observation `6143`. |
| Proposal | Proposal questions and the v0 scope, non-goals, acceptance criteria, and later-phase seams were captured. Engram observation `6163`. |
| Spec | REQ-01 through REQ-10 fixed seeded generation, accounting, schemas, ToolBay/idempotency, the response-loss tape, verifier independence, determinism, unittest gate, artifacts/replay, and honesty boundaries. Engram observation `6170`; `docs/sdd/spec.md`. |
| Design | Module boundaries, canonicalization, immutable state, runner lifecycle, fault resolution, trace closure, verifier independence, artifact storage, and extension seams were designed. Engram observation `6172`; `docs/sdd/design.md`. |
| Tasks | Twenty-five implementation tasks were organized into six batches. The single-PR `size:exception` decision superseded the chained-PR recommendation. Engram observation `6177`; `docs/sdd/tasks.md`. |
| Apply B1 | Core scaffold, canonical authority, schema locks, immutable model, seeded tasks, observation contract, and pure policies. Commit `51bc7ba`; gate 86 green. |
| Apply B2 | Pure transitions, grants, budget accounting, ToolBay, and idempotency ledger. Commit `8e3abaa`; gate 153 green. |
| Apply B3 | Runner lifecycle, deterministic fault targets/tapes, persistence-boundary semantics, and safe response-loss retry. Commit `3059165`; gate 201 green. |
| Apply B4 | Hash-chained trace, verified receipt, artifact store, operation log, and policy-free replay. Commit `a5017e6`; gate 271 green. |
| Apply B5 | Independent verifier, integer scoring, structured violations, adversarial fixtures, and dependency denylist. Commit `c9fbedc`; docs commit `a64782b`; gate 310 green. This batch continued work left by a crashed agent. |
| Apply B6 | Static report, CLI, golden evidence for seeds 17/18/29, rerun equality, and replay integration. Commit `c7dfde1`; docs commit `464d36c`; final gate 354/354 green twice. |
| Verify and remediation | Initial findings were preserved; all blockers were remediated. Remediation commit `86a54f5`; final verification status is PASS. `docs/sdd/verify-report.md`; Engram observation `6256`. |
| External review | Final ChatGPT Sol-high review verdict: APPROVE / ready to merge. The two blockers were fixed before merge: verification reconciliation in `3cbe09d` and CI in `c40f65e`. The README REFUGIO wording was nuanced in `3cbe09d`; no further functional changes were made to the foundational merge. |
| Merge | PR #2 for issue #1 (`status:approved`) merged to master as `433e34a` on 2026-09-02. `feat/v0-evidence-chain` was deleted after merge. |

## Final state

- **Merged:** PR #2 (issue #1, `status:approved`) is merged to master as merge commit `433e34a` on 2026-09-02; the feature branch was deleted.
- **CI:** GitHub Actions `test-gate` uses pinned actions and no credential persistence. It was green on the PR event and on the post-merge master push: 354 tests, 0 failures, 0 errors. The runner installed Python 3.14.7 under `python-version: "3.14"`; this is documented, and follow-up issue #3 remains open to pin 3.14.5.
- **Verification:** PASS. Findings were remediated in `86a54f5`; the report contains one authoritative final status and retains the superseded initial decision for traceability.
- **Delivery:** One PR with the accepted `size:exception`, six work-unit batches, and approximately 19.7k additions overall.
- **Ledger:** All native `sdd-attempt` units are settled/reset clean. B1–B5 required maintainer-accepted budget overruns under the documented `size:exception` pattern.
- **Evidence:** Seed 17 is the public safe-abort baseline (score 50, 5/10); seed 29 is bounded-v0 plumbing-only evidence (safe-abort, score 60, 6/10); AMEND-01 seed 18 is the completion demonstrator (score 100, 5/5, response loss at step 8, one internal retry, 92 policy steps).
- **Known non-blocking notes:** The verifier shares `domain.task.generate_task()` with runtime; its transition and scoring logic are independently reconstructed from artifacts, while the task-generation formulas are trusted shared infrastructure. W-02, the historical TDD-format warning, was assessed as not actionable.

## Artifact index

### Engram artifacts

| Artifact | Topic key | Observation ID | File-backed materialization |
|---|---|---:|---|
| Explore | `sdd/robot-factory-arena/explore` | 6143 | Repository exploration context; no dedicated `docs/sdd/` file |
| Proposal | `sdd/robot-factory-arena/proposal` | 6163 | No dedicated proposal file; proposal is retained in Engram |
| Spec | `sdd/robot-factory-arena/spec` | 6170 | `docs/sdd/spec.md` |
| Design | `sdd/robot-factory-arena/design` | 6172 | `docs/sdd/design.md` |
| Tasks | `sdd/robot-factory-arena/tasks` | 6177 (updated with ARCHIVED marker during closure) | `docs/sdd/tasks.md` |
| Apply progress | `sdd/robot-factory-arena/apply-progress` | 6191 | `docs/sdd/apply-progress.md` |
| Verify report/remediation | `sdd/robot-factory-arena/verify-report` | 6256 | `docs/sdd/verify-report.md` |
| Archive report | `sdd/robot-factory-arena/archive-report` | 6279 | `docs/sdd/archive-report.md` |

### Repository artifacts

- `README.md` — implemented-v0 description, replay command, evidence inventory, roadmap, and honesty boundary.
- `docs/sdd/amendments.md` — accepted AMEND-01 completion-demonstrator decision selecting seed 18.
- `docs/sdd/spec.md`, `docs/sdd/design.md`, `docs/sdd/tasks.md`, `docs/sdd/apply-progress.md`, and `docs/sdd/verify-report.md` — materialized audit documents.
- `tests/fixtures/` — locked schemas, tasks, fault targets, adversarial fixtures, and golden traces/receipts.
- `robot_factory_arena/`, `tests/`, and `robot_factory_arena/cli.py` — merged implementation and test evidence referenced by the reports.
- No `sync-report.md` or OpenSpec archive move applies: this change was operated with Engram-backed SDD artifacts and repository-materialized audit files, not an `openspec/` change directory.

Engram was reachable for archive reads in this session, including all listed artifact observations. The file-authoritative record remains complete if a later Engram HTTP request is unavailable.

## Lessons learned

1. **Ledger budgets undercount real review work.** Per-task estimates substantially understated tests and fixtures; the native attempt ledger blocked four times and batches reached the 5,000-unit ceiling. Maintainer resets were explicitly accepted under `size:exception`, rather than hiding the overrun.
2. **Continuation after a crashed agent must be adversarially verified.** The crashed-agent B5 continuation caught three real verifier holes: forged score claims were not emitting `score_mismatch`, reconstructed final state was not compared to `final_state.json`, and claimed outcomes were not compared to independent classification.
3. **Formatter churn needs a discard policy.** Post-commit formatter/watchers caused repeated behavior-neutral churn and one indentation incident. The established policy is to discard churn on frozen work units, restore authoritative content, and rerun the gate; only real fixes are retained.
4. **AMEND-01 seed 18 resolved a fixture contradiction cleanly.** Seeds 17 and 29 both generate ten orders and therefore safely abort at 100 steps. The deterministic candidate rule selected seed 18 as the five-order completion demonstrator, preserving the locked formulas and fault semantics.
5. **Single versus chained PRs is a real tradeoff.** The forecast recommended chained PRs for reviewability, but the maintainer explicitly accepted one coherent PR with six work-unit commits and `size:exception`. That choice simplified cross-batch evidence and merge coordination at the cost of a larger review surface.

## Non-blocking notes and decisions carried forward

- The verifier/task-generator sharing boundary is documented and does not weaken the independent transition/scoring reconstruction claim.
- The former report-metric warning was corrected in `86a54f5` to use completed deliveries as the denominator.
- Historical W-02 evidence-format cleanup was intentionally not rewritten, because retroactively changing the evidence narrative would reduce traceability.
- No functional changes are authorized as part of this archive closure.

## Next-phase backlog (not part of this closed change)

1. **Issue #3:** Pin CI to Python 3.14.5.
2. **Phase 2:** Add bounded fault depth, restart/recovery, and replay-equivalent safe-abort/unresolved evidence.
3. **Phase 3:** Define arena R1/R2/R3 conditions, multi-seed evaluation, holdout protocol, equalized budgets, anti-adaptive-oracle rules, discovery evidence thresholds, and frontier semantics.
4. **Phase 4:** Add the adopted 11-step LLM/LangGraph/Langfuse/provider layering order without changing the deterministic core; Langfuse remains observer-only and LangGraph coordinates attempts rather than robot moves.
5. **Visualization:** Add an observer-only visualization layer.

## Closure declaration

The v0 evidence-chain SDD change has passed implementation, verification, remediation, external review, CI, and merge. It is **CLOSED** as of the merged state `433e34a`. Any backlog item above requires a new scoped change and must not be folded into this archive record.
