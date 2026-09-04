"""Unit 3 leaderboard tests (design §4): exactly one authority-aware row per
(condition, seed), deterministic key ordering (ties never reorder), sums only
over fully-bound attempts, nulls over declined/evidence-invalid rows, and no
composite score.
"""

import unittest

from harness import leaderboard, strategy_context

TOURNAMENT = {
    "tournament_id": "tournament-unit3-r2",
    "harness_version": "0.1.0",
    "core_version": "0",
}


def launch(condition, seed, ordinal, context_digest="sha256:" + "0" * 64):
    return {
        "event": "launch_registered",
        "condition": condition,
        "seed": seed,
        "attempt_id": f"r2-agent-01-seed-{seed}-attempt-{ordinal:02d}",
        "attempt_ordinal": ordinal,
        "context_digest": context_digest,
    }


def bound(
    condition,
    seed,
    ordinal,
    outcome="completed",
    score=100,
    tokens=(10, 5, 15),
    latency=42,
):
    return {
        "event": "evidence_bound",
        "condition": condition,
        "seed": seed,
        "attempt_ordinal": ordinal,
        "outcome": outcome,
        "verified_score": score,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "total_tokens": tokens[2],
        "latency_ms": latency,
        "core_receipt_digest": "sha256:" + "a" * 64,
        "llm_receipt_digest": "sha256:" + "b" * 64,
        "policy_digest": "sha256:" + "c" * 64,
    }


def unresolved(condition, seed, ordinal, reason="crash_window_unobserved"):
    return {
        "event": "evidence_unresolved",
        "condition": condition,
        "seed": seed,
        "attempt_ordinal": ordinal,
        "reason": reason,
    }


def declined(condition, seed):
    return {"event": "launch_declined", "condition": condition, "seed": seed}


def aggregate(records, declines=()):
    rows = leaderboard.aggregate(records, list(declines), TOURNAMENT)
    doc = leaderboard.leaderboard_document(rows, TOURNAMENT)
    return rows, doc


class TestRowAuthority(unittest.TestCase):
    def test_one_row_per_pair_with_exact_design_fields(self):
        rows, _ = aggregate(
            [
                launch("R2", "18", 1),
                bound("R2", "18", 1),
                launch("R2", "24", 1),
                bound("R2", "24", 1, score=80),
            ]
        )
        self.assertEqual([r["condition"] for r in rows], ["R2", "R2"])
        self.assertEqual([r["seed"] for r in rows], ["18", "24"])
        for row in rows:
            self.assertEqual(sorted(row), sorted(leaderboard.ROW_FIELDS))

    def test_completed_row_sums_all_bound_attempts(self):
        records = [
            launch("R2", "18", 1),
            bound("R2", "18", 1, tokens=(10, 5, 15), latency=42),
            launch("R2", "18", 2),
            bound("R2", "18", 2, tokens=(7, 3, 10), latency=20),
        ]
        rows, _ = aggregate(records)
        row = rows[0]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["attempts_used"], 2)
        self.assertEqual(row["input_tokens"], 17)
        self.assertEqual(row["output_tokens"], 8)
        self.assertEqual(row["total_tokens"], 25)
        self.assertEqual(row["latency_ms"], 62)
        self.assertEqual(row["outcome"], "completed")
        self.assertEqual(row["verified_score"], 100)
        self.assertEqual(row["declined_launches"], 0)
        self.assertEqual(row["core_version"], "0")
        self.assertEqual(len(row["attempt_refs"]), 2)

    def test_unbound_attempt_nulls_usage_but_keeps_receipt_audit(self):
        records = [
            launch("R2", "18", 1),
            unresolved("R2", "18", 1),
            launch("R2", "18", 2),
            bound("R2", "18", 2),
        ]
        rows, _ = aggregate(records)
        row = rows[0]
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertIsNone(row["input_tokens"])
        self.assertIsNone(row["total_tokens"])
        self.assertIsNone(row["latency_ms"])
        # receipt-owned audit values survive, usage never gets zero-filled
        self.assertEqual(row["outcome"], "completed")
        self.assertEqual(row["verified_score"], 100)

    def test_k_exhausted_without_valid_receipt_fails_closed(self):
        records = [
            launch("R2", "18", 1),
            unresolved("R2", "18", 1, "receipt_missing"),
            launch("R2", "18", 2),
            unresolved("R2", "18", 2, "digest_mismatch"),
            launch("R2", "18", 3),
            unresolved("R2", "18", 3, "receipt_missing"),
        ]
        rows, _ = aggregate(records)
        row = rows[0]
        self.assertEqual(row["status"], "evidence_invalid")
        self.assertEqual(row["attempts_used"], 3)
        self.assertIsNone(row["verified_score"])
        self.assertIsNone(row["outcome"])

    def test_valid_safe_abort_is_not_synthetic_success(self):
        rows, _ = aggregate(
            [
                launch("R2", "18", 1),
                bound("R2", "18", 1, outcome="safe_abort", score=40),
            ]
        )
        row = rows[0]
        self.assertEqual(row["status"], "aborted")
        self.assertEqual(row["outcome"], "safe_abort")
        self.assertEqual(row["verified_score"], 40)

    def test_all_declined_row_has_explicit_nulls(self):
        rows, _ = aggregate([declined("R2", "18")])
        row = rows[0]
        self.assertEqual(row["status"], "declined")
        self.assertEqual(row["declined_launches"], 1)
        self.assertEqual(row["attempts_used"], 0)
        self.assertIsNone(row["verified_score"])
        self.assertIsNone(row["input_tokens"])


class TestDeterminism(unittest.TestCase):
    def test_ties_never_reorder_and_rows_are_key_sorted_not_score_sorted(self):
        records = []
        for seed, score in (("18", 100), ("24", 100), ("30", 5)):
            records += [launch("R2", seed, 1), bound("R2", seed, 1, score=score)]
        forward, doc_a = aggregate(records)
        shuffled, doc_b = aggregate(list(reversed(records)))
        self.assertEqual([r["seed"] for r in forward], ["18", "24", "30"])
        self.assertEqual(forward, shuffled)
        self.assertEqual(doc_a, doc_b)

    def test_no_composite_or_rank_fields_exist(self):
        _, doc = aggregate([launch("R2", "18", 1), bound("R2", "18", 1)])
        text = strategy_context.canonical_json_bytes(doc).decode()
        for banned in ("rank", "composite", "total_score", "average"):
            self.assertNotIn(banned, text)

    def test_document_cites_tournament_and_schema(self):
        _, doc = aggregate([])
        self.assertEqual(doc["schema_version"], leaderboard.LEADERBOARD_SCHEMA_VERSION)
        self.assertEqual(doc["tournament_id"], "tournament-unit3-r2")
        self.assertEqual(doc["rows"], [])


if __name__ == "__main__":
    unittest.main()
