"""Golden-trace tests: canonical byte/digest vectors now; trace, receipt, and
observation golden evidence accumulate here through T-03..T-06 and later
batches B4/B6 (REQ-03, REQ-07).
"""
import hashlib
import json
import pathlib
import unittest

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
