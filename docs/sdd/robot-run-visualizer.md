# Robot run visualizer v1

`visualizer.html` is an optional, deterministic, offline explanation released only after the normal evidence validation, independent verification, operation-only replay, report write, and receipt-binding recheck succeed.

## Open and use it

Open the sibling `visualizer.html` directly in a browser, including from a local `file:` URL. It is standalone: no server, network connection, external assets, provider, file picker, or JavaScript build step is required. Use **Play**, **Pause**, **Step forward**, and the fixed `0.5x`, `1x`, `2x`, or `4x` speeds.

A frame is one consumed policy step, not one gateway attempt or domain event. A response-loss fault and its internal retry are ordered subevents within the originating frame. The screen shows the projection's verifier-owned score and outcome without recalculating either.

## Authority and determinism

The renderer consumes only the validated `VisualizerProjection v1`; it does not read raw evidence or act as a verifier, replay engine, transition engine, or scorer. Its HTML bytes are deterministic for equal logical projection inputs. This is a byte-identity guarantee, not a cross-browser screenshot or playback-state guarantee.

The artifact is deliberately excluded from receipt bindings, semantic digest tables, logical-run identity, verification, and replay. Removing it leaves the receipt, report, replay, score, outcome, and core digests unchanged. Rerun compares it only when both executions released it and reports that status separately from semantic/core agreement.

## Failure boundary

Projection, rendering, or atomic-write failures remove `visualizer.html` and its temporary output, then surface a clearly non-authoritative visualizer error. They do not reclassify the core run or rewrite core artifacts. Invalid, unresolved, replay-mismatching, or receipt-binding-failing runs release no official visualizer. v1 intentionally has no diagnostic visualizer.

## Strict projection contract

`VisualizerProjection v1` validation is fail-closed and binds the projection digest over the canonical content with the digest key itself omitted. Beyond exact key sets and enums, the validator enforces:

- v0 tool-call arguments follow the exact per-tool grammar (`move` direction is `north`/`east`/`south`/`west`; `pick` IDs match `shelf-00`–`shelf-09`/`item-00`–`item-09`; `place` IDs match `order-00`–`order-09`); a present tool requires `schema_version: 1`, and a parse-level record keeps absent fields explicitly `null`.
- The projection builder strips unsafe or malformed argument values to `arguments: null` instead of leaking them, so rejected policy steps remain visible without embedding raw candidates.
- `last_result.operation_key` is either the runner's empty-string parse-rejection sentinel or a valid operation key bound to the same logical run; forged, foreign, mapping, or absent keys fail closed.
- Self-verifiable digest claims bind to canonical projection content: `policy_digest` agrees with its logical alias; `initial_state_digest`, `final_state_digest`, and the duplicate terminal final-state digest bind to canonical worlds; the three provider-context aliases agree when present; and `projection_digest` binds the canonical projection with its own key omitted.
- Source-bound digest claims (`config`, `task`, `trace`, and `fault_tape`) are grammar-checked here and are bound to verified artifacts by `build_projection`; the standalone validator does not pretend to reconstruct those opaque sources.
- Committed domain-event data must be the exact v0 shape per `event_type` (bounded coordinates, closed direction enum, grammatical IDs), so arbitrary payloads such as paths or secrets are rejected.
- Frame states chain from the initial world through every frame to the verifier's final state; non-committed frames carry equal snapshots and no committed events.

## Acceptance mapping

| Contract | Evidence |
|---|---|
| VIS-01 frame/subevent grouping | `tests/test_visualizer_projection.py` real recorder fixtures (response-loss, malformed, unsupported, invalid, unauthorized, rejected, idempotent-replay) |
| VIS-02 logical determinism | canonical-byte/digest tests, rerun byte identity, reference-null and LLM pinned-metadata CLI tests |
| VIS-03 validated projection only | strict schema/grammar/event-data/chaining tests plus renderer projection-only isolation tests |
| VIS-04 verified release only | release-gate, core-byte preservation, gate-absence, fail-open CLI, and deletion-isolation tests |
| VIS-05 no diagnostic visualizer | invalid/unresolved absence tests and this document's deferral statement |

Rerun reports visualizer status per prior pair and as an aggregate: `agree` only when every pair released the file and all bytes match, `unavailable (n of m pairs lacked the optional file)` when a sibling is missing, and `differ (non-authoritative)` on a byte difference. An unavailable optional file never fails core deterministic agreement.
