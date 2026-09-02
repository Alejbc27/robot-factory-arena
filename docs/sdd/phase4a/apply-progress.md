# SDD Apply Progress — Phase 4A "first live LLM" (issue #5)

> **Provenance:** apply-phase record for change `phase4a-first-live-llm`, executed on branch `feat/phase4a-first-live-llm` from the binding inputs `docs/sdd/phase4a/tasks.md` (incl. AMEND-4A-1 and the single-PR delivery decision) and `docs/sdd/phase4a/spec.md` (P4A-REQ-01..09), constrained by frozen `docs/sdd/spec.md` (v0 REQ-01..10) and the v0 handoff conventions in `docs/sdd/apply-progress.md`. T4A-10 (live run) is EXCLUDED from this phase per instruction — everything built is green with the FAKE provider alone, no network, no API key.
>
> **Status: T4A-01..T4A-09 COMPLETE — final gate 471/471 OK twice, frozen-core diff empty. One boundary escalation recorded for the orchestrator (see below) — it does not block the code gates but gates the shape of the T4A-10 live run.**

## Session preflight consumed

- Structured SDD status: non-authoritative (`artifactStore: engram`, `nextRecommended: resolve-via-engram`). Readiness resolved from the file artifacts the parent designated; the Engram HTTP server was unreachable during this session (`mem_search` failed: could not reach 127.0.0.7437), so the store-side tasks/spec/design observations could not be fetched — the materialized docs are authoritative here, matching every prior batch's practice.
- Review Workload Forecast gate: `Decision needed before apply: Yes`, `Chained PRs recommended: Yes`, `400-line budget risk: High` — satisfied by the recorded AMEND delivery decision (single PR on `feat/phase4a-first-live-llm`, work-unit commits per batch; session review budget 800 lines). Measured outcome below exceeds 800 — reported honestly, not gamed (no code was compressed to fit).
- Allowed edit surfaces honored: NEW `robot_factory_arena/providers/*`, `robot_factory_arena/policies/llm.py`, `cli.py` wiring points only, `tests/` additions, this progress file. **Frozen core untouched** (evidence below).

## Test gate (REQ-08 command)

- Command: `python3 -m unittest discover -s tests -v` (Python 3.14.5)
- **Run 1: Ran 471 tests — OK — 471 pass / 0 failures / 0 errors, exit 0.**
- **Run 2: Ran 471 tests — OK — 471 pass / 0 failures / 0 errors, exit 0.** (v0 baseline 354 → 471: +117 tests; baseline verified green at session start before any edit.)
- Per-module counts (new): test_llm_provider 21, test_llm_boundaries 16, test_llm_policy 28, test_llm_runner_integration 4, test_llm_artifacts 33, test_llm_openai 17, test_llm_cli 5, test_llm_replay 4 (total +117; modulo later in-batch edits).
- Frozen-core gate: `git diff --stat -- robot_factory_arena/domain robot_factory_arena/runtime robot_factory_arena/evidence robot_factory_arena/verification` → **empty output** after every batch and at the end.

## TDD Cycle Evidence

| Task | RED (observed) | GREEN (observed) | Notes |
|------|----------------|------------------|-------|
| T4A-01 | `ModuleNotFoundError: robot_factory_arena.providers.adapter` (import error; 1 test ran) | contract suite green | exact `ProviderRequest`/`ProviderResult` envelopes (to_mapping key sets), pinned schema_version 1, non-empty request ids, usage total==input+output (bool/float/str rejected), non-negative int latency, success/error exclusivity (both/neither rejected), non-empty ASCII error codes, **no operation_key anywhere** in either envelope, runtime-checkable `ProviderAdapter` protocol |
| T4A-02 | fake import error → green | fake suite green | one exact request/result round trip; finite sequence order; deterministic `script_exhausted` error on exhaustion; requests retained in order (frozen values); pinned identity enforced on adapter AND script entries; no fallback/second result; subprocess tripwire: import+invoke loads ZERO forbidden core modules with credential-bearing env vars scrubbed |
| T4A-03 | adversarial vectors (triangulation — most passed against the already-green contract, as designed) | all green | AST denylist over EVERY provider module (pkgutil walk): no domain/runtime/evidence/verification/cli imports; adapter+fake transport-free (`urllib/socket/http/ssl/asyncio` denied) with exact import allowlists (fake: only the adapter module); contract rejection (envelope construction raises) distinguished from policy rejection (runner-side, Batch B); provider/model mismatch at invoke rejected |
| T4A-04 | `ModuleNotFoundError: robot_factory_arena.policies.llm` | policy suite green | prompt template re-declared byte-for-byte in tests (wording + LF endings frozen); placeholder replaced with canonical JSON of the observation; UTF-8 digest stability; **golden digests pinned**: prompt `sha256:5aa8af30…b6c2`, schema `sha256:1dfd69cb…42c4`, fake/fake-v1 manifest `sha256:f90d2f6d…3650`; schema content locked (3 tools, max_tool_calls=1, parallel_tool_calls=false, additionalProperties=false, exact enums/patterns); digest of the exact six-key manifest; provider/model changes change the digest |
| T4A-05 | one identity-mismatch failure caught (script request_id vs derived request id) then green | normalization suite green | valid move/pick/place → exactly one v0 ToolCall at step used+1 with intent labels `llm-move-D`/`llm-pick-S`/`llm-place-O` (spec scenario: step-4 place key reproduced exactly); provider error / absent tool call → `malformed_call`; unsupported tool → `unsupported_tool`; wrong keys/values → `invalid_arguments` — all as deterministic candidates carrying the current-run `intent-llm-reject` key with canonical-safe `{}` arguments (no raw provider payload crosses into the core; runner ledger path stays canonical); adapter-folded `multiple_tool_calls` → malformed_call; exactly ONE adapter call, no repair; observation never mutated; provider text never in the returned candidate; decisions logged for external evidence |
| T4A-06 | 2 counter/reason expectations corrected to spec semantics, then green | integration suite green | REAL unchanged v0 Runner + LLMPolicy: used=7 provider error → step 8 reserved, observation@9 exposes `last_result {rejected, malformed_call, op/<lr>/step-8/intent-llm-reject}`, counters Counters(100, 7, 7, 0, 93, 101), safe_abort, world shows ONLY the 7 scripted committed moves; valid place domain-rejected (`inventory_empty`) with NO protocol violation; valid move commits (unchanged ToolBay); model-a/model-b → different policy digests AND logical ids; RunConfig key set stays exactly the locked 8; trace.jsonl stays within the locked 13-field shape with zero provider-envelope keys |
| T4A-07 | missing module RED → several triangulation catches (below) | artifacts suite green (33) | exact sidecar envelopes; entries ordered, one per attempt; provider_evidence_digest over the exact provider-run; core_receipt_digest over the unchanged sibling receipt bytes; llm_receipt_digest non-self-referential; binding fields agree; no prompt text/headers/credentials/operation keys in sidecars; recursive + inline scrubbing; residual `APP_AUTH=` env assignment fails closed (`secret_detected`, nothing written, no leak in the whole run dir); Bearer forms redacted before serialization (the spec's own scenario); deleting `llm/` leaves core evidence byte-identical |
| T4A-08 | Template constants were plain `str` (would AttributeError) — caught pre-run; report failures exposed a binding-check design error | report suite green | static appendix via string.Template + html.escape; provider/model + all five digests; usage totals = deterministic sums; per-request table (step, id, latency, usage, normalized call/rejection, error, ESCAPED visible response); `<script>` payload escaped (`&lt;script&gt;`); no JS/javascript:/http(s)/cdn/fetch/xhr/sdk substrings; core outcome/score shown as authoritative + explicit core `report.html` authority marker; missing/mismatched bindings → external-evidence error, core outcome unchanged; rendering deterministic; core report.html byte-untouched |
| T4A-09 | CLI absent RED → validation cases | CLI+replay suites green (9) | fake CLI run emits the full core artifact set + all three `llm/` sidecars + `llm_report:` stdout line, exit 0; strict flag validation (provider⇒model; fake⇒script; openai⇔no script; model/script without provider → exit 2); secret-bearing script → exit 1, no `llm/` dir, core receipt released and untouched, secret nowhere on disk; rerun agreement `true` for LLM runs; documented replay reproduces digest 100/completed `receipt_agreement: match` with credential env vars ABSENT; import tripwire proves replay loads neither `robot_factory_arena.policies` nor `robot_factory_arena.providers`; deleting `llm/` from a copy changes no core artifact byte and replay still matches; replay never modifies sidecars |

### Triangulation catches (tests corrected to spec; implementations correct)

1. Fake identity is exact: scripts whose `request_id` did not match the derived `request-<step>` were correctly refused — tests fixed to derive ids from the policy step.
2. Fail-closed vector corrected to the spec's own semantics: `Authorization: Bearer …` is REDACTED before serialization (P4A-REQ-07 rule 2 + REQ-07 scenario); fail-closed `secret_detected` is for RESIDUAL secrets scrubbing cannot normalize (env-looking `APP_AUTH=` assignment). Both behaviors now covered separately.
3. `provider-run.json` has NO top-level provider/model (exact REQ-05 envelope) — the report's binding check originally compared the wrong level; fixed to compare against the entries' shared pinned identity.
4. Report required an escaped-response column (REQ-08 scenario: "response/error/request values are escaped") — added `visible response` to the per-request table.
5. Integration counters: after the scripted steps, script exhaustion produces one malformed_call per remaining step (98 violations with 2 scripted steps; 93 with 7+1) — expectations derived, not guessed.

## BOUNDARY ESCALATION (orchestrator decision required before T4A-10)

**Finding (verified by a minimal reproduction + reading the frozen code):** the frozen B5 verifier derives `tool_attempts = len(operations)` from the operation log (`verification/verifier.py` ~L1280), while the frozen B4 recorder writes an operation-log entry for EVERY parse-level rejection (`evidence/artifact_store.py` `_on_parse_rejected`, deliberate per the B3 handoff note). For the reference policies these coincide (greedy/bounded never emit parse-level garbage). For LLMPolicy, P4A-REQ-02 MANDATES parse-level garbage candidates (provider errors → malformed_call; unsupported tools; bad arguments), so **any LLM run transcript containing ≥1 parse-level rejection derives tool_attempts ≠ counters.tool_attempts and fails verification with `artifact_mismatch @ counters.tool_attempts`** (e.g. the mandated used=7 error scenario: claims 7, log derives 100).

**What this gates:** the CLI releases `receipt.json`/`replay-result.json`/`report.html` only for VALID verification — so any LLM run whose transcript contains a provider error or malformed tool call cannot release core artifacts, hence no sidecars for that run. The T4A-06 integration scenarios (runner-level, no verifier) are fully green and unaffected, as are all unit-level garbage paths. Receipted acceptance evidence (sidecars/appendix/replay tests, and T4A-10's "valid core receipt" requirement) is only reachable for transcripts whose every candidate parses valid.

**In-scope resolution used for A+B+C:** receipted acceptance paths use all-valid **completing** scripts (seed-18 deterministic greedy plan, 92 steps — every op-log entry is a submission, so the frozen derivation holds and the run terminates before script exhaustion). This exercises the full real flow (run → verify → receipt → sidecars → appendix → replay) without touching the frozen core.

**Escalated options for the orchestrator (frozen-core change REQUIRED either way — outside this phase's authority):**

1. Verifier amendment: derive `tool_attempts` excluding op-log entries with `tool is null` (parse-level rejections) — one-line-class change, keeps the audit log complete; or
2. Recorder amendment: route parse-level rejections to the trace only, keeping the operation log strictly submissions (matches REQ-03's "submission order" wording); or
3. Accept Phase-4A-scoped evidence: T4A-10's live run must then produce an all-valid transcript to release its receipt (domain rejections like `robot_not_at_shelf` are fine — they parse valid and are submissions; provider errors/malformed calls are not).

Per the phase contract I did NOT touch the frozen modules; the conflict is reported, not worked around.

## AMEND-4A-1 delivery (stdlib-urllib OpenAIProvider)

- `robot_factory_arena/providers/openai.py`: permanent, zero-dependency adapter; one chat-completions call per `invoke`; envelope-only translation of the frozen tool schema (`{"type":"function","function":…}`, `parallel_tool_calls:false`); `OPENAI_API_KEY` read at CALL time (never stored — asserted by repr inspection; never logged; transport failures never echo the URL/key); injectable `transport` (all 17 unit tests use a stub — zero network); deterministic error codes: `missing_api_key`, `transport_error`, `http_error`, `invalid_response`, `no_tool_call`, `multiple_tool_calls`; usage total recomputed as input+output; latency via `time.monotonic_ns` (evidence field only).
- Live runs are repeatable by anyone with their own key: `run --provider openai --model <pin>` (no script). GLM adapter remains out of scope.

## Completed tasks (persisted-task note)

- **T4A-01..T4A-09 done** as evidenced above. The batch tasks live in `docs/sdd/phase4a/tasks.md` (no per-task checkboxes in that doc — completion is recorded HERE per the phase instruction, exactly like the v0 batches; Engram was unreachable, so no store-side task update was possible — the orchestrator should mirror T4A-01..09 complete in the Engram tasks observation when the server returns).
- **T4A-10 EXCLUDED** per instruction (user-assisted live run). Exact steps below.

## Files changed (branch vs master: 17 files, +4,259/−5, three work-unit commits)

- Commit `5a67b69` — Batch A (737+): `providers/__init__.py`, `providers/adapter.py`, `providers/fake.py`, `tests/test_llm_provider.py`, `tests/test_llm_boundaries.py`
- Commit `ac5c9d2` — Batch B (1,029+): `policies/llm.py`, `tests/test_llm_policy.py`, `tests/test_llm_runner_integration.py`, `tests/test_llm_boundaries.py` (+45)
- Commit `ba323a9` — Batch C (2,327+/−5): `providers/evidence.py`, `providers/report.py`, `providers/openai.py`, `providers/fake.py` (+loader), `cli.py` (wiring), `tests/test_llm_artifacts.py`, `tests/test_llm_openai.py`, `tests/test_llm_cli.py`, `tests/test_llm_replay.py`, `tests/test_llm_boundaries.py` (+allowlist)
- This file + `docs/sdd/phase4a/spec.md` (the untracked binding spec) in the docs commit.

## Workload / PR boundary

Single PR per recorded AMEND delivery decision. Measured: 4,259 added lines (mostly tests — the four implementation modules total ≈ 1,900 lines). This exceeds the 400 canonical threshold and the 800 session budget; reported per the "honest count" rule (nothing was compressed to fit). Review order suggestion: provider contract + boundaries → policy (digest/prompt/schema locks) → runner integration → evidence/scrubbing → report → CLI/replay → OpenAI adapter.

## T4A-10 — exact user-assisted live-run instructions (EXCLUDED from this phase)

1. **Preconditions:** `OPENAI_API_KEY` set in your shell (NEVER committed); network access approved. The unit suite must stay green WITHOUT the key (verified: 471 OK — the key is read only inside `OpenAIProvider.invoke`).
2. **Run (single labeled live acceptance run):**

   ```bash
   export OPENAI_API_KEY=sk-...   # your key; stays in your shell only
   python3 -m robot_factory_arena.cli run \
     --seed 18 \
     --provider openai \
     --model <your-pinned-model> \
     --artifacts-root artifacts \
     --execution-id execution-live-openai-acceptance
   ```

   Use the exact execution id above so the run is identifiable as THE live run. `--seed 18` (5 orders) is completable within the 100-step budget if the model performs; any seed works for evidence purposes.
3. **Expectation caveat (escalation above):** the run releases its receipt + sidecars ONLY if the transcript contains zero provider errors / malformed tool calls (domain rejections are fine). On transcript garbage the CLI exits 1 with `verification: INVALID` and no sidecars — per the current frozen verifier that is a correct external-evidence failure, not a bug to patch live. If you hit it, re-run with a stronger pinned model, or resolve the escalation first.
4. **Offline replay (credentials REMOVED):**

   ```bash
   unset OPENAI_API_KEY   # or: env -u OPENAI_API_KEY
   LR=$(ls artifacts/runs | grep lr_)   # logical id also printed by `run`
   python3 -m robot_factory_arena.cli replay "artifacts/runs/$LR/execution-live-openai-acceptance"
   ```

   Expect `digest_match: true`, `outcome_match: true`, `receipt_agreement: match`.
5. **Inspect evidence:** `artifacts/runs/$LR/execution-live-openai-acceptance/llm/{provider-run.json,llm-receipt.json,llm-report.html}` — sidecars must contain no key/token (scrubber + fail-closed gate ran); core `report.html` remains authoritative.
6. **Final gates (no key):** `python3 -m unittest discover -s tests` → 471 OK; `git diff --stat master -- robot_factory_arena/domain robot_factory_arena/runtime robot_factory_arena/evidence robot_factory_arena/verification` → empty.
7. Generated `artifacts/` stay untracked (excluded from the review), same convention as v0.

## Deviations / interpretations (documented)

1. **ProviderResult allows any non-empty tool name** in `ToolCallPayload` (spec's result shape annotates move|pick|place): the enum is enforced by LLMPolicy normalization (P4A-REQ-02) because `unsupported_tool` must be representable as provider EVIDENCE for the mandated rejection path; the envelope mirrors the runner's untrusted-candidate posture. Documented in adapter docstrings + tests.
2. **Rejection candidates carry canonical-safe `{}` arguments**: the runner's parse-rejection ledger path canonicalizes candidate arguments, so raw provider payloads (potentially float-bearing) must not cross. The full untrusted payload remains in the scrubbed sidecar evidence path.
3. **`policies/llm.py` imports `providers.adapter`** (the ProviderRequest envelope) in addition to the named allowlist surface: P4A-REQ-02 requires constructing exactly one ProviderRequest; the AST boundary test pins the full set and forbids everything else.
4. **Report renders a `visible response` column** beyond the task's minimum column list: REQ-08's scenario explicitly requires escaped response values.
5. **`OpenAIProvider` recomputes `total_tokens`** from input+output (contract invariant) instead of trusting the API total; documented in-module.
6. **Boundary escalation** (above) — not a deviation from Phase-4A code, but the batch's most important recorded finding.

## Structured status / environment notes

- Consumed non-authoritative SDD status (store: engram); readiness resolved from the materialized docs; Engram server UNREACHABLE all session (mem_search/mem_save fail) — this file is the session-authoritative progress record; mem_save retried best-effort at phase end.
- pi-lens: the documented false-positive pattern recurred ALL session (repo-root import resolution without a project anchor — every flagged import executes in each green gate; `spawn python ENOENT` — its runner invokes `python`, REQ-08 locks `python3`). Its type/lint passes DID add value: caught an untyped transport parameter, Optional-narrowing gaps, a str-vs-Template bug (would have AttributeError'd at runtime), and an indentation corruption it flagged on `cli.py` mid-edit (repaired by scripted rewrite + py_compile + gate, the established B5 remedy pattern). Authoritative verification remains the documented unittest command.
- Edit-tool incident (B5/B6 pattern): one multi-part edit landed over-indented in `cli.py`; repaired deterministically (scripted dedent validated by py_compile + full gate).
- Engram remains the designated authoritative store; T4A-01..09 completion is recorded here and should be mirrored into the Engram tasks observation by the orchestrator once the server is back.
