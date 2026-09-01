# REFUGIO Research Prompt — verify, deepen, connect

> Copy this entire document as the first message of a **new** ChatGPT conversation (separate from any architecture review). You have web browsing: use it.

## Your role

You are a technical researcher. Investigate the REFUGIO Madrid experiment from primary sources, verify a set of facts we already believe, and connect the findings to a project that applies its mechanism. Do not invent facts: if something cannot be verified from a source, mark it **UNVERIFIED**.

## Part 1 — Research tasks

1. Fetch and read the primary source fully: [REFUGIO Madrid — "Speedrunning as a model of progress"](https://refugiomadrid.com/paper.html).
2. Search for secondary sources: the author's write-ups, participant posts/threads (X, Reddit, HN, blogs), and any related discussion of the event.
3. Extract with fidelity:
   - **Event format**: participants, team structure, duration, what tools/agents teams used.
   - **The warehouse task**: grid size, robot count and placement, shelf count, tick count, the submission interface (`create_layout()`, `act()`), scoring metric.
   - **Evaluation infrastructure**: isolation mechanism, resources (CPUs, libraries, timeouts), hidden seeds, replay generation.
   - **The frontier rule**: how scoring against the all-time best worked, cooldowns, and the publication of code + score + replay to all teams.
   - **The speedrunning analogy** as the paper frames it (not a generic summary — the paper's own framing).
   - **What teams discovered**: techniques, coordination mechanisms, layout improvements — with the timeline if the paper gives one.
   - **The seed leak incident**: what happened, its effect.
   - **The follow-up experiment**: autonomous coding agents on the same task — how they behaved (plateauing, local optima), what human+AI teams did differently, which later models found exploits.
   - **The paper's own conclusions and stated limitations** — in the author's words where possible.

## Part 2 — Facts to verify

We currently believe the following. Check each against the source and flag any discrepancy with the exact wording from the paper:

| # | Our current fact |
|---|------------------|
| 1 | 48 selected participants, 16 teams of 3, four-hour hackathon, commercial AI coding agents. |
| 2 | Task: 52×52 grid, 96 robots on the perimeter, 960 shelves, targets appear on shelves, robots deliver to their base. |
| 3 | Submission = one Python file: `create_layout()` + `act()` (one move per robot per tick). |
| 4 | Score = deliveries in 300 ticks, summed across 3 hidden evaluation seeds; 180-second compute budget. |
| 5 | Evaluation: fresh microVM, no network, 2 vCPUs, 5 whitelisted libraries, 240-second wrapper timeout. |
| 6 | Publication: after evaluation, full source + score + replay published to every team; 30-minute cooldown per scored submission. |
| 7 | A submission earned points only when it beat the all-time best (moving-frontier search). |
| 8 | Discoveries included: communication via shared global state, same-tick coordination, centralized planning for all 96 robots, better layouts. |
| 9 | An accidental evaluation-seed leak let some teams tune to the seeds (platform failure). |
| 10 | In the follow-up, autonomous agents plateaued in local optima while human+AI teams advanced via shared discoveries; some newer models later found major exploits — so the claim is task- and time-bound, not universal. |
| 11 | **Disputed by a reviewer**: our appendix says "96 robots docked around the perimeter"; the reviewer claims there were 96 perimeter **bases/docking stations** and robots started on the adjacent interior cell. Which is right per the paper? |
| 12 | **Disputed by a reviewer**: our appendix says `act()` "chooses each robot's move once per robot per tick"; the reviewer claims it chooses each robot's **action** — `PICKUP`, `DROP`/place and `WAIT` are not movements. Which is right per the paper? |

## Part 3 — My project context (how I apply it)

I am building **Robot Factory & Arena**: a deterministic, reproducible agent-evaluation system. A factory assembles bounded agent workers (authorized tools, budgets, QC, release gate); an arena measures how different control strategies attack the same seeded optimization task. I reproduce REFUGIO's **mechanism at small scale, not its size**:

> explore → evaluate → publish evidence → learn from others → beat the frontier

- **v0 (staged first)**: 10×10 grid, 1 robot, 5–10 orders, 100-action budget, 1 public seed + 1 evaluation seed, deterministic stdlib-only Python core, programmatic policies (no LLM yet), independent anti-cheat verifier, fault tapes, JSONL trace + replay + static report.
- **Later phases**: R1 human-directed, R2 autonomous, R3 swarm with shared memory of discoveries; local leaderboard where a run advances the frontier only by beating the best on evaluation seeds; live LLM providers arrive last, one pinned provider per run.
- **Deliberately not copied**: the 52×52 scale, the original platform, the seed leak, scientific-reproduction claims.

## Part 4 — Deliverables

Produce, in this order:

1. **Verified REFUGIO fact sheet** — with citations or short quotes from the source for each key fact.
2. **Discrepancy list** — every row of the Part 2 table that differs from the source, with the exact source wording. If everything matches, say so explicitly.
3. **"What REFUGIO demonstrated"** — 3 to 5 precise, defensible claims with their limits, phrased so they survive interview scrutiny.
4. **"How I apply it"** — one table mapping each REFUGIO mechanism to the corresponding element of my project, plus **3 likely interview questions** about REFUGIO and strong answers grounded in the source.
5. **Optional (max 3)**: mechanisms described in the paper that I am not using but could strengthen the project, with the trade-off.

**Respond in Spanish** (technical terms in English are fine).
