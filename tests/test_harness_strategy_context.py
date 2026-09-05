"""Unit 5 StrategyContext contract tests (design §2.1): R3-produced discoveries
satisfy the AMEND-4A-3 mirror contract BEFORE anything can be frozen, the
snapshot bytes are canonical and integers-only (floats/NaN rejected at both the
validator and the serializer), duplicates are resolved by declared identity,
and canonical identity is permutation-invariant — reordering discoveries or
shifting the snapshot ordinal alone never changes the digest, while any change
of semantic content does.

The R3 discovery samples extend the PR #18 blocker-3 parity mechanism: every
sample is asserted against BOTH the harness mirror and the core policy
validator (tests may import the core; harness source may not) — they must
accept and reject identically.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness import strategy_context, versions
from robot_factory_arena.policies.llm import validate_strategy_context


def _discovery(**mutations):
    record = {
        "source_attempt_id": "r3-agent-01-seed-18-attempt-01",
        "explorer_id": "explorer-01",
        "discovery_ordinal": 0,
        "producing_condition": "R3",
        "producing_agent_id": "agent-01",
        "producing_seed": "18",
        "producing_attempt_ordinal": 1,
        "evidence": {
            "run_status": "finished",
            "core_receipt_digest": "sha256:" + "a" * 64,
            "llm_receipt_digest": "sha256:" + "b" * 64,
        },
        "text": "The gate opens only after the blue switch flips",
    }
    for key, value in mutations.items():
        if value is None:
            record.pop(key)
        else:
            record[key] = value
    return record


def _context(**mutations):
    context = {
        "schema_version": versions.CONTEXT_SCHEMA_VERSION,
        "source_tournament_id": "tournament-unit5-r3",
        "guidance": [],
        "discoveries": [_discovery()],
    }
    context.update(mutations)
    return context


# (name, context-or-null, should_pass) — dual-validated mirror vs core.
R3_PARITY_SAMPLES = (
    ("valid-r3-discovery", _context(), True),
    ("valid-null", None, True),
    (
        "valid-multi-branch-discoveries",
        _context(
            discoveries=[
                _discovery(
                    source_attempt_id="r3-agent-02-seed-24-attempt-01",
                    explorer_id="explorer-01",
                    producing_agent_id="agent-02",
                    producing_seed="24",
                ),
                _discovery(
                    source_attempt_id="r3-agent-01-seed-18-attempt-02",
                    producing_attempt_ordinal=2,
                ),
                _discovery(
                    source_attempt_id="r3-agent-01-seed-18-attempt-01",
                    explorer_id="explorer-02",
                ),
            ]
        ),
        True,
    ),
    (
        "producing-condition-r2",
        _context(discoveries=[_discovery(producing_condition="R2")]),
        False,
    ),
    (
        "producing-condition-r1",
        _context(discoveries=[_discovery(producing_condition="R1")]),
        False,
    ),
    (
        "producing-condition-missing",
        _context(discoveries=[_discovery(producing_condition=None)]),
        False,
    ),
    (
        "producing-attempt-ordinal-zero",
        _context(discoveries=[_discovery(producing_attempt_ordinal=0)]),
        False,
    ),
    (
        "producing-attempt-ordinal-float",
        _context(discoveries=[_discovery(producing_attempt_ordinal=1.0)]),
        False,
    ),
    (
        "discovery-ordinal-bool",
        _context(discoveries=[_discovery(discovery_ordinal=True)]),
        False,
    ),
    (
        "discovery-ordinal-float",
        _context(discoveries=[_discovery(discovery_ordinal=0.5)]),
        False,
    ),
    (
        "discovery-ordinal-nan",
        _context(discoveries=[_discovery(discovery_ordinal=float("nan"))]),
        False,
    ),
    (
        "producing-seed-int-not-string",
        _context(discoveries=[_discovery(producing_seed=18)]),
        False,
    ),
    (
        "evidence-run-status-not-finished",
        _context(
            discoveries=[
                _discovery(
                    evidence={
                        "run_status": "running",
                        "core_receipt_digest": "sha256:" + "a" * 64,
                        "llm_receipt_digest": "sha256:" + "b" * 64,
                    }
                )
            ]
        ),
        False,
    ),
    (
        "evidence-core-digest-untyped",
        _context(
            discoveries=[
                _discovery(
                    evidence={
                        "run_status": "finished",
                        "core_receipt_digest": "deadbeef",
                        "llm_receipt_digest": "sha256:" + "b" * 64,
                    }
                )
            ]
        ),
        False,
    ),
    (
        "evidence-llm-digest-uppercase",
        _context(
            discoveries=[
                _discovery(
                    evidence={
                        "run_status": "finished",
                        "core_receipt_digest": "sha256:" + "a" * 64,
                        "llm_receipt_digest": "sha256:" + "B" * 64,
                    }
                )
            ]
        ),
        False,
    ),
    (
        "duplicate-discovery-identity",
        _context(discoveries=[_discovery(), _discovery(text="another text")]),
        False,
    ),
    (
        "equal-text-two-explorers-stay-separate",
        _context(discoveries=[_discovery(), _discovery(explorer_id="explorer-02")]),
        True,
    ),
    (
        "discovery-extra-key",
        _context(discoveries=[_discovery(score_hint=100)]),
        False,
    ),
    (
        "evidence-extra-key",
        _context(
            discoveries=[
                _discovery(
                    evidence={
                        "run_status": "finished",
                        "core_receipt_digest": "sha256:" + "a" * 64,
                        "llm_receipt_digest": "sha256:" + "b" * 64,
                        "latency_hint": 1,
                    }
                )
            ]
        ),
        False,
    ),
    ("empty-text", _context(discoveries=[_discovery(text="")]), False),
    ("float-schema-version", _context(schema_version=1.0), False),
)


class TestR3DiscoveryParity(unittest.TestCase):
    def test_mirror_and_core_accept_and_reject_identically(self):
        for name, sample, should_pass in R3_PARITY_SAMPLES:
            with self.subTest(sample=name):
                if should_pass:
                    strategy_context.validate_context(sample)
                    validate_strategy_context(sample)
                else:
                    with self.assertRaises(strategy_context.ContextError):
                        strategy_context.validate_context(sample)
                    with self.assertRaises(ValueError):
                        validate_strategy_context(sample)


class TestIntegersOnlyCanonicalBytes(unittest.TestCase):
    """The snapshot bytes are canonical: integers-only, NaN rejected."""

    def test_serializer_rejects_floats_and_nan(self):
        with self.assertRaises(ValueError):
            strategy_context.canonical_json_bytes({"ordinal": 1.5})
        with self.assertRaises(ValueError):
            strategy_context.canonical_json_bytes({"ordinal": float("nan")})
        with self.assertRaises(ValueError):
            strategy_context.canonical_json_bytes({"ordinal": float("inf")})

    def test_freeze_rejects_non_canonical_content_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy-context-900.json"
            for bad in (
                _context(discoveries=[_discovery(discovery_ordinal=1.0)]),
                _context(discoveries=[_discovery(discovery_ordinal=float("nan"))]),
                _context(discoveries=[_discovery(producing_seed=18)]),
            ):
                with self.assertRaises(strategy_context.ContextError):
                    strategy_context.freeze_snapshot(bad, path)
            # the atomic freeze never leaves partial or stale bytes behind
            self.assertFalse(path.exists())

    def test_snapshot_bytes_are_canonical_and_digest_bound(self):
        context = _context()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy-context-000.json"
            frozen = strategy_context.freeze_snapshot(context, path)
            data = path.read_bytes()
            self.assertEqual(data, strategy_context.canonical_json_bytes(context))
            self.assertEqual(frozen["digest"], strategy_context.sha256_digest(data))
            self.assertNotIn(b", ", data)  # compact separators only
            self.assertNotIn(b": ", data)
            self.assertNotIn(b"\n", data)
            # JSON numbers in the snapshot are integers only
            for token in json.dumps(json.loads(data)).split('"'):
                if token.replace(".", "", 1).isdigit():
                    self.assertNotIn(".", token)


class TestFreezeCrashWindowContract(unittest.TestCase):
    """freeze_snapshot never overwrites an existing snapshot with different
    bytes: a crash between the atomic write and the frozen event re-derives
    the SAME ordinal on restart, so an existing path is reused
    byte-identically (the caller re-appends the missing frozen event) or the
    freeze fails closed — a frozen snapshot is never replaced."""

    def test_existing_identical_bytes_are_reused_without_replacement(self):
        context = _context()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy-context-000.json"
            first = strategy_context.freeze_snapshot(context, path)
            data = path.read_bytes()
            second = strategy_context.freeze_snapshot(context, path)
            self.assertEqual(second["digest"], first["digest"])
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), [path.name])

    def test_existing_different_bytes_fail_closed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy-context-000.json"
            strategy_context.freeze_snapshot(_context(), path)
            data = path.read_bytes()
            different = _context(
                discoveries=[
                    _discovery(text="A different semantic context value"),
                    _discovery(
                        explorer_id="explorer-02",
                        discovery_ordinal=1,
                        text="Second record",
                    ),
                ]
            )
            with self.assertRaises(strategy_context.ContextError):
                strategy_context.freeze_snapshot(different, path)
            self.assertEqual(path.read_bytes(), data)


class TestIdentityIsPermutationInvariant(unittest.TestCase):
    """Merged identity depends on declared content, never on arrival order."""

    def test_reordered_discoveries_yield_identical_bytes_and_digest(self):
        first = _discovery(source_attempt_id="r3-agent-01-seed-18-attempt-01")
        second = _discovery(
            source_attempt_id="r3-agent-02-seed-24-attempt-01",
            explorer_id="explorer-01",
            producing_agent_id="agent-02",
            producing_seed="24",
            text="Ramp stalls the walker",
        )
        forward = strategy_context.normalize_context(
            _context(discoveries=[first, second])
        )
        backward = strategy_context.normalize_context(
            _context(discoveries=[second, first])
        )
        assert forward is not None and backward is not None  # static narrowing
        self.assertEqual(
            strategy_context.canonical_json_bytes(forward),
            strategy_context.canonical_json_bytes(backward),
        )
        self.assertEqual(
            strategy_context.digest_json(forward),
            strategy_context.digest_json(backward),
        )
        self.assertEqual(
            [d["source_attempt_id"] for d in forward["discoveries"]],
            [
                "r3-agent-01-seed-18-attempt-01",
                "r3-agent-02-seed-24-attempt-01",
            ],
        )

    def test_semantic_change_moves_identity_but_equal_content_does_not(self):
        first = strategy_context.normalize_context(_context())
        changed = strategy_context.normalize_context(
            _context(discoveries=[_discovery(text="a different finding")])
        )
        assert first is not None and changed is not None  # static narrowing
        self.assertNotEqual(
            strategy_context.digest_json(first), strategy_context.digest_json(changed)
        )
        # snapshot ordinals are bookkeeping: they never enter the context, so
        # the same content at any ordinal carries one digest
        self.assertEqual(
            strategy_context.digest_json(first),
            strategy_context.digest_json(
                strategy_context.normalize_context(_context())
            ),
        )


if __name__ == "__main__":
    unittest.main()
