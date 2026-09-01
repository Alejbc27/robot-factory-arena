# Spec amendments — robot-factory-arena v0

Accepted amendments to the v0 specification (Engram observation 6170 / working copy `docs/sdd/spec.md`). Each amendment is a maintainer decision recorded before the implementing batch runs.

## AMEND-01 — Completion-demonstrator fixture seed

- **Accepted**: 2026-09-01, maintainer `alejandrobosch` (interactive decision, SDD apply phase).
- **Context**: locked seeds 17 (public) and 29 (evaluation/plumbing) both generate `n = 10` orders (`5 + s mod 6` with `17 mod 6 = 29 mod 6 = 5`). The reference policies need ~190 policy steps for 10 orders, so every full golden run on 17/29 ends `safe_abort` at the locked 100-step budget. REQ-05 requires the faulted fixture to finish with outcome `completed` — impossible on a 10-order run.
- **Decision**: add a third fixture seed with `n = 5` orders as the **completion demonstrator**. Deterministic selection rule: the first seed `s` in the ordered candidate list `(18, 24, 30, 36)` whose full `greedy-v0` run completes all orders within the 100 `policy_steps` budget; expected winner is 18 (`q = 8`, `n = 5`). B6 (T-24) verifies the measured step count and freezes the chosen seed plus its golden artifacts. `tests/fixtures/fault_targets.json` gains the completion-seed fault entry (first delivery `place`, same derivation method); the existing 17/29 entries are unchanged.
- **Scope**: fixture set only. REQ-01 formulas, seeds 17/29 and their roles, budget 100, all locked schemas, and every other requirement are unchanged. The REQ-05 faulted-delivery demonstrator runs on the completion seed so the run ends `completed` with one effect, one internal retry, and no duplicate reward.
