# Phase 4A Mini-Tasks — First Live LLM

> **Provenance:** Task breakdown for issue #5 / change `phase4a-first-live-llm`, derived from the binding contract in `docs/sdd/phase4a/spec.md`, design context in `docs/phase4-llm-integration-review.md`, frozen v0 constraints in `docs/sdd/spec.md`, and the frozen verification findings in `docs/sdd/verify-report.md`.
>
> **Authority and boundary:** `docs/sdd/phase4a/spec.md` is binding. This slice adds only the external LLM envelope. Do not modify `RunConfig`, `ToolCall`, `Policy`, Runner behavior, core evidence schemas, verifier, replay implementation, or files under `robot_factory_arena/{domain,runtime,evidence,verification}`. The v0 core report remains authoritative; `llm-report.html` is an external appendix.
>
> **TDD order:** For each task, write the named assertions first (RED), implement only the listed paths (GREEN), add boundary/adversarial vectors (TRIANGULATE), then simplify without widening the contract (REFACTOR). Every batch must pass the standard gate before the next batch.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 530–790 including tests and CLI wiring; generated run artifacts excluded |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: Batch A + Batch B (provider contract and policy); PR 2: Batch C (sidecars, replay assertion, CLI, acceptance evidence) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending human decision; prefer a feature-branch chain if approved |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

The two-PR recommendation follows the repository's small issue→PR slices: provider/policy semantics can be reviewed independently of artifact/export plumbing, while Batch C remains blocked on the stable policy evidence shape. If the measured implementation stays below the canonical review threshold, the owner may explicitly choose one PR; do not infer that exception during apply.

## Batch A — ProviderAdapter contract and fake provider

### T4A-01 — Define the provider-neutral request/result contract and enforce the import boundary

**Goal:** Add exact, stdlib-only `ProviderRequest`/`ProviderResult` value contracts and `invoke(request) -> result` semantics without exposing a core `ToolCall` or `operation_key`.

- **Files:** new `robot_factory_arena/providers/__init__.py`; new `robot_factory_arena/providers/adapter.py`; new `tests/test_llm_provider.py`; new `tests/test_llm_boundaries.py`.
- **P4A-REQs:** P4A-REQ-01; boundary portions of P4A-REQ-09.
- **Tests required when done:** exact top-level keys and nested usage/error/tool-call shapes; pinned provider/model echo; non-empty request IDs; integer/non-negative usage and latency; `total_tokens == input_tokens + output_tokens`; success/error exclusivity; no `operation_key`; AST/import denylist for `providers/` against `domain`, `runtime`, `evidence`, and `verification`.
- **Dependencies:** frozen v0 contracts are read-only inputs; none of the later provider, policy, CLI, or sidecar tasks.
- **Done-check:** contract tests are GREEN after TRIANGULATE vectors for null/malformed/mixed success cases; `python3 -m unittest discover -s tests -v` passes; `git diff --stat -- robot_factory_arena/domain robot_factory_arena/runtime robot_factory_arena/evidence robot_factory_arena/verification` prints no lines.
- **Estimate:** 35–55 changed lines.

### T4A-02 — Implement a deterministic finite fake provider

**Goal:** Supply a network-free, credential-free fake adapter that records every request and returns one scripted result per invocation, with no fallback or parallel behavior.

- **Files:** new `robot_factory_arena/providers/fake.py`; extend `tests/test_llm_provider.py` only.
- **P4A-REQs:** P4A-REQ-01 and its fake-provider scenario; P4A-REQ-06's provider-importability premise.
- **Tests required when done:** one exact request/result round trip; finite sequence ordering and exhaustion behavior; request retention; pinned identity; no network calls or credential reads; subprocess/import tripwire proving importing and invoking the fake does not load forbidden core modules.
- **Dependencies:** T4A-01.
- **Done-check:** fake-only tests pass without API environment variables or network; no provider-specific fields leak into the result contract; full unittest gate and frozen-directory diff gate are both empty/green.
- **Estimate:** 55–80 changed lines.

### T4A-03 — Close the provider boundary with contract-level triangulation

**Goal:** Verify that malformed provider data is rejected at the envelope boundary and that the provider package remains independently importable as a standard-library package.

- **Files:** extend `tests/test_llm_provider.py` and `tests/test_llm_boundaries.py`; only mechanical refactors in new `robot_factory_arena/providers/` files if needed.
- **P4A-REQs:** P4A-REQ-01; P4A-REQ-09.
- **Tests required when done:** adversarial mappings with extra/missing keys, booleans-as-integers, negative counts, mismatched totals, both `tool_call` and `error`, and provider/model mismatch; AST check covers all provider modules rather than only `adapter.py`.
- **Dependencies:** T4A-01 and T4A-02.
- **Done-check:** assertions distinguish contract rejection from policy rejection; fake-provider suite remains network-free; standard unittest gate passes and the frozen-core `git diff --stat` command remains empty.
- **Estimate:** 30–45 changed lines.

**Batch A gate:** Run `python3 -m unittest discover -s tests -v`; it must be green with no API key. Then run `git diff --stat -- robot_factory_arena/domain robot_factory_arena/runtime robot_factory_arena/evidence robot_factory_arena/verification`; expected output is empty. Any non-empty output stops the batch as a boundary violation.

## Batch B — LLMPolicy, frozen prompt/schema, digest, and rejection semantics

### T4A-04 — Pin the exact prompt, tool schema, and golden digest vectors

**Goal:** Freeze one English prompt template and one common JSON tool schema byte-for-byte, with canonical prompt/schema digest vectors before policy implementation.

- **Files:** new `robot_factory_arena/policies/llm.py`; new `tests/test_llm_policy.py`; extend `tests/test_llm_boundaries.py` for the policy import allowlist.
- **P4A-REQs:** P4A-REQ-03 and P4A-REQ-04.
- **Tests required when done:** exact prompt wording, placeholder replacement, UTF-8/line-ending stability, unchanged canonical observation embedding; exact schema key/order/content, one-tool allowance, no parallel calls, all three argument shapes, rejection of extra keys/unknown IDs/multiple calls; golden `prompt_template_digest` and `tool_schema_digest` values.
- **Dependencies:** T4A-01; use the existing canonical digest authority only where the spec permits; do not modify it.
- **Done-check:** RED tests fail for any wording/schema/digest drift and GREEN after implementation; AST/import test allows only the existing policy/tool-call contracts and canonical digest authority for `policies/llm.py`; full unittest and frozen-directory diff gates pass.
- **Estimate:** 45–65 changed lines.

### T4A-05 — Normalize one provider result into one legal v0 policy candidate

**Goal:** Implement `LLMPolicy` with the existing `Policy` protocol, one serialized observation per request, one adapter invocation, exact move/pick/place normalization, and deterministic rejection candidates.

- **Files:** implement/complete new `robot_factory_arena/policies/llm.py`; extend `tests/test_llm_policy.py`.
- **P4A-REQs:** P4A-REQ-02, including valid-action and absent/malformed/unsupported/invalid/multiple/error scenarios.
- **Tests required when done:** request preserves the observation and contains the exact rendered prompt/schema; valid move/pick/place output yields exactly one v0 `ToolCall` with step `used + 1` and intent labels `llm-move-D`, `llm-pick-S`, and `llm-place-O`; all rejected outputs produce a current-run `intent-llm-reject` key and the existing reasons (`malformed_call`, `unsupported_tool`, `invalid_arguments`) without raising an ordinary policy exception; no second repair call; no ToolBay/world/verifier/runtime-state access.
- **Dependencies:** T4A-02 and T4A-04; existing runner normalization behavior is immutable.
- **Done-check:** fake-provider unit tests and direct policy tests are green; provider response/error/raw text is not placed in the returned candidate; operation-key grammar and no-leading-zero step are asserted; standard gate and frozen-core diff gate pass.
- **Estimate:** 80–120 changed lines.

### T4A-06 — Prove runner accounting and logical identity through the unchanged core

**Goal:** Exercise the real v0 Runner with `LLMPolicy` to prove rejection consumes one policy step, produces existing trace/accounting behavior, has no world effect, and provider/model changes flow only through `policy_digest`.

- **Files:** new `tests/test_llm_runner_integration.py`; extend `tests/test_llm_policy.py` only for shared vectors; no changes to `robot_factory_arena/runtime/runner.py` or any frozen core file.
- **P4A-REQs:** P4A-REQ-02, P4A-REQ-03, P4A-REQ-09.
- **Tests required when done:** the `used=7` provider-error scenario reserves step 8, records `malformed_call`, increments `contract_violations`, makes no ToolBay submission/effect, and exposes `last_result.status == rejected`; valid place reaches the unchanged runner; two otherwise identical configs with different LLM model/provider digests produce different policy digests and logical IDs while retaining the exact frozen `RunConfig` key set; trace contains only existing normalized policy-decision/rejection fields.
- **Dependencies:** T4A-05 plus the existing v0 Runner test fixtures.
- **Done-check:** tests prove behavior through public interfaces rather than monkey-patching core internals; `git diff --stat` over all four frozen directories is empty; standard unittest gate is green.
- **Estimate:** 45–70 changed lines.

**Batch B gate:** All fake-provider policy and Runner integration tests pass under `python3 -m unittest discover -s tests -v` with no network or API credentials. The frozen-directory diff-stat command must print nothing. Only after this gate may sidecar or live-provider work begin.

## Batch C — External sidecars, scrubbing, appendix, replay, CLI, and acceptance

### T4A-07 — Build external provider evidence, digest binding, and fail-closed secret scrubbing

**Goal:** Serialize only the exact external `llm/provider-run.json` and `llm/llm-receipt.json` envelopes, scrub before every export boundary, and fail closed with `secret_detected` without writing unsafe artifacts.

- **Files:** new `robot_factory_arena/providers/evidence.py`; new `tests/test_llm_artifacts.py`; new generated-path fixture helpers under `tests/fixtures/llm/` only if needed.
- **P4A-REQs:** P4A-REQ-05 and P4A-REQ-07.
- **Tests required when done:** exact sidecar shapes and canonical bytes; one ordered request entry per decision attempt; provider evidence digest over exact `provider-run.json`; core receipt digest over unchanged sibling receipt; non-self-referential LLM receipt digest; logical/execution/provider/model binding; no prompt/headers/credentials/operation key; recursive field and inline-pattern redaction; residual credential/env-assignment detection; unsafe artifact is not created and rejected value never appears in errors, trace, or sidecars.
- **Dependencies:** Batch B; consume core receipt and run identifiers as mappings/paths without importing frozen core modules from `providers/`.
- **Done-check:** temp-directory tests verify sidecars live only under `run_dir/llm`; deleting that directory leaves the core receipt/replay/verifier inputs untouched; artifact tests and the full unittest/frozen-directory gates are green.
- **Estimate:** 80–120 changed lines.

### T4A-08 — Render the static escaped LLM appendix

**Goal:** Add a standard-library-only renderer for `llm-report.html` that presents external evidence and authoritative core outcome/score without changing core `report.html`.

- **Files:** new `robot_factory_arena/providers/report.py`; extend `tests/test_llm_artifacts.py`.
- **P4A-REQs:** P4A-REQ-08 and the report portions of P4A-REQ-05, P4A-REQ-07, and P4A-REQ-09.
- **Tests required when done:** renderer reads core receipt plus both sidecars; provider/model and all five requested digests; deterministic summed usage totals; two-row/per-request table with rejection/error fields; HTML escaping for response/error/request values; no `<script>`, JavaScript, network URL, CDN, external asset, or provider SDK; explicit marker that core `report.html` remains authoritative; missing/mismatched sidecars render external-evidence error without changing core outcome.
- **Dependencies:** T4A-07; renderer must accept generic mappings and must not import `domain`, `runtime`, `evidence`, or `verification`.
- **Done-check:** static HTML tests pass with metacharacters and mismatched bindings; core report/receipt/trace bytes are unchanged; full unittest and frozen-directory diff gates pass.
- **Estimate:** 75–110 changed lines.

### T4A-09 — Wire fake/live LLM runs while preserving offline replay

**Goal:** Add the smallest CLI composition hook needed to run `LLMPolicy` and write sidecars after the core receipt is released, while leaving the existing `replay` command and its provider-free import path unchanged.

- **Files:** modify only the LLM wiring points in existing `robot_factory_arena/cli.py`; new `tests/test_llm_cli.py`; new `tests/test_llm_replay.py`.
- **P4A-REQs:** P4A-REQ-05, P4A-REQ-06, P4A-REQ-08, and P4A-REQ-09.
- **Tests required when done:** fake-only CLI run emits core artifacts plus all three `run_dir/llm` sidecars; replay subprocess runs with provider API/token variables absent, providers importable, and an import/network tripwire, reproducing final-state digest, verifier-owned score, and outcome; replay does not import `LLMPolicy` or `providers`; deleting `llm/` does not affect core replay/verifier/report; LLM run/rerun wiring does not add fields to `RunConfig` or core receipt and never appends to core `report.html`.
- **Dependencies:** T4A-06, T4A-07, and T4A-08. Existing greedy/bounded CLI behavior and replay behavior are regression inputs, not rewrite targets.
- **Done-check:** all automated acceptance paths use the fake provider, no network is needed, documented replay command remains equivalent to `python3 -m robot_factory_arena.cli replay <run-directory>`, and both standard/frozen-core gates pass.
- **Estimate:** 70–100 changed lines.

### T4A-10 — Execute the single labeled live-provider acceptance run and final gate

**Goal:** Perform exactly one explicitly labeled user-assisted live OpenAI acceptance run, then replay its recorded normalized operations offline and preserve the evidence boundary.

- **Files:** generated `artifacts/runs/<logical_run_id>/<execution_id>/llm/{provider-run.json,llm-receipt.json,llm-report.html}` and sibling core artifacts; no committed provider-specific adapter file unless separately authorized because OpenAI/GLM-specific adapters are an explicit Phase 4A non-goal.
- **P4A-REQs:** P4A-REQ-06 and P4A-REQ-09, with evidence from P4A-REQ-05, P4A-REQ-07, and P4A-REQ-08.
- **Tests/evidence required when done:** prior fake-provider suite is already green; a human supplies `OPENAI_API_KEY` and approves network use; the run records pinned provider/model, scrubbed sidecars, valid core receipt, and appendix; the exact run is labeled live (not a unit/acceptance fake), then `python3 -m robot_factory_arena.cli replay <run-directory>` runs with credentials removed and matches final-state digest, verifier-owned score, and outcome.
- **Dependencies:** T4A-09 and explicit human authorization for secret-bearing network access. If the repository has no approved real-adapter injection point, stop and escalate that contract gap; do not add silent fallback, SDK-specific behavior, or weaken fake-only gates.
- **Done-check:** exactly one live run is identified in the review evidence; all other tests/runs are fake and network-free; live sidecars contain no original secret; core `report.html` remains authoritative; standard unittest gate passes without the key; frozen-directory diff-stat is empty.
- **Estimate:** 15–25 changed lines in durable wiring/docs, plus generated acceptance artifacts excluded from the review estimate.

**Batch C gate and release checklist:** First run `python3 -m unittest discover -s tests -v` with no API key; it must pass. Then inspect the live/fake run layout, run offline replay with credentials absent, verify sidecar deletion independence, and assert the frozen-directory diff-stat command has empty output. A failed sidecar check is an external-evidence failure; it must not be converted into a new core outcome or repaired by changing frozen core files.

## Final mapping and stop conditions

- P4A-REQ-01: T4A-01–03.
- P4A-REQ-02: T4A-05–06.
- P4A-REQ-03: T4A-04 and T4A-06.
- P4A-REQ-04: T4A-04–05.
- P4A-REQ-05: T4A-07–09.
- P4A-REQ-06: T4A-09–10.
- P4A-REQ-07: T4A-07–10.
- P4A-REQ-08: T4A-08–10.
- P4A-REQ-09: all tasks, with the final evidence gate in T4A-10.

Stop and report a boundary violation if any implementation proposal changes a frozen core module/schema, places provider evidence inside core artifacts, puts raw provider output in core trace, imports providers during replay, adds cost-ceiling machinery, or introduces OpenAI/GLM/LangGraph/Langfuse/R1/R2/R3 behavior into this slice.

## Amendments

- **AMEND-4A-1 (user decision, 2026-09-02):** commit a minimal **stdlib-urllib `OpenAIProvider`** as permanent code (zero third-party dependencies; stdlib-only preserved). Supersedes T4A-10's "no committed provider-specific adapter file" clause and the Phase 4A non-goal wording for the OpenAI adapter specifically. Live runs are repeatable by anyone with their own `OPENAI_API_KEY`. GLM adapter remains out of scope.
- **Delivery (user decision, 2026-09-02):** single PR for Phase 4A on branch `feat/phase4a-first-live-llm`, work-unit commits per batch. The suggested two-PR split is superseded.
