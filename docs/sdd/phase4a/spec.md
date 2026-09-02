# Phase 4A Mini-Spec — First Live LLM

> **Binding boundary:** issue #5, corrected boundary (external LLM envelope). The v0 core schemas and core report are frozen. This mini-spec covers only Phase 4A steps 1–4: `ProviderAdapter`, `LLMPolicy`, pinned-provider identity, prompt/tool normalization, provider evidence, and the first live-provider acceptance run.
>
> **Inputs:** `docs/sdd/spec.md` (frozen v0 contract); `docs/phase4-llm-integration-review.md` (adopted layering and 11-step order); issue #5 corrected boundary/checklist; `docs/sdd/verify-report.md` (frozen verification and reporting behavior).
>
> RFC 2119 terms are normative. All JSON objects below are exact shapes: no extra keys, omitted defaults, or alternate encodings are permitted. Canonical JSON means the v0 canonical serializer: UTF-8, compact separators, lexicographically sorted object keys, explicit nulls, and no floating-point values.

## Non-negotiable boundary

Phase 4A MUST add only layers outside the deterministic core. The existing `RunConfig`, `ToolCall`, `Policy` protocol, receipt, trace, verifier, replay input, and `report.html` schemas MUST NOT change. `policy_digest` is the existing field through which LLM identity enters `RunConfig` and `logical_run_id`; no provider/model fields may be added to `RunConfig` or the core receipt.

The proposed layout is:

- `robot_factory_arena/providers/adapter.py`: provider-neutral request/result contract; standard-library-only.
- `robot_factory_arena/providers/fake.py`: deterministic fake provider for tests.
- `robot_factory_arena/providers/evidence.py`: external provider-evidence serialization and secret gate.
- `robot_factory_arena/providers/report.py`: external static appendix renderer.
- `robot_factory_arena/policies/llm.py`: `LLMPolicy` adapter to the existing `Policy` protocol.
- `tests/test_llm_*.py`: contract, normalization, boundary, artifact, replay, and report tests.

The `providers` modules MUST have no imports from `robot_factory_arena.domain`, `.runtime`, `.evidence`, or `.verification`; an AST boundary test MUST enforce this. `policies/llm.py` MAY import only the existing policy/tool-call contracts and the existing canonical digest authority needed to interoperate with them. It MUST NOT import `Runner`, `ToolBay`, domain transitions, the verifier, or mutable runtime state. If a proposed implementation requires changing a frozen core module or schema, work MUST stop and be reported as a boundary violation.

## P4A-REQ-01 — ProviderAdapter contract and fake provider

A provider adapter MUST expose one operation with the semantic contract:

```text
invoke(request: ProviderRequest) -> ProviderResult
```

It MUST perform one provider request at most, MUST not perform fallback to another provider/model, MUST not issue parallel tool calls, and MUST not import or call the v0 core. Provider and model are pinned on the adapter instance and every result MUST echo them exactly.

`ProviderRequest` has exactly this shape:

```text
{
  "schema_version": 1,
  "provider": string,
  "model": string,
  "request_id": non-empty string,
  "observation": serialized v0 observation object,
  "prompt": string,
  "tool_schema": frozen Phase-4A tool-schema object
}
```

`ProviderResult` has exactly this shape:

```text
{
  "schema_version": 1,
  "provider": string,
  "model": string,
  "request_id": non-empty string,
  "usage": {
    "input_tokens": non-negative integer,
    "output_tokens": non-negative integer,
    "total_tokens": non-negative integer
  },
  "latency_ms": non-negative integer,
  "raw_visible_response": string | null,
  "tool_call": {
    "tool": "move" | "pick" | "place",
    "arguments": object
  } | null,
  "error": {
    "code": non-empty ASCII string,
    "message": non-empty string
  } | null
}
```

`total_tokens` MUST equal `input_tokens + output_tokens`. A successful result has a non-null `tool_call` and null `error`; an unsuccessful result has a null `tool_call` and a non-null `error`. The result is provider evidence, not a core `ToolCall` and MUST contain no `operation_key`.

The fake provider MUST be deterministic, network-free, and scriptable with a finite sequence of `ProviderResult` values. It MUST retain the requests it received for assertions, return results in sequence, and expose no credentials or network dependency. A test MUST prove that importing and invoking the fake provider imports no core module.

#### Scenario: Fake provider satisfies the contract without the core

- **GIVEN** a fake provider pinned to provider `fake` and model `fake-v1`, with one scripted successful result
- **WHEN** `invoke` receives one exact `ProviderRequest`
- **THEN** it returns one exact `ProviderResult`, records the request, performs no network operation, and the provider package imports no `domain`, `runtime`, `evidence`, or `verification` module.

## P4A-REQ-02 — LLMPolicy normalization and rejection accounting

`LLMPolicy` MUST implement the existing `Policy` protocol exactly: it exposes `policy_id`, `policy_digest`, and `decide(observation) -> object`. It MUST receive only the serialized v0 observation, construct exactly one `ProviderRequest`, invoke the pinned adapter once, and return at most one normalized v0 `ToolCall`.

The request MUST contain the observation unchanged as the serialized observation, the frozen English prompt rendered with that observation, and the frozen tool schema. The policy MUST never mutate world state, call the ToolBay, inspect the verifier, access execution metadata, or make a second provider call to repair a response.

For a successful provider result, the provider `tool_call` MUST be normalized as follows:

1. The tool name MUST be exactly one of `move`, `pick`, or `place`.
2. The arguments MUST have exactly the v0 shape: `move` has only `{"direction": "north"|"east"|"south"|"west"}`; `pick` has only `{"shelf_id": "shelf-00".."shelf-09", "item_id": "item-00".."item-09"}`; `place` has only `{"order_id": "order-00"..}`.
3. The policy step MUST be `observation.policy_steps.used + 1`.
4. The operation key MUST be `op/<logical_run_id>/step-<decimal>/intent-<token>`, with no leading zero in the decimal step and an intent token of 1–64 ASCII letters, digits, `.`, `_`, or `-`.

Deterministic LLM intent labels MUST be:

| Normalized decision | Intent token |
|---|---|
| `move` with direction `D` | `llm-move-D` |
| `pick` for shelf `S` | `llm-pick-S` |
| `place` for order `O` | `llm-place-O` |
| rejected provider output | `llm-reject` |

These labels follow the same ASCII grammar and step-scoped intent semantics as `greedy-v0` and `bounded-v0`; retries never create a new key.

Absent or malformed provider tool-calls MUST be converted to a deterministic malformed candidate rather than raised as an ordinary policy exception. The candidate MUST carry the current-run key with `intent-llm-reject` and MUST be rejected by the existing runner as `malformed_call`. Unsupported tool names MUST become `unsupported_tool`; wrong argument keys or values MUST become `invalid_arguments`; multiple tool calls MUST become `malformed_call`; a provider result with `error` MUST use the same deterministic `malformed_call` rejection path. No new core rejection reason is permitted.

The existing runner behavior is binding: it reserves `policy_steps` after `decide` returns and before normalization/validation/dispatch. Therefore every such garbage candidate consumes exactly one policy step, is emitted in the existing policy-decision/rejection trace path, increments `contract_violations` according to v0 `REQ-02`, performs no tool attempt and no world effect, and is reflected in the next `last_result`. Phase 4A MUST NOT alter this runner behavior.

#### Scenario: Absent provider tool-call consumes one step and cannot mutate the world

- **GIVEN** a non-terminal observation with `used = 7` and a provider result whose `tool_call` is null and `error` is non-null
- **WHEN** `LLMPolicy.decide` returns its deterministic rejection candidate and the unchanged v0 runner processes it
- **THEN** the runner reserves step 8, records `malformed_call` and the rejection in the existing trace/accounting path, makes no ToolBay submission or world transition, and requests the next observation with `last_result.status = "rejected"`.

#### Scenario: Valid provider call becomes one legal ToolCall

- **GIVEN** an observation at step 3 and a provider result containing `place({"order_id":"order-03"})`
- **WHEN** `LLMPolicy.decide` normalizes the result
- **THEN** it returns exactly one v0 `ToolCall` with key `op/<logical_run_id>/step-4/intent-llm-place-order-03`, exact place arguments, and no provider-specific fields.

## P4A-REQ-03 — Exact LLM policy digest and logical identity

For every LLM policy instance, `policy_digest` MUST be the SHA-256 digest of the canonical JSON bytes of exactly this object:

```json
{
  "implementation_version": "1",
  "model": "<pinned model>",
  "policy_id": "llm-v0",
  "prompt_template_digest": "sha256:<64 lowercase hex>",
  "provider": "<pinned provider>",
  "tool_schema_digest": "sha256:<64 lowercase hex>"
}
```

No other key, default, execution ID, request ID, timestamp, provider response, rendered prompt, or filesystem path may enter this digest. `prompt_template_digest` is the SHA-256 digest of the frozen prompt template's UTF-8 bytes. `tool_schema_digest` is the v0 canonical-JSON digest of the exact frozen tool-schema object.

The LLM run MUST place this digest in the existing `RunConfig.policy_digest` field. The unchanged v0 `compute_logical_run_id` derivation then MUST produce different logical IDs for otherwise identical task/config/grant/fault inputs when only provider or model differs. No `RunConfig` field may be added for this purpose.

#### Scenario: Changing only the model changes logical identity

- **GIVEN** two otherwise identical LLM runs with provider `fake`, the same prompt/schema, and models `model-a` and `model-b`
- **WHEN** each policy digest and logical run ID is derived through the existing v0 identity path
- **THEN** the two policy digests and logical run IDs differ, while both `RunConfig` mappings retain exactly the frozen v0 key set.

## P4A-REQ-04 — Frozen English prompt and JSON tool schema

Phase 4A MUST freeze exactly one English prompt template, with no provider-specific variant:

```text
You are the bounded controller for Robot Factory & Arena warehouse-v0.

Read the serialized observation below. Choose exactly one next warehouse action.
You may call exactly one tool: move, pick, or place. Do not call tools in
parallel. Use only the arguments defined by the tool schema. Do not invent
items, shelves, orders, directions, operation keys, grants, or state changes.
The host will add the operation key and will validate the action. If no legal
action is possible, still select the single best action supported by the
observation.

Serialized observation:
{{observation_json}}
```

`{{observation_json}}` MUST be replaced by the v0 canonical JSON serialization of the received observation; the surrounding template, line endings, wording, and placeholder name are frozen. Providers MUST receive the same logical prompt and the same common schema; provider adapters may only translate the envelope required by their API.

The one frozen tool-schema object is:

```json
{
  "schema_version": 1,
  "max_tool_calls": 1,
  "parallel_tool_calls": false,
  "tools": [
    {
      "name": "move",
      "description": "Move one cardinal grid step.",
      "parameters": {
        "type": "object",
        "additionalProperties": false,
        "required": ["direction"],
        "properties": {
          "direction": {"type": "string", "enum": ["north", "east", "south", "west"]}
        }
      }
    },
    {
      "name": "pick",
      "description": "Pick the named item from the named shelf.",
      "parameters": {
        "type": "object",
        "additionalProperties": false,
        "required": ["shelf_id", "item_id"],
        "properties": {
          "shelf_id": {"type": "string", "pattern": "^shelf-(0[0-9])$"},
          "item_id": {"type": "string", "pattern": "^item-(0[0-9])$"}
        }
      }
    },
    {
      "name": "place",
      "description": "Place the carried item into the named order.",
      "parameters": {
        "type": "object",
        "additionalProperties": false,
        "required": ["order_id"],
        "properties": {
          "order_id": {"type": "string", "pattern": "^order-(0[0-9])$"}
        }
      }
    }
  ]
}
```

The schema MUST be tested against the exact v0 argument shapes, including rejection of extra keys, unknown IDs, and parallel/multiple calls. Both digests MUST be recorded in the external LLM receipt.

#### Scenario: Prompt and schema are stable and exact

- **GIVEN** the same serialized observation supplied twice to the same pinned LLM policy
- **WHEN** its provider requests are inspected
- **THEN** the rendered prompt and tool-schema bytes are identical, the request contains one tool-call allowance, and the recorded prompt/schema digests are identical.

## P4A-REQ-05 — External provider artifacts and deletion independence

Provider evidence MUST be an external envelope, stored alongside (never inside or merged into) the core artifacts:

```text
artifacts/runs/<logical_run_id>/<execution_id>/
  config.json ... receipt.json replay-input.json trace.jsonl report.html
  llm/
    provider-run.json
    llm-receipt.json
    llm-report.html
```

The exact canonical `provider-run.json` shape is:

```json
{
  "schema_version": 1,
  "logical_run_id": "lr_<64 lowercase hex>",
  "execution_id": "<physical execution id>",
  "requests": [
    {
      "policy_step": 1,
      "provider": "<pinned provider>",
      "model": "<pinned model>",
      "request_id": "<non-empty request id>",
      "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
      "latency_ms": 0,
      "raw_visible_response": "<scrubbed string or null>",
      "tool_call": {"tool": "move", "arguments": {"direction": "north"}},
      "error": null
    }
  ]
}
```

Each request entry MUST use the exact `ProviderResult` evidence fields, MUST occur once for each LLM decision attempt, and MUST be ordered by ascending policy step. The example tool call is illustrative; the schema permits the three exact normalized tool-call shapes from P4A-REQ-02 or null. `provider-run.json` MUST contain no prompt text, API request headers, credentials, hidden provider metadata, or operation key.

The exact canonical `llm-receipt.json` shape is:

```json
{
  "schema_version": 1,
  "logical_run_id": "lr_<64 lowercase hex>",
  "execution_id": "<physical execution id>",
  "provider": "<pinned provider>",
  "model": "<pinned model>",
  "prompt_digest": "sha256:<64 lowercase hex>",
  "tool_schema_digest": "sha256:<64 lowercase hex>",
  "provider_evidence_digest": "sha256:<64 lowercase hex>",
  "core_receipt_digest": "sha256:<64 lowercase hex>",
  "llm_receipt_digest": "sha256:<64 lowercase hex>"
}
```

`provider_evidence_digest` MUST hash the exact canonical `provider-run.json` object. `core_receipt_digest` MUST hash the exact canonical bytes of the unchanged sibling `receipt.json`. `llm_receipt_digest` MUST hash the canonical object formed by removing `llm_receipt_digest` from `llm-receipt.json`; it MUST NOT be self-referential. The sidecar receipt MUST bind the same logical and physical run IDs and pinned provider/model as the provider evidence.

Deleting `llm/` MUST NOT invalidate the core receipt, core verifier result, core `replay-input.json`, core `replay-result.json`, or core `report.html`. Core verification MUST ignore the sidecar directory; sidecar verification failure is an external-evidence failure, not a new core outcome.

#### Scenario: Sidecar artifacts bind without changing core evidence

- **GIVEN** a completed fake-provider run with valid core artifacts and the three `llm/` artifacts
- **WHEN** the sidecar digests are recomputed and then the entire `llm/` directory is deleted
- **THEN** the sidecar initially verifies its provider/core bindings, and after deletion the unchanged core receipt still verifies and v0 replay produces the same final-state digest, score, and outcome.

## P4A-REQ-06 — Offline replay from recorded ToolCalls

The existing v0 `cli replay` command MUST remain unchanged and MUST work on an LLM run by consuming only the existing recorded operations/replay material. Replay MUST start from recorded normalized/rejected operations, never re-render a prompt, import `LLMPolicy`, import `providers`, or call a network provider. The provider response and model inference are not replay inputs to the core transition.

Phase 4A MUST add an assertion path that runs the documented command with no API key or token environment variables present while the providers package remains importable. The test MUST use a provider-import tripwire or equivalent and MUST prove that replay reproduces final-state digest, verifier-owned score, and outcome without network access. All acceptance runs except the one explicitly designated live-provider run MUST use the fake provider.

#### Scenario: LLM run replays offline without provider credentials

- **GIVEN** a valid LLM run directory copied to a machine with no provider API key/token and an importable providers package
- **WHEN** `python3 -m robot_factory_arena.cli replay artifacts/runs/<logical_run_id>/<execution_id>` is executed
- **THEN** replay makes no provider or policy decision, exits with the unchanged v0 success/match behavior, and reports matching final-state digest, score, and outcome.

## P4A-REQ-07 — Secret scrubbing and fail-closed export

No key, token, secret, authorization value, password, credential, or environment assignment MUST appear in `provider-run.json`, `llm-receipt.json`, `llm-report.html`, or the core `trace.jsonl`. Provider evidence MUST be scrubbed before canonical serialization, before writing either sidecar artifact, before report rendering, and before any optional observer/export hook.

The deterministic scrubber MUST:

1. Replace values of case-insensitive fields matching `api_key`, `apikey`, `authorization`, `bearer`, `token`, `secret`, `password`, `credential`, or names ending in `_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD`, or `_CREDENTIAL` with the literal `[REDACTED]`.
2. Replace common inline credential forms in strings (`Bearer <value>`, `api[_-]?key=<value>`, `token=<value>`, `secret=<value>`, and provider key prefixes such as `sk-<non-whitespace>`) with `[REDACTED]`.
3. After scrubbing, reject serialization with deterministic error `secret_detected` if any string still matches either a credential pattern or an environment-looking assignment `NAME=value` where `NAME` contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `AUTH`, or `CREDENTIAL`.
4. Never serialize the rejected value, even in an error message or trace record.

The core trace MUST receive only the existing normalized policy-decision/tool-result fields; raw provider responses and provider errors MUST stay in the scrubbed external evidence path. No cost ceiling or cost ledger is part of Phase 4A; usage is recorded only.

#### Scenario: Secret-bearing provider output cannot cross the export boundary

- **GIVEN** a provider response containing `Authorization: Bearer secret-value` in its visible response
- **WHEN** Phase 4A prepares provider evidence and the static appendix
- **THEN** the credential is redacted before serialization, no sidecar or trace contains the original value, and any remaining env-looking secret causes serialization to fail closed with `secret_detected` without writing the unsafe artifact.

## P4A-REQ-08 — Static `llm-report.html` appendix

Phase 4A MUST render a separate static `llm-report.html` using Python standard-library templating (for example, `string.Template`) and HTML escaping for every dynamic value. It MUST contain no JavaScript, network URL, CDN, external asset, or provider SDK dependency.

The renderer MUST read the unchanged core receipt, `llm-receipt.json`, and the bound `provider-run.json`. It MUST show:

- provider and model;
- prompt, tool-schema, provider-evidence, core-receipt, and LLM-receipt digests;
- usage totals (input, output, total tokens) calculated as deterministic sums of the request entries;
- a per-request table with policy step, request ID, latency, usage, normalized tool call/rejection, and error;
- the authoritative core outcome/score and a clear statement that the authoritative report remains core `report.html`.

The appendix MUST not alter, regenerate, or append to core `report.html`, core receipt, core trace, or verifier artifacts. Missing or mismatched sidecar bindings MUST be displayed as an external-evidence error and MUST NOT be represented as a changed core outcome.

#### Scenario: Appendix is static, escaped, and subordinate to the core report

- **GIVEN** a valid sidecar with a response containing HTML metacharacters and two provider requests
- **WHEN** `llm-report.html` is rendered
- **THEN** all dynamic response/error/request values are escaped, totals and both request rows are shown, no script/network dependency is present, all bound digests are shown, and the page explicitly says that core `report.html` remains authoritative.

## P4A-REQ-09 — Phase 4A acceptance gates and issue mapping

Acceptance MUST map to issue #5's corrected checklist as follows:

| Issue #5 checklist/gate | Binding Phase 4A evidence |
|---|---|
| Core schemas untouched; external envelope | P4A-REQ-05 plus an AST/import and artifact-layout test; no sidecar field is added to `RunConfig`, core receipt, trace, verifier, or replay schemas. |
| ProviderAdapter contract and fake provider | P4A-REQ-01 unit tests; fake-only provider tests are network-free. |
| LLMPolicy under `Policy` with one normalized action | P4A-REQ-02 unit/integration tests, including valid move/pick/place and all deterministic malformed paths. |
| Provider/model identity is bound | P4A-REQ-03 digest vectors and two logical-run IDs differing only by model. |
| Frozen prompt and tool schema | P4A-REQ-04 byte/digest golden vectors and argument-shape tests. |
| Provider evidence and secrets-before-export | P4A-REQ-05 and P4A-REQ-07 sidecar schema, digest, scrub, and fail-closed tests. |
| Replay without an API | P4A-REQ-06 CLI assertion with absent credentials and an import/network tripwire. |
| Separate LLM report appendix | P4A-REQ-08 static HTML, escaping, digest, and authority-marker tests. |
| First live-provider proof | Exactly one explicitly labeled acceptance run against a real pinned provider/model; its core run is replayed offline afterward. No live calls are used by the unit suite or other acceptance paths. |

The standard unit gate MUST remain:

```text
python3 -m unittest discover -s tests -v
```

It MUST pass under the active Python 3.14.5 interpreter with no network requirement. The core-file gate MUST show no changes under the frozen implementation directories:

```text
git diff --stat -- robot_factory_arena/domain robot_factory_arena/runtime robot_factory_arena/evidence robot_factory_arena/verification
```

The expected result is empty. A non-empty result is a boundary violation, not a reason to weaken this spec. No Phase 4A acceptance may introduce cost-ceiling machinery; usage recording is sufficient and the cost ledger is deferred.

#### Scenario: Phase 4A passes all gates without rewriting the core

- **GIVEN** the fake-provider unit suite, one labeled live-provider acceptance run, its offline replay, and the external sidecar artifacts
- **WHEN** the reviewer runs the standard unittest gate and inspects the frozen-directory diff
- **THEN** all unit tests pass without network access, replay succeeds without provider credentials, the live run has provider evidence and a scrubbed appendix, and the frozen-directory diff is empty.

## Explicit non-goals and risks

- OpenAI/GLM-specific adapters, LangGraph, Langfuse, R1/R2/R3, discovery/frontier behavior, and the real cost ledger are outside this Phase 4A slice.
- A fresh LLM inference is not claimed to be byte-deterministic; deterministic replay begins at the recorded normalized `ToolCall`/operation records.
- Provider latency and token usage are evidence fields only and do not enter core semantic digests, scores, outcomes, or budgets.
- The issue command requested for provenance is an external repository operation; this spec uses the corrected boundary and checklist supplied for issue #5 together with the reviewed repository documents. If the canonical issue text conflicts with that corrected boundary, the boundary is binding and the discrepancy must be escalated rather than resolved by changing the frozen core.
