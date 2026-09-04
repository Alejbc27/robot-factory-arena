"""Authority-aware deterministic leaderboard aggregation (design §4).

Exactly one row per ``(condition, seed)``. ``verified_score``/``outcome``
cite the sealed terminal receipt; token/latency sums cite digest-bound
provider evidence and exist ONLY when every counted launch bound valid
evidence; orchestration fields come from the append-only attempt log. Rows
are ordered by key — never by score — so ties cannot reorder output, and no
composite or aggregate score field exists.
"""

from .strategy_context import atomic_write_bytes, canonical_json_bytes

LEADERBOARD_SCHEMA_VERSION = 1

ROW_FIELDS = (
    "condition",
    "seed",
    "status",
    "outcome",
    "verified_score",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "latency_ms",
    "attempts_used",
    "declined_launches",
    "core_receipt_digest",
    "llm_receipt_digest",
    "policy_digest",
    "strategy_context_digest",
    "harness_version",
    "core_version",
    "attempt_refs",
)

_BOUND = "bound"
_UNRESOLVED = "unresolved"


def _pair_sums(bound, ordinals):
    return (
        sum(bound[o]["input_tokens"] for o in ordinals),
        sum(bound[o]["output_tokens"] for o in ordinals),
        sum(bound[o]["total_tokens"] for o in ordinals),
        sum(bound[o]["latency_ms"] for o in ordinals),
    )


def _row(pair, launches, attempt_records, declined, tournament):
    condition, seed = pair
    ordered = sorted(launches, key=lambda r: r["attempt_ordinal"])
    bound = {}
    for record in attempt_records:
        if (record.get("condition"), record.get("seed")) != pair:
            continue
        if record.get("event") == "evidence_bound":
            bound[record["attempt_ordinal"]] = record
    all_bound = bool(ordered) and all(r["attempt_ordinal"] in bound for r in ordered)
    terminal = bound[max(bound)] if bound else None
    if not ordered:
        status = "declined"
    elif terminal is not None and all_bound:
        status = "completed" if terminal["outcome"] == "completed" else "aborted"
    else:
        status = "evidence_invalid"
    sums = _pair_sums(bound, sorted(bound)) if all_bound else (None, None, None, None)
    terminal_launch = None
    if terminal is not None:
        terminal_launch = next(
            r for r in ordered if r["attempt_ordinal"] == terminal["attempt_ordinal"]
        )
    return {
        "condition": condition,
        "seed": seed,
        "status": status,
        "outcome": terminal["outcome"] if terminal else None,
        "verified_score": terminal["verified_score"] if terminal else None,
        "input_tokens": sums[0],
        "output_tokens": sums[1],
        "total_tokens": sums[2],
        "latency_ms": sums[3],
        "attempts_used": len(ordered),
        "declined_launches": declined,
        "core_receipt_digest": terminal["core_receipt_digest"] if terminal else None,
        "llm_receipt_digest": terminal["llm_receipt_digest"] if terminal else None,
        "policy_digest": terminal["policy_digest"] if terminal else None,
        "strategy_context_digest": terminal_launch["context_digest"]
        if terminal_launch
        else None,
        "harness_version": tournament["harness_version"],
        "core_version": tournament["core_version"],
        "attempt_refs": [
            {
                "attempt_id": r["attempt_id"],
                "attempt_ordinal": r["attempt_ordinal"],
                "evidence_status": _BOUND
                if r["attempt_ordinal"] in bound
                else _UNRESOLVED,
            }
            for r in ordered
        ],
    }


def aggregate(attempt_records, decline_records, tournament):
    """Deterministic rows over append-only records; order never uses timing."""
    launches = {}
    for record in attempt_records:
        if record.get("event") == "launch_registered":
            launches.setdefault((record["condition"], record["seed"]), []).append(
                record
            )
    declines = {}
    for record in list(decline_records) + list(attempt_records):
        if record.get("event") == "launch_declined":
            key = (record["condition"], record["seed"])
            declines[key] = declines.get(key, 0) + 1
    return [
        _row(
            pair,
            launches.get(pair, []),
            attempt_records,
            declines.get(pair, 0),
            tournament,
        )
        for pair in sorted(set(launches) | set(declines))
    ]


def leaderboard_document(rows, tournament):
    return {
        "schema_version": LEADERBOARD_SCHEMA_VERSION,
        "tournament_id": tournament["tournament_id"],
        "harness_version": tournament["harness_version"],
        "core_version": tournament["core_version"],
        "rows": rows,
    }


def write_leaderboard(path, rows, tournament):
    atomic_write_bytes(
        path, canonical_json_bytes(leaderboard_document(rows, tournament))
    )
