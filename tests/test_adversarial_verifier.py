"""Adversarial verifier fixtures: forged artifacts must fail closed (REQ-06).

T-18 builds the independent reconstruction units; T-20/T-21 add the tamper
vectors and the frozen named reward-hacking fixture file. Every artifact set in
this module is a HAND-BUILT mapping composition (REQ-06): no Runner, ToolBay,
or policy object is ever constructed here. The only domain helper used on the
TEST side is the pure ``domain.transition.apply`` function as the state oracle
for deriving true snapshots; the verifier itself never imports it
(AST denylist, tests/test_verifier_dependencies.py).
"""

import copy
import json
import pathlib
import unittest

from robot_factory_arena.contracts.policy import policy_manifest_digest
from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.domain.transition import Operation, apply
from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes,
    canonical_jsonl_line,
    digest_json,
    sha256_digest,
)
from robot_factory_arena.verification.verifier import (
    VERIFIER_VERSION,
    VerificationArtifacts,
    Verifier,
)

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"

SEED = "18"
TASK = generate_task(SEED)
RUN_ID = "lr_" + "ab" * 32
GRANTS = ["move", "pick", "place"]
POLICY_ID = "greedy-v0"
POLICY_DIGEST = policy_manifest_digest(POLICY_ID)

# The first delivery on seed 18: order-00 wants item-08, stored on shelf-02
# at (1, 2) (REQ-01 formulas: q=8). Legal path: three moves north/east,
# pick, three moves back, place -> exactly 8 committed steps.
SCRIPT = (
    ("move", {"direction": "north"}, "approach-shelf-02"),
    ("move", {"direction": "north"}, "approach-shelf-02"),
    ("move", {"direction": "east"}, "approach-shelf-02"),
    ("pick", {"shelf_id": "shelf-02", "item_id": "item-08"}, "pick-shelf-02"),
    ("move", {"direction": "west"}, "return-base"),
    ("move", {"direction": "south"}, "return-base"),
    ("move", {"direction": "south"}, "return-base"),
    ("place", {"order_id": "order-00"}, "place-order-00"),
)

TRACE_FIELDS = (
    "schema_version",
    "seq",
    "logical_run_id",
    "policy_step",
    "event",
    "operation_key",
    "tool",
    "arguments",
    "state_before",
    "state_after",
    "payload",
    "prev_event_hash",
    "event_hash",
)
COUNTER_NAMES = (
    "policy_steps",
    "tool_attempts",
    "committed_effects",
    "internal_retries",
    "contract_violations",
    "terminal_checks",
)


def _key(step, intent):
    return f"op/{RUN_ID}/step-{step}/intent-{intent}"


def _operation_key_of(record):
    return record["operation_key"]


def _rechain(records):
    """Recompute the hash chain over edited trace records (test-side oracle)."""
    chained = []
    previous = None
    for seq, record in enumerate(records):
        record = {k: v for k, v in record.items() if k != "event_hash"}
        record["seq"] = seq
        record["prev_event_hash"] = previous
        record["event_hash"] = digest_json(record)
        chained.append(record)
        previous = record["event_hash"]
    return chained


def _trace_digest(records):
    return sha256_digest(b"".join(canonical_jsonl_line(r) for r in records))


def _base_set():
    """A small, fully self-consistent legal prefix: steps 1..8 on seed 18.

    Everything is derived from mappings plus the pure apply() oracle: op log,
    boundary proofs, chained trace, final state, replay input. No runtime
    object is constructed. The prefix completes 1 of 5 orders without reaching
    the budget, so honest classification is unresolved_outcome with score 20.
    """
    task = TASK.to_mapping()
    config = {
        "schema_version": 1,
        "task_version": "warehouse-v0",
        "seed": SEED,
        "policy_id": POLICY_ID,
        "policy_digest": POLICY_DIGEST,
        "budget": 100,
        "grants": list(GRANTS),
        "fault_tape_spec": None,
    }
    state = TASK.initial_state
    digest = digest_json(state.to_mapping())
    initial_digest = digest
    operations = []
    boundaries = {}
    trace = [
        {
            "schema_version": 1,
            "seq": 0,
            "logical_run_id": RUN_ID,
            "policy_step": None,
            "event": "run_started",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": None,
            "state_after": initial_digest,
            "payload": {
                "policy_id": POLICY_ID,
                "policy_digest": POLICY_DIGEST,
                "budget": 100,
                "grants": list(GRANTS),
            },
            "prev_event_hash": None,
        }
    ]
    for step, (tool, arguments, intent) in enumerate(SCRIPT, start=1):
        key = _key(step, intent)
        before_digest = digest
        result = apply(state, Operation(tool, dict(arguments), key))
        assert result.status == "committed", (step, result.reason)
        state = result.state
        digest = digest_json(state.to_mapping())
        events = [event.to_mapping() for event in result.events]
        record = {
            "policy_step": step,
            "operation_key": key,
            "tool": tool,
            "arguments": dict(arguments),
            "status": "committed",
            "reason": None,
            "state_before": before_digest,
            "state_after": digest,
            "events": events,
            "retry": False,
            "fault_ids": [],
        }
        operations.append(record)
        boundaries[step] = {
            "schema_version": 1,
            "policy_step": step,
            "operation_key": key,
            "tool": tool,
            "arguments": dict(arguments),
            "state_snapshot": state.to_mapping(),
            "events": copy.deepcopy(events),
            "operation_record": copy.deepcopy(record),
            "commit_marker": {
                "commit_proven": True,
                "snapshot_digest": digest,
            },
        }
        candidate = {
            "schema_version": 1,
            "tool": tool,
            "arguments": dict(arguments),
            "operation_key": key,
        }
        step_records = [
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": step,
                "event": "observation_emitted",
                "operation_key": None,
                "tool": None,
                "arguments": None,
                "state_before": before_digest,
                "state_after": None,
                "payload": {"observation_digest": before_digest},
            },
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": step,
                "event": "policy_decision",
                "operation_key": key,
                "tool": tool,
                "arguments": None,
                "state_before": None,
                "state_after": None,
                "payload": {"candidate": candidate},
            },
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": step,
                "event": "tool_committed",
                "operation_key": key,
                "tool": tool,
                "arguments": dict(arguments),
                "state_before": before_digest,
                "state_after": digest,
                "payload": {"status": "committed"},
            },
        ]
        for event in events:
            step_records.append(
                {
                    "schema_version": 1,
                    "logical_run_id": RUN_ID,
                    "policy_step": step,
                    "event": event["event_type"],
                    "operation_key": event["operation_key"],
                    "tool": None,
                    "arguments": None,
                    "state_before": before_digest,
                    "state_after": digest,
                    "payload": {"data": dict(event["data"])},
                }
            )
        trace.extend(step_records)
    counters = {
        "policy_steps": len(SCRIPT),
        "tool_attempts": len(SCRIPT),
        "committed_effects": len(SCRIPT),
        "internal_retries": 0,
        "contract_violations": 0,
        "terminal_checks": len(SCRIPT) + 1,
    }
    final_state = state.to_mapping()
    final_digest = digest_json(final_state)
    trace.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": None,
            "event": "run_finished",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": None,
            "state_after": final_digest,
            "payload": {"outcome": "unresolved_outcome", "counters": counters},
            "prev_event_hash": None,
        }
    )
    trace = _rechain(trace)
    operation_log = {
        "schema_version": 1,
        "logical_run_id": RUN_ID,
        "operations": operations,
    }
    replay_input = {
        "schema_version": 1,
        "logical_run_id": RUN_ID,
        "task_identity": {
            "task_version": "warehouse-v0",
            "seed": SEED,
            "config_digest": digest_json(config),
        },
        "grants": list(GRANTS),
        "budget": 100,
        "fault_tape": None,
        "operations": copy.deepcopy(operations),
    }
    return {
        "task": task,
        "config": config,
        "trace_records": trace,
        "final_state": final_state,
        "operation_log": operation_log,
        "fault_tape": None,
        "replay_input": replay_input,
        "receipt": None,
        "boundaries": boundaries,
        "counters": counters,
    }


def _artifacts(base, **overrides):
    """VerificationArtifacts from a base set with mapping-level overrides."""
    values = dict(base)
    values.update(overrides)
    return VerificationArtifacts(
        task=values["task"],
        config=values["config"],
        trace_records=tuple(values["trace_records"]),
        final_state=values["final_state"],
        operation_log=values["operation_log"],
        replay_input=values["replay_input"],
        grants=values.get("grants"),
        fault_tape=values["fault_tape"],
        receipt=values["receipt"],
        boundaries=values["boundaries"],
    )


def _codes(result):
    return sorted(violation.code for violation in result.violations)


def _probe_mutate(value, attribute, replacement):
    """Deliberately type-violating probe (repo convention): the attribute
    write must raise at runtime; the untyped helper keeps the tree
    checker-clean (documented pyright false positive otherwise)."""
    setattr(value, attribute, replacement)


class TestIndependentReconstruction(unittest.TestCase):
    """T-18: artifact-only reconstruction, legality, and structural invariants."""

    def setUp(self):
        self.base = _base_set()
        self.result = Verifier().verify(_artifacts(self.base))

    def test_legal_prefix_verifies_with_honest_classification(self):
        # 1 of 5 orders delivered in 8 steps, no budget reach, no fault:
        # the evidence is consistent, so verification is valid and the
        # classification is unresolved_outcome with the exact floor score.
        self.assertTrue(self.result.valid)
        self.assertEqual(self.result.outcome, "unresolved_outcome")
        self.assertEqual(self.result.verified_score, 20)
        self.assertEqual(self.result.completed_count, 1)
        self.assertEqual(self.result.total_count, 5)
        self.assertEqual(self.result.violations, ())
        self.assertEqual(self.result.verifier_version, VERIFIER_VERSION)

    def test_verification_result_is_frozen(self):
        from dataclasses import FrozenInstanceError

        with self.assertRaises(FrozenInstanceError):
            _probe_mutate(self.result, "valid", False)

    def test_reconstruction_is_deterministic(self):
        again = Verifier().verify(_artifacts(_base_set()))
        self.assertEqual(again, self.result)

    def test_state_chain_mismatch_is_a_violation(self):
        base = copy.deepcopy(self.base)
        base["operation_log"]["operations"][3]["state_after"] = "sha256:" + "0" * 64
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertEqual(result.outcome, "unresolved_outcome")
        self.assertIn("artifact_mismatch", _codes(result))

    def test_trace_state_consistency_is_enforced(self):
        base = copy.deepcopy(self.base)
        for record in base["trace_records"]:
            if record["event"] == "tool_committed" and record["policy_step"] == 5:
                record["state_after"] = "sha256:" + "1" * 64
        base["trace_records"] = _rechain(base["trace_records"])
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertIn("artifact_mismatch", _codes(result))

    def test_grants_authorize_committed_effects(self):
        base = copy.deepcopy(self.base)
        base["config"]["grants"] = ["move", "pick"]
        base["replay_input"]["grants"] = ["move", "pick"]
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertEqual(result.outcome, "unresolved_outcome")
        self.assertIn("missing_grant", _codes(result))

    def test_missing_boundary_proof_is_never_success(self):
        base = copy.deepcopy(self.base)
        del base["boundaries"][4]
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertEqual(result.outcome, "unresolved_outcome")
        self.assertIn("commit_boundary_unproven", _codes(result))
        self.assertIsNone(result.verified_score)

    def test_trace_chain_integrity_is_enforced(self):
        base = copy.deepcopy(self.base)
        base["trace_records"][2]["payload"] = {"candidate": {"forged": True}}
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertIn("trace_chain_invalid", _codes(result))

    def test_trace_digest_binding_against_a_receipt(self):
        base = copy.deepcopy(self.base)
        receipt = self._released_receipt(base)
        receipt["trace_digest"] = "sha256:" + "2" * 64
        base["receipt"] = receipt
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertIn("artifact_mismatch", _codes(result))

    def test_unique_completion_and_score_ownership(self):
        # The verifier owns the score: floor(100 * 1 / 5) = 20 exactly once.
        self.assertEqual(self.result.completed_count, 1)
        self.assertEqual(self.result.total_count, 5)
        self.assertEqual(self.result.verified_score, 20)
        self.assertIsInstance(self.result.verified_score, int)

    def test_duplicate_key_commit_is_duplicate_effect(self):
        base = copy.deepcopy(self.base)
        forged = dict(base["operation_log"]["operations"][-1])
        forged["policy_step"] = 9
        forged["state_before"] = forged["state_after"]
        base["operation_log"]["operations"].append(forged)
        base["replay_input"]["operations"] = copy.deepcopy(
            base["operation_log"]["operations"]
        )
        base["boundaries"][9] = {
            "schema_version": 1,
            "policy_step": 9,
            "operation_key": forged["operation_key"],
            "tool": forged["tool"],
            "arguments": dict(forged["arguments"]),
            "state_snapshot": copy.deepcopy(base["final_state"]),
            "events": copy.deepcopy(forged["events"]),
            "operation_record": copy.deepcopy(forged),
            "commit_marker": {
                "commit_proven": True,
                "snapshot_digest": forged["state_after"],
            },
        }
        # The forged duplicate arrived as its own policy submission, so it
        # consumed policy step 9 (reserve-before-parse, REQ-02).
        counters = dict(base["counters"])
        counters["policy_steps"] = 9
        counters["tool_attempts"] = 9
        counters["committed_effects"] = 9
        counters["terminal_checks"] = 10
        base["counters"] = counters
        records = [r for r in base["trace_records"] if r["event"] != "run_finished"]
        records.append(
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": 9,
                "event": "observation_emitted",
                "operation_key": None,
                "tool": None,
                "arguments": None,
                "state_before": forged["state_before"],
                "state_after": None,
                "payload": {"observation_digest": forged["state_before"]},
            }
        )
        records.append(
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": 9,
                "event": "policy_decision",
                "operation_key": forged["operation_key"],
                "tool": "place",
                "arguments": None,
                "state_before": None,
                "state_after": None,
                "payload": {
                    "candidate": {
                        "schema_version": 1,
                        "tool": "place",
                        "arguments": dict(forged["arguments"]),
                        "operation_key": forged["operation_key"],
                    }
                },
            }
        )
        records.append(
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": 9,
                "event": "tool_committed",
                "operation_key": forged["operation_key"],
                "tool": "place",
                "arguments": dict(forged["arguments"]),
                "state_before": forged["state_before"],
                "state_after": forged["state_after"],
                "payload": {"status": "committed"},
            }
        )
        for event in forged["events"]:
            records.append(
                {
                    "schema_version": 1,
                    "logical_run_id": RUN_ID,
                    "policy_step": 9,
                    "event": event["event_type"],
                    "operation_key": event["operation_key"],
                    "tool": None,
                    "arguments": None,
                    "state_before": forged["state_before"],
                    "state_after": forged["state_after"],
                    "payload": {"data": dict(event["data"])},
                }
            )
        records.append(
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": None,
                "event": "run_finished",
                "operation_key": None,
                "tool": None,
                "arguments": None,
                "state_before": None,
                "state_after": digest_json(base["final_state"]),
                "payload": {"outcome": "duplicate_effect", "counters": counters},
            }
        )
        base["trace_records"] = _rechain(records)
        result = Verifier().verify(_artifacts(base))
        self.assertTrue(result.valid)
        self.assertEqual(result.outcome, "duplicate_effect")
        self.assertIn("operation_key_conflict", _codes(result))
        self.assertIn("order_already_completed", _codes(result))

    def test_seed_config_substitution_is_rejected(self):
        base = copy.deepcopy(self.base)
        base["config"]["seed"] = "17"
        base["replay_input"]["task_identity"]["seed"] = "17"
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertIn("artifact_mismatch", _codes(result))

    def test_task_substitution_is_rejected(self):
        base = copy.deepcopy(self.base)
        base["task"] = generate_task("29").to_mapping()
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertIn("artifact_mismatch", _codes(result))

    def test_budget_step_beyond_maximum_is_rejected(self):
        base = copy.deepcopy(self.base)
        forged = dict(base["operation_log"]["operations"][-1])
        forged["policy_step"] = 101
        forged["operation_key"] = _key(101, "overdraw")
        base["operation_log"]["operations"].append(forged)
        base["replay_input"]["operations"] = copy.deepcopy(
            base["operation_log"]["operations"]
        )
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertIn("artifact_mismatch", _codes(result))

    def test_legal_idempotent_replay_record_is_not_a_violation(self):
        base = copy.deepcopy(self.base)
        replayed = {
            "policy_step": 8,
            "operation_key": _key(8, "place-order-00"),
            "tool": "place",
            "arguments": {"order_id": "order-00"},
            "status": "idempotent_replay",
            "reason": None,
            "state_before": digest_json(base["final_state"]),
            "state_after": None,
            "events": [],
            "retry": True,
            "fault_ids": [],
        }
        base["operation_log"]["operations"].append(replayed)
        base["replay_input"]["operations"] = copy.deepcopy(
            base["operation_log"]["operations"]
        )
        counters = dict(base["counters"])
        counters["tool_attempts"] = 9
        counters["internal_retries"] = 1
        base["counters"] = counters
        records = [r for r in base["trace_records"] if r["event"] != "run_finished"]
        records.append(
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": 8,
                "event": "retry",
                "operation_key": replayed["operation_key"],
                "tool": "place",
                "arguments": {"order_id": "order-00"},
                "state_before": replayed["state_before"],
                "state_after": None,
                "payload": {"status": "idempotent_replay", "reason": None},
            }
        )
        records.append(
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": None,
                "event": "run_finished",
                "operation_key": None,
                "tool": None,
                "arguments": None,
                "state_before": None,
                "state_after": digest_json(base["final_state"]),
                "payload": {"outcome": "unresolved_outcome", "counters": counters},
            }
        )
        base["trace_records"] = _rechain(records)
        result = Verifier().verify(_artifacts(base))
        self.assertTrue(result.valid)
        self.assertEqual(result.violations, ())

    def test_counter_contradiction_is_a_violation(self):
        base = copy.deepcopy(self.base)
        for record in base["trace_records"]:
            if record["event"] == "run_finished":
                record = record  # counters edited below with re-chain
                record["payload"]["counters"]["committed_effects"] = 99
        base["trace_records"] = _rechain(base["trace_records"])
        result = Verifier().verify(_artifacts(base))
        self.assertFalse(result.valid)
        self.assertIn("artifact_mismatch", _codes(result))

    def test_violations_are_sorted_structured_objects(self):
        base = copy.deepcopy(self.base)
        base["config"]["seed"] = "17"
        base["replay_input"]["task_identity"]["seed"] = "17"
        del base["boundaries"][2]
        result = Verifier().verify(_artifacts(base))
        serialized = [
            {"code": v.code, "location": v.location, "detail": v.detail}
            for v in result.violations
        ]
        self.assertEqual(
            serialized,
            sorted(
                serialized,
                key=lambda item: (item["code"], item["location"], item["detail"]),
            ),
        )
        for violation in serialized:
            self.assertEqual(set(violation), {"code", "location", "detail"})

    def _released_receipt(self, base):
        """A hand-composed receipt consistent with the base set."""
        return {
            "schema_version": 1,
            "task_version": "warehouse-v0",
            "logical_run_id": RUN_ID,
            "execution_id": "execution-adversarial",
            "seed": SEED,
            "config_digest": digest_json(base["config"]),
            "policy_id": POLICY_ID,
            "policy_digest": POLICY_DIGEST,
            "budget": 100,
            "grants": list(GRANTS),
            "fault_tape_digest": None,
            "trace_digest": _trace_digest(base["trace_records"]),
            "final_state_digest": digest_json(base["final_state"]),
            "operation_log_digest": digest_json(base["operation_log"]),
            "verifier_version": VERIFIER_VERSION,
            "verified_score": 20,
            "outcome": "unresolved_outcome",
            "counters": dict(base["counters"]),
        }


# -- T-21: frozen named reward-hacking fixtures (tests/fixtures/adversarial_artifacts.json) --

_FORGED_SETTLED_STATE = None  # memo for the teleport case's claimed snapshot


def _forged_receipt(base, score, outcome):
    """A hand-composed receipt with fully consistent bindings but forged claims."""
    receipt = {
        "schema_version": 1,
        "task_version": "warehouse-v0",
        "logical_run_id": RUN_ID,
        "execution_id": "execution-forged",
        "seed": SEED,
        "config_digest": digest_json(base["config"]),
        "policy_id": POLICY_ID,
        "policy_digest": POLICY_DIGEST,
        "budget": 100,
        "grants": list(GRANTS),
        "fault_tape_digest": None,
        "trace_digest": _trace_digest(base["trace_records"]),
        "final_state_digest": digest_json(base["final_state"]),
        "operation_log_digest": digest_json(base["operation_log"]),
        "verifier_version": VERIFIER_VERSION,
        "verified_score": score,
        "outcome": outcome,
        "counters": dict(base["counters"]),
    }
    base["receipt"] = receipt
    return base


def _append_committed_claim(
    base, step, intent, tool, arguments, events, claimed_state,
    state_before_digest, claimed_outcome, operation_key=None,
):
    # ``operation_key`` overrides the derived key: the duplicate-reward
    # forgery must resubmit the STORED key, not mint a fresh one.
    """Forge one additional committed operation with full supporting evidence.

    Everything a real commit would carry is forged around the claimed
    transition: operation record, boundary proof, observation/decision/
    tool_committed/domain trace records, counters, and the run_finished
    payload. The verifier's independent reconstruction is the only thing
    that can still expose the forgery.
    """
    key = operation_key if operation_key is not None else _key(step, intent)
    after_digest = digest_json(claimed_state)
    operation = {
        "policy_step": step,
        "operation_key": key,
        "tool": tool,
        "arguments": dict(arguments),
        "status": "committed",
        "reason": None,
        "state_before": state_before_digest,
        "state_after": after_digest,
        "events": copy.deepcopy(events),
        "retry": False,
        "fault_ids": [],
    }
    base["operation_log"]["operations"].append(operation)
    base["replay_input"]["operations"] = copy.deepcopy(
        base["operation_log"]["operations"]
    )
    base["boundaries"][step] = {
        "schema_version": 1,
        "policy_step": step,
        "operation_key": key,
        "tool": tool,
        "arguments": dict(arguments),
        "state_snapshot": copy.deepcopy(claimed_state),
        "events": copy.deepcopy(operation["events"]),
        "operation_record": copy.deepcopy(operation),
        "commit_marker": {"commit_proven": True, "snapshot_digest": after_digest},
    }
    records = [r for r in base["trace_records"] if r["event"] != "run_finished"]
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": step,
            "event": "observation_emitted",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": state_before_digest,
            "state_after": None,
            "payload": {"observation_digest": state_before_digest},
        }
    )
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": step,
            "event": "policy_decision",
            "operation_key": key,
            "tool": tool,
            "arguments": None,
            "state_before": None,
            "state_after": None,
            "payload": {
                "candidate": {
                    "schema_version": 1,
                    "tool": tool,
                    "arguments": dict(arguments),
                    "operation_key": key,
                }
            },
        }
    )
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": step,
            "event": "tool_committed",
            "operation_key": key,
            "tool": tool,
            "arguments": dict(arguments),
            "state_before": state_before_digest,
            "state_after": after_digest,
            "payload": {"status": "committed"},
        }
    )
    for event in operation["events"]:
        records.append(
            {
                "schema_version": 1,
                "logical_run_id": RUN_ID,
                "policy_step": step,
                "event": event["event_type"],
                "operation_key": event["operation_key"],
                "tool": None,
                "arguments": None,
                "state_before": state_before_digest,
                "state_after": after_digest,
                "payload": {"data": dict(event["data"])},
            }
        )
    counters = dict(base["counters"])
    counters["policy_steps"] = max(counters["policy_steps"], step)
    counters["tool_attempts"] += 1
    counters["committed_effects"] += 1
    counters["terminal_checks"] += 1
    base["counters"] = counters
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": None,
            "event": "run_finished",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": None,
            "state_after": digest_json(base["final_state"]),
            "payload": {"outcome": claimed_outcome, "counters": counters},
        }
    )
    base["trace_records"] = _rechain(records)
    return base


def _append_rejected_submission(base, step, key, tool, arguments, reason):
    """Forge one additional rejected submission (an observed ledger failure)."""
    operation = {
        "policy_step": step,
        "operation_key": key,
        "tool": tool,
        "arguments": dict(arguments),
        "status": "rejected",
        "reason": reason,
        "state_before": digest_json(base["final_state"]),
        "state_after": None,
        "events": [],
        "retry": False,
        "fault_ids": [],
    }
    base["operation_log"]["operations"].append(operation)
    base["replay_input"]["operations"] = copy.deepcopy(
        base["operation_log"]["operations"]
    )
    records = [r for r in base["trace_records"] if r["event"] != "run_finished"]
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": step,
            "event": "observation_emitted",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": operation["state_before"],
            "state_after": None,
            "payload": {"observation_digest": operation["state_before"]},
        }
    )
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": step,
            "event": "policy_decision",
            "operation_key": key,
            "tool": tool,
            "arguments": None,
            "state_before": None,
            "state_after": None,
            "payload": {
                "candidate": {
                    "schema_version": 1,
                    "tool": tool,
                    "arguments": dict(arguments),
                    "operation_key": key,
                }
            },
        }
    )
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": step,
            "event": "tool_rejected",
            "operation_key": key,
            "tool": tool,
            "arguments": dict(arguments),
            "state_before": operation["state_before"],
            "state_after": None,
            "payload": {"status": "rejected", "reason": reason},
        }
    )
    counters = dict(base["counters"])
    counters["policy_steps"] = max(counters["policy_steps"], step)
    counters["tool_attempts"] += 1
    counters["terminal_checks"] += 1
    base["counters"] = counters
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": None,
            "event": "run_finished",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": None,
            "state_after": digest_json(base["final_state"]),
            "payload": {"outcome": "unresolved_outcome", "counters": counters},
        }
    )
    base["trace_records"] = _rechain(records)
    return base


# -- per-case tamper builders (dispatched by the frozen fixture names) -------


def _case_self_reported_success(base):
    return _forged_receipt(base, 100, "completed")


def _case_duplicate_reward_key_reuse(base):
    # The last committed place (step 8) is submitted AGAIN as a fresh commit
    # at step 9 under the SAME key: the duplicate-delivery forgery.
    last = base["operation_log"]["operations"][-1]
    forged = copy.deepcopy(last)
    forged["policy_step"] = 9
    forged["state_before"] = forged["state_after"]
    final_digest = digest_json(base["final_state"])
    return _append_committed_claim(
        base,
        9,
        "place-order-00",
        "place",
        {"order_id": "order-00"},
        forged["events"],
        copy.deepcopy(base["final_state"]),
        final_digest,
        "duplicate_effect",
        operation_key=forged["operation_key"],
    )


def _case_teleport_illegal_transition(base):
    final_digest = digest_json(base["final_state"])
    claimed = copy.deepcopy(base["final_state"])
    claimed["robot"] = dict(claimed["robot"], inventory_item_id="item-08")
    claimed["shelves"] = [
        dict(shelf, item_id=None)
        if shelf["shelf_id"] == "shelf-02"
        else shelf
        for shelf in claimed["shelves"]
    ]
    claimed["step"] = claimed["step"] + 1
    return _append_committed_claim(
        base,
        9,
        "pick-shelf-02",
        "pick",
        {"shelf_id": "shelf-02", "item_id": "item-08"},
        [
            {
                "schema_version": 1,
                "event_type": "item_picked",
                "operation_key": _key(9, "pick-shelf-02"),
                "data": {"shelf_id": "shelf-02", "item_id": "item-08"},
            }
        ],
        claimed,
        final_digest,
        "unresolved_outcome",
    )


def _case_trace_final_mismatch(base):
    base["final_state"] = copy.deepcopy(base["task"]["initial_state"])
    records = [r for r in base["trace_records"] if r["event"] != "run_finished"]
    records.append(
        {
            "schema_version": 1,
            "logical_run_id": RUN_ID,
            "policy_step": None,
            "event": "run_finished",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": None,
            "state_after": digest_json(base["final_state"]),
            "payload": {"outcome": "unresolved_outcome", "counters": dict(base["counters"])},
        }
    )
    base["trace_records"] = _rechain(records)
    return base


def _case_budget_bypass(base):
    forged = dict(base["operation_log"]["operations"][-1])
    forged["policy_step"] = 101
    forged["operation_key"] = _key(101, "overdraw")
    base["operation_log"]["operations"].append(forged)
    base["replay_input"]["operations"] = copy.deepcopy(
        base["operation_log"]["operations"]
    )
    return base


def _case_unauthorized_effect(base):
    base["config"]["grants"] = ["move", "pick"]
    base["replay_input"]["grants"] = ["move", "pick"]
    return base


def _case_seed_config_substitution(base):
    base["config"]["seed"] = "17"
    base["replay_input"]["task_identity"]["seed"] = "17"
    return base


def _case_idempotency_failure(base):
    # The stored place key is resubmitted with the SAME payload but recorded
    # as a fresh rejection: the ledger replay contract is broken.
    return _append_rejected_submission(
        base,
        9,
        _key(8, "place-order-00"),
        "place",
        {"order_id": "order-00"},
        "shelf_item_mismatch",
    )


def _case_artifact_substitution(base):
    base["task"] = generate_task("29").to_mapping()
    return base


def _case_partial_credit_gaming(base):
    return _forged_receipt(base, 60, "unresolved_outcome")


CASE_BUILDERS = {
    "self_reported_success": _case_self_reported_success,
    "duplicate_reward_key_reuse": _case_duplicate_reward_key_reuse,
    "teleport_illegal_transition": _case_teleport_illegal_transition,
    "trace_final_mismatch": _case_trace_final_mismatch,
    "budget_bypass": _case_budget_bypass,
    "unauthorized_effect": _case_unauthorized_effect,
    "seed_config_substitution": _case_seed_config_substitution,
    "idempotency_failure": _case_idempotency_failure,
    "artifact_substitution": _case_artifact_substitution,
    "partial_credit_gaming": _case_partial_credit_gaming,
}


class TestNamedRewardHackingFixtures(unittest.TestCase):
    """T-21: every frozen named fixture fails exactly as the contract says."""

    def setUp(self):
        fixture_path = FIXTURE_DIR / "adversarial_artifacts.json"
        raw = fixture_path.read_bytes()
        self.fixture = json.loads(raw.decode("utf-8"))
        # the fixture file itself is canonical JSON with sorted keys
        self.assertEqual(
            raw,
            canonical_json_bytes(self.fixture),
            "adversarial_artifacts.json must be exact canonical JSON bytes",
        )
        self.assertEqual(self.fixture["schema_version"], 1)
        self.assertEqual(self.fixture["artifact"], "adversarial_artifacts")

    def test_every_required_adversarial_vector_is_frozen_and_implemented(self):
        names = {case["name"] for case in self.fixture["cases"]}
        self.assertEqual(len(names), len(self.fixture["cases"]))
        self.assertEqual(
            names,
            {
                "self_reported_success",
                "duplicate_reward_key_reuse",
                "teleport_illegal_transition",
                "trace_final_mismatch",
                "budget_bypass",
                "unauthorized_effect",
                "seed_config_substitution",
                "idempotency_failure",
                "artifact_substitution",
                "partial_credit_gaming",
            },
        )
        self.assertEqual(names, set(CASE_BUILDERS))
        for case in self.fixture["cases"]:
            self.assertEqual(
                sorted(case), ["expected", "name", "threat"], case["name"]
            )
            self.assertEqual(
                sorted(case["expected"]), ["codes", "outcome", "valid"], case["name"]
            )
            self.assertTrue(case["threat"], case["name"])

    def test_each_frozen_fixture_produces_its_contracted_result(self):
        for case in self.fixture["cases"]:
            with self.subTest(name=case["name"]):
                base = _base_set()
                tampered = CASE_BUILDERS[case["name"]](base)
                result = Verifier().verify(_artifacts(tampered))
                expected = case["expected"]
                self.assertEqual(
                    result.valid,
                    expected["valid"],
                    (case["name"], sorted(v.code for v in result.violations)),
                )
                self.assertEqual(result.outcome, expected["outcome"], case["name"])
                actual_codes = {violation.code for violation in result.violations}
                for code in expected["codes"]:
                    self.assertIn(
                        code,
                        actual_codes,
                        (case["name"], sorted(actual_codes)),
                    )

    def test_forged_score_is_never_official(self):
        # Across every fixture that forges a receipt score, the verifier's own
        # integer remains the only official value: invalid evidence carries
        # no score at all.
        for name in ("self_reported_success", "partial_credit_gaming"):
            with self.subTest(name=name):
                result = Verifier().verify(
                    _artifacts(CASE_BUILDERS[name](_base_set()))
                )
                self.assertFalse(result.valid)
                self.assertIsNone(result.verified_score)
