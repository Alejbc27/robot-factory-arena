"""Versioned deterministic identity formulas (stdlib only).

Slice A3 of Unit A: the design's exact canonical ID formulas. Every ID is
``<prefix> + sha256_hex(canonical_json_bytes([version-tag, identity inputs]))``
over stable artifact identity only — never host paths, wall-clock time,
random values, credentials, or HTTP order. The same evidence yields identical
IDs across machines and repeated exports.
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
    """``tr_`` identity binding tournament, attempt, execution, and digests."""
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
