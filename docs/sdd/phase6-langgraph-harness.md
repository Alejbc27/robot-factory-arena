# Phase 6 — LangGraph Tournament Harness: invocation, acceptance, and honest limits

The harness orchestrates R1/R2/R3 tournaments over the frozen deterministic core: an
immutable tournament config freezes comparability, every core launch goes through a
registered subprocess with a frozen StrategyContext snapshot, evidence is bound
fail-closed, and one authoritative leaderboard row per `(condition, seed)` is written
from append-only logs. This document is the operator contract: how to invoke it, what
the fake acceptance proves, how to run the gated Luna acceptance, and — just as
important — what the harness does **not** guarantee.

## Quick path

```text
# 0) optional extra (the core itself stays stdlib-only)
python3 -m pip install -e ".[harness]"

# 1) fake acceptance: full R1+R2+R3 tournament, zero cost, fully deterministic
python3 -m harness --config tests/fixtures/harness/unit6-acceptance-config.json \
    --runner fake --non-interactive --root /tmp/harness-demo

# 2) inspect the prescribed artifact tree
find /tmp/harness-demo/artifacts/tournaments/<tournament_id> -type f | sort
```

`python -m harness` and `python -m harness.run` are the same entry point. Exit codes:
`0` tournament complete, `1` fail-closed contract violation (evidence paths printed to
stderr), `2` usage or config error.

## Invocation

| Flag | Meaning |
|------|---------|
| `--config FILE` | Required. Frozen tournament config JSON (§5 fields; validated and sealed before any launch). |
| `--root DIR` | Directory that receives `artifacts/` (default: cwd). |
| `--runner fake\|core` | `fake` = deterministic zero-cost subprocess CLI wrapper (acceptance only); `core` = the real `python3 -m robot_factory_arena.cli` subprocess. There is **no fallback** between them. |
| `--non-interactive` | R1 terminal auto-declines every proposal (explicit logged declines; R1 rows are `declined`, R2/R3 still complete). |
| `--answers FILE` | Scripted R1 answers: a JSON list of `{"decision": "yes"/"no"}` or `{"decision": "edit", "guidance": [...]}` objects, validated **before** any side effect, replayed in prompt order, defaulting to `yes` when exhausted (the Unit-4 seam). |
| `--timeout N` | Optional subprocess timeout in seconds. |

With neither `--non-interactive` nor `--answers`, R1 prompts interactively on stdin:
each proposal view is printed as canonical JSON and one JSON answer object is read per
prompt (exact Unit-4 answer contract; closed input fails closed). The runner and the
terminal are injectable seams (`harness.subprocess_adapter.local_runner`,
`harness.interruption`); the CLI wires them, and tests can replace both.

The driver prefers the compiled LangGraph topology when the optional extra is
installed and otherwise uses an identical stdlib walker over the same node/route
functions (`harness.driver.run_tournament` / `drive`). Both paths are held
byte-identical by the acceptance tests.

## What the fake acceptance proves

`python3 -m unittest tests.test_harness_acceptance` (or the command above) runs one
full R1+R2+R3 tournament over seeds 18/24/30 with K=3 using the fake wrapper, which
writes valid canonical receipt triples (`receipt.json`, `llm/provider-run.json`,
`llm/llm-receipt.json`) and R3 discovery candidates. It locks:

- exactly **nine rows** (3 conditions × 3 seeds); auto-declined R1 pairs allocate
  **no attempt at all** (6 launched attempts, all ids unique);
- **receipt-owned scores**: each row's `verified_score`/`outcome` equal the sealed
  terminal receipt's values, and `core_receipt_digest` matches the receipt bytes'
  SHA-256. The fake's stdout prints a deliberate `verified_score: 999` decoy that
  never becomes official;
- **provider-evidence-owned tokens**: usage sums exist only because the five
  `llm-receipt` digest bindings verified; declined rows carry explicit nulls;
- **determinism**: two independent runs produce **byte-identical** `leaderboard.json`;
- **digest chain intact**: every `attempts/<id>/launch.json` context digest still
  matches its frozen snapshot bytes;
- **append-only continuity**: all three logs (`attempt-log.jsonl`,
  `decision-log.jsonl`, `discoveries.jsonl`) verify as digest-chained sequences;
- **fail-closed exits**: a mid-log content substitution (valid JSON, same `seq`) and a
  deleted log line both exit `1` via log-integrity verification; tournament-config
  drift on restart exits `1`; missing/malformed config and unknown flags exit `2`.

The fake acceptance costs nothing and contacts no network.

## Gated Luna live acceptance (documented procedure — run it deliberately)

1. **Prereqs**: `python3 -m pip install -e ".[harness]"`; export
   `OPENAI_API_KEY` in the shell environment. The key is read by the existing core
   provider path at launch time only — it must never be placed in the tournament
   config, answers file, or any artifact; the evidence loader's secret scan fails
   closed if a secret pattern ever reaches an artifact.
2. **Write a frozen tournament config** with the pinned provider/model, e.g.
   `provider: "openai"`, `model: "gpt-5.6-luna"`, `generation` carrying the
   AMEND-4A-2 Luna settings (including `reasoning_effort: none`), the standard frozen
   seeds `[18, 24, 30]`, conditions, `k_attempts`, and a **new unique**
   `tournament_id`. Keep provider/model/generation constant across R1/R2/R3 — one
   tournament freezes one provider identity.
3. **Run it**:

   ```text
   python3 -m harness --config <luna-config.json> --runner core --answers <answers.json> --root <dir>
   ```

   Use `--non-interactive` instead of `--answers` for a decline-only dry run; use the
   stdin terminal (no flag) to answer prompts live.
4. **Verify** (the acceptance checklist):
   - every `evidence_bound` attempt cites a receipt triple whose five digest bindings
     recompute (`core_receipt_digest`, `provider_evidence_digest`, the non-self-
     referential `llm_receipt_digest`, `prompt_digest`, `tool_schema_digest`);
   - offline **replay** of each core run matches: `python3 -m robot_factory_arena.cli
     replay <core>/runs/<logical_run_id>/<execution_id>` — replay reads only recorded
     artifacts and **never calls an LLM**;
   - the leaderboard rows are receipt-owned (scores) and provider-evidence-owned
     (tokens), with R1 declines/retries explicit;
   - token accounting: `input_tokens`/`output_tokens`/`total_tokens` sum only bound
     `provider-run.json` requests; the core CLI's estimated-cost line is derived from
     the same evidence.
5. **Do not commit credentials or live artifacts.** Tournament outputs stay untracked
   (`artifacts/` is not committed by convention).

**Cost expectations (from Phase 4A evidence, not a quote)**: one completed Luna launch
made 94–100 API requests totaling ≈86–95K input + ≈1.6–1.7K output tokens, estimated
at ≈$0.0194–$0.0205 per launch at list prices ($0.20/M input, $1.20/M output). A full
nine-pair tournament at K=3 is 9–27 launches, i.e. on the order of **$0.20–$0.55**
depending on retries; declining R1 pairs removes three launches. Treat these as
estimates recomputed from provider evidence, never as a price guarantee. GLM
(`glm-4.5`) remains optional, non-gating smoke coverage via the same core provider
path.

## Guarantees — and the two binding precision notes

| The harness guarantees | The harness does NOT guarantee |
|---|---|
| Deterministic tournament identity: frozen config, canonical snapshots, deterministic merge/barrier ordering, byte-identical leaderboards across identical runs. | Any cross-provider comparison inside one tournament (one frozen provider/model per tournament; different identities require a separate tournament id). |
| Fail-closed evidence: scores only from sealed receipts, tokens only from digest-bound provider evidence, explicit nulls instead of zeros, stdout claims never official. | A composite or aggregate score across rows; no such field exists. |
| Recovery equivalence: receipt-bound work is never relaunched; crash windows resume to the identical decision with explicit recovery events; restart never exceeds K. | Upgrading unresolved evidence to success; unresolved stays explicit. |
| Sequence/continuity validation of append-only logs, with replay revalidation of core artifacts. | **Precision note 1 (binding):** "Checkpoint/logs give SEQUENCE/CONTINUITY validation and replay revalidation — NOT general tamper-evidence/authenticity: a valid-to-valid external substitution of content is not cryptographically bound. Do not claim tamper-evidence anywhere stronger than this." |
| Internal policy identity consistency across a tournament's attempts. | **Precision note 2 (binding):** "First-bind-wins for prompt_digest/tool_schema_digest is INTERNAL consistency after the first run — not a pre-frozen tournament expectation recorded before the tournament." (The tournament config does not pre-freeze the LLM prompt/tool digests; the first bound attempt establishes them, and later binds must agree or fail closed.) |
| Replay independence: core replay never imports a policy/provider and never calls an LLM. | Any scientific, frontier, security-boundary, or exactly-once claim. |

## R1/R2/R3 semantics in one table

| Condition | Control | Launch rule | Context |
|---|---|---|---|
| R2 | Autonomous reference | No human prompt; stops at the first valid receipt or exactly K launches; retries use new ordinals. | Canonical `null` first-route snapshot per pair. |
| R1 | Human-in-the-loop | Every launch is gated by an injectable approval terminal (`yes`/`no`/`edit`); declines are explicit events with no attempt allocated; edits are logged before their follow-up prompt. | Approved guidance enters ONLY through the canonical StrategyContext pipeline (frozen, digest-bound). |
| R3 | Shared discovery | Three fixed branches (agent-01→18, agent-02→24, agent-03→30); round barriers wait for every branch's prior-round resolution; one launch per branch per round. | Round snapshots freeze only receipt-bound, sealed discoveries, merged by declared identity; a branch never sees its own same-round discoveries. |

## Digest chain and artifact layout

```text
canonical StrategyContext bytes → SHA-256 → strategy_context_digest
  → CLI-reported strategy_context_digest line → policy_digest → logical_run_id / receipt
```

Every launch records the snapshot path/digest before process start; the chain is
verified fail-closed at evidence binding. External artifact tree per tournament
(`artifacts/tournaments/<tournament_id>/`):

```text
config.json              # sealed once; drift rejected forever after
contexts/strategy-context-NNN.json
checkpoints/checkpoint-NNN.json   # pre_prompt .. post_binding boundaries
decision-log.jsonl  attempt-log.jsonl  discoveries.jsonl   # LF-terminated, chained
attempts/<attempt_id>/launch.json (+ observed.json, stdout.bin, stderr.bin)
leaderboard.json
core/runs/<logical_run_id>/<execution_id>/   # untouched core artifacts
```

## Recovery windows (Units 3–5 contracts, unchanged)

- **Launch window (U3)**: a registered-but-unobserved launch is ambiguous — counted,
  never rerun, classified `crash_window_unobserved` with an explicit recovery event
  and a new ordinal; an observed attempt binds its own evidence; a bound terminal
  receipt is never repeated; exactly-K is never exceeded. Timeouts/non-zero exits
  resolve unresolved BEFORE any binding, even with a valid run dir on disk.
- **Approval/recovery (U4)**: checkpoints at pre-prompt, post-answer (write-ahead
  answer replay), pre-launch, post-registration, post-binding; restart replays the
  answer instead of re-prompting; standing grants are never re-prompted; truncated or
  overwritten logs fail closed at deps construction.
- **R3 barrier/admission (U5)**: rounds open only when every branch is terminal or has
  resolved its prior-round attempt; pending work drains deterministically;
  discoveries are sealed write-ahead BEFORE the bind and promoted exactly once, so a
  crash in the bind→admit window still lands in the next snapshot; a frozen snapshot
  is never overwritten with different bytes.

## Version and budget notes

- No version bumps were needed for Unit 6: `HARNESS_VERSION` stays `0.1.0`, config
  schema stays `1`, and the core is untouched (`core_version: "0"`).
- The fake acceptance is zero-cost and offline; the Luna procedure references the
  Phase 4A estimates above. Optional GLM smoke stays non-gating.
