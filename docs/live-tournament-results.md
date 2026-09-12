# Live tournament results

## Result at a glance

The read-only tournament artifacts record these verifier outcomes:

- **GLM (`glm-5.3-flash`):** 9/9 condition/seed rows completed with a verified score of **100**.
- **OpenAI (`gpt-5.6-luna`), first run:** all nine rows have outcome `safe_abort`, with verified scores from **40 to 80**.
- **OpenAI (`gpt-5.6-luna`), replication:** all nine rows have outcome `safe_abort`, with verified scores from **40 to 80**.

`status` is the harness row status; `outcome` and `verified_score` are the recorded result fields. Each OpenAI tournament's nine bound reports contain 900 successful provider requests (100 per report), with zero recorded provider request errors. Every bound run then exhausted the frozen 100-step budget; that operational evidence distinguishes policy inefficiency under this profile from provider availability failure. These counts describe the bound reports, not additional physical attempts such as the unresolved first attempt documented below.

## Exact condition/seed rows

| Tournament | Condition | Seed | Status | Outcome | Verified score | Attempts |
|---|---:|---:|---|---|---:|---:|
| GLM-5.3-Flash | R1 | 18 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R1 | 24 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R1 | 30 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R2 | 18 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R2 | 24 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R2 | 30 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R3 | 18 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R3 | 24 | completed | completed | 100 | 1 |
| GLM-5.3-Flash | R3 | 30 | completed | completed | 100 | 1 |
| OpenAI, first run | R1 | 18 | aborted | safe_abort | 60 | 1 |
| OpenAI, first run | R1 | 24 | aborted | safe_abort | 40 | 1 |
| OpenAI, first run | R1 | 30 | aborted | safe_abort | 60 | 1 |
| OpenAI, first run | R2 | 18 | evidence_invalid | safe_abort | 80 | 2 |
| OpenAI, first run | R2 | 24 | aborted | safe_abort | 80 | 1 |
| OpenAI, first run | R2 | 30 | aborted | safe_abort | 40 | 1 |
| OpenAI, first run | R3 | 18 | aborted | safe_abort | 60 | 1 |
| OpenAI, first run | R3 | 24 | aborted | safe_abort | 60 | 1 |
| OpenAI, first run | R3 | 30 | aborted | safe_abort | 60 | 1 |
| OpenAI, replication | R1 | 18 | aborted | safe_abort | 60 | 1 |
| OpenAI, replication | R1 | 24 | aborted | safe_abort | 60 | 1 |
| OpenAI, replication | R1 | 30 | aborted | safe_abort | 40 | 1 |
| OpenAI, replication | R2 | 18 | aborted | safe_abort | 80 | 1 |
| OpenAI, replication | R2 | 24 | aborted | safe_abort | 80 | 1 |
| OpenAI, replication | R2 | 30 | aborted | safe_abort | 40 | 1 |
| OpenAI, replication | R3 | 18 | aborted | safe_abort | 40 | 1 |
| OpenAI, replication | R3 | 24 | aborted | safe_abort | 80 | 1 |
| OpenAI, replication | R3 | 30 | aborted | safe_abort | 60 | 1 |

## R1 human-approval evidence

The R1 human-guidance gate records `approval_granted` at `proposal_round: 0` for seeds **18, 24, and 30** in each tournament:

- GLM: `artifacts/live-tournaments/glm-5-3-flash/tournament-glm-5-3-flash-live-20260911-2053/decision-log.jsonl` (sequences 12, 16, 20).
- OpenAI first run: `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/decision-log.jsonl` (sequences 17, 21, 25).
- OpenAI replication: `artifacts/live-tournaments/openai-luna-replication/tournament-openai-luna-replication-20260911-234302/decision-log.jsonl` (sequences 12, 16, 20).

## OpenAI first-run R2 seed 18

The first OpenAI tournament's `R2 / 18` row is `evidence_invalid`, outcome `safe_abort`, verified score **80**, and `attempts_used: 2`. The first registered attempt records `evidence_unresolved` with the exact reason `crash_window_unobserved`; recovery uses attempt ordinal 2, whose bound run verifies the safe-abort score of 80.

- Row: `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/leaderboard.json`.
- First attempt registration: `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/attempts/r2-agent-01-seed-18-attempt-01/launch.json`.
- Unresolved event and retry evidence: `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/attempt-log.jsonl` and `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/decision-log.jsonl`.
- Bound retry receipt: `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/core/runs/lr_7c5b310cd570bd383fd35651cdf447a0cedf81d4361a0ea79f895652d66e15e3/execution-d3b6bb9b40f245deb2ee688d1db2bdd6/receipt.json`.

## README replay screenshot provenance

The README screenshot is derived from the terminal frame of this exact validated projection:

| Field | Value |
|---|---|
| Source tournament | `tournament-glm-5-3-flash-live-20260911-2053` |
| Condition / seed | R1 / 18 |
| Logical run | `lr_bb8a537de98cfea03fae440d04797dc89fb9b176bb1c16fd9035b474ec348d84` |
| Execution | `execution-1ebab7a469c8439e979992ea35254b53` |
| Source | verified `visualizer.html`, terminal frame 92 / 92 |
| Receipt file SHA-256 | `sha256:bee8684c2679e929f91e3afd5743add6d71740e2ab9a365b747df85abb086318` |
| Embedded projection SHA-256 | `sha256:0a25b4b828d7cd08e7ba8d3c55722d4a4a4204ca2120b4a7584a68c7048bcf2d` |
| Output | `docs/assets/visualizer-overview.png` |

The source tournament artifacts remain intentionally untracked; the identifiers and digests provide a stable provenance record without publishing those generated artifacts.

## Artifact source

All paths above are repository-relative references to the local, read-only tournament artifacts, which remain intentionally untracked. The complete result sources are:

- GLM leaderboard: `artifacts/live-tournaments/glm-5-3-flash/tournament-glm-5-3-flash-live-20260911-2053/leaderboard.json`.
- OpenAI first-run leaderboard: `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/leaderboard.json`.
- OpenAI replication leaderboard: `artifacts/live-tournaments/openai-luna-replication/tournament-openai-luna-replication-20260911-234302/leaderboard.json`.

## Caveat

This is a small result set: three seeds, one bounded task, and nine rows per tournament. It is an evidence report, not a benchmark, scientific comparison, or general leaderboard claim.
