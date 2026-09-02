"""Golden-trace tests: canonical byte/digest vectors now; trace, receipt, and
observation golden evidence accumulate here through T-03..T-06 and later
batches B4/B6 (REQ-03, REQ-07).
"""
import hashlib
import json
import pathlib
import unittest
from dataclasses import FrozenInstanceError

from robot_factory_arena.contracts.outcomes import BudgetView
from robot_factory_arena.domain.model import WorldState
from robot_factory_arena.domain.observation import Observation, serialize_observation
from robot_factory_arena.domain.task import generate_task, serialize_task
from robot_factory_arena.evidence.canonical import canonical_json_bytes, digest_json

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"

SCHEMA_FIXTURE_NAMES = (
    "task_definition_schema.json",
    "run_config_schema.json",
    "operation_log_schema.json",
    "replay_input_schema.json",
    "verifier_violation_schema.json",
)


def _fixture_state(name):
    """Load one hand-checked WorldState mapping from the transition fixture."""
    raw = (FIXTURE_DIR / "transition_states.json").read_text(encoding="utf-8")
    return json.loads(raw)["states"][name]


class TestSchemaFixturesAreCanonical(unittest.TestCase):
    """T-03: schema fixtures are reviewable, byte-canonical golden JSON."""

    def test_schema_fixture_files_are_canonical_json(self):
        for name in SCHEMA_FIXTURE_NAMES:
            with self.subTest(fixture=name):
                raw = (FIXTURE_DIR / name).read_bytes()
                parsed = json.loads(raw.decode("utf-8"))
                self.assertEqual(raw, canonical_json_bytes(parsed))

    def test_every_schema_fixture_declares_schema_version_one(self):
        for name in SCHEMA_FIXTURE_NAMES:
            with self.subTest(fixture=name):
                parsed = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
                self.assertEqual(parsed["schema_version"], 1)


class TestStateGoldenBytes(unittest.TestCase):
    """T-04: canonical golden bytes for the hand-checked start state."""

    def test_start_state_canonical_bytes(self):
        raw = (FIXTURE_DIR / "transition_states.json").read_text(encoding="utf-8")
        start = json.loads(raw)["states"]["start"]
        expected = (
            '{"completed_orders":[],"height":10,'
            '"orders":[{"item_id":"item-05","order_id":"order-00",'
            '"quantity":1},{"item_id":"item-08","order_id":"order-01",'
            '"quantity":1}],"robot":{"base_position":[0,0],'
            '"inventory_item_id":null,"position":[0,0]},'
            '"schema_version":1,"shelves":[{"item_id":"item-05",'
            '"position":[1,2],"shelf_id":"shelf-00"},{"item_id":"item-08",'
            '"position":[3,2],"shelf_id":"shelf-01"}],"step":0,"width":10}'
        )
        self.assertEqual(canonical_json_bytes(start), expected.encode("utf-8"))


class TestCanonicalGoldenVectors(unittest.TestCase):
    """T-02: fixed canonical byte and digest vectors shared across batches."""

    def test_golden_canonical_bytes_and_digest(self):
        value = {"policy_id": "greedy-v0", "schema_version": 1}
        expected_bytes = b'{"policy_id":"greedy-v0","schema_version":1}'
        self.assertEqual(canonical_json_bytes(value), expected_bytes)
        self.assertEqual(
            digest_json(value),
            "sha256:" + hashlib.sha256(expected_bytes).hexdigest(),
        )


class TestTaskGoldenBytes(unittest.TestCase):
    """T-05: seed 17 (public) and seed 29 (evaluation plumbing) golden bytes."""

    def test_seed_17_matches_golden_fixture_bytes(self):
        raw = (FIXTURE_DIR / "task-17.json").read_bytes()
        self.assertEqual(
            canonical_json_bytes(serialize_task(generate_task(17))), raw
        )

    def test_seed_29_matches_golden_fixture_bytes(self):
        raw = (FIXTURE_DIR / "task-29.json").read_bytes()
        self.assertEqual(
            canonical_json_bytes(serialize_task(generate_task(29))), raw
        )

    def test_regeneration_is_byte_identical_for_both_seeds(self):
        for seed in (17, 29):
            with self.subTest(seed=seed):
                first = canonical_json_bytes(serialize_task(generate_task(seed)))
                second = canonical_json_bytes(serialize_task(generate_task(seed)))
                self.assertEqual(first, second)

    def test_seed_29_is_evaluation_plumbing_only(self):
        # REQ-01/REQ-10 honesty: seed 29 exists as plumbing-only evaluation
        # evidence and must never tune policy behavior or support an
        # experimental claim. The locked schema has no label field, so the
        # labeling lives here and in the B6 report honesty boundary.
        self.assertEqual(generate_task(29).seed, "29")
        self.assertEqual(generate_task(17).seed, "17")


class TestObservationGoldenBytes(unittest.TestCase):
    """T-06: serialized observation wraps the canonical state with exact keys."""

    RUN_ID = "lr_" + "0123456789abcdef" * 4

    def test_start_observation_canonical_bytes(self):
        world = WorldState.from_mapping(_fixture_state("start"))
        observation = Observation(
            world_state=world,
            logical_run_id=self.RUN_ID,
            grants=("move", "pick", "place"),
            policy_steps=BudgetView(0, 100, 100),
            last_result=None,
        )
        expected = (
            '{"grants":["move","pick","place"],"last_result":null,'
            f'"logical_run_id":"{self.RUN_ID}",'
            '"policy_steps":{"maximum":100,"remaining":100,"used":0},'
            '"schema_version":1,"world_state":'
            + canonical_json_bytes(world.to_mapping()).decode("utf-8")
            + "}"
        )
        self.assertEqual(
            canonical_json_bytes(serialize_observation(observation)),
            expected.encode("utf-8"),
        )


# ---------------------------------------------------------------------------
# T-14: append-only hash-chained trace (REQ-03, REQ-05, REQ-07)
# ---------------------------------------------------------------------------

from robot_factory_arena.evidence.trace import (  # noqa: E402
    DOMAIN_TRACE_EVENTS,
    RUNTIME_TRACE_EVENTS,
    TRACE_RECORD_FIELDS,
    TraceEvent,
    TraceWriter,
)

TRACE_RUN_ID = "lr_" + "0123456789abcdef" * 4


def _run_started_event(state_digest):
    return TraceEvent(
        event="run_started",
        state_after=state_digest,
        payload={"budget": 100, "grants": ["move", "pick", "place"],
                 "policy_id": "scripted-v0"},
    )


def _committed_event(step, key, before, after):
    return TraceEvent(
        event="tool_committed",
        policy_step=step,
        operation_key=key,
        tool="place",
        arguments={"order_id": "order-00"},
        state_before=before,
        state_after=after,
        payload={"status": "committed"},
    )


_EMPTY = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_FAKE_A = "sha256:" + "a" * 64
_FAKE_B = "sha256:" + "b" * 64


class TestTraceRecordShape(unittest.TestCase):
    """T-14: exactly the REQ-03 trace record fields; no execution metadata."""

    def test_record_has_exactly_the_thirteen_req03_fields(self):
        # Binding spec REQ-03 enumerates exactly these 13 fields (tasks.md
        # prose says "14-field"; spec + design both list 13 — spec wins, and
        # this test freezes the exact field set so drift fails loudly).
        self.assertEqual(
            set(TRACE_RECORD_FIELDS),
            {
                "schema_version", "seq", "logical_run_id", "policy_step",
                "event", "operation_key", "tool", "arguments",
                "state_before", "state_after", "payload",
                "prev_event_hash", "event_hash",
            },
        )
        self.assertEqual(len(TRACE_RECORD_FIELDS), 13)

    def test_appended_record_carries_exact_fields_and_values(self):
        writer = TraceWriter(TRACE_RUN_ID)
        record = writer.append(_run_started_event(_FAKE_A))
        mapping = record.to_mapping()
        self.assertEqual(sorted(mapping), sorted(TRACE_RECORD_FIELDS))
        self.assertEqual(mapping["schema_version"], 1)
        self.assertEqual(mapping["seq"], 0)
        self.assertEqual(mapping["logical_run_id"], TRACE_RUN_ID)
        self.assertIsNone(mapping["policy_step"])
        self.assertEqual(mapping["event"], "run_started")
        self.assertIsNone(mapping["operation_key"])
        self.assertIsNone(mapping["tool"])
        self.assertIsNone(mapping["arguments"])
        self.assertIsNone(mapping["state_before"])
        self.assertEqual(mapping["state_after"], _FAKE_A)
        self.assertEqual(
            mapping["payload"],
            {"budget": 100, "grants": ["move", "pick", "place"],
             "policy_id": "scripted-v0"},
        )
        self.assertIsNone(mapping["prev_event_hash"])
        self.assertRegex(mapping["event_hash"], r"sha256:[0-9a-f]{64}")

    def test_arguments_are_recorded_exactly_as_received(self):
        writer = TraceWriter(TRACE_RUN_ID)
        writer.append(_run_started_event(_FAKE_A))
        record = writer.append(_committed_event(
            3, f"op/{TRACE_RUN_ID}/step-3/intent-place-order-00",
            _FAKE_A, _FAKE_B,
        ))
        # Exact normalized arguments, insertion order irrelevant (canonical
        # bytes sort keys): the record must carry the mapping verbatim.
        self.assertEqual(record.to_mapping()["arguments"],
                         {"order_id": "order-00"})


class TestTraceHashChain(unittest.TestCase):
    """T-14: seq/prev chain; event_hash over the record minus event_hash."""

    def test_first_prev_is_null_and_chain_links_hashes(self):
        writer = TraceWriter(TRACE_RUN_ID)
        first = writer.append(_run_started_event(_FAKE_A))
        second = writer.append(_committed_event(
            1, f"op/{TRACE_RUN_ID}/step-1/intent-place-order-00",
            _FAKE_A, _FAKE_B,
        ))
        third = writer.append(TraceEvent(event="run_finished",
                                         state_after=_FAKE_B,
                                         payload={"outcome": "completed"}))
        self.assertIsNone(first.to_mapping()["prev_event_hash"])
        self.assertEqual(
            second.to_mapping()["prev_event_hash"],
            first.to_mapping()["event_hash"],
        )
        self.assertEqual(
            third.to_mapping()["prev_event_hash"],
            second.to_mapping()["event_hash"],
        )
        self.assertEqual([r.seq for r in (first, second, third)], [0, 1, 2])

    def test_event_hash_is_digest_of_record_without_event_hash(self):
        writer = TraceWriter(TRACE_RUN_ID)
        writer.append(_run_started_event(_FAKE_A))
        record = writer.append(TraceEvent(event="observation_emitted",
                                          policy_step=1,
                                          state_before=_FAKE_A,
                                          payload={"observation_digest": _FAKE_A}))
        mapping = record.to_mapping()
        without_hash = {k: v for k, v in mapping.items() if k != "event_hash"}
        self.assertEqual(
            mapping["event_hash"], digest_json(without_hash)
        )

    def test_identical_semantics_identical_hash_and_equivalent_mapping_too(self):
        def build():
            writer = TraceWriter(TRACE_RUN_ID)
            writer.append(_run_started_event(_FAKE_A))
            return writer.append(TraceEvent(
                event="tool_rejected", policy_step=2,
                operation_key=f"op/{TRACE_RUN_ID}/step-2/intent-x",
                tool="move", arguments={"direction": "north"},
                state_before=_FAKE_A, payload={"reason": "not_at_base"},
            )).to_mapping()["event_hash"]

        # Same semantics -> same hash; argument key insertion order is
        # irrelevant because hashing runs over canonical bytes.
        reversed_args = {
            "event": "tool_rejected", "policy_step": 2,
            "operation_key": f"op/{TRACE_RUN_ID}/step-2/intent-x",
            "tool": "move", "arguments": {"direction": "north"},
            "state_before": _FAKE_A, "payload": {"reason": "not_at_base"},
        }
        writer = TraceWriter(TRACE_RUN_ID)
        writer.append(_run_started_event(_FAKE_A))
        record = writer.append(TraceEvent(**reversed_args))
        self.assertEqual(build(), record.to_mapping()["event_hash"])


class TestTraceClosureOrdering(unittest.TestCase):
    """T-14: locked decision 3 — run events -> run_finished -> close/digest."""

    def test_jsonl_bytes_are_lf_terminated_canonical_lines(self):
        writer = TraceWriter(TRACE_RUN_ID)
        writer.append(_run_started_event(_FAKE_A))
        writer.append(TraceEvent(event="run_finished", state_after=_FAKE_A,
                                 payload={"outcome": "completed"}))
        writer.close()
        raw = writer.jsonl_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        lines = raw.split(b"\n")[:-1]
        self.assertEqual(len(lines), 2)
        for line, record in zip(lines, writer.records):
            self.assertEqual(line, canonical_json_bytes(record.to_mapping()))

    def test_digest_is_sha256_of_all_jsonl_bytes_only_after_close(self):
        writer = TraceWriter(TRACE_RUN_ID)
        with self.assertRaises(RuntimeError):
            writer.digest()
        writer.append(_run_started_event(_FAKE_A))
        writer.append(TraceEvent(event="run_finished", state_after=_FAKE_A,
                                 payload={"outcome": "completed"}))
        writer.close()
        expected = "sha256:" + hashlib.sha256(writer.jsonl_bytes()).hexdigest()
        self.assertEqual(writer.digest(), expected)
        # Stable after close: repeated calls never recompute differently.
        self.assertEqual(writer.digest(), writer.digest())

    def test_close_requires_run_finished_as_the_final_record(self):
        writer = TraceWriter(TRACE_RUN_ID)
        with self.assertRaises(RuntimeError):
            writer.close()  # empty trace
        writer.append(_run_started_event(_FAKE_A))
        with self.assertRaises(RuntimeError):
            writer.close()  # missing run_finished
        writer.append(TraceEvent(event="run_finished", state_after=_FAKE_A,
                                 payload={"outcome": "completed"}))
        writer.close()

    def test_append_after_run_finished_or_close_is_rejected(self):
        writer = TraceWriter(TRACE_RUN_ID)
        writer.append(_run_started_event(_FAKE_A))
        writer.append(TraceEvent(event="run_finished", state_after=_FAKE_A,
                                 payload={"outcome": "completed"}))
        with self.assertRaises(RuntimeError):
            writer.append(TraceEvent(event="run_started"))
        writer.close()
        with self.assertRaises(RuntimeError):
            writer.append(TraceEvent(event="run_started"))

    def test_verification_finished_is_never_a_trace_record(self):
        # Locked decision 3: verification_finished is reserved for the receipt
        # verification block; the closed trace must never carry it.
        self.assertNotIn("verification_finished", RUNTIME_TRACE_EVENTS)
        self.assertNotIn("verification_finished", DOMAIN_TRACE_EVENTS)
        writer = TraceWriter(TRACE_RUN_ID)
        with self.assertRaises(ValueError):
            writer.append(TraceEvent(event="verification_finished"))

    def test_unknown_event_names_are_rejected(self):
        writer = TraceWriter(TRACE_RUN_ID)
        with self.assertRaises(ValueError):
            writer.append(TraceEvent(event="mystery_event"))

    def test_no_execution_id_may_enter_any_record(self):
        writer = TraceWriter(TRACE_RUN_ID)
        with self.assertRaises(ValueError):
            writer.append(TraceEvent(event="run_started",
                                     payload={"execution_id": "exec-1"}))
        with self.assertRaises(ValueError):
            writer.append(TraceEvent(
                event="run_started",
                payload={"nested": {"execution_id": "exec-1"}},
            ))

    def test_writer_validates_run_id_and_event_fields(self):
        with self.assertRaises(ValueError):
            TraceWriter("not-a-run-id")
        writer = TraceWriter(TRACE_RUN_ID)
        with self.assertRaises(ValueError):
            writer.append(TraceEvent(event="tool_committed", policy_step=0))
        with self.assertRaises(ValueError):
            writer.append(TraceEvent(event="tool_committed", tool="assemble"))
        with self.assertRaises(ValueError):
            writer.append(_run_started_event("sha256:zz"))


# ---------------------------------------------------------------------------
# T-15: receipt candidate and verified receipt serialization (REQ-03/06/09)
# ---------------------------------------------------------------------------

from robot_factory_arena.contracts.outcomes import (  # noqa: E402
    OUTCOMES,
    Counters,
    VerificationResult,
    VerifierViolation,
)
from robot_factory_arena.evidence.receipt import (  # noqa: E402
    RECEIPT_KEYS,
    VERIFICATION_BLOCK_KEYS,
    ReceiptCandidate,
    ReceiptNotReleasedError,
    ReceiptWriter,
    verification_finished_record,
)

GOOD_VERIFIER_VERSION = "verifier-v0"


def _stub_violation(code="artifact_mismatch", location="trace.jsonl",
                    detail="seed mismatch"):
    return VerifierViolation(code, location, detail)


def _valid_verification(**overrides):
    """Stub VerificationResult factory; explicit keys keep types honest."""
    return VerificationResult(
        valid=overrides.get("valid", True),
        outcome=overrides.get("outcome", "completed"),
        verified_score=overrides.get("verified_score", 40),
        completed_count=overrides.get("completed_count", 2),
        total_count=overrides.get("total_count", 5),
        violations=overrides.get("violations", ()),
        verifier_version=overrides.get("verifier_version", GOOD_VERIFIER_VERSION),
    )


def _candidate(**overrides):
    """ReceiptCandidate stub factory; explicit keys keep types honest."""
    return ReceiptCandidate(
        schema_version=overrides.get("schema_version", 1),
        task_version=overrides.get("task_version", "warehouse-v0"),
        logical_run_id=overrides.get("logical_run_id", TRACE_RUN_ID),
        execution_id=overrides.get("execution_id", "execution-t15"),
        seed=overrides.get("seed", "17"),
        config_digest=overrides.get("config_digest", "sha256:" + "c" * 64),
        policy_id=overrides.get("policy_id", "greedy-v0"),
        policy_digest=overrides.get("policy_digest", "sha256:" + "d" * 64),
        budget=overrides.get("budget", 100),
        grants=overrides.get("grants", ("move", "pick", "place")),
        fault_tape_digest=overrides.get("fault_tape_digest"),
        trace_digest=overrides.get("trace_digest", "sha256:" + "e" * 64),
        final_state_digest=overrides.get(
            "final_state_digest", "sha256:" + "1" * 64
        ),
        operation_log_digest=overrides.get(
            "operation_log_digest", "sha256:" + "2" * 64
        ),
        counters=overrides.get("counters", Counters(100, 190, 0, 0, 0, 101)),
        outcome=overrides.get("outcome", "safe_abort"),
    )


class TestReceiptShape(unittest.TestCase):
    """T-15: the receipt binds identity, digests, and verifier-owned fields."""

    def test_receipt_has_exactly_the_req03_keys_plus_verification_block(self):
        receipt = ReceiptWriter.write_final(_candidate(), _valid_verification())
        self.assertEqual(
            set(receipt),
            set(RECEIPT_KEYS),
        )
        self.assertEqual(
            sorted(RECEIPT_KEYS),
            [
                "budget", "config_digest", "counters", "execution_id",
                "fault_tape_digest", "final_state_digest", "grants",
                "logical_run_id", "operation_log_digest", "outcome",
                "policy_digest", "policy_id", "schema_version", "seed",
                "task_version", "trace_digest", "verification",
                "verified_score", "verifier_version",
            ],
        )

    def test_receipt_binds_identity_and_digest_formats(self):
        receipt = ReceiptWriter.write_final(_candidate(), _valid_verification())
        # Distinct logical vs physical identity (REQ-03).
        self.assertNotEqual(receipt["logical_run_id"], receipt["execution_id"])
        self.assertEqual(receipt["logical_run_id"], TRACE_RUN_ID)
        self.assertEqual(receipt["execution_id"], "execution-t15")
        self.assertEqual(receipt["task_version"], "warehouse-v0")
        self.assertEqual(receipt["seed"], "17")
        for name in ("config_digest", "policy_digest", "trace_digest",
                     "final_state_digest", "operation_log_digest"):
            self.assertRegex(receipt[name], r"^sha256:[0-9a-f]{64}$")
        self.assertIsNone(receipt["fault_tape_digest"])
        self.assertEqual(receipt["budget"], 100)
        self.assertEqual(receipt["grants"], ["move", "pick", "place"])
        self.assertEqual(
            receipt["counters"],
            {"policy_steps": 100, "tool_attempts": 190, "committed_effects": 0,
             "internal_retries": 0, "contract_violations": 0,
             "terminal_checks": 101},
        )

    def test_outcome_enum_is_exactly_the_four_supported_strings(self):
        self.assertEqual(
            OUTCOMES,
            ("completed", "duplicate_effect", "safe_abort",
             "unresolved_outcome"),
        )
        for outcome in OUTCOMES:
            verification = _valid_verification(outcome=outcome)
            receipt = ReceiptWriter.write_final(_candidate(), verification)
            self.assertEqual(receipt["outcome"], outcome)

    def test_candidate_carries_no_runtime_score_claim(self):
        # verified_score is verifier-owned: the candidate has no score field
        # at all, so no runtime/policy claim can enter the receipt.
        self.assertNotIn("verified_score", _candidate().to_mapping())

    def test_candidate_outcome_is_a_runtime_claim_not_the_receipt_outcome(self):
        # The verifier reclassifies: the receipt releases ITS outcome.
        verification = _valid_verification(outcome="safe_abort")
        receipt = ReceiptWriter.write_final(
            _candidate(outcome="completed"), verification
        )
        self.assertEqual(receipt["outcome"], "safe_abort")


class TestReceiptVerificationBlock(unittest.TestCase):
    """T-15: the exact verification block; verification_finished stays out."""

    def test_block_has_exactly_the_t15_keys(self):
        receipt = ReceiptWriter.write_final(_candidate(), _valid_verification())
        block = receipt["verification"]
        self.assertEqual(set(block), set(VERIFICATION_BLOCK_KEYS))
        self.assertEqual(block["status"], "valid")
        self.assertEqual(block["verifier_version"], GOOD_VERIFIER_VERSION)
        self.assertEqual(block["verified_score"], 40)
        self.assertEqual(block["completed_count"], 2)
        self.assertEqual(block["total_count"], 5)
        self.assertEqual(block["violations"], [])

    def test_block_violations_are_sorted_structured_objects(self):
        violation = _stub_violation()
        receipt = ReceiptWriter.write_final(
            _candidate(),
            _valid_verification(violations=(violation,)),
        )
        self.assertEqual(
            receipt["verification"]["violations"],
            [{"code": "artifact_mismatch", "location": "trace.jsonl",
              "detail": "seed mismatch"}],
        )

    def test_verification_finished_auxiliary_record_is_post_verification(self):
        # Locked decision 3: the auxiliary record lives in the receipt block,
        # never in the closed trace.
        record = verification_finished_record(_valid_verification())
        self.assertEqual(
            record,
            {
                "event": "verification_finished",
                "schema_version": 1,
                "payload": {
                    "outcome": "completed",
                    "verified_score": 40,
                    "verifier_version": GOOD_VERIFIER_VERSION,
                    "violations_count": 0,
                },
            },
        )
        receipt = ReceiptWriter.write_final(_candidate(), _valid_verification())
        self.assertEqual(
            receipt["verification"]["verification_finished"], record
        )

    def test_trace_never_receives_verification_finished_after_binding(self):
        writer = TraceWriter(TRACE_RUN_ID)
        writer.append(_run_started_event(_FAKE_A))
        writer.append(TraceEvent(event="run_finished", state_after=_FAKE_A,
                                 payload={"outcome": "completed"}))
        writer.close()
        bound_digest = writer.digest()
        candidate = _candidate(trace_digest=bound_digest)
        receipt = ReceiptWriter.write_final(candidate, _valid_verification())
        self.assertEqual(receipt["trace_digest"], bound_digest)
        # The bound trace stays sealed: no post-verification appends.
        with self.assertRaises(RuntimeError):
            writer.append(TraceEvent(event="run_started"))


class TestReceiptReleaseGate(unittest.TestCase):
    """T-15: no verified score and no receipt without a VerificationResult."""

    def test_write_final_requires_a_verification_result(self):
        with self.assertRaises(TypeError):
            ReceiptWriter.write_final(_candidate(), None)
        with self.assertRaises(TypeError):
            ReceiptWriter.write_final(_candidate(), {"valid": True})
        with self.assertRaises(TypeError):
            ReceiptWriter.write_final({"logical_run_id": "x"},
                                      _valid_verification())

    def test_invalid_verification_releases_no_receipt(self):
        # Tampered artifacts never become a released receipt (REQ-06).
        with self.assertRaises(ReceiptNotReleasedError):
            ReceiptWriter.write_final(
                _candidate(),
                _valid_verification(valid=False, verified_score=None),
            )

    def test_valid_verification_without_score_cannot_release(self):
        with self.assertRaises(ValueError):
            ReceiptWriter.write_final(
                _candidate(), _valid_verification(verified_score=None)
            )
        # A float score never reaches the writer: the VerificationResult
        # contract rejects non-int scores outright (defense in depth).
        with self.assertRaises(TypeError):
            _valid_verification(verified_score=40.0)

    def test_candidate_rejects_drift(self):
        with self.assertRaises(ValueError):
            _candidate(logical_run_id="lr_short")
        with self.assertRaises(ValueError):
            _candidate(trace_digest="deadbeef")
        with self.assertRaises(ValueError):
            _candidate(budget=99)
        with self.assertRaises(ValueError):
            _candidate(grants=("place", "move"))
        with self.assertRaises(ValueError):
            _candidate(outcome="completed_but_more")
        with self.assertRaises(TypeError):
            _candidate(counters={"policy_steps": 1})
        with self.assertRaises(FrozenInstanceError):
            _candidate().execution_id = "other"


# ---------------------------------------------------------------------------
# T-16: physical artifact store and the operation-log boundary (REQ-03/05/07/09)
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from robot_factory_arena.contracts.policy import policy_manifest_digest  # noqa: E402
from robot_factory_arena.domain.model import (  # noqa: E402
    OrderState,
    RobotState,
    ShelfState,
)
from robot_factory_arena.domain.task import (  # noqa: E402
    SLOT_POSITIONS,
    TaskDefinition,
)
from robot_factory_arena.evidence.artifact_store import (  # noqa: E402
    REQUIRED_ARTIFACT_NAMES,
    RunEvidenceRecorder,
)
from robot_factory_arena.policies.greedy import GreedyPolicy  # noqa: E402
from robot_factory_arena.runtime.runner import (  # noqa: E402
    EvidenceCallbacks,
    RunConfig,
    Runner,
    compute_logical_run_id,
)

_GOLDEN_RUN_ID = "lr_" + "0123456789abcdef" * 4


def _carrying_task_for_golden():
    """One-order task with the robot already carrying (greedy places step 1)."""
    shelves = (ShelfState("shelf-00", (1, 2), None),)
    orders = (OrderState("order-00", "item-05", 1),)
    initial = WorldState(
        1, 10, 10, RobotState((0, 0), "item-05", (0, 0)), shelves, orders, (), 0
    )
    return TaskDefinition(
        "warehouse-v0", 1, "17", 10, 10, SLOT_POSITIONS, shelves, orders,
        initial,
        {"generator": "formula-v0", "generator_version": "1",
         "slot_layout": "p10-two-rows"},
    )


def _greedy_config(task):
    return RunConfig(
        1, task.task_version, task.seed, "greedy-v0",
        policy_manifest_digest("greedy-v0"), 100, ("move", "pick", "place"),
        None,
    )


def _recorded_run(root, execution_id="execution-golden"):
    task = _carrying_task_for_golden()
    config = _greedy_config(task)
    logical_run_id = compute_logical_run_id(task, config)
    recorder = RunEvidenceRecorder(root, task, config, logical_run_id,
                                   execution_id)
    evidence = EvidenceCallbacks(**recorder.callback_map())
    result = Runner(evidence=evidence).run(task, GreedyPolicy(), config,
                                           execution_id)
    recorder.close()
    return result, recorder


def _read_json(path):
    return _json.loads(path.read_text(encoding="utf-8"))


class TestRunDirectoryLayout(unittest.TestCase):
    """T-16: artifacts/runs/<logical_run_id>/<execution_id>/ completeness."""

    def test_required_artifacts_exist_at_the_locked_per_run_path(self):
        with tempfile.TemporaryDirectory() as root:
            result, recorder = _recorded_run(root)
            directory = (_Path(root) / "runs" / result.logical_run_id
                         / "execution-golden")
            self.assertTrue(directory.is_dir())
            for name in REQUIRED_ARTIFACT_NAMES:
                with self.subTest(artifact=name):
                    self.assertTrue((directory / name).is_file(), name)

    def test_every_json_artifact_is_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            for name in ("config.json", "task.json", "fault_tape.json",
                         "final_state.json", "operation-log.json",
                         "replay-input.json"):
                raw = (recorder.directory / name).read_bytes()
                self.assertEqual(raw, canonical_json_bytes(_read_json(
                    recorder.directory / name)), name)

    def test_trace_jsonl_is_canonical_lf_records_ending_run_finished(self):
        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            raw = (recorder.directory / "trace.jsonl").read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            lines = raw.split(b"\n")[:-1]
            records = [_json.loads(line) for line in lines]
            self.assertEqual(records[0]["event"], "run_started")
            self.assertEqual(records[-1]["event"], "run_finished")
            for line, record in zip(lines, records):
                self.assertEqual(line, canonical_json_bytes(record))
            # The closed-trace binding: recorded digest == final record set.
            self.assertEqual(recorder.receipt_candidate.trace_digest,
                             "sha256:" + __import__("hashlib")
                             .sha256(raw).hexdigest())

    def test_no_temp_files_survive_atomic_writes(self):
        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            leftovers = [p.name for p in recorder.directory.rglob("*.tmp")]
            self.assertEqual(leftovers, [])


class TestRecordedDigestBindings(unittest.TestCase):
    """T-16: receipt digests bind the persisted bytes, not runtime claims."""

    def test_candidate_digests_match_the_persisted_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            result, recorder = _recorded_run(root)
            candidate = recorder.receipt_candidate
            directory = recorder.directory
            self.assertEqual(
                candidate.config_digest,
                digest_json(_read_json(directory / "config.json")),
            )
            self.assertEqual(
                candidate.final_state_digest,
                digest_json(_read_json(directory / "final_state.json")),
            )
            self.assertEqual(
                candidate.operation_log_digest,
                digest_json(_read_json(directory / "operation-log.json")),
            )
            self.assertIsNone(candidate.fault_tape_digest)  # no tape declared
            self.assertEqual(candidate.outcome, result.outcome)
            self.assertEqual(candidate.counters, result.counters)
            self.assertNotEqual(candidate.logical_run_id,
                                candidate.execution_id)

    def test_operation_log_matches_the_locked_schema_exactly(self):
        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            log = _read_json(recorder.directory / "operation-log.json")
            self.assertEqual(sorted(log),
                             ["logical_run_id", "operations", "schema_version"])
            self.assertEqual(log["logical_run_id"],
                             recorder.receipt_candidate.logical_run_id)
            self.assertEqual(len(log["operations"]), 1)
            operation = log["operations"][0]
            self.assertEqual(
                sorted(operation),
                ["arguments", "events", "fault_ids", "operation_key",
                 "policy_step", "reason", "retry", "state_after",
                 "state_before", "status", "tool"],
            )
            self.assertEqual(operation["status"], "committed")
            self.assertFalse(operation["retry"])
            self.assertEqual(operation["fault_ids"], [])
            self.assertIsNone(operation["reason"])
            self.assertEqual(operation["tool"], "place")
            self.assertEqual(operation["arguments"], {"order_id": "order-00"})
            self.assertEqual(len(operation["events"]), 1)
            self.assertIsNotNone(operation["state_before"])
            self.assertIsNotNone(operation["state_after"])

    def test_replay_input_carries_identity_operations_and_null_tape(self):
        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            replay_input = _read_json(
                recorder.directory / "replay-input.json"
            )
            self.assertEqual(
                sorted(replay_input),
                ["budget", "fault_tape", "grants", "logical_run_id",
                 "operations", "schema_version", "task_identity"],
            )
            self.assertIsNone(replay_input["fault_tape"])
            self.assertEqual(replay_input["budget"], 100)
            self.assertEqual(replay_input["grants"],
                             ["move", "pick", "place"])
            self.assertEqual(
                sorted(replay_input["task_identity"]),
                ["config_digest", "seed", "task_version"],
            )
            self.assertEqual(
                replay_input["operations"],
                _read_json(recorder.directory / "operation-log.json")[
                    "operations"
                ],
            )


class TestArtifactPathIndependence(unittest.TestCase):
    """T-16/REQ-07/REQ-09: deterministic bytes never depend on path or id."""

    def test_identical_runs_in_different_roots_produce_identical_bytes(self):
        with tempfile.TemporaryDirectory() as root_a, \
                tempfile.TemporaryDirectory() as root_b:
            _, recorder_a = _recorded_run(root_a, "execution-one")
            _, recorder_b = _recorded_run(root_b, "execution-two")
            self.assertNotEqual(recorder_a.directory, recorder_b.directory)
            for name in REQUIRED_ARTIFACT_NAMES:
                with self.subTest(artifact=name):
                    self.assertEqual(
                        (recorder_a.directory / name).read_bytes(),
                        (recorder_b.directory / name).read_bytes(),
                    )
            cand_a = recorder_a.receipt_candidate.to_mapping()
            cand_b = recorder_b.receipt_candidate.to_mapping()
            self.assertNotEqual(cand_a["execution_id"],
                                cand_b["execution_id"])
            for key, value in cand_a.items():
                if key == "execution_id":
                    continue
                self.assertEqual(value, cand_b[key], key)

    def test_boundary_evidence_is_storage_only(self):
        # Commit-boundary files prove the simulated persistence boundary but
        # are path-addressed storage, excluded from semantic digests (design
        # §2): they are not declared artifacts and not digest inputs.
        self.assertFalse(
            any(name.startswith("boundaries")
                for name in REQUIRED_ARTIFACT_NAMES)
        )
        with tempfile.TemporaryDirectory() as root:
            _, recorder = _recorded_run(root)
            boundary = recorder.directory / "boundaries" / "step-1.json"
            self.assertTrue(boundary.is_file())
            record = _read_json(boundary)
            self.assertEqual(record["commit_marker"]["commit_proven"], True)
            self.assertEqual(record["policy_step"], 1)
            self.assertEqual(record["operation_record"]["status"],
                             "committed")
            self.assertEqual(len(record["events"]), 1)
            self.assertEqual(
                record["state_snapshot"],
                _read_json(recorder.directory / "final_state.json"),
            )
            self.assertTrue(record["operation_key"].endswith(
                "/intent-place-order-00"))


class TestStaticReportRendering(unittest.TestCase):
    """T-22: the dependency-free static report and honesty boundary (REQ-09,
    REQ-10). The report renders the VERIFICATION RESULT — never receipt score
    or outcome claims — as escaped static HTML with four SEPARATE signal
    sections, raw counters, all digests, and the exact limitations. No
    JavaScript, no CDN, no URLs, no composite score, no execution id (REQ-07:
    deterministic contents must not depend on the physical execution).
    """

    def _verification(self, **overrides):
        from robot_factory_arena.contracts.outcomes import VerificationResult

        return VerificationResult(
            valid=overrides.get("valid", True),
            outcome=overrides.get("outcome", "safe_abort"),
            verified_score=overrides.get("verified_score", 50),
            completed_count=overrides.get("completed_count", 5),
            total_count=overrides.get("total_count", 10),
            violations=overrides.get("violations", ()),
            verifier_version=overrides.get("verifier_version", "verifier-v0"),
        )

    def _receipt(self, verification, **overrides):
        """A realistic RELEASED receipt (T-15 writer) for the verification."""
        from robot_factory_arena.contracts.outcomes import Counters
        from robot_factory_arena.evidence.receipt import (
            ReceiptCandidate,
            ReceiptWriter,
        )

        candidate = ReceiptCandidate(
            schema_version=1,
            task_version="warehouse-v0",
            logical_run_id="lr_" + "ab" * 32,
            execution_id="golden-17",
            seed="17",
            config_digest="sha256:" + "11" * 32,
            policy_id="greedy-v0",
            policy_digest="sha256:" + "22" * 32,
            budget=100,
            grants=("move", "pick", "place"),
            fault_tape_digest=None,
            trace_digest="sha256:" + "33" * 32,
            final_state_digest="sha256:" + "44" * 32,
            operation_log_digest="sha256:" + "55" * 32,
            counters=Counters(100, 100, 92, 0, 0, 101),
            outcome="safe_abort",
        )
        if verification.valid:
            receipt = ReceiptWriter.write_final(candidate, verification)
        else:
            # Invalid verification releases NO receipt (REQ-06): only the
            # scoreless candidate mapping exists for the report to render.
            receipt = candidate.to_mapping()
        receipt.update(overrides)
        return receipt

    def _render(self, verification=None, receipt_overrides=None,
                artifact_digests=None):
        from robot_factory_arena.evidence.report import render_report

        verification = (
            self._verification() if verification is None else verification
        )
        receipt = self._receipt(verification, **(receipt_overrides or {}))
        digests = {
            "config.json": "sha256:" + "11" * 32,
            "trace.jsonl": "sha256:" + "33" * 32,
        }
        if artifact_digests is not None:
            digests = artifact_digests
        return render_report(receipt, verification, digests)

    def test_render_report_requires_a_verification_result(self):
        from robot_factory_arena.evidence.report import render_report

        def render_with_wrong_verification(value):
            # Negative probe: the verification argument is deliberately
            # wrong-typed; the runtime contract must fail closed.
            return render_report({}, value, {})

        with self.assertRaises(TypeError):
            render_with_wrong_verification({"status": "valid"})
        with self.assertRaises(TypeError):
            render_with_wrong_verification(None)

    def test_report_contains_the_four_separate_signal_sections(self):
        html = self._render()
        for marker in (
            "signal-task-quality",
            "signal-efficiency",
            "signal-robustness",
            "signal-contract-hygiene",
        ):
            self.assertIn(marker, html)

    def test_report_shows_raw_counters_completion_budget_and_outcome(self):
        html = self._render()
        for expected in (
            "policy_steps", "tool_attempts", "committed_effects",
            "internal_retries", "contract_violations", "terminal_checks",
        ):
            self.assertIn(expected, html)
        self.assertIn("100", html)  # budget and policy steps used
        self.assertIn("5 / 10", html)  # completed / total orders
        self.assertIn("safe_abort", html)  # outcome (from verification)

    def test_report_renders_the_verification_not_receipt_claims(self):
        # The receipt mapping deliberately carries DRIFTED claims; the report
        # must show the verifier's score/outcome only.
        html = self._render(receipt_overrides={"verified_score": 999,
                                               "outcome": "completed"})
        self.assertIn("safe_abort", html)
        self.assertNotIn("999", html)

    def test_report_shows_all_digests_and_verifier_status_version(self):
        digest = "sha256:" + "dd" * 32
        html = self._render(artifact_digests={"final_state.json": digest})
        self.assertIn("final_state.json", html)
        self.assertIn(digest, html)
        self.assertIn("verifier-v0", html)
        self.assertIn("valid", html)  # verifier status

    def test_report_shows_fault_details(self):
        html_faulted = self._render(
            receipt_overrides={"fault_tape_digest": "sha256:" + "66" * 32}
        )
        self.assertIn("response_lost_after_commit", html_faulted)
        self.assertIn("response_lost", html_faulted)
        self.assertIn("after_commit_before_response", html_faulted)
        html_plain = self._render()
        self.assertIn("none", html_plain)  # explicit no-fault presentation

    def test_report_shows_violations_and_audit_completeness(self):
        from robot_factory_arena.contracts.outcomes import VerifierViolation

        violation = VerifierViolation(
            code="score_mismatch", location="receipt",
            detail="receipt score disagrees with the recomputed score",
        )
        html = self._render(
            self._verification(
                valid=False,
                outcome="unresolved_outcome",
                verified_score=None,
                violations=(violation,),
            )
        )
        self.assertIn("score_mismatch", html)
        self.assertIn("receipt", html)
        self.assertIn("invalid", html)  # verifier status and audit wording

    def test_report_states_the_exact_limitations(self):
        html = self._render()
        for phrase in (
            "not authentication",
            "exactly-once",
            "hostile-Python",
            "plumbing-only",
            "REFUGIO",
            "never collapsed",
        ):
            self.assertIn(phrase, html)

    def test_report_is_static_html_without_js_cdn_or_urls(self):
        html = self._render()
        self.assertTrue(html.lstrip().startswith("<!DOCTYPE html>"))
        lowered = html.lower()
        for forbidden in ("<script", "javascript:", "cdn", "http://",
                          "https://"):
            self.assertNotIn(forbidden, lowered)

    def test_report_escapes_interpolated_values(self):
        hostile = "<script>alert(1)</script>"
        html = self._render(receipt_overrides={"policy_id": hostile})
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_report_is_deterministic_and_execution_id_free(self):
        first = self._render()
        second = self._render()
        self.assertEqual(first, second)
        # A receipt whose only difference is the physical execution id must
        # render the exact same bytes (REQ-07 path/execution independence).
        other = self._render(receipt_overrides={"execution_id": "other-exec"})
        self.assertEqual(first, other)
        self.assertNotIn("other-exec", other)
        self.assertNotIn("golden-17", first)

    def test_report_shows_exact_actions_per_delivery_ratio(self):
        html = self._render()  # 100 tool attempts / 92 committed effects
        self.assertIn("100/92", html)
        empty = self._render(
            self._verification(
                verified_score=0,
                completed_count=0,
            ),
            receipt_overrides={"counters": {
                "policy_steps": 100, "tool_attempts": 100,
                "committed_effects": 0, "internal_retries": 0,
                "contract_violations": 0, "terminal_checks": 101,
            }},
        )
        self.assertIn("n/a", empty)


# ---------------------------------------------------------------------------
# T-24: committed golden run evidence (REQ-01..REQ-09, AMEND-01)
# ---------------------------------------------------------------------------

def _golden_verified_run(seed, policy_id, execution_id, root,
                         fault_step=None, fault_intent=None):
    """The full B5-proven verified flow for one formula seed.

    run -> recorder -> close -> verify -> write_final -> persist receipt.json.
    Returns (recorder, receipt, verification) with receipt.json persisted in
    the run directory (canonical bytes). Deterministic bytes only: the trace
    and every semantic artifact are independent of path and execution id.
    """
    from robot_factory_arena.evidence.receipt import ReceiptWriter
    from robot_factory_arena.faults.model import (
        FAULT_ID_RESPONSE_LOST,
        HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
        KIND_RESPONSE_LOST,
    )
    from robot_factory_arena.faults.tape import FaultTapeSpec, FaultTarget
    from robot_factory_arena.policies.bounded import BoundedPolicy
    from robot_factory_arena.verification.verifier import (
        Verifier,
        load_verification_artifacts,
    )

    task = generate_task(seed)
    policy = GreedyPolicy() if policy_id == "greedy-v0" else BoundedPolicy()
    spec = None
    if fault_step is not None and fault_intent is not None:
        # Callers always declare the pair together; the explicit narrowing
        # keeps the frozen target typed str.
        spec = FaultTapeSpec(
            1, FAULT_ID_RESPONSE_LOST, HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST, FaultTarget(fault_step, fault_intent),
        )
    config = RunConfig(
        1, task.task_version, task.seed, policy_id,
        policy_manifest_digest(policy_id), 100, ("move", "pick", "place"),
        spec,
    )
    logical_run_id = compute_logical_run_id(task, config)
    recorder = RunEvidenceRecorder(
        root, task, config, logical_run_id, execution_id
    )
    evidence = EvidenceCallbacks(**recorder.callback_map())
    Runner(evidence=evidence).run(task, policy, config, execution_id)
    recorder.close()
    verification = Verifier().verify(
        load_verification_artifacts(recorder.directory)
    )
    receipt = ReceiptWriter.write_final(
        recorder.receipt_candidate, verification
    )
    (recorder.directory / "receipt.json").write_bytes(
        canonical_json_bytes(receipt)
    )
    return recorder, receipt, verification


def _parse_golden_trace(name):
    raw = (FIXTURE_DIR / name).read_bytes()
    records = []
    for line in raw.split(b"\n")[:-1]:
        records.append(json.loads(line.decode("utf-8")))
    return raw, records


class TestGoldenSeed17Evidence(unittest.TestCase):
    """T-24: committed public seed-17 golden evidence (greedy-v0, no tape).

    The full greedy run needs ~190 policy steps for 10 orders, so the golden
    public run ends ``safe_abort`` at the locked 100-step budget with 5/10
    orders delivered and the verifier-owned partial score 50.
    """

    def test_golden_trace_fixture_is_canonical_lf_jsonl_with_chain(self):
        raw, records = _parse_golden_trace("golden-trace-17.jsonl")
        self.assertTrue(raw.endswith(b"\n"))
        events = [record["event"] for record in records]
        self.assertEqual(events[0], "run_started")
        self.assertEqual(events[-1], "run_finished")
        self.assertIn("budget_exhausted", events)
        self.assertNotIn("verification_finished", events)
        previous = None
        for position, record in enumerate(records):
            with self.subTest(seq=position):
                self.assertEqual(record["seq"], position)
                self.assertEqual(record["prev_event_hash"], previous)
                without_hash = {
                    key: value for key, value in record.items()
                    if key != "event_hash"
                }
                self.assertEqual(
                    record["event_hash"], digest_json(without_hash)
                )
            previous = record["event_hash"]

    def test_seed_17_full_run_reproduces_the_golden_trace_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, _, _ = _golden_verified_run(
                17, "greedy-v0", "golden-17", root
            )
            self.assertEqual(
                (recorder.directory / "trace.jsonl").read_bytes(),
                (FIXTURE_DIR / "golden-trace-17.jsonl").read_bytes(),
            )
            # The golden run directory carries the committed public task.
            self.assertEqual(
                (recorder.directory / "task.json").read_bytes(),
                (FIXTURE_DIR / "task-17.json").read_bytes(),
            )

    def test_seed_17_golden_receipt_reproduces_byte_identical(self):
        fixture_receipt = (FIXTURE_DIR / "golden-receipt-17.json").read_bytes()
        with tempfile.TemporaryDirectory() as root:
            _, receipt, verification = _golden_verified_run(
                17, "greedy-v0", "golden-17", root
            )
            self.assertTrue(verification.valid)
            self.assertEqual(verification.outcome, "safe_abort")
            self.assertEqual(verification.verified_score, 50)
            self.assertEqual(verification.completed_count, 5)
            self.assertEqual(verification.total_count, 10)
            self.assertEqual(
                canonical_json_bytes(receipt), fixture_receipt
            )

    def test_seed_17_golden_receipt_binds_the_expected_evidence(self):
        receipt = _read_json(FIXTURE_DIR / "golden-receipt-17.json")
        self.assertEqual(receipt["seed"], "17")
        self.assertEqual(receipt["policy_id"], "greedy-v0")
        self.assertEqual(receipt["execution_id"], "golden-17")
        self.assertIsNone(receipt["fault_tape_digest"])
        self.assertEqual(receipt["outcome"], "safe_abort")
        self.assertEqual(receipt["verified_score"], 50)
        self.assertEqual(
            receipt["counters"],
            {"policy_steps": 100, "tool_attempts": 100,
             "committed_effects": 100, "internal_retries": 0,
             "contract_violations": 0, "terminal_checks": 101},
        )
        block = receipt["verification"]
        self.assertEqual(block["status"], "valid")
        self.assertEqual(block["verified_score"], 50)
        self.assertEqual(block["completed_count"], 5)
        self.assertEqual(block["total_count"], 10)
        self.assertEqual(block["violations"], [])
        self.assertEqual(
            block["verification_finished"]["payload"],
            {"outcome": "safe_abort", "verified_score": 50,
             "verifier_version": "verifier-v0", "violations_count": 0},
        )

    def test_seed_17_golden_trace_digest_matches_the_receipt_binding(self):
        receipt = _read_json(FIXTURE_DIR / "golden-receipt-17.json")
        raw = (FIXTURE_DIR / "golden-trace-17.jsonl").read_bytes()
        self.assertEqual(
            receipt["trace_digest"],
            "sha256:" + hashlib.sha256(raw).hexdigest(),
        )


class TestGoldenSeed18Completion(unittest.TestCase):
    """T-24/AMEND-01: the faulted completion demonstrator (seed 18).

    Seed 18 (q=8, n=5) completes all five orders in 92 greedy policy steps,
    so the REQ-05 response-loss demonstrator (first delivery, step 8,
    place-order-00) ends ``completed`` with one internal retry, no duplicate
    reward, and the verifier-owned full score 100.
    """

    def test_seed_18_fixture_matches_the_formula_generator(self):
        raw = (FIXTURE_DIR / "task-18.json").read_bytes()
        self.assertEqual(
            canonical_json_bytes(serialize_task(generate_task(18))), raw
        )

    def test_seed_18_faulted_run_reproduces_golden_trace_and_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, receipt, verification = _golden_verified_run(
                18, "greedy-v0", "golden-18", root,
                fault_step=8, fault_intent="place-order-00",
            )
            self.assertTrue(verification.valid)
            self.assertEqual(verification.outcome, "completed")
            self.assertEqual(verification.verified_score, 100)
            self.assertEqual(verification.completed_count, 5)
            self.assertEqual(verification.total_count, 5)
            self.assertEqual(
                (recorder.directory / "trace.jsonl").read_bytes(),
                (FIXTURE_DIR / "golden-trace-18.jsonl").read_bytes(),
            )
            self.assertEqual(
                canonical_json_bytes(receipt),
                (FIXTURE_DIR / "golden-receipt-18.json").read_bytes(),
            )
        self.assertEqual(
            _read_json(FIXTURE_DIR / "golden-receipt-18.json")["counters"],
            {"policy_steps": 92, "tool_attempts": 93,
             "committed_effects": 92, "internal_retries": 1,
             "contract_violations": 0, "terminal_checks": 94},
        )

    def test_seed_18_golden_trace_shows_response_loss_one_effect_retry(self):
        _, records = _parse_golden_trace("golden-trace-18.jsonl")
        faults = [r for r in records if r["event"] == "fault_injected"]
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["policy_step"], 8)
        self.assertEqual(
            faults[0]["payload"]["fault_id"], "response_lost_after_commit"
        )
        retries = [r for r in records if r["event"] == "retry"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["payload"]["status"], "idempotent_replay")
        self.assertEqual(
            retries[0]["operation_key"], faults[0]["operation_key"]
        )
        deliveries = [r for r in records if r["event"] == "order_delivered"]
        self.assertEqual(len(deliveries), 5)
        order00 = [
            r for r in deliveries
            if r["payload"]["data"]["order_id"] == "order-00"
        ]
        # Exactly ONE delivery effect for the faulted order: the retried key
        # must never duplicate the reward (REQ-05).
        self.assertEqual(len(order00), 1)
        events = [record["event"] for record in records]
        self.assertNotIn("budget_exhausted", events)
        self.assertEqual(records[-1]["payload"]["outcome"], "completed")


class TestGoldenSeed29Evaluation(unittest.TestCase):
    """T-25: evaluation-seed plumbing evidence and rerun equality.

    Seed 29 is PLUMBING-ONLY (REQ-01/REQ-10): its golden run (bounded-v0, no
    tape) ends ``safe_abort`` at the locked 100-step budget with 6/10 orders
    and the verifier-owned partial score 60. It tunes nothing and supports no
    experimental claim.
    """

    def test_seed_29_golden_trace_and_receipt_reproduce_byte_identical(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, receipt, verification = _golden_verified_run(
                29, "bounded-v0", "golden-29", root
            )
            self.assertTrue(verification.valid)
            self.assertEqual(verification.outcome, "safe_abort")
            self.assertEqual(verification.verified_score, 60)
            self.assertEqual(verification.completed_count, 6)
            self.assertEqual(verification.total_count, 10)
            self.assertEqual(
                (recorder.directory / "trace.jsonl").read_bytes(),
                (FIXTURE_DIR / "golden-trace-29.jsonl").read_bytes(),
            )
            self.assertEqual(
                canonical_json_bytes(receipt),
                (FIXTURE_DIR / "golden-receipt-29.json").read_bytes(),
            )

    def test_seed_29_golden_receipt_stays_plumbing_only_evidence(self):
        receipt = _read_json(FIXTURE_DIR / "golden-receipt-29.json")
        self.assertEqual(receipt["seed"], "29")
        self.assertEqual(receipt["policy_id"], "bounded-v0")
        self.assertEqual(receipt["execution_id"], "golden-29")
        self.assertEqual(receipt["outcome"], "safe_abort")
        self.assertEqual(receipt["verified_score"], 60)
        self.assertEqual(
            receipt["counters"],
            {"policy_steps": 100, "tool_attempts": 100,
             "committed_effects": 100, "internal_retries": 0,
             "contract_violations": 0, "terminal_checks": 101},
        )
        block = receipt["verification"]
        self.assertEqual(block["status"], "valid")
        self.assertEqual(block["verified_score"], 60)
        self.assertEqual(
            block["verification_finished"]["payload"]["outcome"],
            "safe_abort",
        )

    def test_seed_29_golden_trace_digest_matches_the_receipt_binding(self):
        receipt = _read_json(FIXTURE_DIR / "golden-receipt-29.json")
        raw = (FIXTURE_DIR / "golden-trace-29.jsonl").read_bytes()
        self.assertEqual(
            receipt["trace_digest"],
            "sha256:" + hashlib.sha256(raw).hexdigest(),
        )

    def test_seed_29_golden_trace_records_exactly_100_policy_steps(self):
        # The kill switch: exactly 100 observations/decisions, no 101st, then
        # budget_exhausted -> run_finished(safe_abort). Every step in 1..100
        # appears exactly once, in order.
        _, records = _parse_golden_trace("golden-trace-29.jsonl")
        observations = [
            record for record in records
            if record["event"] == "observation_emitted"
        ]
        decisions = [
            record for record in records
            if record["event"] == "policy_decision"
        ]
        self.assertEqual(len(observations), 100)
        self.assertEqual(len(decisions), 100)
        self.assertEqual(
            [record["policy_step"] for record in observations],
            list(range(1, 101)),
        )
        events = [record["event"] for record in records]
        self.assertIn("budget_exhausted", events)
        self.assertEqual(records[-1]["payload"]["outcome"], "safe_abort")

    def test_two_physical_seed29_reruns_are_byte_identical(self):
        # Rerun equality (REQ-07): distinct physical executions under the
        # SAME logical identity produce byte-identical deterministic
        # artifacts and an identical verifier score; only execution_id (and
        # its containing receipt bytes) differ.
        with tempfile.TemporaryDirectory() as root_a, \
                tempfile.TemporaryDirectory() as root_b:
            recorder_a, receipt_a, _ = _golden_verified_run(
                29, "bounded-v0", "golden-29-rerun-a", root_a
            )
            recorder_b, receipt_b, _ = _golden_verified_run(
                29, "bounded-v0", "golden-29-rerun-b", root_b
            )
            # Read the persisted bytes INSIDE the contexts: the temp roots
            # disappear when the block exits.
            trace_a = (recorder_a.directory / "trace.jsonl").read_bytes()
            trace_b = (recorder_b.directory / "trace.jsonl").read_bytes()
            ops_a = (recorder_a.directory / "operation-log.json").read_bytes()
            ops_b = (recorder_b.directory / "operation-log.json").read_bytes()
        self.assertNotEqual(
            receipt_a["execution_id"], receipt_b["execution_id"]
        )
        self.assertEqual(
            receipt_a["logical_run_id"], receipt_b["logical_run_id"]
        )
        for field in ("trace_digest", "final_state_digest",
                      "operation_log_digest", "verified_score", "outcome"):
            self.assertEqual(receipt_a[field], receipt_b[field], field)
        self.assertEqual(trace_a, trace_b)
        self.assertEqual(ops_a, ops_b)
        del receipt_a["execution_id"], receipt_b["execution_id"]
        self.assertEqual(
            canonical_json_bytes(receipt_a), canonical_json_bytes(receipt_b)
        )
