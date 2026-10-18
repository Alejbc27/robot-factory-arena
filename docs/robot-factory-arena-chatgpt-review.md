# Robot Factory & Arena — Brief for External Architecture Review

> **Use:** Upload this file to ChatGPT Web and select a high-reasoning model. Then paste the review prompt at the end of this document.
>
> **Repository:** `robot-factory-arena`
>
> **Current status:** SDD exploration is complete. The application code has not been implemented yet. The next SDD artifact is the formal proposal.

## 1. What we are building

Robot Factory & Arena is a small, local, reproducible system for studying LLM-agent search, orchestration, reliability, evaluation, and shared discoveries.

It has two layers:

- **Factory:** assembles bounded robot workers with a provider/model, tools, permissions, budgets, quality control, traces, and a release gate.
- **Arena:** runs different control conditions against the same seeded optimization task and compares their results.

The project is inspired by the mechanism of REFUGIO Madrid, not a scientific reproduction of it.

## 2. Decisions already taken

### 2.1 First vertical slice

The first slice must include the complete experimental shape, but at deliberately small scale:

- a real LLM;
- LangGraph;
- Langfuse;
- two LLM providers: OpenAI and GLM;
- R1 human-directed;
- R2 autonomous;
- R3 swarm with shared memory;
- one deeply designed deterministic warehouse/assignment task;
- independent verification;
- hard budgets;
- deterministic fault injection;
- traces, receipts, replay, and a static report.

The project must not start by building a general-purpose multi-agent platform.

### 2.2 Deterministic core

The simulator and verifier must remain independent of the LLM:

```text
serialized observation
        ↓
LLM or policy selects an action
        ↓
tool bay checks authorization and budget
        ↓
deterministic simulator applies or rejects it
        ↓
independent verifier scores the state
```

The LLM decides through a bounded interface. It must not directly mutate the world or bypass the tool bay.

The core should be small and serializable:

- small seeded warehouse/grid;
- one robot initially, unless the swarm condition requires a minimal extension;
- a small number of orders;
- actions such as `move`, `pick`, `place`, and optionally `assemble`;
- explicit world state;
- no hidden mutable state.

### 2.3 R1 — Human-directed

R1 must represent meaningful human-plus-AI collaboration without requiring the human to choose every movement.

Recommended shape:

```text
human gives strategic guidance
        ↓
LLM executes a short bounded episode
        ↓
human reviews and may change direction
```

Human intervention happens at explicit checkpoints between short episodes. The human may change strategy, reject a plan, or provide new instructions.

### 2.4 R2 — Autonomous

R2 is one LLM agent operating without human intervention during the run.

It must have hard limits on:

- model calls;
- actions;
- elapsed time;
- token usage;
- cost;
- failed attempts.

A budget breach is a kill-switch outcome, not merely a warning.

### 2.5 R3 — Minimal swarm

R3 starts with the smallest credible shared-discovery swarm:

```text
coordinator
├── explorer A
└── explorer B
```

The explorers investigate alternatives. The coordinator schedules or selects experiments. Discoveries are stored in an append-only, serializable, auditable shared store.

Do not begin with three peer agents and a complex communication protocol.

### 2.6 Two providers: OpenAI and GLM

Both providers must be available through one adapter:

```text
ProviderAdapter
├── OpenAI implementation
└── GLM implementation
```

Rules:

- Pin exactly one provider and model configuration per run.
- Record provider, model, configuration, latency, tokens, cost, errors, prompts, responses, and tool calls.
- Do not silently switch provider during a run.
- Defer automatic fallback because it confounds provider comparison and complicates replay.
- Use the same task, seed, action budget, and verifier when comparing providers.

Do not immediately run the complete Cartesian product of providers × conditions × seeds × faults.

Recommended staged comparison:

1. Baseline, OpenAI, and GLM on the same small task and seed.
2. R1, R2, and R3 with one pinned primary provider to compare conditions cleanly.
3. Repeat at least R2 with the second provider.
4. Expand provider comparisons only if the smoke runs are cheap and stable.

### 2.7 LangGraph

LangGraph orchestrates the first end-to-end flow, but it does not own the truth of the simulated world.

Expected graph responsibilities:

- represent agent/factory state;
- route observation, decision, authorization, execution, verification, and checkpoints;
- support R1, R2, and R3;
- expose bounded transitions and stop conditions;
- preserve state needed for traces and replay.

Conceptual graph:

```text
initialize → observe → decide → authorize/budget-check
     → execute tool → verify → continue / checkpoint / stop
```

The simulator, tool contracts, verifier, budgets, and replay remain ordinary testable Python components behind the graph.

### 2.8 Langfuse

Langfuse is included from the beginning as the observability layer for real LLM runs.

It should trace:

- LangGraph node transitions;
- model calls and responses;
- provider/model identifiers;
- tokens, cost, and latency;
- tool calls and authorization decisions;
- budgets and kill-switch events;
- verifier results;
- fault events;
- run and receipt identifiers.

Secrets must never enter traces, receipts, or reports. Local JSONL remains the portable evidence format; Langfuse is the queryable observability view.

### 2.9 Replay and nondeterminism

A fresh LLM call is not assumed to reproduce the same token sequence. Replay must therefore persist enough evidence to reproduce the recorded effects:

- serialized observations;
- model requests and responses or resulting action decisions;
- tool results;
- fault events;
- action sequence;
- final state and verifier score.

The defensible claim is:

> A recorded LLM execution can be independently replayed and verified against the deterministic environment.

We must not claim that a fresh call to the same model is perfectly deterministic.

## 3. Minimum experiment design

The first experiment should remain small enough for one focused week:

- one deterministic task;
- one public development seed;
- one held-out evaluation seed;
- one greedy baseline;
- R1, R2, and R3;
- nominal and fault-injected executions;
- OpenAI and GLM available through the same adapter;
- one provider pinned per run;
- replay and regression checks for every recorded run.

The exact provider × condition matrix should be finalized after cheap smoke runs. The design must prioritize clean comparisons over maximum run count.

## 4. Evidence and scoring

Every run reports four separate signals, never one collapsed score:

1. **Task quality:** independent deterministic verifier score.
2. **Efficiency:** actions, model calls, tokens, cost, and wall time.
3. **Robustness:** behavior under deterministic fault tapes.
4. **Contract hygiene:** permission violations, idempotency breaches, audit completeness, and invalid calls.

The verifier must reject:

- self-reported success;
- partial-credit gaming;
- state corruption;
- forged traces;
- unauthorized tools;
- reward hacking;
- invalid operations disguised as successful actions.

## 5. Faults, permissions, and safety

The tool bay must enforce least privilege and explicit grants. Invalid or unauthorized calls are rejected, traced, and counted; they must not crash the evaluator.

Initial deterministic fault types may include:

- response lost after commit;
- duplicate delivery;
- process restart between commit and checkpoint.

Every outcome must be classified, for example:

- `completed`;
- `duplicate_effect`;
- `safe_abort`;
- `unresolved_outcome`.

No provider key may enter a trace, receipt, report, prompt artifact, or shared discovery store.

## 6. Scope and honesty constraints

### Included

- real LLM calls;
- OpenAI and GLM adapters;
- LangGraph;
- Langfuse;
- tiny R1/R2/R3 conditions;
- deterministic simulator and verifier;
- hard action/token/time/cost budgets;
- fault injection;
- JSONL traces;
- receipts;
- replay;
- static `report.html`.

### Deferred

- large task libraries;
- large swarms;
- automatic provider fallback;
- full provider × condition × seed × fault matrices;
- production deployment;
- multi-tenant infrastructure;
- model training or fine-tuning;
- complex long-term memory;
- broad framework comparisons.

### Claims we may make

- We built a local, bounded LLM-agent evaluation loop.
- We compared explicitly defined control conditions on a common deterministic task.
- We measured quality, efficiency, robustness, and contract hygiene separately.
- We traced real provider interactions.
- We replayed recorded effects and independently verified outcomes.
- We tested whether shared discoveries changed behavior in this local experiment.

### Claims we must not make

- This is a scientific reproduction of REFUGIO.
- The results generalize to all LLM agents or AGI.
- Fresh LLM reruns are perfectly deterministic.
- The sandbox provides exactly-once effects.
- The prototype is production-ready.
- A one-week experiment proves a statistically general law.

## 7. Proposed implementation order

1. Define the task, world schema, actions, and verifier invariants.
2. Build the deterministic simulator and greedy baseline.
3. Build the independent verifier and anti-cheat cases.
4. Add tools, authorization, idempotency, and budgets.
5. Add the minimal LangGraph flow.
6. Add OpenAI and GLM provider adapters.
7. Add Langfuse and portable JSONL traces.
8. Add R1 checkpoints, R2 autonomous execution, and R3 coordinator plus two explorers.
9. Add deterministic faults and classified outcomes.
10. Add receipts, replay, and `report.html`.
11. Run the smallest comparison matrix and document limitations.

The first milestone is one complete end-to-end run with evidence. Do not expand the task or swarm before that run is replayable and independently verified.

---

# Appendix — How the original REFUGIO challenge worked

Source: [REFUGIO Madrid — “Speedrunning as a model of progress”](https://refugiomadrid.com/paper.html)

This appendix describes the original event as reported by its author. It explains the mechanism that inspired Robot Factory & Arena; it is not a claim that our project reproduces the original experiment.

## A.1 Event format

- 48 selected technical participants.
- 16 teams of three.
- Four-hour hackathon.
- Commercial AI coding agents.
- One previously unseen optimization problem for all teams.

## A.2 Warehouse task

Each team programmed a robotic warehouse:

- `52 × 52` grid;
- `96` robots around the perimeter;
- `960` shelves;
- targets appearing on shelves;
- robots retrieving targets and delivering them to their own base.

A submission was one Python file exposing two functions:

- `create_layout()`: placed the shelves;
- `act()`: selected each robot's action once per robot per tick.

The raw score was the number of deliveries completed in `300` ticks, summed over three hidden evaluation seeds, under a `180`-second compute budget.

## A.3 Evaluation and publication loop

The platform evaluated submissions in an isolated environment:

- fresh microVM from a pre-built snapshot;
- no network access;
- two virtual CPUs;
- five whitelisted libraries;
- hard `240`-second wrapper timeout around the `180`-second policy budget;
- three hidden evaluation seeds;
- result and replay files written after evaluation.

After evaluation, the leaderboard updated and the complete source, score, and replay were published to every other team. A 30-minute cooldown followed a scored submission to control server load and discourage spam.

A submission earned points only when it beat the all-time best. This created a moving frontier rather than a simple pass/fail benchmark.

## A.4 The speedrunning mechanism

The central loop was:

```text
many local searchers
        ↓
new strategy or exploit discovered
        ↓
solution executed and verified
        ↓
source, score, and replay published
        ↓
others build on the new frontier
```

The paper connects this to speedrunning communities: players explore different parts of a huge search space, publish evidence for records, and allow later players to start from the latest known frontier.

The important mechanisms were:

- a difficult and wide search space;
- multiple independent searchers;
- competition against the current best;
- compulsory disclosure of successful solutions;
- executable verification;
- source inspection and replay;
- tools and infrastructure that make experimentation fast.

## A.5 Discoveries and limitations

The paper describes discoveries including:

- communication through shared global state;
- same-tick coordination;
- centralized planning for all `96` robots;
- improved warehouse layouts.

It also reports an accidental leak of evaluation seed information. Some teams used that leak to tune layouts and schedules. This was a platform failure, not an intended feature, and Robot Factory & Arena must not copy it.

The author later ran autonomous frontier coding agents on the same challenge. Several found reasonable local strategies and then plateaued, while human-agent teams advanced by sharing discoveries and changing search direction. Later models independently found some major exploits. These results are specific to that task, harness, model generation, and time period; they are not universal claims about humans or LLMs.

## A.6 What we take from REFUGIO

We take the mechanism at small scale:

- deterministic optimization task;
- explicit metric;
- independent search policies;
- human, autonomous, and shared-memory conditions;
- executable verification;
- traces and replay;
- publication of evidence;
- observation of whether shared discoveries improve the search frontier.

We do not copy:

- the original scale;
- the original platform infrastructure;
- the accidental seed leak;
- scientific-reproduction language;
- an assumption that a short experiment proves a general theory of LLMs.

---

# Prompt to paste into ChatGPT Web

Read the attached file completely before answering. Treat it as the current project brief, not as a request to write code.

Act as a senior architect and experimental-design reviewer. Be critical and specific. Do not merely validate the plan and do not turn the response into a generic multi-agent framework recommendation.

Review:

1. Whether the first vertical slice with a real LLM, LangGraph, Langfuse, OpenAI, GLM, R1, R2, and R3 is feasible in one focused week.
2. Whether using two providers improves the experiment or creates confounding variables.
3. Whether the proposed R1 checkpoint interaction is genuinely human-directed.
4. Whether the two-explorer-plus-coordinator R3 is sufficient to test shared discovery.
5. Whether LangGraph is properly bounded and whether Langfuse adds enough value from day one.
6. Whether replay, independent verification, permissions, idempotency, budgets, and fault tapes produce defensible evidence.
7. Whether the proposed comparison matrix is statistically or experimentally meaningful at this prototype scale.
8. Which parts are must-fix, acceptable for a portfolio prototype, or better deferred.
9. What you would simplify without removing the explicit requirements for LLM, LangGraph, Langfuse, OpenAI, GLM, R1, R2, and R3.
10. Whether the interpretation of the original REFUGIO experiment is accurate and what important differences remain.

Structure the response as:

A. Executive verdict
B. What is strong
C. Critical blockers before implementation
D. One-week feasibility assessment
E. Recommended architecture
F. Recommended experiment matrix
G. OpenAI/GLM provider strategy
H. LangGraph/Langfuse assessment
I. Replay and reproducibility assessment
J. Verifier, safety, and anti-cheat assessment
K. What to postpone
L. Final smallest credible implementation plan
M. Acceptance criteria and failure criteria
N. Three decisions to make before coding

For every criticism, explain the technical reason and propose a concrete correction. Distinguish facts, assumptions, risks, and recommendations. Do not invent REFUGIO facts. Do not claim scientific replication or general conclusions about LLMs.

Respond in Spanish, keeping technical names and code identifiers in English.
