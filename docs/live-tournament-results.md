# Live tournament results

## Result at a glance

The read-only tournament artifacts record these verifier outcomes:

- **GLM (`glm-5.3-flash`):** 9/9 condition/seed rows completed with a verified score of **100**.
- **OpenAI (`gpt-5.6-luna`), first run:** all nine rows have outcome `safe_abort`, with verified scores from **40 to 80**.
- **OpenAI (`gpt-5.6-luna`), replication:** all nine rows have outcome `safe_abort`, with verified scores from **40 to 80**.

`status` is the harness row status; `outcome` and `verified_score` are the recorded result fields. Both OpenAI tournaments completed 900 successful provider requests without recorded provider errors, then exhausted the frozen 100-step budget in every row; that operational evidence distinguishes policy inefficiency under this profile from provider availability failure.

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

## Artifact source

All paths above are repository-relative references to the local, read-only tournament artifacts, which remain intentionally untracked. The complete result sources are:

- GLM leaderboard: `artifacts/live-tournaments/glm-5-3-flash/tournament-glm-5-3-flash-live-20260911-2053/leaderboard.json`.
- OpenAI first-run leaderboard: `artifacts/live-tournaments/openai-luna/tournament-openai-luna-20260911-1852/leaderboard.json`.
- OpenAI replication leaderboard: `artifacts/live-tournaments/openai-luna-replication/tournament-openai-luna-replication-20260911-234302/leaderboard.json`.

## Caveat

This is a small result set: three seeds, one bounded task, and nine rows per tournament. It is an evidence report, not a benchmark, scientific comparison, or general leaderboard claim.
