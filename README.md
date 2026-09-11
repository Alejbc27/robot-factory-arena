# Robot Factory & Arena

## Deterministic evidence for comparing agent strategies

Robot Factory & Arena runs bounded agent strategies against the **same seeded task**, then produces evidence that can be replayed and independently verified. It separates what an agent proposes from what the deterministic system executes—and separates both from the score a verifier is allowed to report.

> **In one line:** explore fairly, execute deterministically, seal the evidence, verify independently.

**Current state:** the v0 deterministic evidence-chain core is implemented and stdlib-only. Optional provider adapters, the R1/R2/R3 tournament harness, and self-hosted Langfuse projection are documented separately. This page is the portfolio map; implementation/spec documents and tests define the detailed contracts.

![Robot Factory and Arena system overview](docs/architecture/system-overview.svg)

See the [human semantic architecture reference](docs/architecture/system-overview.md) for layer boundaries, authority, and connector semantics. The diagram is documentation, not a new runtime source of truth.

## Why this exists

Agent evaluations become difficult to trust when the task changes between runs, context leaks across conditions, agents report their own success, or observability is mistaken for authority. This project makes those boundaries explicit:

- **Same challenge:** a seed creates the task and initial world state.
- **Same execution contract:** budgets, grants, tools, and transition rules are fixed by the core.
- **Honest result:** sealed artifacts feed an independent verifier; policy output and stdout do not set the official score.
- **Useful comparison:** R1 human-directed, R2 autonomous, and R3 shared-discovery experiments can search under a frozen `StrategyContext` before each run.

The project is inspired by the REFUGIO Madrid question about shared discoveries and local optima. It instruments that question; it does **not** claim to reproduce the paper scientifically.

## How it works

```text
Experiment / Search (R1 / R2 / R3)
  -> freeze StrategyContext
LLMPolicy / ProviderAdapter
  -> normalize a proposal to ToolCall
Deterministic core
  -> enforce budget, grants, tools, and WorldState transitions
Sealed evidence
  -> trace, operation log, replay input, receipt, and report
Independent Verifier
  -> Verified Score and outcome
```

| Stage | What it does | Boundary that matters |
|---|---|---|
| **Experiment / Search** | R1 accepts human guidance, R2 runs autonomously, and R3 shares sealed discoveries at round barriers. | `StrategyContext` is frozen per run and remains advisory context—not world state or score authority. |
| **LLMPolicy / ProviderAdapter** | An optional provider proposes the next action; the policy normalizes it into a `ToolCall`. | Provider output cannot execute directly or bypass the core. |
| **Deterministic core** | `Runner`, `Budget`, `Grants`, `ToolBay`, `apply()`, and serializable `WorldState` enforce the execution contract. | The core owns authorization, committed operations, and state transitions. |
| **Sealed evidence** | The run records canonical artifacts, append-only trace data, receipt bindings, and replay inputs. | Evidence is the input to review; a self-reported success is not evidence authority. |
| **Independent Verifier / Verified Score** | The artifact-only verifier checks invariants and derives the official integer task score and outcome. | Verification is independent of policy/provider execution and observation views. |

The core can run without an LLM or external service using the reference policies `greedy-v0` and `bounded-v0`. Provider-backed runs are optional integrations, not a reason to weaken the deterministic boundary.

## v0 implementation boundary

The v0 implementation is intentionally narrow: one seeded warehouse task, programmatic reference policies, a bounded local core, and evidence that can be replayed without a provider. It makes **no exactly-once** claim, no scientific REFUGIO reproduction claim, and no production-security claim. The detailed limitations are part of the design, not a footnote.

## Try the core

Requirements: Python 3.14+ (the current test gate is exercised with Python 3.14.5). The core has no runtime dependencies.

```text
# full stdlib unittest gate
python3 -m unittest discover -s tests -v

# execute one evidenced run; the command prints its run directory
python3 -m robot_factory_arena.cli run --seed 17 --policy greedy-v0

# execute a fresh physical run under the same logical identity and compare it
# with prior executions (keep seed and policy the same)
python3 -m robot_factory_arena.cli rerun --seed 17 --policy greedy-v0

# replay the exact run directory printed by run or rerun
python3 -m robot_factory_arena.cli replay artifacts/runs/<logical_run_id>/<execution_id>
```

`run` releases a verified receipt and a static `report.html`. `rerun` invokes the policy again and compares deterministic artifacts; it is not replay. `replay` reads only `replay-input.json` and `receipt.json`, never imports or calls a policy/provider, and reports whether the final-state digest and outcome match the receipt while displaying the verifier-owned score recorded there.

Exit codes are intentionally meaningful: `0` is success or agreement, `1` is an evidence disagreement or invalid verification, and `2` is a usage or artifact error.

### Optional visualizer

A successful verified run may also release a self-contained `visualizer.html` beside `report.html`. There is no separate visualizer command: open the sibling file from the `run_directory` printed by the CLI.

```text
# macOS
open <run_directory>/visualizer.html

# Linux
xdg-open <run_directory>/visualizer.html
```

It works from a local `file:` URL, needs no server or network, and provides Play, Pause, Step forward, and fixed `0.5x`/`1x`/`2x`/`4x` speeds. One frame is one consumed policy step; a response-loss retry remains an ordered subevent of that frame. The visualizer is a deterministic projection of verified inputs, not a verifier, scorer, replay engine, or evidence authority. If generation fails or the optional file is deleted, the core receipt, report, replay, score, and outcome remain valid.

For a deterministic response-loss demonstration, the fault target is declared rather than discovered by a policy:

```text
python3 -m robot_factory_arena.cli run --seed 18 --policy greedy-v0 \
    --fault-step 8 --fault-intent place-order-00
```

The committed seed-18 fixture exercises one response loss after commit, an internal retry, and a verifier-owned score of 100 within the locked 100-step budget. Seed 17 (`greedy-v0`) and seed 29 (`bounded-v0`) remain the public and plumbing-only evidence fixtures respectively; fixture details belong to the tests and evidence contracts.

## Why the engineering is interesting

| Choice | Evidence-led value |
|---|---|
| **Deterministic execution** | Seeded tasks, serializable state, canonical artifacts, and byte-comparable reruns make a behavior explainable instead of anecdotal. |
| **Bounded tools and budgets** | `ToolBay` exposes narrow operations, explicit `move`/`pick`/`place` grants, idempotency keys, and a hard 100-policy-step budget. |
| **Independent verification** | The verifier reads the recorded artifacts, rejects invalid or self-reported success, and owns the official score/outcome. |
| **Replayability** | Offline replay rechecks a recorded run without importing policy code or contacting a provider; rerun separately tests deterministic re-execution. |
| **Observability separation** | Langfuse and the offline visualizer are downstream projections. They can be unavailable without authorizing, scoring, verifying, or reclassifying a run. |

## Signals, not a composite score

Each run reports four separate signals, each on a 0–100 scale:

1. **Task quality** — deterministic verifier score.
2. **Efficiency** — actions, tokens/cost, and wall time.
3. **Robustness** — behavior under injected faults.
4. **Contract hygiene** — permission violations, idempotency breaches, and audit completeness.

They are intentionally never collapsed into one composite score.

## Exactly-once, stated precisely

The system does **not** claim exactly-once effects. Idempotency keys and the operation/audit ledger support duplicate detection; replay and rerun support comparison and reconciliation. But the persistence boundary is not a distributed transaction. If a response is lost after a commit, the caller can be uncertain whether the effect was applied. The deterministic fault tape exercises that ambiguity, and the system classifies the result safely from recorded evidence rather than upgrading it to an exactly-once guarantee.

The core trace hash chain and receipt/digest bindings provide tamper evidence for those recorded core artifacts, not authentication or proof of external provenance. Harness checkpoints and discovery/decision logs provide sequence and continuity validation with replay revalidation; they are not general cryptographic tamper evidence.

## What this project proves—and does not prove

### It demonstrates

- A bounded, seeded execution path whose artifacts can be replayed and compared.
- A separation between proposal, authorization, committed state transition, evidence, and verification.
- Independent score/outcome derivation from sealed artifacts rather than agent claims.
- Fault-aware handling of duplicate delivery and response loss, including safe-abort or unresolved classifications.
- A deterministic fake acceptance path for R1/R2/R3 harness behavior, with no network cost.

### It does not claim

- A scientific reproduction of REFUGIO, a frontier benchmark, or a meaningful leaderboard from the current fixtures.
- Production readiness, a hostile-Python sandbox, or a general security boundary for arbitrary policy code.
- Exactly-once guarantees, transactional sandbox effects, or authenticated artifact provenance.
- Hosted deployment, real users, or real-world integrations such as payments, email, or production APIs.
- Model training or fine-tuning; providers are inference integrations only.
- A wide task library: v0 focuses on one task, with 2–4 deeply designed tasks as the later ceiling.
- AGI-adjacent claims or a live-provider performance result for v0. Provider adapters and documented live-acceptance procedures are integration paths, not experimental conclusions.
- A composite score: task quality, efficiency, robustness, and contract hygiene remain separate signals.

## Optional tournament harness

The optional `harness/` package runs R1/R2/R3 tournaments over the frozen core. It freezes one provider/model per tournament, snapshots `StrategyContext`, launches the core through registered subprocesses, binds evidence fail-closed, and writes one authoritative leaderboard row per `(condition, seed)`. The core remains stdlib-only; the harness is an outer orchestration layer.

```text
python3 -m pip install -e ".[harness]"
python3 -m harness --config tests/fixtures/harness/unit6-acceptance-config.json \
    --runner fake --non-interactive --root /tmp/harness-demo
```

The fake acceptance is deterministic and zero-cost. See the [harness operator contract](docs/sdd/phase6-langgraph-harness.md) for the execution-profile table, R1/R2/R3 semantics, artifact layout, recovery windows, and the deliberately narrow guarantees.

## Optional observability

`observability/` provides a downstream-only, self-hosted [Langfuse](https://langfuse.com/docs) v3.114.0 projection and a stdlib-only exporter for sealed run evidence. The source artifacts remain authoritative; removing the stack cannot alter them. Start-up, environment-variable names, pins, export behavior, and the optional smoke are documented in [observability.md](docs/observability.md). The UI port is `3000`; credentials stay in the environment and are never part of the repository or evidence artifacts.

## Roadmap

| Phase | Status | Evidence or next step |
|---|---|---|
| **0. Charter** | Complete | Scope, constraints, and the evidence-first architecture are documented. |
| **1. MVP core** | Implemented | Seeded task, world, ToolBay, policies, budgets, verifier, trace/replay, receipt, and report. |
| **2. Robustness** | Implemented | Fault tapes, duplicate detection, safe-abort/unresolved classification, and replay-equivalent recovery paths. |
| **3. Arena conditions** | Harness implemented | R1/R2/R3 orchestration and deterministic fake acceptance are available; scientific comparison remains future work. |
| **4. Providers and observability** | Optional paths | Provider adapters and downstream Langfuse projection are documented and gated; v0 makes no live-provider performance claim. |
| **5. Analysis and defense** | Next | Run controlled experiments, compare separate signals, publish receipts and limitations, and keep claims tied to evidence. |

## Technical references

- [Semantic architecture reference](docs/architecture/system-overview.md) and [architecture SVG](docs/architecture/system-overview.svg)
- [Core CLI](robot_factory_arena/cli.py), [core implementation](robot_factory_arena/), and [test suite](tests/)
- [Deterministic core specification](docs/sdd/spec.md), [design](docs/sdd/design.md), [tasks](docs/sdd/tasks.md), and [verification report](docs/sdd/verify-report.md)
- [Tournament harness contract](docs/sdd/phase6-langgraph-harness.md)
- [Visualizer contract](docs/sdd/robot-run-visualizer.md)
- [Langfuse observability contract](docs/observability.md)
- [Focused CLI and replay tests](tests/test_cli.py), [rerun/replay tests](tests/test_rerun_replay.py), [harness acceptance](tests/test_harness_acceptance.py), and [visualizer release tests](tests/test_visualizer_release.py)
- REFUGIO Madrid 2026 — [“Speedrunning as a model of progress”](https://refugiomadrid.com/paper.html), the inspiration for the shared-discovery question
- Terminal-Bench 2.x and [“What Makes a Good Terminal-Agent Benchmark Task”](https://arxiv.org/abs/2604.28093), for adversarial deterministic verification context
- ARC-AGI-3, for treating action efficiency as a first-class metric; and GAIA ([arXiv:2311.12983](https://arxiv.org/abs/2311.12983)), for the documented human–agent performance gap
- [Roadmap](#roadmap)
