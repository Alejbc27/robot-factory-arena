# Robot Factory & Arena

A deterministic, reproducible agent-evaluation system: a **factory** that assembles bounded agent workers (authorized tools, QC, budgets, release gate) and an **arena** that measures how different control strategies — human-directed, autonomous, swarm — attack the same optimization task. Inspired by the REFUGIO finding (Madrid 2026): autonomous agents fall into local optima when they cannot share discoveries; human+AI teams do not. This project tests that hypothesis with its own instrumentation — measured, not asserted.

**Status: early.** SDD exploration complete; no source code yet. This document is the project charter: goals, scope boundary, and roadmap.

## What I want to achieve

| Goal | Statement |
|------|-----------|
| **Production claim** | A small but complete agent production loop — assembly, authorized tools, QC/anti-cheat, cost budget, release gate — that is observable and measured end-to-end. |
| **Experiment claim** | A controlled local experiment on the REFUGIO question: does shared memory of discoveries close the local-optima gap for autonomous agents? Reported with separate, honest signals. |
| **Engineering claim** | Deterministic, replayable, cheap, explainable. Every run leaves a trace, a receipt, and a report that can be re-verified from a seed. |

## MVP scope (v0 — deterministic core, stdlib-first)

The critical path is **deterministic and local**. No framework or live provider is required to run v0.

| Component | Boundary |
|-----------|----------|
| Seeded task | One small warehouse/scheduling optimization task, generated from a seed. |
| World state | Fully serializable; no hidden mutable state. |
| Tool bay | 2–3 narrow sandbox tools; explicit grants, least privilege, idempotency keys. |
| Policies | Greedy human reference baseline + one bounded agent policy. |
| Budgets | Hard cost/step ceilings; breach aborts the run mid-flight, reproducibly. |
| Verifier | Independent, deterministic, anti-cheat: rejects self-reported success and reward hacking. |
| Fault injection | Deterministic fault tapes (response loss, duplicate delivery, restart) with safe-abort/unresolved outcomes. |
| Evidence | JSONL trace + receipt per run; replay reproduces trace and score; static `report.html`. |

## Score model

Four signals per run, each 0–100, **reported separately — never collapsed into one number**:

1. **Task quality** — deterministic verifier score.
2. **Efficiency** — actions, tokens/cost, wall time.
3. **Robustness** — behavior under injected faults.
4. **Contract hygiene** — permission violations, idempotency breaches, audit completeness.

## Roadmap

| Phase | Deliverable | Exit criteria |
|-------|-------------|---------------|
| **0. Charter** (this doc) | Goals, scope, roadmap committed. | Repo on GitHub; SDD exploration captured. |
| **1. MVP core** | Seeded task, world, tool bay, baseline, budgets, verifier, trace/replay, report. | Deterministic reruns; verifier rejects a plausible cheat; budget breach kills a run; replay reproduces trace + score. |
| **2. Robustness** | Fault tapes, safe-abort classification, recovery. | Fault outcomes classified (safe-abort / unresolved); recovery replay-equivalent. |
| **3. Arena conditions** | R1 human-in-the-loop, R2 autonomous, R3 swarm with shared memory; seeded leaderboard. | Same tasks/seeds/budgets across conditions; multi-signal comparison report. |
| **4. Real providers & observability** | LangGraph assembly line, self-hosted Langfuse tracing, multi-provider fallback, real cost ledger. | Live runs traced; hard € ceilings enforced; provider keys never in traces. |
| **5. Analysis & defense** | Final report, receipts, write-up of results and limitations. | Claims match evidence; limitations stated; results reproducible from seeds. |

Phases run through SDD (proposal → spec → design → tasks → apply → verify), one slice at a time.

## Non-goals (honesty constraints)

- Not a scientific reproduction of REFUGIO — **paper-inspired**, labeled as such.
- No exactly-once effect claims; sandbox effects are audited and replayable, not transactional.
- No hosted service, no real users, no real-world integrations (payments, email, production APIs).
- No model training/fine-tuning; providers via inference only.
- 2–4 deeply designed tasks maximum — never a wide shallow set.
- No AGI-adjacent claims, no production-readiness claims.

## References

- REFUGIO Madrid 2026 — "Speedrunning as a model of progress" (refugiomadrid.com/paper.html)
- Terminal-Bench 2.x + "What Makes a Good Terminal-Agent Benchmark Task" (arXiv 2604.28093) — adversarial deterministic verification
- ARC-AGI-3 — action efficiency as a first-class metric
- GAIA (arXiv 2311.12983) — documented human–agent performance gap
