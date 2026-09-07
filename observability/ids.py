"""Versioned deterministic identity formulas (stdlib only).

Slice A3 of Unit A: the design's exact canonical ID formulas. Every ID is
``<prefix> + sha256_hex(canonical_json_bytes([version-tag, identity inputs]))``
over stable artifact identity only — never host paths, wall-clock time,
random values, credentials, or HTTP order. The same evidence yields identical
IDs across machines and repeated exports.

Trace-only amendment (v1, Unit A slice A2b): the design's ``trace-v1`` input
list gives trace-only rows (declined/unbound leaderboard rows with no run)
null attempt/execution/digest slots, so two declined rows of one tournament
collided on one ``tr_`` ID. :func:`trace_only_trace_id` derives their
identity under the distinct ``trace-only-v1`` version tag from the row's
stable pair identity — tournament plus (condition, seed) — and the
leaderboard's authoritative attempt ordinals and declined-launch count. The
formula shape is unchanged; the distinct tag makes cross-shape collisions
impossible, and Unit B must use ``trace_only_trace_id`` for rows without
bound run evidence and ``trace_id`` otherwise.
"""

import hashlib

from .artifacts import canonical_json_bytes

TRACE_VERSION_TAG = "trace-v1"
GENERATION_VERSION_TAG = "generation-v1"
SCORE_VERSION_TAG = "score-v1"
EVENT_VERSION_TAG = "event-v1"


def _versioned_id(prefix, version_tag, inputs):
    payload = canonical_json_bytes([version_tag, *inputs])
    return prefix + hashlib.sha256(payload).hexdigest()


def trace_id(
    tournament_id, attempt_id, execution_id, core_receipt_digest, llm_receipt_digest
):
    """``tr_`` identity for a bound item: tournament, attempt, execution, digests.

    Bound items only; rows without run evidence must use
    :func:`trace_only_trace_id` (see the module docstring's v1 amendment).
    """
    return _versioned_id(
        "tr_",
        TRACE_VERSION_TAG,
        [
            tournament_id,
            attempt_id,
            execution_id,
            core_receipt_digest,
            llm_receipt_digest,
        ],
    )


def generation_id(execution_id, request_id, provider_evidence_digest):
    """``gen_`` identity binding one digest-bound provider request."""
    return _versioned_id(
        "gen_",
        GENERATION_VERSION_TAG,
        [execution_id, request_id, provider_evidence_digest],
    )


def score_id(trace_id_value, score_name, receipt_digest):
    """``score_`` identity binding one receipt-owned score to its trace."""
    return _versioned_id(
        "score_",
        SCORE_VERSION_TAG,
        [trace_id_value, score_name, receipt_digest],
    )


def event_id(object_type, object_id):
    """``evt_`` wrapper identity for one ingestion event."""
    return _versioned_id("evt_", EVENT_VERSION_TAG, [object_type, object_id])


TRACE_ONLY_VERSION_TAG = "trace-only-v1"


def trace_only_trace_id(
    tournament_id, condition, seed, attempt_ordinals, declined_launches
):
    """``tr_`` identity for a trace-only row (no bound run evidence).

    v1 amendment (slice A2b): the ``trace-v1`` input list holds null
    attempt/execution/digest slots for trace-only rows, so two declined rows
    of one tournament collided. Identity is instead derived under the
    ``trace-only-v1`` tag from the row's stable pair identity (tournament,
    condition, seed) plus the leaderboard's authoritative attempt ordinals
    (in validated sorted order) and declined-launch count. The versioned
    formula shape is unchanged; the distinct tag prevents cross-shape
    collisions with bound ``trace_id`` values.
    """
    return _versioned_id(
        "tr_",
        TRACE_ONLY_VERSION_TAG,
        [tournament_id, condition, seed, list(attempt_ordinals), declined_launches],
    )
