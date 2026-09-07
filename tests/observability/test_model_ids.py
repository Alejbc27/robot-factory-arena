"""Slice A3 deterministic-ID formula tests over the sealed fixture identity.

The design's exact versioned formulas are recomputed independently in the
tests (canonical bytes + hashlib) and compared against ``observability.ids``.
Path/discovery-order independence is proven at the formula level and at the
model level (``assemble_items`` re-assembly); trace-only rows use the
``trace-only-v1`` amendment to avoid the null-slot collision.
"""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from observability import assemble_items, build_trace_records, canonical_json_bytes, ids

from .test_artifacts import _bound_row, _row, _sealed_root, write_leaderboard

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "unit3-sealed"


def _sha256_hex(payload):
    return hashlib.sha256(payload).hexdigest()


def _expected(prefix, version_tag, *inputs):
    """Independent recompute of one design formula over canonical bytes."""
    return prefix + _sha256_hex(canonical_json_bytes([version_tag, *inputs]))


def _identity_from_root(root):
    """Stable identity inputs extracted from one artifact root copy."""
    root = Path(root)
    receipt = json.loads((root / "receipt.json").read_text())
    llm = json.loads((root / "llm" / "llm-receipt.json").read_text())
    provider = json.loads((root / "llm" / "provider-run.json").read_text())
    return {
        "tournament_id": "tournament-observed",
        "attempt_id": "attempt-t1",
        "execution_id": receipt["execution_id"],
        "core_receipt_digest": llm["core_receipt_digest"],
        "llm_receipt_digest": llm["llm_receipt_digest"],
        "provider_evidence_digest": llm["provider_evidence_digest"],
        "request_id": provider["requests"][0]["request_id"],
    }


def _trace_id(values):
    return ids.trace_id(
        values["tournament_id"],
        values["attempt_id"],
        values["execution_id"],
        values["core_receipt_digest"],
        values["llm_receipt_digest"],
    )


def _generation_id(values):
    return ids.generation_id(
        values["execution_id"],
        values["request_id"],
        values["provider_evidence_digest"],
    )


class TestDeterministicIDFormulas(unittest.TestCase):
    def setUp(self):
        self.values = _identity_from_root(FIXTURE)

    def test_exact_versioned_formulas(self):
        v = self.values
        trace = _trace_id(v)
        self.assertEqual(
            trace,
            _expected(
                "tr_",
                "trace-v1",
                v["tournament_id"],
                v["attempt_id"],
                v["execution_id"],
                v["core_receipt_digest"],
                v["llm_receipt_digest"],
            ),
        )
        self.assertEqual(
            _generation_id(v),
            _expected(
                "gen_",
                "generation-v1",
                v["execution_id"],
                v["request_id"],
                v["provider_evidence_digest"],
            ),
        )
        score = ids.score_id(trace, "verified_score", v["core_receipt_digest"])
        self.assertEqual(
            score,
            _expected(
                "score_", "score-v1", trace, "verified_score", v["core_receipt_digest"]
            ),
        )
        self.assertEqual(
            ids.event_id("trace-create", trace),
            _expected("evt_", "event-v1", "trace-create", trace),
        )
        for prefix, value in (
            ("tr_", trace),
            ("gen_", _generation_id(v)),
            ("score_", score),
            ("evt_", ids.event_id("trace-create", trace)),
        ):
            self.assertTrue(value.startswith(prefix), value)
            self.assertRegex(value[len(prefix) :], r"^[0-9a-f]{64}$")

    def test_every_identity_input_is_sensitive(self):
        trace = _trace_id(self.values)
        for key in (
            "tournament_id",
            "attempt_id",
            "execution_id",
            "core_receipt_digest",
            "llm_receipt_digest",
        ):
            with self.subTest(input=key):
                changed = dict(self.values)
                changed[key] = "changed-" + str(self.values[key])
                self.assertNotEqual(_trace_id(changed), trace)
        generation = _generation_id(self.values)
        for key in ("execution_id", "request_id", "provider_evidence_digest"):
            with self.subTest(input=key):
                changed = dict(self.values)
                changed[key] = "changed-" + str(self.values[key])
                self.assertNotEqual(_generation_id(changed), generation)

    def test_version_tag_is_part_of_the_identity(self):
        v = self.values
        inputs = [
            v["tournament_id"],
            v["attempt_id"],
            v["execution_id"],
            v["core_receipt_digest"],
            v["llm_receipt_digest"],
        ]
        self.assertEqual(_trace_id(v), _expected("tr_", "trace-v1", *inputs))
        self.assertNotEqual(_trace_id(v), _expected("tr_", "trace-v2", *inputs))

    def test_path_independence_across_roots(self):
        roots = []
        for index in range(2):
            tmp = Path(tempfile.mkdtemp(prefix=f"obsids-root{index}-"))
            shutil.copytree(FIXTURE, tmp / "evidence")
            roots.append(tmp / "evidence")
        self.assertNotEqual(str(roots[0]), str(roots[1]))
        first, second = (_identity_from_root(root) for root in roots)
        self.assertEqual(_trace_id(first), _trace_id(second))
        self.assertEqual(_generation_id(first), _generation_id(second))

    def test_discovery_order_independence(self):
        request_ids = ["req-0", "req-1"]
        generation = {
            request_id: ids.generation_id(
                self.values["execution_id"],
                request_id,
                self.values["provider_evidence_digest"],
            )
            for request_id in request_ids
        }
        reversed_order = list(reversed(request_ids))
        self.assertEqual(
            generation,
            {
                request_id: ids.generation_id(
                    self.values["execution_id"],
                    request_id,
                    self.values["provider_evidence_digest"],
                )
                for request_id in reversed_order
            },
        )
        self.assertEqual(
            _trace_id(self.values),
            _trace_id(dict(reversed(list(self.values.items())))),
        )

    def test_repeated_export_stability(self):
        def export_ids():
            trace = _trace_id(self.values)
            return {
                "trace": trace,
                "generation": _generation_id(self.values),
                "score": ids.score_id(
                    trace, "verified_score", self.values["core_receipt_digest"]
                ),
                "event": ids.event_id("trace-create", trace),
            }

        self.assertEqual(export_ids(), export_ids())


def _declined_root(prefix, rows):
    """Leaderboard-only root: declined rows need no run or launch evidence."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    write_leaderboard(tmp, rows)
    return tmp


def _trace_only_ids(root):
    """Assemble a root and map its trace-only rows to amended trace IDs."""
    items, skips = assemble_items(root)
    assert skips == []
    identities = {}
    for item in items:
        ordinals = tuple(ref["attempt_ordinal"] for ref in item.attempt_refs)
        identities[(item.condition, item.seed)] = ids.trace_only_trace_id(
            item.tournament_id,
            item.condition,
            item.seed,
            ordinals,
            item.declined_launches,
        )
    return identities


class TestTraceOnlyIdentityAmendment(unittest.TestCase):
    """v1 amendment: trace-only rows must not collide on null formula slots."""

    def test_two_declined_rows_get_distinct_stable_trace_ids(self):
        rows = [_row("R1", "18", "declined"), _row("R2", "19", "declined")]
        root = _declined_root("obsids-declined-", rows)
        first = _trace_only_ids(root)
        self.assertEqual(set(first), {("R1", "18"), ("R2", "19")})
        self.assertEqual(len(set(first.values())), 2)
        tournament = assemble_items(root)[0][0].tournament_id

        # Regression pin: the pre-amendment trace-v1 shape fed null slots for
        # every trace-only row, so both rows collided on a single ID.
        def legacy(unused):
            payload = canonical_json_bytes(
                ["trace-v1", tournament, None, None, None, None]
            )
            return "tr_" + hashlib.sha256(payload).hexdigest()

        self.assertEqual(legacy("R1"), legacy("R2"))
        self.assertEqual(len({legacy("R1"), *first.values()}), 3)

        # Stable across re-export of the same root and of an independent copy.
        self.assertEqual(_trace_only_ids(root), first)
        mirror = Path(tempfile.mkdtemp(prefix="obsids-mirror-")) / "copy"
        shutil.copytree(root, mirror)
        self.assertEqual(_trace_only_ids(mirror), first)

    def test_decline_ordinal_and_count_are_load_bearing(self):
        base = _row("R1", "18", "declined")
        root = _declined_root("obsids-ordinal-", [base])
        (item,) = assemble_items(root)[0]
        ordinals = tuple(ref["attempt_ordinal"] for ref in item.attempt_refs)
        identity = ids.trace_only_trace_id(
            item.tournament_id,
            item.condition,
            item.seed,
            ordinals,
            item.declined_launches,
        )
        for changed in (
            ids.trace_only_trace_id(
                item.tournament_id,
                item.condition,
                item.seed,
                (7,),
                item.declined_launches,
            ),
            ids.trace_only_trace_id(
                item.tournament_id,
                item.condition,
                item.seed,
                ordinals,
                item.declined_launches + 1,
            ),
        ):
            self.assertNotEqual(changed, identity)


class TestModelLevelReassemblyStability(unittest.TestCase):
    """A3 addendum: assembled items yield stable IDs across fresh re-assembly."""

    def test_bound_ids_are_stable_across_reassembly(self):
        def export():
            root, _ = _sealed_root("obsids-stable-", [_bound_row()])
            items, skips = assemble_items(root)
            assert skips == []
            (record,) = build_trace_records(items)
            trace = ids.trace_id(
                record.tournament_id,
                record.attempt_id,
                record.execution_id,
                record.digests.core_receipt,
                record.digests.llm_receipt,
            )
            return {
                "trace": trace,
                "generation": ids.generation_id(
                    record.execution_id,
                    record.generations[0].request_id,
                    record.digests.provider_evidence,
                ),
                "score": ids.score_id(
                    trace, "verified_score", record.digests.core_receipt
                ),
            }

        self.assertEqual(export(), export())

    def test_trace_only_ids_stay_stable_through_reassembly(self):
        rows = [_row("R1", "18", "declined"), _row("R2", "19", "declined")]
        root, _ = _sealed_root("obsids-traceonly-stable-", rows)
        self.assertEqual(_trace_only_ids(root), _trace_only_ids(root))


if __name__ == "__main__":
    unittest.main()
