# Architecture Review Brief — Robot Factory & Arena (v0 core)

> **How to use this document**: You are being consulted as a senior software architect. Read the full brief, then attack the open questions in section 7. Challenge our assumptions — do not just validate them. Answer in the output format of section 9.

## 1. Context

- Solo developer building a portfolio project with an SDD (spec-driven development) workflow.
- Time horizon: focused sprint (~1 week) for a defensible portfolio piece, then iterative phases.
- The deterministic v0 core is Python, **stdlib-first** (no framework on the critical path).
- Environment: Python 3.14.5, zero dependencies, no test suite yet — architecture must be testable via golden traces (recorded runs replayed as tests).
- **No LLM in the v0 loop.** Both v0 policies are programmatic (pure functions over serialized observations). The policy interface must stay LLM-compatible — observation in → tool-call request out — because live providers attach in phase 4.
- Philosophy: evidence over assertion. Every run must be deterministic, replayable, budgeted, and independently verified. The system's own verifier must resist reward hacking.

## 2. What the project is

A deterministic, reproducible agent-evaluation system with two halves:

- **Factory**: assembles bounded agent workers — authorized tools, QC, cost/step budgets, release gate.
- **Arena**: measures how different control strategies (R1 human-directed, R2 autonomous, R3 swarm with shared memory) attack the same seeded optimization task, reporting separate signals.

Inspired by REFUGIO (Madrid 2026). The mechanism we reproduce is the **collective search loop**, not the original scale:

> explore → evaluate → publish evidence → learn from others → beat the frontier

In REFUGIO, every team programmed the same warehouse (two levels: layout design + movement policy, `create_layout()` + `act(...)`), runs were scored on hidden seeds by an isolated evaluator that never trusted self-reported results, and every evaluated submission published its code, score, and replay to everyone — so each team started from the best known solution instead of zero. The observed finding: autonomous agents quickly reach a reasonable solution then plateau in local optima; humans+AI teams jump to new regions of the search space by exchanging discoveries. **The claim is deliberately limited**: in that problem, under those conditions, exploration diversity and discovery-sharing were decisive against local optima. Not "humans always win" — later models did find key discoveries.

| REFUGIO original | Our MVP |
|------------------|---------|
| 52×52 grid, 96 robots, 960 shelves, 300 ticks, score = deliveries | Small seeded scenario (§3): 10×10, 1 robot, deliveries within 100 actions |
| `create_layout()` + `act(...)` — layout and policy search levels | v0: seeded layout + policy only; layout-as-search-space is a staged later extension |
| Frontier scoring: a submission counts only if it beats the historical best | Local leaderboard: the baseline sets the initial frontier; a run advances the record only if it beats the best on evaluation seeds |
| Published code + result + replay to all teams | Every evaluated run publishes config + score + trace + replay in-repo — the evidence loop |
| Isolated evaluation, hidden seeds | No network, no writes outside sandbox, CPU/timeout limits; public seed for development, evaluation seeds for scoring |

**Paper-inspired, not a reproduction.** The experiment claim is tested with our own instrumentation, measured — never asserted. The Factory adds what REFUGIO did not study: how the agent itself is built and controlled (permissions, budgets, tools, QC, release gate).

## 3. The v0 task — working definition

> A working definition so you can reason about schemas, verifier, and layout. The final task spec is authored in our design phase — challenge it if it creates bad architecture pressure, but do not expand scope.

- **Seeded warehouse**: a deterministic generator (seed → world) produces: a **10×10 grid**, items on shelves (goals appear on shelves, like REFUGIO), and **5–10 orders** (each order = pick a goal from its shelf and deliver it to the robot's base, or an assembly combining two items at a destination).
- **One robot**: has a position, executes atomic operations (`move`, `pick`, `place`, and possibly `assemble`).
- **Action definition**: an action is **any atomic operation, not only a movement** — `pick`, `place`, and `assemble` count as actions too. The 100-action budget counts atomic operations. (Whether rejected/invalid operations consume budget is a spec-phase decision — flag it when designing.)
- **Objective**: maximize completed deliveries within a **100-action budget**; efficiency = actions per delivery.
- **Seeds**: 1 public seed for development and replay tests + 1 evaluation seed for scoring, never used for tuning (REFUGIO's accidental seed leak was a platform flaw, not a feature to copy).
- **Verifier scores**: final world state + trace against order invariants — never the robot's self-report.
- **Failure paths**: invalid operations (pick from empty station, move into wall, unknown tool, operation without grant) are rejected, traced, and counted — they never crash the run. Budget exhaustion aborts mid-run.

## 4. Prior art — machinery already proven in a reference spike

A stdlib-only contract-recovery spike exists in the course repo and fixes the vocabulary. Reuse these idioms; extend, don't reinvent:

| Idiom | Established shape |
|-------|-------------------|
| Tool calls | Frozen dataclass `ToolCall(tool, arguments, operation_key)` — stable operation keys |
| Event log | Append-only `Event(sequence, kind, tool, operation_key, details)` — the trace spine |
| Idempotency | Operation ledger: repeated `operation_key` returns the committed result without re-executing |
| Fault tape | Ordered fault list, cursor-matched by tool; kinds: `failure_before_commit`, `response_lost_after_commit`, `duplicate_delivery`, `process_restart_between_commit_and_checkpoint` |
| Outcome classification | `completed`, `duplicate_effect`, `safe_abort`, `unresolved_outcome` — never ambiguous |
| Independent evaluator | Checks state invariants (counts, statuses) + raw execution status → classifies; never trusts the executor |

## 5. v0 MVP boundary (already explored and fixed — do not expand)

| Component | Boundary |
|-----------|----------|
| Seeded task | One small warehouse/scheduling optimization task, generated from a seed. |
| World state | Fully serializable; no hidden mutable state. |
| Tool bay | 2–3 narrow sandbox tools; explicit grants, least privilege, idempotency keys. |
| Policies | Greedy human reference baseline + one bounded agent policy — both **programmatic** (see context: no LLM in v0). |
| Budgets | Hard **action/step** ceilings (€ ceilings arrive with live providers in phase 4); breach aborts the run mid-flight, reproducibly. |
| Verifier | Independent, deterministic, anti-cheat: rejects self-reported success and reward hacking. |
| Fault injection | Deterministic fault tapes (response loss, duplicate delivery, restart); safe-abort / unresolved outcomes. |
| Evidence | JSONL trace + receipt per run; replay reproduces trace and score; static `report.html`. |

**Deferred (out of v0 critical path)**: LangGraph, Langfuse, live LLM providers, multi-provider calls. When they arrive (phase 4): one pinned provider+model per run (recorded in the receipt), no silent fallback during a run (it confounds comparison and breaks replay), and replay works from recorded observations/responses — fresh LLM calls are never assumed byte-deterministic.

## 6. Hard constraints

- Deterministic: same seed + same policy ⇒ identical trace and score, always.
- Verifier is independent of any LLM and of the agent's own claims.
- Scores reported as four separate signals (task quality, efficiency, robustness, contract hygiene) — never collapsed.
- R1/R2/R3 conditions must plug into the same core later **without rewriting it**.
- No exactly-once effect claims; sandbox effects are audited and replayable.
- Budget breach is a kill switch, not a report.
- Unauthorized tool calls (no grant) are rejected, traced, and counted as contract-hygiene violations — they never crash the run.
- Every fault outcome is classified (`completed` / `duplicate_effect` / `safe_abort` / `unresolved_outcome`) — no ambiguous outcomes.
- Scoring happens on evaluation seeds, never on seeds used for development or tuning.
- Execution is isolated: no network, no filesystem writes outside the sandbox, CPU and time limits per run.
- Every evaluated run publishes its evidence (config, score, trace, replay) — the discovery-sharing loop is part of the design, not an afterthought.

## 7. Open questions (attack these)

1. **World & task modeling.** Given the working task definition (§3): how should warehouse state be modeled so it is fully serializable, diffable, and replay-safe — event-sourced (append-only event log) vs. snapshot + operation log? What are the failure modes of each under fault injection?
2. **Agent loop architecture.** How should the bounded policy interact with the tool bay — pure decision function over serialized observations, issuing operation requests with stable keys and idempotency? Where does the budget enforcement live (per-call, per-step, per-run)?
3. **Verifier design.** How do we make the verifier genuinely independent and cheat-proof? Enumerate the reward-hacking classes it must reject (self-reported success, partial-credit gaming, state corruption, trace forgery, verifier probing).
4. **Trace, receipt & replay.** What should the JSONL trace schema and run receipt contain so replay reproduces score exactly and tampering is detectable? Content-addressing (hash chaining) worth it at this scale?
5. **Package layout.** Propose a module layout for a stdlib-only Python v0 core where world, tool bay, policy, verifier, faults, and runner are cleanly separable — building on the spike idioms (§4) — so R1/R2/R3 and live providers attach later without touching the core.
6. **Fault injection mechanics.** How to make fault tapes deterministic and replayable (seeded schedule keyed to operation keys?) while keeping outcomes classified as safe-abort vs. unresolved?
7. **Biggest risks.** What is most likely to break determinism or verifiability in this design? Where would you *not* build what we plan?
8. **Discovery-sharing & frontier mechanics.** R3 lets several agents share discoveries ("what worked") and the leaderboard tracks a frontier (beats-best-on-evaluation-seeds). How should a "discovery" be modeled — serialized record, replayable highlight, distilled policy hint? — so sharing attaches in phase 3 without touching the v0 core?

## 8. Non-goals (do not propose)

- No frameworks, providers, or services in the v0 critical path.
- No hosted deployment, no real users, no real-world integrations.
- No wide task library (one task in v0; 2–4 deeply designed max later).
- No scientific-reproduction claims, no exactly-once claims, no AGI-adjacent claims.

## 9. Output format we want back

For each open question (1–8):

- **Decision** — one clear recommendation.
- **Alternatives rejected** — and why (this is the valuable part).
- **Risks / failure modes** — what breaks, when.
- **Concrete shape** — schema, module names, or pseudocode where it helps.

End with a **summary list of ADR-style decisions** we can paste directly into our design phase, plus the top 3 things you would change about our plan.

## Appendix A — How the original REFUGIO challenge worked

This appendix summarizes the original mechanism reported in [REFUGIO Madrid's paper, "Speedrunning as a model of progress"](https://refugiomadrid.com/paper.html). It is included so the reviewer understands what this project is inspired by and what it is **not** reproducing.

### A.1 The event

- REFUGIO brought together 48 selected technical participants.
- They worked in 16 teams of three.
- The technical challenge was a four-hour hackathon.
- Teams used commercial AI coding agents.
- They all attacked the same previously unseen optimization problem.

### A.2 The warehouse task

Each team programmed a robotic warehouse:

- a `52 × 52` grid;
- `96` robots docked around the perimeter;
- `960` shelves inside the warehouse;
- targets appearing on shelves;
- robots fetching targets and delivering them to their own base.

A submission was a single Python file defining two functions:

- `create_layout()`: places the shelves;
- `act()`: chooses each robot's move, once per robot per tick.

The raw score was the number of deliveries completed in `300` ticks, summed across three hidden evaluation seeds, with a `180`-second compute budget.

### A.3 Evaluation and publication loop

The platform evaluated submissions in an isolated environment:

- a fresh microVM from a pre-built snapshot;
- no network access;
- exactly two virtual CPUs;
- five whitelisted libraries;
- a hard `240`-second wrapper timeout around the `180`-second policy budget;
- three hidden evaluation seeds;
- result and replay files written after evaluation.

Once a submission completed, the leaderboard updated and the full source code, score, and replay were published to every other team. A 30-minute cooldown was imposed after a scored submission to control server load and discourage spamming.

A submission earned points only when it beat the all-time best score. This made the competition a moving-frontier search rather than a simple pass/fail benchmark.

### A.4 Why this resembles speedrunning

The paper's central analogy is:

```text
many local searchers
        ↓
new strategy or exploit discovered
        ↓
recorded and verified
        ↓
knowledge published to everyone
        ↓
others build on the new frontier
```

Speedrunners explore different parts of a huge search space. When a record is submitted with evidence, the community learns the route and searches for the next improvement. REFUGIO transferred that mechanism to software agents and an optimization task.

The important ingredients were:

- a difficult and wide search space;
- multiple independent searchers;
- competition against a current best;
- compulsory disclosure of successful solutions;
- executable verification;
- replays and source inspection;
- tools and infrastructure that make experimentation fast.

### A.5 Discoveries and limitations reported by the paper

During the event, teams discovered important architectural improvements, including:

- a communication channel through shared global state;
- same-tick coordination between robots;
- centralized planning for all `96` robots;
- improved warehouse layouts.

The paper also reports an accidental leak of evaluation seed information. Some teams used that leak to tune layouts and schedules. The seed leak is a platform failure and is **not** an intended feature of this project.

The author later ran autonomous frontier coding agents on the same challenge. Several agents found a reasonable local strategy and then plateaued, while human-agent teams had an advantage from sharing discoveries and changing search direction. Some newer models independently found major exploits, so the result should be interpreted as evidence about that task, harness, and time period — not as a universal claim that humans outperform LLM agents.

### A.6 What Robot Factory & Arena takes from REFUGIO

We take the mechanism, not the scale:

- deterministic optimization task;
- explicit evaluation metric;
- independent policies exploring different strategies;
- human, autonomous, and shared-memory conditions;
- compulsory evidence and replay;
- a moving comparison frontier;
- visibility into how discoveries change later runs.

We deliberately do not copy:

- the `52 × 52` scale;
- `96` robots and `960` shelves;
- the original platform infrastructure;
- the accidental seed leak;
- scientific-reproduction language;
- an assumption that a short experiment can establish a general law about LLMs.
