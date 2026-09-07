"""Unit 3 evidence-authority tests (design §4): fail-closed binding over the
sealed plain-JSON artifacts only — receipt.json owns score/outcome, the
digest-bound llm/provider-run.json owns usage/latency, stdout owns nothing.
"""

import json
import shutil
import unittest
from pathlib import Path

from harness import evidence, strategy_context

FIXTURE = Path(__file__).parent / "fixtures" / "harness" / "unit3-sealed"
FROZEN = {"provider": "fake", "model": "fake-v1"}
LAUNCH = {"seed": "18"}
BINDING = {
    "context_digest": "sha256:" + "0" * 64,
    "reported_digest": "null",
    "policy_digest": None,  # filled per case from the receipt
    "logical_run_id": None,
}
PROMPT_DIGEST = json.loads((FIXTURE / "llm" / "llm-receipt.json").read_bytes())[
    "prompt_digest"
]
TOOL_DIGEST = json.loads((FIXTURE / "llm" / "llm-receipt.json").read_bytes())[
    "tool_schema_digest"
]
# Binding identity comes from the PRISTINE fixture, never the mutated copy.
_FIXTURE_RECEIPT = evidence.load_receipt(FIXTURE)


def _canonical(path, value):
    path.write_bytes(strategy_context.canonical_json_bytes(value))


def _recompute_self_digest(value):
    value.pop("llm_receipt_digest", None)
    value["llm_receipt_digest"] = strategy_context.digest_json(value)


def _consistent_llm(run_dir, mutate):
    """Spoof an llm-receipt field while keeping the envelope digest-coherent."""
    path = run_dir / "llm" / "llm-receipt.json"
    value = json.loads(path.read_bytes())
    mutate(value)
    _recompute_self_digest(value)
    _canonical(path, value)


def _consistent_provider(run_dir, mutate):
    """Spoof provider-run identity and refresh both covering digests."""
    provider_path = run_dir / "llm" / "provider-run.json"
    llm_path = run_dir / "llm" / "llm-receipt.json"
    provider = json.loads(provider_path.read_bytes())
    mutate(provider)
    llm = json.loads(llm_path.read_bytes())
    llm["provider_evidence_digest"] = strategy_context.digest_json(provider)
    _recompute_self_digest(llm)
    _canonical(llm_path, llm)
    _canonical(provider_path, provider)


def _consistent_receipt(run_dir, mutate):
    """Spoof a receipt field and refresh both covering digests."""
    receipt_path = run_dir / "receipt.json"
    llm_path = run_dir / "llm" / "llm-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    mutate(receipt)
    llm = json.loads(llm_path.read_bytes())
    llm["core_receipt_digest"] = strategy_context.sha256_digest(
        strategy_context.canonical_json_bytes(receipt)
    )
    _recompute_self_digest(llm)
    _canonical(llm_path, llm)
    _canonical(receipt_path, receipt)


def _rewrite(path, mutate):
    """Apply a structural mutation and re-canonicalize the artifact bytes."""
    value = json.loads(path.read_bytes())
    mutate(value)
    _canonical(path, value)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile_dir()
        self.run_dir = self._tmp / "run"
        shutil.copytree(FIXTURE, self.run_dir)

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def bind(self, expectations=None):
        binding = dict(BINDING)
        binding["policy_digest"] = _FIXTURE_RECEIPT["policy_digest"]
        binding["logical_run_id"] = _FIXTURE_RECEIPT["logical_run_id"]
        return evidence.bind_attempt_evidence(
            self.run_dir,
            FROZEN,
            LAUNCH,
            binding,
            expectations
            or {"prompt_digest": PROMPT_DIGEST, "tool_schema_digest": TOOL_DIGEST},
        )


def tempfile_dir():
    import tempfile

    return Path(tempfile.mkdtemp())


class TestHappyPath(Base):
    def test_null_raw_visible_response_binds_like_the_core_contract(self):
        """Live-acceptance finding: real tool-call responses legitimately carry
        raw_visible_response=null (core adapter: str | None, adapter.py L152-155);
        the binder must bind, never reject, that valid provider evidence."""
        provider_run = self.run_dir / "llm" / "provider-run.json"
        _rewrite(
            provider_run, lambda v: v["requests"][0].update(raw_visible_response=None)
        )
        result = self.bind()
        self.assertEqual(result.status, "bound")
        self.assertIsNone(result.reason)
        self.assertEqual(result.verified_score, 100)

    def test_sealed_fixture_binds_official_metrics(self):
        result = self.bind()
        self.assertEqual(result.status, "bound")
        self.assertIsNone(result.reason)
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.verified_score, 100)
        self.assertEqual((result.input_tokens, result.output_tokens), (10, 5))
        self.assertEqual(result.total_tokens, 15)
        self.assertEqual(result.latency_ms, 42)
        self.assertTrue(
            result.logical_run_id and result.logical_run_id.startswith("lr_")
        )

    def test_load_receipt_missing_fails_closed(self):
        with self.assertRaises(evidence.EvidenceError):
            evidence.load_receipt(self.run_dir / "nope")


class TestAdversarialMatrix(Base):
    """Every broken binding, identity, byte, or value fails closed with nulls."""

    def _cases(self):
        def drop(path):
            path.unlink()

        def rewrite(path, mutate):
            value = json.loads(path.read_bytes())
            mutate(value)
            _canonical(path, value)

        llm_receipt = self.run_dir / "llm" / "llm-receipt.json"
        provider_run = self.run_dir / "llm" / "provider-run.json"
        receipt = self.run_dir / "receipt.json"
        return [
            ("receipt_deleted", lambda: drop(receipt), "missing"),
            ("provider_run_deleted", lambda: drop(provider_run), "missing"),
            ("llm_receipt_deleted", lambda: drop(llm_receipt), "missing"),
            (
                "receipt_malformed",
                lambda: receipt.write_bytes(b"{not json"),
                "malformed",
            ),
            (
                "receipt_non_canonical",
                lambda: receipt.write_bytes(
                    json.dumps(json.loads(receipt.read_bytes()), indent=2).encode()
                ),
                "non-canonical",
            ),
            (
                "core_receipt_digest_tampered",
                lambda: rewrite(
                    llm_receipt,
                    lambda v: v.update(core_receipt_digest="sha256:" + "f" * 64),
                ),
                "core_receipt_digest",
            ),
            (
                "provider_evidence_digest_tampered",
                lambda: rewrite(
                    llm_receipt,
                    lambda v: v.update(provider_evidence_digest="sha256:" + "f" * 64),
                ),
                "provider_evidence_digest",
            ),
            (
                "llm_receipt_digest_tampered",
                lambda: rewrite(
                    llm_receipt,
                    lambda v: v.update(llm_receipt_digest="sha256:" + "f" * 64),
                ),
                "llm_receipt_digest",
            ),
            (
                "prompt_digest_drift",
                lambda: rewrite(
                    llm_receipt, lambda v: v.update(prompt_digest="sha256:" + "e" * 64)
                ),
                "prompt_digest",
            ),
            (
                "tool_schema_digest_drift",
                lambda: rewrite(
                    llm_receipt,
                    lambda v: v.update(tool_schema_digest="sha256:" + "e" * 64),
                ),
                "tool_schema_digest",
            ),
            (
                "provider_model_spoofed",
                lambda: _consistent_llm(
                    self.run_dir, lambda v: v.update(model="other-v9")
                ),
                "provider/model",
            ),
            (
                "logical_run_id_mismatch",
                lambda: _consistent_provider(
                    self.run_dir, lambda v: v.update(logical_run_id="lr_" + "b" * 64)
                ),
                "logical_run_id",
            ),
            (
                "execution_id_mismatch",
                lambda: _consistent_llm(
                    self.run_dir, lambda v: v.update(execution_id="other-exec")
                ),
                "execution_id",
            ),
            (
                "seed_mismatch",
                lambda: _consistent_receipt(
                    self.run_dir, lambda v: v.update(seed="24")
                ),
                "seed",
            ),
            (
                "float_latency",
                # the canonical mirror refuses to SERIALIZE floats, so the
                # tampered artifact lands as raw non-canonical JSON bytes and
                # the binder classifies it instead of the setup raising
                lambda: provider_run.write_bytes(
                    json.dumps(
                        {
                            **json.loads(provider_run.read_bytes()),
                            "requests": [
                                {
                                    **json.loads(provider_run.read_bytes())["requests"][
                                        0
                                    ],
                                    "latency_ms": 42.5,
                                }
                            ],
                        }
                    ).encode()
                ),
                "numeric",
            ),
            (
                "bool_usage",
                lambda: rewrite(
                    provider_run,
                    lambda v: v["requests"][0]["usage"].update(total_tokens=True),
                ),
                "numeric",
            ),
            (
                "outcome_not_in_vocabulary",
                lambda: rewrite(receipt, lambda v: v.update(outcome="totally-won")),
                "outcome",
            ),
        ]

    def test_every_tamper_yields_invalid_with_nulls(self):
        for name, mutate, reason in self._cases():
            with self.subTest(case=name):
                # each case mutates its own pristine copy (no cross-case leaks)
                shutil.rmtree(self.run_dir)
                shutil.copytree(FIXTURE, self.run_dir)
                mutate()
                result = self.bind()
                self.assertEqual(result.status, "invalid", name)
                self.assertIsInstance(result.reason, str, name)
                self.assertIn(reason, result.reason or "", name)
                self.assertIsNone(result.verified_score, name)
                self.assertIsNone(result.input_tokens, name)
                self.assertIsNone(result.total_tokens, name)
                self.assertIsNone(result.latency_ms, name)
                self.assertIsNone(result.outcome, name)

    def test_missing_llm_directory(self):
        shutil.rmtree(self.run_dir / "llm")
        result = self.bind()
        self.assertEqual(result.status, "invalid")
        self.assertIsInstance(result.reason, str)
        self.assertIn("missing", result.reason or "")

    def test_planted_secret_fails_closed(self):
        path = self.run_dir / "llm" / "provider-run.json"
        value = json.loads(path.read_bytes())
        value["requests"][0]["raw_visible_response"] = "key=sk-abcdefghijklmnop1234"
        _canonical(path, value)
        result = self.bind()
        self.assertEqual(result.status, "invalid")
        self.assertIsInstance(result.reason, str)
        self.assertIn("secret", result.reason or "")

    def test_stdout_score_claim_is_never_evidence(self):
        result = self.bind()
        self.assertEqual(result.status, "bound")
        self.assertEqual(result.verified_score, 100)


class TestStructuralValidation(Base):
    """PR #20 blocker 3: nested content errors must classify invalid — never
    escape the binder as KeyError/TypeError."""

    def _cases(self):
        provider_run = self.run_dir / "llm" / "provider-run.json"
        receipt = self.run_dir / "receipt.json"
        return [
            (
                "request_missing_policy_step",
                lambda: _rewrite(
                    provider_run, lambda v: v["requests"][0].pop("policy_step")
                ),
                "policy_step",
            ),
            (
                "request_missing_usage",
                lambda: _rewrite(provider_run, lambda v: v["requests"][0].pop("usage")),
                "usage",
            ),
            (
                "usage_missing_token_field",
                lambda: _rewrite(
                    provider_run,
                    lambda v: v["requests"][0]["usage"].pop("input_tokens"),
                ),
                "input_tokens",
            ),
            (
                "request_not_an_object",
                lambda: _rewrite(provider_run, lambda v: v.update(requests=["nope"])),
                "object",
            ),
            (
                "logical_run_id_not_a_string",
                lambda: _rewrite(receipt, lambda v: v.update(logical_run_id=12345)),
                "lr_ plus 64 hex",
            ),
            (
                "outcome_not_a_string",
                lambda: _rewrite(receipt, lambda v: v.update(outcome=["completed"])),
                "outcome",
            ),
        ]

    def test_structural_content_errors_classify_invalid_without_raising(self):
        for name, mutate, reason in self._cases():
            with self.subTest(case=name):
                shutil.rmtree(self.run_dir)
                shutil.copytree(FIXTURE, self.run_dir)
                mutate()
                result = self.bind()  # must not raise for content
                self.assertEqual(result.status, "invalid", name)
                self.assertIsInstance(result.reason, str, name)
                self.assertIn(reason, result.reason or "", name)
                self.assertIsNone(result.verified_score, name)
                self.assertIsNone(result.input_tokens, name)


class TestCanonicalParsing(Base):
    """PR #20 final blocker: artifact-content parse failures must classify
    invalid — never escape the binder as UnicodeDecodeError/ValueError, and
    finite floats are non-canonical per the core writer's integers-only
    contract (REQ-07)."""

    def test_invalid_utf8_classifies_invalid_never_raises(self):
        receipt = self.run_dir / "receipt.json"
        receipt.write_bytes(b'{"outcome": "\xff\xfe"}')
        result = self.bind()  # must not raise for content
        self.assertEqual(result.status, "invalid")
        self.assertIn("utf-8", (result.reason or "").lower())
        self.assertIsNone(result.verified_score)
        self.assertIsNone(result.input_tokens)

    def test_nan_constant_classifies_invalid_never_raises(self):
        receipt = self.run_dir / "receipt.json"
        receipt.write_bytes(b'{"score": NaN}')
        result = self.bind()  # must not raise for content
        self.assertEqual(result.status, "invalid")
        self.assertIn("float", (result.reason or "").lower())
        self.assertIsNone(result.verified_score)
        self.assertIsNone(result.input_tokens)

    def test_finite_float_anywhere_classifies_invalid_never_raises(self):
        receipt = self.run_dir / "receipt.json"
        # Canonical-looking bytes carrying a finite float: previously
        # round-tripped and passed, though the writer can never emit it.
        receipt.write_bytes(b'{"untracked": 1.5}')
        result = self.bind()  # must not raise for content
        self.assertEqual(result.status, "invalid")
        self.assertIn("float", (result.reason or "").lower())
        self.assertIsNone(result.verified_score)
        self.assertIsNone(result.input_tokens)


if __name__ == "__main__":
    unittest.main()
