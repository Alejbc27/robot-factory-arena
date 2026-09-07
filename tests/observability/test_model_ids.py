"""Slice A3 deterministic-ID formula tests over the sealed fixture identity.

The design's exact versioned formulas are recomputed independently in the
tests (canonical bytes + hashlib) and compared against ``observability.ids``.
Path/discovery-order independence is proven at the formula level; model-level
re-assembly stability lands with the row-assembly slice.
"""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from observability import canonical_json_bytes, ids

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


if __name__ == "__main__":
    unittest.main()
