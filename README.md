# Robot Factory & Arena

A deterministic, reproducible agent-evaluation system: a **factory** that assembles bounded agent workers (authorized tools, QC, budgets, release gate) and an **arena** that measures how different control strategies — human-directed, autonomous, swarm — attack the same optimization task. Inspired by REFUGIO Madrid (2026), where autonomous frontier agents on that task tended to plateau while human+AI teams advanced through shared discoveries; later models recovered some of the same exploits independently. This project tests that hypothesis with its own instrumentation — measured, not asserted.

**Status: v0 implemented.** The deterministic evidence-chain core (SDD batches B1–B6) is complete and green under the one-command gate below; no live providers yet. The charter sections of this document are unchanged.

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

## v0 implementation (deterministic evidence-chain)

The v0 core is a stdlib-only Python 3.14.5 package (`robot_factory_arena/`): seeded warehouse tasks from the frozen REQ-01 formulas, pure `greedy-v0`/`bounded-v0` reference policies, a runner-owned hard 100-step `policy_steps` budget, an authorized ToolBay with an idempotency ledger, exactly one declarative fault tape (`response_lost_after_commit`), an append-only hash-chained trace, canonical artifacts under `artifacts/runs/<logical_run_id>/<execution_id>/`, an independent artifact-only verifier that owns the official integer score, and a static offline `report.html` per run.

One-command test gate (stdlib `unittest` only, Python 3.14.5):

```text
python3 -m unittest discover -s tests -v
```

CLI — run, rerun, and replay are distinct operations:

```text
# execute one evidenced run and release its verified receipt
python3 -m robot_factory_arena.cli run --seed 17 --policy greedy-v0

# same logical identity, fresh physical execution, deterministic artifacts compared
python3 -m robot_factory_arena.cli rerun --seed 17 --policy bounded-v0

# offline replay of one recorded run
python3 -m robot_factory_arena.cli replay artifacts/runs/<logical_run_id>/<execution_id>
```

Replay reads only the run directory's `replay-input.json` and `receipt.json`; it never imports or calls a policy or provider, and it reports whether the final-state digest, verified score, and outcome match the receipt. Exit codes: 0 success/match, 1 evidence disagreement (rerun bytes differ, replay mismatch, invalid verification), 2 usage or artifact errors.

The response-loss demonstrator can be declared on `run`/`rerun` with `--fault-step 8 --fault-intent place-order-00`; both constants are predeclared — no policy is ever consulted to resolve a fault target.

Each run directory contains `config.json`, `task.json`, `fault_tape.json` (or the explicit canonical `null` artifact), `trace.jsonl`, `final_state.json`, `operation-log.json`, `replay-input.json`, the verified `receipt.json`, `replay-result.json`, the static `report.html`, and the `boundaries/` commit-proof files. The `boundaries/` files are part of the run evidence, not optional: removing them flips verification to `unresolved_outcome`. Copy a run directory wholesale when moving it offline.

Golden evidence (committed fixtures): public seed 17 (`greedy-v0`, no tape) ends `safe_abort` at the 100-step budget with 5 of 10 orders delivered, verified score 50; evaluation seed 29 (`bounded-v0`, plumbing-only) ends `safe_abort` with 6 of 10, score 60; the completion demonstrator seed 18 (AMEND-01) declares the response-loss tape at step 8 and completes all 5 orders in 92 policy steps with one internal retry, verified score 100.

### v0 honesty boundary

- The trace hash chain and the digest bindings are **tamper evidence, not authentication**: edits are detected, but artifact provenance is not proven.
- There is **no exactly-once guarantee**: the persistence boundary is a durability and audit proof, not a transaction.
- There is **no hostile-Python sandbox**: policy outputs are semantically untrusted, but policy source is not treated as hostile malware; no hostile arbitrary-Python isolation is claimed.
- Evaluation seed 29 is **plumbing-only**: it exercises the plumbing and carries no tuning or experimental claim.
- **No scientific REFUGIO claim**: the project is paper-inspired; v0 makes no scientific reproduction, leaderboard, R1/R2/R3 arena, or production-security claim.
- The four report signals are reported separately and are never collapsed into one number; no composite score is computed.

## Score model

Four signals per run, each 0–100, **reported separately — never collapsed into one number**:

1. **Task quality** — deterministic verifier score.
2. **Efficiency** — actions, tokens/cost, wall time.
3. **Robustness** — behavior under injected faults.
4. **Contract hygiene** — permission violations, idempotency breaches, audit completeness.

## Tournament harness (optional — Phase 6)

An optional LangGraph orchestration layer (`harness/`, installed via `pip install -e ".[harness]"`) runs R1/R2/R3 **tournaments** over the frozen core: one immutable config per tournament, every core launch a registered subprocess with a frozen StrategyContext snapshot, fail-closed evidence binding, and exactly one authoritative leaderboard row per `(condition, seed)`. The core itself stays stdlib-only and is never imported by the harness — the only interface is the CLI subprocess plus sealed JSON artifacts.

```text
# zero-cost deterministic acceptance: full R1+R2+R3 over seeds 18/24/30
python3 -m pip install -e ".[harness]"
python3 -m harness --config tests/fixtures/harness/unit6-acceptance-config.json \
    --runner fake --non-interactive --root /tmp/harness-demo
```

Exit codes: 0 complete, 1 fail-closed (evidence paths printed), 2 usage/config. Re-running the same tournament id resumes under the recovery contracts and never reruns receipt-bound work. R1 approvals are injectable: `--non-interactive` (auto-decline), `--answers FILE` (scripted), or the default stdin terminal. Full operator contract — Luna live-acceptance procedure, artifact layout, R1/R2/R3 semantics, recovery windows — lives in `docs/sdd/phase6-langgraph-harness.md`.

What the harness guarantees, and what it does not:

- **Deterministic tournaments**: identical runs produce byte-identical leaderboards; one tournament freezes one provider/model — no cross-provider mixing and no fallback.
- **Receipt authority**: scores come only from sealed `receipt.json`, tokens only from digest-bound provider evidence; stdout claims are never official and fail-closed rows carry explicit nulls, never zeros.
- **Honest logs** — binding precision note: "Checkpoint/logs give SEQUENCE/CONTINUITY validation and replay revalidation — NOT general tamper-evidence/authenticity: a valid-to-valid external substitution of content is not cryptographically bound."
- **Internal identity consistency** — binding precision note: "First-bind-wins for prompt_digest/tool_schema_digest is INTERNAL consistency after the first run — not a pre-frozen tournament expectation recorded before the tournament."
- **No composite score** and no scientific/security/exactly-once claims; replay never calls an LLM.

## Roadmap

| Phase | Deliverable | Exit criteria |
|-------|-------------|---------------|
| **0. Charter** (this doc) | Goals, scope, roadmap committed. | Repo on GitHub; SDD exploration captured. |
| **1. MVP core** | Seeded task, world, tool bay, baseline, budgets, verifier, trace/replay, report. | Deterministic reruns; verifier rejects a plausible cheat; budget breach kills a run; replay reproduces trace + score. |
| **2. Robustness** | Fault tapes, safe-abort classification, recovery. | Fault outcomes classified (safe-abort / unresolved); recovery replay-equivalent. |
| **3. Arena conditions** | R1 human-in-the-loop, R2 autonomous, R3 swarm with shared memory; seeded leaderboard. | Same tasks/seeds/budgets across conditions; multi-signal comparison report. |
| **4. Real providers & observability** | LangGraph outer harness (coordinates attempts, not robot moves), self-hosted Langfuse tracing, one pinned provider+model per run (no silent fallback), real cost ledger. | Live runs traced; hard € ceilings enforced; provider keys never in traces; replay reproduces from recorded calls without any API access. |
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
