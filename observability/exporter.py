"""One-shot per-item export orchestration (Unit B, tasks 2/4/6).

Flow per the persisted design: validated items from :func:`model.assemble_items`
map to exactly one atomic Langfuse batch each — one ``trace-create``, one
``generation-create`` per digest-bound provider request, one ``score-create``
per receipt-owned score — using the design's versioned deterministic IDs and
preserving trace/generation/score relationships. The health guard runs before
any ingestion; a failed item never blocks independent items and never yields a
partial success.

Payload policy: default payloads are metadata-only — no ``raw_visible_response``,
no tool arguments, no free-text equivalents, and no generation ``input``/
``output`` text fields. ``include_text=True`` copies eligible guarded values
into the exact Langfuse fields (response text -> ``output``, tool arguments ->
``metadata.tool_call.arguments``) only after the evidence secret scan, and only
within the design limits (64 KiB per field, 512 KiB per item batch; over-limit
content skips the item rather than truncating). A final recursive payload guard
walks every composed batch in both modes (forbidden structural fields) and
byte-scans the canonical serialization (secret patterns) before anything can
reach the transport client, retries, reports, or logs.

Guarded text is structurally unreachable in metadata mode: values live only in
``GuardedText`` slots, which ``canonical_json_bytes`` refuses to serialize.
"""

from pathlib import Path

from . import ids, langfuse_http, model
from .artifacts import (
    LEADERBOARD_NAME,
    RECEIPT_NAME,
    SECRET_DETECTED,
    UNSUPPORTED_SCHEMA,
    SkipRecord,
    canonical_json_bytes,
)
from .report import ExportedItem, ExportReport
from .secrets import find_secret

TEXT_FIELD_LIMIT_BYTES = 64 * 1024
ITEM_BATCH_LIMIT_BYTES = 512 * 1024
TEXT_FIELD_LIMIT_EXCEEDED = "text_field_limit_exceeded"
ITEM_SIZE_LIMIT_EXCEEDED = "item_size_limit_exceeded"

TRACE_EVENT = "trace-create"
GENERATION_EVENT = "generation-create"
SCORE_EVENT = "score-create"
TRACE_NAME = "robot-factory-attempt"
GENERATION_NAME = "provider-request"

_FORBIDDEN_KEYS = frozenset({"raw_visible_response"})


class ExportError(ValueError):
    """An item cannot be composed into a safe batch; fail closed per item."""

    def __init__(self, reason, detail, path):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.path = str(path)


def _is_bound(item):
    """Structural bound detection: true run evidence carries an execution id.

    Deliberately not ``isinstance``: tests import the runtime package through
    the ``tests.observability`` shim's private alias, so two module copies of
    the same dataclass can exist per process and identity checks would
    misclassify valid items. ``ValidatedRun`` always holds a validated
    execution id; ``TraceOnlyRow`` has no such field at all.
    """
    return getattr(item, "execution_id", None) is not None


def item_path(root, item):
    """Safe artifact path identifying one item in skip records and errors.

    Bound rows identify by their sealed receipt; rows without run evidence by
    the leaderboard that declares them. Paths are diagnostic-only and never
    enter IDs or payloads.
    """
    base = Path(root) if root is not None else Path("")
    if _is_bound(item):
        return str(
            base
            / "core"
            / "runs"
            / item.logical_run_id
            / item.execution_id
            / RECEIPT_NAME
        )
    return str(base / LEADERBOARD_NAME)


def _trace_id_for(item):
    """The design trace identity: ``trace-v1`` when bound, ``trace-only-v1`` else."""
    if _is_bound(item):
        return ids.trace_id(
            item.tournament_id,
            item.attempt_id,
            item.execution_id,
            item.digests.core_receipt,
            item.digests.llm_receipt,
        )
    ordinals = [ref["attempt_ordinal"] for ref in item.attempt_refs]
    return ids.trace_only_trace_id(
        item.tournament_id,
        item.condition,
        item.seed,
        ordinals,
        item.declined_launches,
    )


def _trace_body(item, trace_id_value):
    """Trace body from authoritative record fields only; nulls are declared."""
    bound = _is_bound(item)
    digests = item.digests
    return {
        "id": trace_id_value,
        "name": TRACE_NAME,
        "userId": item.tournament_id,
        "metadata": {
            "tournament_id": item.tournament_id,
            "attempt_id": item.attempt_id if bound else None,
            "attempt_ordinal": item.attempt_ordinal if bound else None,
            "execution_id": item.execution_id if bound else None,
            "logical_run_id": item.logical_run_id if bound else None,
            "condition": item.condition,
            "seed": item.seed,
            "status": item.status,
            "outcome": item.outcome,
            "attempts_used": item.attempts_used,
            "declined_launches": item.declined_launches,
            "harness_version": item.harness_version,
            "core_version": item.core_version,
            "artifact_digests": {
                "core_receipt": digests.core_receipt,
                "llm_receipt": digests.llm_receipt,
                "provider_evidence": digests.provider_evidence,
                "policy": digests.policy,
                "strategy_context": digests.strategy_context,
                "prompt": digests.prompt,
                "tool_schema": digests.tool_schema,
            },
        },
    }


def _included_bytes(value):
    """UTF-8 bytes of one eligible text value (string or arguments mapping)."""
    if isinstance(value, str):
        return value.encode("utf-8")
    return canonical_json_bytes(value)


def _check_included(value, path):
    """Secret scan plus field limit for one value entering a payload."""
    raw = _included_bytes(value)
    secret = find_secret(raw)
    if secret is not None:
        raise ExportError(SECRET_DETECTED, f"secret pattern {secret!r}", path)
    if len(raw) > TEXT_FIELD_LIMIT_BYTES:
        raise ExportError(
            TEXT_FIELD_LIMIT_EXCEEDED,
            f"field exceeds {TEXT_FIELD_LIMIT_BYTES} bytes",
            path,
        )


def _generation_event(item, request, trace_id_value, include_text, path):
    """One digest-bound generation; text only through the explicit flag."""
    gen_id = ids.generation_id(
        item.execution_id, request.request_id, item.digests.provider_evidence
    )
    metadata = {
        "request_id": request.request_id,
        "policy_step": request.policy_step,
        "latency_ms": request.latency_ms,
        "provider": request.provider,
        "error": dict(request.error) if request.error is not None else None,
        "tool_call": None,
    }
    body = {
        "id": gen_id,
        "traceId": trace_id_value,
        "name": GENERATION_NAME,
        "model": request.model,
        "usage": {
            "input": request.usage["input_tokens"],
            "output": request.usage["output_tokens"],
            "total": request.usage["total_tokens"],
        },
        "metadata": metadata,
    }
    if request.tool_call is not None:
        tool_call = {
            "tool": request.tool_call.tool,
            "structural_fields": dict(request.tool_call.structural_fields),
        }
        if include_text:
            arguments = dict(request.tool_call.arguments.value)
            _check_included(arguments, path)
            tool_call["arguments"] = arguments
        metadata["tool_call"] = tool_call
    if include_text and request.raw_visible_response is not None:
        _check_included(request.raw_visible_response.value, path)
        body["output"] = request.raw_visible_response.value
    return {
        "id": ids.event_id(GENERATION_EVENT, gen_id),
        "type": GENERATION_EVENT,
        "body": body,
    }


def _score_event(item, trace_id_value):
    """The single receipt-owned verified score as one score event."""
    score_id_value = ids.score_id(
        trace_id_value, model.SCORE_NAME, item.digests.core_receipt
    )
    return {
        "id": ids.event_id(SCORE_EVENT, score_id_value),
        "type": SCORE_EVENT,
        "body": {
            "id": score_id_value,
            "traceId": trace_id_value,
            "name": model.SCORE_NAME,
            "value": item.verified_score,
            "dataType": model.SCORE_DATA_TYPE,
            "metadata": {"status": "valid", "source": "receipt"},
        },
    }


def _walk_keys(value):
    """Every mapping key in the payload, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_keys(item)


def guard_payload(batch, *, include_text, path):
    """Final recursive payload guard: structure then canonical byte scan.

    ``raw_visible_response`` is forbidden at any depth in both modes (included
    text travels only in the exact Langfuse fields). Metadata mode additionally
    forbids tool ``arguments`` anywhere and ``input``/``output`` text fields on
    generation bodies (usage counters keep their own nested keys). The canonical
    serialization of the whole batch is then secret-scanned, so no pattern can
    enter requests, retries, reports, or logs.
    """
    forbidden = set(_FORBIDDEN_KEYS)
    if not include_text:
        forbidden |= {"arguments"}
    hit = sorted(set(_walk_keys({"batch": batch})) & forbidden)
    if hit:
        raise ExportError(UNSUPPORTED_SCHEMA, f"forbidden payload fields {hit}", path)
    for event in batch:
        # The guard classifies any batch shape; crafted or malformed events
        # must fail closed, never crash the scanner.
        if event.get("type") == GENERATION_EVENT and not include_text:
            body = event.get("body") or {}
            clash = sorted(set(body) & {"input", "output"})
            if clash:
                raise ExportError(
                    UNSUPPORTED_SCHEMA,
                    f"generation body carries text fields {clash}",
                    path,
                )
    secret = find_secret(canonical_json_bytes({"batch": batch}))
    if secret is not None:
        raise ExportError(SECRET_DETECTED, f"secret pattern {secret!r}", path)


def build_batch(item, *, include_text=False, root=None):
    """Compose one validated item into its single atomic event batch."""
    path = item_path(root, item)
    trace_id_value = _trace_id_for(item)
    batch = [
        {
            "id": ids.event_id(TRACE_EVENT, trace_id_value),
            "type": TRACE_EVENT,
            "body": _trace_body(item, trace_id_value),
        }
    ]
    if _is_bound(item):
        batch.extend(
            _generation_event(item, request, trace_id_value, include_text, path)
            for request in item.requests
        )
        batch.append(_score_event(item, trace_id_value))
    guard_payload(batch, include_text=include_text, path=path)
    if include_text:
        size = len(canonical_json_bytes({"batch": batch}))
        if size > ITEM_BATCH_LIMIT_BYTES:
            raise ExportError(
                ITEM_SIZE_LIMIT_EXCEEDED,
                f"batch exceeds {ITEM_BATCH_LIMIT_BYTES} bytes",
                path,
            )
    return batch


def run_export(root, client, *, include_text=False):
    """One finite export run: health guard, per-item batches, redacted report."""
    items, validation_skips = model.assemble_items(root)
    skips = list(validation_skips)
    exported = []
    failed = 0
    error = None
    health = client.check_health()
    if not health.healthy:
        error = health.reason if health.reason else langfuse_http.ENDPOINT_UNHEALTHY
        skips.extend(SkipRecord(item_path(root, item), error) for item in items)
    else:
        for item in items:
            try:
                batch = build_batch(item, include_text=include_text, root=root)
            except ExportError as exc:
                skips.append(SkipRecord(exc.path, exc.reason))
                continue
            result = client.ingest(batch)
            if result.accepted:
                outcomes = result.event_outcomes
                exported.append(
                    ExportedItem(
                        trace_id=_trace_id_for(item),
                        created=outcomes.count(langfuse_http.CREATED),
                        updated_or_replayed=outcomes.count(
                            langfuse_http.UPDATED_OR_REPLAYED
                        ),
                        unchanged=outcomes.count(langfuse_http.UNCHANGED),
                    )
                )
            else:
                failed += 1
                skips.append(
                    SkipRecord(
                        item_path(root, item),
                        result.reason
                        if result.reason
                        else langfuse_http.INGESTION_FAILED,
                    )
                )
    counts = {
        "processed": len(items) + len(validation_skips),
        "exported": len(exported),
        "created": sum(entry.created for entry in exported),
        "updated_or_replayed": sum(entry.updated_or_replayed for entry in exported),
        "unchanged": sum(entry.unchanged for entry in exported),
        "failed": failed,
        "skipped": len(skips),
    }
    # ExportReport.to_dict reads exactly COUNT_KEYS from counts; a missing
    # key fails closed there rather than silently rendering a short report.
    return ExportReport(
        counts=counts, exported=tuple(exported), skipped=tuple(skips), error=error
    )
