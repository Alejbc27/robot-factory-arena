"""Slice A1 artifact-contract tests over copies of the sealed unit3 fixture.

The committed fixture is read-only input: every test copies it into a
temporary run root and mutates only the copy.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from observability import (
    ArtifactError,
    SkipRecord,
    artifacts,
    canonical_json_bytes,
    digest_json,
    sha256_digest,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "unit3-sealed"


def _fixture_json(name):
    return json.loads((FIXTURE / name).read_text())


def _write_canonical(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _run_dir(root):
    receipt = _fixture_json("receipt.json")
    return (
        Path(root)
        / "core"
        / "runs"
        / receipt["logical_run_id"]
        / receipt["execution_id"]
    )


def _reseal(run_dir):
    """Recompute the llm-receipt digest bindings over the current copy."""
    raw = (run_dir / "receipt.json").read_bytes()
    provider = json.loads((run_dir / "llm" / "provider-run.json").read_text())
    llm = json.loads((run_dir / "llm" / "llm-receipt.json").read_text())
    llm["core_receipt_digest"] = sha256_digest(raw)
    llm["provider_evidence_digest"] = digest_json(provider)
    llm.pop("llm_receipt_digest")
    llm["llm_receipt_digest"] = digest_json(llm)
    _write_canonical(run_dir / "llm" / "llm-receipt.json", llm)
    return (
        llm["core_receipt_digest"],
        llm["provider_evidence_digest"],
        llm["llm_receipt_digest"],
    )


def build_root(prefix, *, patch=None, post_patch=None):
    """Copy the sealed fixture into a fresh temp run root; mutate the copy."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    run_dir = _run_dir(tmp)
    shutil.copytree(FIXTURE, run_dir)
    if patch is not None:
        patch(run_dir)
    digests = _reseal(run_dir)
    if post_patch is not None:
        post_patch(run_dir)
    return tmp, digests


def add_execution(root, exec_id):
    """Second execution of the same logical run, resealed consistently."""
    dst = _run_dir(root).parent / exec_id
    shutil.copytree(FIXTURE, dst)
    for rel in ("receipt.json", "llm/provider-run.json", "llm/llm-receipt.json"):
        p = dst / rel
        value = json.loads(p.read_text())
        value["execution_id"] = exec_id
        _write_canonical(p, value)
    return _reseal(dst)


class TestCanonicalContract(unittest.TestCase):
    def test_canonical_bytes_rules(self):
        self.assertEqual(canonical_json_bytes({"b": 1, "a": None}), b'{"a":null,"b":1}')
        self.assertEqual(canonical_json_bytes({"k": "é"}), '{"k":"é"}'.encode())
        with self.assertRaises(ArtifactError) as raised:
            canonical_json_bytes({"x": 0.5})
        self.assertEqual(raised.exception.reason, "non_canonical_json")

    def test_sealed_fixture_binds_authoritatively(self):
        root, digests = build_root("obsa-happy-")
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(skips, [])
        self.assertEqual(len(evidence), 1)
        bound = evidence[0]
        receipt = bound["receipt"]
        self.assertEqual(receipt["outcome"], "completed")
        self.assertEqual(receipt["verified_score"], 100)
        self.assertEqual(receipt["verification"]["status"], "valid")
        self.assertEqual(receipt["seed"], "18")
        self.assertEqual(bound["execution_id"], "sealed-fixture-01")
        self.assertEqual(bound["logical_run_id"], receipt["logical_run_id"])
        (request,) = bound["provider_run"]["requests"]
        self.assertEqual(request["provider"], "fake")
        self.assertEqual(request["model"], "fake-v1")
        self.assertEqual(request["request_id"], "req-0")
        self.assertEqual(request["policy_step"], 0)
        self.assertEqual(request["latency_ms"], 42)
        self.assertEqual(
            request["usage"],
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        self.assertIsNone(request["error"])
        self.assertIsNone(request["tool_call"])
        llm = bound["llm_receipt"]
        self.assertEqual(bound["core_receipt_digest"], digests[0])
        self.assertEqual(bound["provider_evidence_digest"], digests[1])
        self.assertEqual(bound["llm_receipt_digest"], digests[2])
        self.assertEqual(
            llm["prompt_digest"], _fixture_json("llm/llm-receipt.json")["prompt_digest"]
        )
        self.assertEqual(
            llm["tool_schema_digest"],
            _fixture_json("llm/llm-receipt.json")["tool_schema_digest"],
        )

    def test_discovery_order_is_identity_sorted(self):
        root, _ = build_root("obsa-order-")
        add_execution(root, "sealed-fixture-02")
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(skips, [])
        found = [(e["logical_run_id"], e["execution_id"]) for e in evidence]
        self.assertEqual(found, sorted(found))
        self.assertEqual(found[0][1], "sealed-fixture-01")


class TestAdversarialSkips(unittest.TestCase):
    def _assert_skip(self, root, reason):
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(evidence, [])
        self.assertEqual([s.reason for s in skips], [reason])
        self.assertIsInstance(skips[0], SkipRecord)
        self.assertIn(skips[0].reason, artifacts.REASONS)
        return skips

    def test_stable_skip_reasons(self):
        def delete_provider(d):
            (d / "llm" / "provider-run.json").unlink()

        def malformed(d):
            (d / "receipt.json").write_bytes(b'{"execution_id": ')

        def spaced(d):
            value = json.loads((d / "receipt.json").read_text())
            (d / "receipt.json").write_bytes(json.dumps(value, sort_keys=True).encode())

        def floated(d):
            value = json.loads((d / "receipt.json").read_text())
            value["residual"] = 0.5
            (d / "receipt.json").write_bytes(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            )

        def del_seed(d):
            value = json.loads((d / "receipt.json").read_text())
            del value["seed"]
            _write_canonical(d / "receipt.json", value)

        def schema_two(d):
            value = json.loads((d / "receipt.json").read_text())
            value["schema_version"] = 2
            _write_canonical(d / "receipt.json", value)

        def tamper_provider(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["latency_ms"] = 43
            _write_canonical(d / "llm" / "provider-run.json", provider)

        def disagree_identity(d):
            path = d / "llm" / "llm-receipt.json"
            llm = json.loads(path.read_text())
            llm["execution_id"] = "execution-disagree"
            llm.pop("llm_receipt_digest")
            llm["llm_receipt_digest"] = digest_json(llm)
            _write_canonical(path, llm)

        def secret(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["raw_visible_response"] = "sk-live-secret-token"
            _write_canonical(d / "llm" / "provider-run.json", provider)

        cases = {
            "missing_artifact": dict(post_patch=delete_provider),
            "malformed_json": dict(patch=malformed),
            "non_canonical_json": dict(patch=spaced),
            "non_canonical_json_float": dict(patch=floated),
            "missing_field": dict(patch=del_seed),
            "unsupported_schema": dict(patch=schema_two),
            "digest_mismatch": dict(post_patch=tamper_provider),
            "identity_mismatch": dict(post_patch=disagree_identity),
            "secret_detected": dict(patch=secret),
        }
        for expected, kwargs in cases.items():
            with self.subTest(reason=expected):
                root, _ = build_root(f"obsa-{expected}-", **kwargs)
                skips = self._assert_skip(root, expected.split("_float")[0])
                self.assertNotIn("sk-live-secret-token", repr(skips))

    def test_null_raw_visible_response_stays_null(self):
        def null_response(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["raw_visible_response"] = None
            _write_canonical(d / "llm" / "provider-run.json", provider)

        root, _ = build_root("obsa-null-", patch=null_response)
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(skips, [])
        (request,) = evidence[0]["provider_run"]["requests"]
        self.assertIsNone(request["raw_visible_response"])
        self.assertNotEqual(request["latency_ms"], 0)


if __name__ == "__main__":
    unittest.main()
