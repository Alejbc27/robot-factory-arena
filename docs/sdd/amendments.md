# Spec amendments — robot-factory-arena v0

Accepted amendments to the v0 specification (Engram observation 6170 / working copy `docs/sdd/spec.md`). Each amendment is a maintainer decision recorded before the implementing batch runs.

## AMEND-01 — Completion-demonstrator fixture seed

- **Accepted**: 2026-09-01, maintainer `alejandrobosch` (interactive decision, SDD apply phase).
- **Context**: locked seeds 17 (public) and 29 (evaluation/plumbing) both generate `n = 10` orders (`5 + s mod 6` with `17 mod 6 = 29 mod 6 = 5`). The reference policies need ~190 policy steps for 10 orders, so every full golden run on 17/29 ends `safe_abort` at the locked 100-step budget. REQ-05 requires the faulted fixture to finish with outcome `completed` — impossible on a 10-order run.
- **Decision**: add a third fixture seed with `n = 5` orders as the **completion demonstrator**. Deterministic selection rule: the first seed `s` in the ordered candidate list `(18, 24, 30, 36)` whose full `greedy-v0` run completes all orders within the 100 `policy_steps` budget; expected winner is 18 (`q = 8`, `n = 5`). B6 (T-24) verifies the measured step count and freezes the chosen seed plus its golden artifacts. `tests/fixtures/fault_targets.json` gains the completion-seed fault entry (first delivery `place`, same derivation method); the existing 17/29 entries are unchanged.
- **Scope**: fixture set only. REQ-01 formulas, seeds 17/29 and their roles, budget 100, all locked schemas, and every other requirement are unchanged. The REQ-05 faulted-delivery demonstrator runs on the completion seed so the run ends `completed` with one effect, one internal retry, and no duplicate reward.

## AMEND-v0-2 — Verifier `tool_attempts` derivation excludes parse-level rejections

- **Accepted**: 2026-09-02, maintainer `alejandrobosch` (interactive decision, SDD apply phase; issue #8).
- **Context**: REQ-02 defines `tool_attempts` as ToolBay submissions (initial + internal retries, including rejected submissions); parse-level rejections (`malformed_call`, `unsupported_tool`, `operation_key_invalid`) never reach the ToolBay and must not count. The frozen B4 recorder deliberately writes an operation-log entry for every parse-level rejection with `tool: null` (full audit trail), while the frozen B5 verifier derived `tool_attempts = len(operations)` over the whole log. The two agree only for transcripts without parse-level garbage (reference policies, v0 golden fixtures); any LLM-policy transcript containing a mandated garbage candidate derives `tool_attempts` ≠ the runner's counters and fails verification with `artifact_mismatch @ counters.tool_attempts` (exposed by Phase 4A, escalation section of `docs/sdd/phase4a/apply-progress.md`).
- **Decision**: the verifier's counter derivation counts operation-log entries whose `tool` is non-null: `tool_attempts = |{op ∈ operations : op.tool is not null}|`. Parse-level rejection entries (tool null) stay in the operation log and the trace, fully auditable, and keep consuming their reserved `policy_steps` unit and incrementing `contract_violations`; they only stop inflating `tool_attempts`. This is escalation option 1 (verifier amendment) of the Phase 4A escalation note; the recorder's audit behavior is explicitly preserved over options 2 (route rejections to trace only) and 3 (restrict receipts to all-valid transcripts).
- **Scope**: the counter derivation in `verification/verifier.py` (+ tests) only. REQ-02's counter definitions, the operation-log schema (including `tool: null` rejection records), runner counter behavior, the receipt/trace cross-checks, all locked schemas, and the v0 golden fixtures (which contain no parse-rejection entries, so their expected values are unchanged) are untouched. Frozen-module rule: this is the single authorized verifier correction under AMEND-v0-2; no other frozen behavior changes.

## AMEND-4A-2 — Prompt wording reconciled with real-provider acceptance

- **Accepted**: 2026-09-02, maintainer `alejandrobosch` (issue #11).
- **Context**: real-provider acceptance exposed insufficient navigation guidance in the exact P4A-REQ-04 prompt wording.
- **Decision**: issue #11 supersedes that wording; the authoritative prompt is now the template frozen by the new prompt digest.
- **Scope**: prompt template and prompt digest only. Core schemas, verifier, and replay are unchanged.
