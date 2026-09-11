"""Focused contract tests for VisualizerProjection v1 (work unit A)."""

import copy
import unittest

from robot_factory_arena.evidence.canonical import canonical_json_bytes, sha256_digest
from robot_factory_arena.evidence.visualizer_projection import (
    VisualizerLogicalMetadata,
    canonical_projection_bytes,
    validate_projection,
)

D = "sha256:" + "a" * 64
FAULT_ID = "response_lost_after_commit"
FAULT_HOOK = "after_commit_before_response"
FAULT_KIND = "response_lost"


def rebind(value):
    """Recompute the projection digest after an otherwise-valid mutation."""
    digest_input = copy.deepcopy(value)
    del digest_input["semantic_digests"]["projection_digest"]
    value["semantic_digests"]["projection_digest"] = sha256_digest(
        canonical_json_bytes(digest_input)
    )
    return value


def world(step=0):
    return {
        "schema_version": 1,
        "width": 10,
        "height": 10,
        "robot": {
            "position": [0, 0],
            "inventory_item_id": None,
            "base_position": [0, 0],
        },
        "shelves": [{"shelf_id": "shelf-00", "position": [1, 1], "item_id": "item-00"}],
        "orders": [{"order_id": "order-00", "item_id": "item-00", "quantity": 1}],
        "completed_orders": [],
        "step": step,
    }


def projection():
    before = dict.fromkeys(
        (
            "policy_steps",
            "tool_attempts",
            "committed_effects",
            "internal_retries",
            "contract_violations",
            "terminal_checks",
        ),
        0,
    )
    after = {**before, "policy_steps": 1}
    value = {
        "projection_type": "VisualizerProjection",
        "schema_version": 1,
        "logical_run": {
            "logical_run_id": "lr_" + "b" * 64,
            "task_version": "warehouse-v0",
            "seed": "18",
            "generator": "formula-v0",
            "generator_version": "1",
            "slot_layout": "p10-two-rows",
            "policy_id": "reference",
            "policy_digest": D,
            "provider": None,
            "model": None,
            "strategy_context_digest": None,
            "tool_schema_digest": None,
            "prompt_digest": None,
            "grants": [],
            "budget": {"maximum": 1},
            "fault_tape": None,
        },
        "initial_world": world(),
        "frames": [
            {
                "policy_step": 1,
                "observation": {
                    "policy_steps_before": 0,
                    "policy_steps_remaining_before": 1,
                    "policy_steps_maximum": 1,
                    "last_result": None,
                },
                "tool_call": {
                    "schema_version": None,
                    "tool": None,
                    "arguments": None,
                    "operation_key": None,
                },
                "candidate_shape": "non_mapping",
                "classification": "malformed",
                "reason": "malformed_call",
                "attempts": [
                    {
                        "attempt_index": 1,
                        "kind": "primary",
                        "tool_call": {
                            "schema_version": None,
                            "tool": None,
                            "arguments": None,
                            "operation_key": None,
                        },
                        "status": "rejected",
                        "reason": "malformed_call",
                        "response": "received",
                        "fault_ids": [],
                    }
                ],
                "subevents": [],
                "committed_domain_events": [],
                "state_before": world(),
                "state_after": world(),
                "state_delta": {
                    "changed": False,
                    "robot": {
                        "position_before": [0, 0],
                        "position_after": [0, 0],
                        "inventory_before": None,
                        "inventory_after": None,
                    },
                    "shelves": [],
                    "completed_orders_added": [],
                    "step_before": 0,
                    "step_after": 0,
                },
                "counters": {"before": before, "after": after},
            }
        ],
        "counters": after,
        "terminal_result": {
            "outcome": "safe_abort",
            "outcome_authority": "independent_verifier",
            "verified_score": 0,
            "completed_count": 0,
            "total_count": 1,
            "final_state": world(),
            "final_state_digest": D,
            "verifier_status": "valid",
            "verifier_version": "v1",
        },
        "semantic_digests": {
            key: (
                None
                if key
                in {
                    "fault_tape_digest",
                    "strategy_context_digest",
                    "tool_schema_digest",
                    "prompt_digest",
                }
                else D
            )
            for key in (
                "config_digest",
                "task_digest",
                "policy_digest",
                "fault_tape_digest",
                "trace_digest",
                "initial_state_digest",
                "final_state_digest",
                "projection_digest",
                "strategy_context_digest",
                "tool_schema_digest",
                "prompt_digest",
            )
        },
    }
    # Final-state digest claims are recomputed from the canonical world
    # the fixture actually embeds: the semantic digest and the
    # terminal_result duplicate claim; D remains source-opaque only.
    final_digest = sha256_digest(
        canonical_json_bytes(value["terminal_result"]["final_state"])
    )
    value["terminal_result"]["final_state_digest"] = final_digest
    value["semantic_digests"]["initial_state_digest"] = sha256_digest(
        canonical_json_bytes(value["initial_world"])
    )
    value["semantic_digests"]["final_state_digest"] = final_digest
    return rebind(value)


def committed_projection():
    """One committed pick frame with a response-loss retry (validator-valid)."""
    run = "lr_" + "b" * 64
    key = f"op/{run}/step-1/intent-take"

    def world(step=0, *, inventory=None, shelf_item: str | None = "item-00"):
        return {
            "schema_version": 1,
            "width": 10,
            "height": 10,
            "robot": {
                "position": [1, 1],
                "inventory_item_id": inventory,
                "base_position": [0, 0],
            },
            "shelves": [
                {"shelf_id": "shelf-00", "position": [1, 1], "item_id": shelf_item}
            ],
            "orders": [{"order_id": "order-00", "item_id": "item-00", "quantity": 1}],
            "completed_orders": [],
            "step": step,
        }

    before = dict.fromkeys(
        (
            "policy_steps",
            "tool_attempts",
            "committed_effects",
            "internal_retries",
            "contract_violations",
            "terminal_checks",
        ),
        0,
    )
    before["terminal_checks"] = 1
    after = {
        "policy_steps": 1,
        "tool_attempts": 2,
        "committed_effects": 1,
        "internal_retries": 1,
        "contract_violations": 0,
        "terminal_checks": 3,
    }
    call = {
        "schema_version": 1,
        "tool": "pick",
        "arguments": {"shelf_id": "shelf-00", "item_id": "item-00"},
        "operation_key": key,
    }
    value = {
        "projection_type": "VisualizerProjection",
        "schema_version": 1,
        "logical_run": {
            "logical_run_id": run,
            "task_version": "warehouse-v0",
            "seed": "18",
            "generator": "formula-v0",
            "generator_version": "1",
            "slot_layout": "p10-two-rows",
            "policy_id": "reference",
            "policy_digest": D,
            "provider": None,
            "model": None,
            "strategy_context_digest": None,
            "tool_schema_digest": None,
            "prompt_digest": None,
            "grants": ["move", "pick", "place"],
            "budget": {"maximum": 100},
            "fault_tape": None,
        },
        "initial_world": world(),
        "frames": [
            {
                "policy_step": 1,
                "observation": {
                    "policy_steps_before": 0,
                    "policy_steps_remaining_before": 99,
                    "policy_steps_maximum": 100,
                    "last_result": None,
                },
                "tool_call": call,
                "candidate_shape": "tool_call",
                "classification": "committed",
                "reason": None,
                "attempts": [
                    {
                        "attempt_index": 1,
                        "kind": "primary",
                        "tool_call": call,
                        "status": "committed",
                        "reason": None,
                        "response": "lost",
                        "fault_ids": [FAULT_ID],
                    },
                    {
                        "attempt_index": 2,
                        "kind": "internal_retry",
                        "tool_call": call,
                        "status": "idempotent_replay",
                        "reason": None,
                        "response": "received",
                        "fault_ids": [FAULT_ID],
                    },
                ],
                "subevents": [
                    {
                        "sequence": 1,
                        "kind": "fault_injected",
                        "operation_key": key,
                        "fault_id": FAULT_ID,
                        "hook": FAULT_HOOK,
                        "fault_kind": FAULT_KIND,
                        "attempt_index": 1,
                        "status": "lost",
                        "reason": "response_lost",
                    },
                    {
                        "sequence": 2,
                        "kind": "internal_retry",
                        "operation_key": key,
                        "fault_id": None,
                        "hook": None,
                        "fault_kind": None,
                        "attempt_index": 2,
                        "status": "idempotent_replay",
                        "reason": None,
                    },
                ],
                "committed_domain_events": [
                    {
                        "schema_version": 1,
                        "event_type": "item_picked",
                        "operation_key": key,
                        "data": {"shelf_id": "shelf-00", "item_id": "item-00"},
                    }
                ],
                "state_before": world(),
                "state_after": world(1, inventory="item-00", shelf_item=None),
                "state_delta": {
                    "changed": True,
                    "robot": {
                        "position_before": [1, 1],
                        "position_after": [1, 1],
                        "inventory_before": None,
                        "inventory_after": "item-00",
                    },
                    "shelves": [
                        {
                            "shelf_id": "shelf-00",
                            "item_id_before": "item-00",
                            "item_id_after": None,
                        }
                    ],
                    "completed_orders_added": [],
                    "step_before": 0,
                    "step_after": 1,
                },
                "counters": {"before": before, "after": after},
            }
        ],
        "counters": after,
        "terminal_result": {
            "outcome": "safe_abort",
            "outcome_authority": "independent_verifier",
            "verified_score": 0,
            "completed_count": 0,
            "total_count": 1,
            "final_state": world(1, inventory="item-00", shelf_item=None),
            "final_state_digest": D,
            "verifier_status": "valid",
            "verifier_version": "v1",
        },
        "semantic_digests": {
            key_name: (
                None
                if key_name
                in {
                    "fault_tape_digest",
                    "strategy_context_digest",
                    "tool_schema_digest",
                    "prompt_digest",
                }
                else D
            )
            for key_name in (
                "config_digest",
                "task_digest",
                "policy_digest",
                "fault_tape_digest",
                "trace_digest",
                "initial_state_digest",
                "final_state_digest",
                "projection_digest",
                "strategy_context_digest",
                "tool_schema_digest",
                "prompt_digest",
            )
        },
    }
    # Final-state digest claims are recomputed from the canonical world
    # the fixture actually embeds: the semantic digest and the
    # terminal_result duplicate claim; D remains source-opaque only.
    final_digest = sha256_digest(
        canonical_json_bytes(value["terminal_result"]["final_state"])
    )
    value["terminal_result"]["final_state_digest"] = final_digest
    value["semantic_digests"]["initial_state_digest"] = sha256_digest(
        canonical_json_bytes(value["initial_world"])
    )
    value["semantic_digests"]["final_state_digest"] = final_digest
    return rebind(value)


class VisualizerProjectionTests(unittest.TestCase):
    def test_exact_schema_nulls_and_canonical_order(self):
        value = projection()
        parsed = validate_projection(value)
        self.assertEqual(parsed.to_mapping(), value)
        self.assertEqual(
            canonical_projection_bytes(parsed),
            canonical_projection_bytes(validate_projection(copy.deepcopy(value))),
        )
        value["semantic_digests"]["projection_digest"] = D
        with self.assertRaises(ValueError):
            validate_projection(value)

    def test_key_float_and_physical_metadata_are_rejected(self):
        for mutate in (
            lambda v: v.__setitem__("execution_id", "ex_no"),
            lambda v: v["frames"][0].__setitem__("latency", 1),
            lambda v: v["counters"].__setitem__("policy_steps", 1.0),
            lambda v: v["logical_run"].pop("provider"),
        ):
            value = projection()
            mutate(value)
            with self.assertRaises((TypeError, ValueError)):
                validate_projection(value)

    def test_metadata_is_immutable_and_validates_only_logical_values(self):
        metadata = VisualizerLogicalMetadata(None, None, None, None, None)
        self.assertIsNone(metadata.provider)
        with self.assertRaises((TypeError, ValueError)):
            VisualizerLogicalMetadata("p", None, "not-a-digest", None, None)

    def test_rejected_frame_requires_equal_states_and_six_counter_prefixes(self):
        value = projection()
        value["frames"][0]["state_after"] = world(1)
        with self.assertRaises(ValueError):
            validate_projection(value)
        value = projection()
        value["frames"][0]["counters"]["after"].pop("terminal_checks")
        with self.assertRaises(ValueError):
            validate_projection(value)

    def test_build_reconstructs_rejected_policy_step_from_verified_sources(self):
        from types import SimpleNamespace

        from robot_factory_arena.evidence.visualizer_projection import build_projection

        initial = world()
        config = {
            "schema_version": 1,
            "task_version": "warehouse-v0",
            "seed": "18",
            "policy_id": "reference",
            "policy_digest": D,
            "budget": 1,
            "grants": ["move"],
            "fault_tape_spec": None,
        }
        artifacts = SimpleNamespace(
            task={
                "task_version": "warehouse-v0",
                "seed": "18",
                "config_identity": {
                    "generator": "formula-v0",
                    "generator_version": "1",
                    "slot_layout": "p10-two-rows",
                },
                "initial_state": initial,
            },
            config=config,
            operation_log={
                "logical_run_id": "lr_" + "b" * 64,
                "operations": [
                    {
                        "policy_step": 1,
                        "operation_key": None,
                        "tool": None,
                        "arguments": None,
                        "status": "rejected",
                        "reason": "malformed_call",
                        "state_before": D,
                        "state_after": None,
                        "events": [],
                        "retry": False,
                        "fault_ids": [],
                    }
                ],
            },
            trace_records=[
                {
                    "seq": 1,
                    "policy_step": 1,
                    "event": "policy_decision",
                    "payload": {"candidate_type": "str"},
                }
            ],
            boundaries={},
            final_state=initial,
            fault_tape=None,
        )
        receipt = {
            "logical_run_id": "lr_" + "b" * 64,
            "counters": {
                "policy_steps": 1,
                "tool_attempts": 0,
                "committed_effects": 0,
                "internal_retries": 0,
                "contract_violations": 1,
                "terminal_checks": 2,
            },
            "config_digest": sha256_digest(canonical_json_bytes(config)),
            "task_digest": D,
            "policy_digest": D,
            "fault_tape_digest": None,
            "trace_digest": sha256_digest(
                canonical_json_bytes(artifacts.trace_records[0]) + b"\n"
            ),
            "final_state_digest": sha256_digest(canonical_json_bytes(initial)),
            "operation_log_digest": sha256_digest(
                canonical_json_bytes(artifacts.operation_log)
            ),
        }
        release = SimpleNamespace(
            verified=True,
            artifacts=artifacts,
            receipt=receipt,
            verification=SimpleNamespace(
                valid=True,
                outcome="safe_abort",
                verified_score=0,
                completed_count=0,
                total_count=1,
                verifier_version="v1",
            ),
            logical_metadata=VisualizerLogicalMetadata(None, None, None, None, None),
            replay_result=SimpleNamespace(matches_receipt=True),
        )
        built = build_projection(release).to_mapping()
        self.assertEqual(built["frames"][0]["classification"], "malformed")
        self.assertEqual(built["frames"][0]["state_before"], initial)
        self.assertEqual(built["frames"][0]["state_after"], initial)
        self.assertEqual(built["counters"], receipt["counters"])
        self.assertNotIn("candidate_type", str(built))

    def test_build_fails_closed_when_primary_source_steps_are_missing(self):
        from types import SimpleNamespace

        from robot_factory_arena.evidence.visualizer_projection import build_projection

        release = SimpleNamespace(
            verified=True,
            artifacts=SimpleNamespace(operation_log={"operations": []}),
            receipt={},
            verification=SimpleNamespace(valid=True),
        )
        with self.assertRaises(ValueError):
            build_projection(release)

    def test_nested_schema_rejects_unsafe_events_sub_events_and_delta(self):
        mutations = (
            lambda value: value["frames"][0]["subevents"].append({"leak": "raw"}),
            lambda value: value["frames"][0]["committed_domain_events"].append(
                {
                    "schema_version": 1,
                    "event_type": "robot_moved",
                    "operation_key": "bad",
                    "data": {},
                }
            ),
            lambda value: value["frames"][0]["state_delta"]["robot"].__setitem__(
                "extra", 1
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = projection()
                mutate(value)
                with self.assertRaises(ValueError):
                    validate_projection(value)

    def test_closed_reason_taxonomy_rejects_unknown_values(self):
        value = projection()
        value["frames"][0]["reason"] = "unapproved_reason"
        with self.assertRaises(ValueError):
            validate_projection(value)

    def test_real_response_loss_artifacts_reconstruct_one_faulted_frame(self):
        """Exercise recorder-produced sources, not a hand-authored retry record."""
        import tempfile
        from types import SimpleNamespace

        from robot_factory_arena.contracts.policy import policy_manifest_digest
        from robot_factory_arena.domain.task import generate_task
        from robot_factory_arena.evidence.artifact_store import RunEvidenceRecorder
        from robot_factory_arena.evidence.receipt import ReceiptWriter
        from robot_factory_arena.evidence.replay import replay
        from robot_factory_arena.evidence.visualizer_projection import build_projection
        from robot_factory_arena.faults.model import (
            FAULT_ID_RESPONSE_LOST,
            HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST,
        )
        from robot_factory_arena.faults.tape import FaultTapeSpec, FaultTarget
        from robot_factory_arena.policies.greedy import GreedyPolicy
        from robot_factory_arena.runtime.runner import (
            EvidenceCallbacks,
            RunConfig,
            Runner,
            compute_logical_run_id,
        )
        from robot_factory_arena.verification.verifier import (
            Verifier,
            load_verification_artifacts,
        )

        with tempfile.TemporaryDirectory() as root:
            task, policy = generate_task(18), GreedyPolicy()
            tape = FaultTapeSpec(
                1,
                FAULT_ID_RESPONSE_LOST,
                HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
                KIND_RESPONSE_LOST,
                FaultTarget(8, "place-order-00"),
            )
            config = RunConfig(
                1,
                task.task_version,
                task.seed,
                "greedy-v0",
                policy_manifest_digest("greedy-v0"),
                100,
                ("move", "pick", "place"),
                tape,
            )
            recorder = RunEvidenceRecorder(
                root,
                task,
                config,
                compute_logical_run_id(task, config),
                "execution-viz-test",
            )
            result = Runner(evidence=EvidenceCallbacks(**recorder.callback_map())).run(
                task, policy, config, "execution-viz-test"
            )
            directory = recorder.close()
            initial_verification = Verifier().verify(
                load_verification_artifacts(directory)
            )
            self.assertTrue(initial_verification.valid)
            receipt = ReceiptWriter.write_final(
                recorder.receipt_candidate, initial_verification
            )
            (directory / "receipt.json").write_bytes(canonical_json_bytes(receipt))
            artifacts_for_release = load_verification_artifacts(directory)
            verification = Verifier().verify(artifacts_for_release)
            replay_result = replay(artifacts_for_release.replay_input, receipt=receipt)
            self.assertTrue(verification.valid)
            self.assertTrue(replay_result.matches_receipt)
            counters = {
                name: getattr(result.counters, name)
                for name in (
                    "policy_steps",
                    "tool_attempts",
                    "committed_effects",
                    "internal_retries",
                    "contract_violations",
                    "terminal_checks",
                )
            }
            release = SimpleNamespace(
                verified=True,
                artifacts=artifacts_for_release,
                receipt=receipt,
                verification=verification,
                replay_result=replay_result,
                logical_metadata=VisualizerLogicalMetadata(
                    None, None, None, None, None
                ),
            )
            built = build_projection(release).to_mapping()
        faulted = [frame for frame in built["frames"] if frame["subevents"]]
        self.assertEqual(len(built["frames"]), counters["policy_steps"])
        self.assertEqual(len(faulted), 1)
        self.assertEqual(
            [event["kind"] for event in faulted[0]["subevents"]],
            ["fault_injected", "internal_retry"],
        )
        self.assertEqual(faulted[0]["attempts"][0]["response"], "lost")
        self.assertEqual(faulted[0]["attempts"][1]["status"], "idempotent_replay")

    def test_real_verified_replay_fixtures_cover_rejected_and_replay_paths(self):
        """Recorder fixtures use an independently verified, replay-bound release."""
        import tempfile
        from types import SimpleNamespace

        from robot_factory_arena.contracts.policy import policy_manifest_digest
        from robot_factory_arena.domain.task import generate_task
        from robot_factory_arena.evidence.artifact_store import RunEvidenceRecorder
        from robot_factory_arena.evidence.receipt import ReceiptWriter
        from robot_factory_arena.evidence.replay import replay
        from robot_factory_arena.evidence.visualizer_projection import build_projection
        from robot_factory_arena.runtime.runner import (
            EvidenceCallbacks,
            RunConfig,
            Runner,
            compute_logical_run_id,
        )
        from robot_factory_arena.verification.verifier import (
            Verifier,
            load_verification_artifacts,
        )

        def fixture(candidate_for, expected, reason, grants=("move", "pick", "place")):
            task = generate_task(18)
            config = RunConfig(
                1,
                task.task_version,
                task.seed,
                "scripted-v0",
                policy_manifest_digest("scripted-v0"),
                100,
                grants,
                None,
            )
            run_id = compute_logical_run_id(task, config)
            candidates = candidate_for(run_id)

            class Policy:
                policy_id, policy_digest = "scripted-v0", config.policy_digest

                def __init__(self):
                    self.index = 0

                def decide(self, _):
                    candidate = (
                        candidates(self.index)
                        if callable(candidates)
                        else candidates[min(self.index, len(candidates) - 1)]
                    )
                    self.index += 1
                    return candidate

            with tempfile.TemporaryDirectory() as root:
                recorder = RunEvidenceRecorder(root, task, config, run_id, "fixture")
                result = Runner(
                    evidence=EvidenceCallbacks(**recorder.callback_map())
                ).run(task, Policy(), config, "fixture")
                directory = recorder.close()
                initial_verification = Verifier().verify(
                    load_verification_artifacts(directory)
                )
                self.assertTrue(
                    initial_verification.valid, initial_verification.violations
                )
                receipt = ReceiptWriter.write_final(
                    recorder.receipt_candidate, initial_verification
                )
                (directory / "receipt.json").write_bytes(canonical_json_bytes(receipt))
                artifacts = load_verification_artifacts(directory)
                verification = Verifier().verify(artifacts)
                replay_result = replay(artifacts.replay_input, receipt=receipt)
                # The projection gets only a receipt-backed, independently
                # verified and replay-bound release handoff.
                self.assertTrue(verification.valid)
                self.assertTrue(replay_result.matches_receipt)
                projection = build_projection(
                    SimpleNamespace(
                        verified=True,
                        artifacts=artifacts,
                        receipt=receipt,
                        verification=verification,
                        replay_result=replay_result,
                        logical_metadata=VisualizerLogicalMetadata(
                            None, None, None, None, None
                        ),
                    )
                ).to_mapping()
            self.assertEqual(len(projection["frames"]), result.counters.policy_steps)
            matching = [
                frame
                for frame in projection["frames"]
                if frame["classification"] == expected and frame["reason"] == reason
            ]
            self.assertTrue(matching)
            for frame in projection["frames"]:
                if frame["classification"] != "committed":
                    self.assertEqual(frame["state_before"], frame["state_after"])
            return projection, result, artifacts.trace_records

        def call(tool, arguments, key):
            return {
                "schema_version": 1,
                "tool": tool,
                "arguments": arguments,
                "operation_key": key,
            }

        def repeated_call(tool, arguments, intent):
            return lambda run: lambda index: call(
                tool, arguments, f"op/{run}/step-{index + 1}/intent-{intent}"
            )

        cases = (
            (
                lambda run: lambda index: {
                    "schema_version": 1,
                    "tool": "assemble",
                    "arguments": {},
                    "operation_key": f"op/{run}/step-{index + 1}/intent-assemble",
                },
                "unsupported",
                "unsupported_tool",
                None,
            ),
            (
                lambda run: [call("move", {"direction": "north"}, "invalid-key")],
                "invalid",
                "operation_key_invalid",
                None,
            ),
            (
                repeated_call("move", {"direction": "sideways"}, "bad"),
                "invalid",
                "invalid_arguments",
                None,
            ),
            (
                repeated_call("place", {"order_id": "order-00"}, "place-order-00"),
                "unauthorized",
                "missing_grant",
                ("move",),
            ),
            (
                repeated_call("move", {"direction": "south"}, "south"),
                "rejected",
                "destination_out_of_grid",
                None,
            ),
            (lambda run: [{"nonsense": True}], "malformed", "malformed_call", None),
            (
                lambda run: [
                    call("move", {"direction": "east"}, f"op/{run}/step-1/intent-east"),
                    call("move", {"direction": "east"}, f"op/{run}/step-1/intent-east"),
                ],
                "idempotent_replay",
                None,
                None,
            ),
        )
        for candidates, expected, reason, grants in cases:
            with self.subTest(expected=expected, reason=reason):
                projection, result, traces = fixture(
                    candidates, expected, reason, grants or ("move", "pick", "place")
                )
                # Budget exhaustion is terminal evidence, not a synthetic 101st
                # policy-step frame: all 100 consumed decisions remain frames.
                self.assertEqual(result.outcome, "safe_abort")
                self.assertEqual(projection["frames"][-1]["policy_step"], 100)
                self.assertEqual(projection["counters"]["policy_steps"], 100)
                self.assertTrue(any(t["event"] == "budget_exhausted" for t in traces))
                self.assertFalse(
                    any(
                        frame["reason"] == "budget_exhausted"
                        for frame in projection["frames"]
                    )
                )

    def test_projection_is_defensively_immutable_and_release_gated(self):
        from types import SimpleNamespace

        from robot_factory_arena.evidence.visualizer_projection import build_projection

        source = projection()
        parsed = validate_projection(source)
        source["frames"][0]["reason"] = "changed-after-validation"
        self.assertEqual(parsed.to_mapping()["frames"][0]["reason"], "malformed_call")
        with self.assertRaises(ValueError):
            build_projection(
                SimpleNamespace(verified=False, projection_mapping=projection())
            )
        built = build_projection(
            SimpleNamespace(verified=True, projection_mapping=projection())
        )
        self.assertEqual(built.to_mapping()["projection_type"], "VisualizerProjection")


class VisualizerProjectionRemediationTests(unittest.TestCase):
    """Regression tests for the verify-report 7011 fail-closed findings."""

    def _key(self, intent: str) -> str:
        return f"op/lr_{'b' * 64}/step-1/intent-{intent}"

    def test_tool_call_arguments_follow_exact_v0_grammar(self):
        def call(**override):
            value = {
                "schema_version": 1,
                "tool": "move",
                "arguments": {"direction": "north"},
                "operation_key": self._key("north"),
            }
            value.update(override)
            return value

        unsafe = (
            call(schema_version=None),
            call(arguments={"direction": "sideways"}),
            call(arguments={"direction": "north", "speed": "fast"}),
            call(tool="pick", arguments={"shelf_id": "shelf-99", "item_id": "item-00"}),
            call(tool="pick", arguments={"shelf_id": "shelf-00", "item_id": "item-9"}),
            call(tool="place", arguments={"order_id": "order-x"}),
            call(schema_version=2),
            call(schema_version=None, arguments=None),
            call(tool="teleport", arguments=None),
        )
        for tool_call in unsafe:
            with self.subTest(tool_call=tool_call):
                value = projection()
                value["frames"][0]["tool_call"] = tool_call
                with self.assertRaises(ValueError):
                    validate_projection(rebind(value))

    def test_retained_attempt_tool_calls_fail_closed_too(self):
        value = projection()
        value["frames"][0]["attempts"][0]["tool_call"] = {
            "schema_version": 1,
            "tool": "move",
            "arguments": {"direction": "sideways"},
            "operation_key": self._key("north"),
        }
        with self.assertRaises(ValueError):
            validate_projection(rebind(value))

    def test_last_result_operation_keys_are_bound_or_explicit_sentinel(self):
        mutations = (
            lambda value: value["frames"][0]["observation"].__setitem__(
                "last_result",
                {"status": "committed", "operation_key": {"forged": 1}, "reason": None},
            ),
            lambda value: value["frames"][0]["observation"].__setitem__(
                "last_result",
                {
                    "status": "rejected",
                    "operation_key": f"op/lr_{'c' * 64}/step-1/intent-x",
                    "reason": "invalid_arguments",
                },
            ),
            lambda value: value["frames"][0]["observation"].__setitem__(
                "last_result",
                {"status": "rejected", "operation_key": "junk-key", "reason": None},
            ),
            lambda value: value["frames"][0]["observation"].__setitem__(
                "last_result",
                {"status": "committed", "operation_key": None, "reason": None},
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = projection()
                mutate(value)
                with self.assertRaises(ValueError):
                    validate_projection(rebind(value))

    def test_empty_operation_key_sentinel_stays_valid(self):
        value = projection()
        value["frames"][0]["observation"]["last_result"] = {
            "status": "rejected",
            "operation_key": "",
            "reason": "malformed_call",
        }
        validate_projection(rebind(value))

    def test_required_semantic_digests_are_non_null(self):
        required = (
            "config_digest",
            "task_digest",
            "policy_digest",
            "trace_digest",
            "initial_state_digest",
            "final_state_digest",
            "projection_digest",
        )
        for name in required:
            with self.subTest(digest=name):
                value = projection()
                value["semantic_digests"][name] = None
                if name != "projection_digest":
                    # rebinding legitimately repairs a null self-digest; its
                    # guard is the digest-binding check below instead.
                    value = rebind(value)
                with self.assertRaises(ValueError):
                    validate_projection(value)

    def test_nullable_semantic_digests_stay_nullable(self):
        value = projection()
        for name in (
            "fault_tape_digest",
            "strategy_context_digest",
            "tool_schema_digest",
            "prompt_digest",
        ):
            value["semantic_digests"][name] = None
        validate_projection(value)

    def test_final_state_semantic_digest_must_bind_canonical_final_state(self):
        # The outer projection digest is correctly rebound, so the
        # unrelated self-digest guard passes and cannot mask this
        # assertion: rejection must come from final-state binding.
        value = projection()
        value["semantic_digests"]["final_state_digest"] = "sha256:" + "c" * 64
        with self.assertRaisesRegex(
            ValueError, "semantic final_state_digest does not bind"
        ):
            validate_projection(rebind(value))

    def test_terminal_final_state_digest_must_bind_canonical_final_state(self):
        # terminal_result.final_state_digest is a duplicate final-state claim,
        # not an independent field: mutating only this alias and correctly
        # rebounding the outer projection digest must still fail closed, so
        # the self-digest guard can never launder the forgery.
        value = projection()
        value["terminal_result"]["final_state_digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(
            ValueError, "terminal final_state_digest does not bind"
        ):
            validate_projection(rebind(value))

    def test_initial_state_semantic_digest_must_bind_canonical_initial_world(self):
        value = projection()
        value["semantic_digests"]["initial_state_digest"] = "sha256:" + "d" * 64
        with self.assertRaisesRegex(
            ValueError, "semantic initial_state_digest does not bind"
        ):
            validate_projection(rebind(value))

    def test_policy_digest_aliases_must_agree(self):
        for mutate in (
            lambda value: value["logical_run"].__setitem__(
                "policy_digest", "sha256:" + "e" * 64
            ),
            lambda value: value["semantic_digests"].__setitem__(
                "policy_digest", "sha256:" + "e" * 64
            ),
        ):
            with self.subTest(mutate=mutate):
                value = projection()
                mutate(value)
                with self.assertRaisesRegex(
                    ValueError, "policy digest aliases disagree"
                ):
                    validate_projection(rebind(value))

    def test_consistent_world_mutation_with_recomputed_state_digests_stays_valid(
        self,
    ):
        # The binding checks compare against canonical content, not fixed
        # constants: an honestly recomputed and rebound projection stays valid.
        moved = world()
        moved["shelves"][0]["position"] = [2, 2]
        value = projection()
        value["initial_world"] = moved
        value["frames"][0]["state_before"] = moved
        value["frames"][0]["state_after"] = moved
        value["terminal_result"]["final_state"] = moved
        value["semantic_digests"]["initial_state_digest"] = sha256_digest(
            canonical_json_bytes(moved)
        )
        value["terminal_result"]["final_state_digest"] = sha256_digest(
            canonical_json_bytes(moved)
        )
        value["semantic_digests"]["final_state_digest"] = sha256_digest(
            canonical_json_bytes(moved)
        )
        validate_projection(rebind(value))

    def test_domain_event_data_is_the_exact_v0_shape(self):
        key = self._key("take")

        def with_event(event):
            value = committed_projection()
            value["frames"][0]["committed_domain_events"] = [event]
            return value

        valid = validate_projection(
            rebind(
                with_event(
                    {
                        "schema_version": 1,
                        "event_type": "item_picked",
                        "operation_key": key,
                        "data": {"shelf_id": "shelf-00", "item_id": "item-00"},
                    }
                )
            )
        )
        self.assertEqual(
            valid.to_mapping()["frames"][0]["committed_domain_events"][0]["data"],
            {"shelf_id": "shelf-00", "item_id": "item-00"},
        )
        unsafe = (
            {
                "schema_version": 1,
                "event_type": "item_picked",
                "operation_key": key,
                "data": {"secret": "physical/path"},
            },
            {
                "schema_version": 1,
                "event_type": "robot_moved",
                "operation_key": key,
                "data": {"from": [0, 0], "to": [0, 1], "direction": "sideways"},
            },
            {
                "schema_version": 1,
                "event_type": "robot_moved",
                "operation_key": key,
                "data": {"from": [0, 0], "to": [99, 0], "direction": "east"},
            },
            {
                "schema_version": 1,
                "event_type": "item_picked",
                "operation_key": key,
                "data": {"shelf_id": "shelf-00"},
            },
            {
                "schema_version": 1,
                "event_type": "order_delivered",
                "operation_key": key,
                "data": {"order_id": "order-00", "item_id": "item-77"},
            },
        )
        for event in unsafe:
            with self.subTest(event=event), self.assertRaises(ValueError):
                validate_projection(rebind(with_event(event)))

    def test_non_committed_frames_carry_no_committed_events(self):
        value = projection()
        value["frames"][0]["committed_domain_events"] = [
            {
                "schema_version": 1,
                "event_type": "item_picked",
                "operation_key": self._key("take"),
                "data": {"shelf_id": "shelf-00", "item_id": "item-00"},
            }
        ]
        with self.assertRaises(ValueError):
            validate_projection(rebind(value))

    def test_frame_states_chain_from_initial_world_to_final_state(self):
        value = projection()
        value["frames"][0]["state_before"] = world(7)
        with self.assertRaises(ValueError):
            validate_projection(rebind(value))
        value = projection()
        value["terminal_result"]["final_state"] = world(2)
        with self.assertRaises(ValueError):
            validate_projection(rebind(value))

    def test_committed_fixture_is_valid(self):
        parsed = validate_projection(committed_projection())
        frame = parsed.to_mapping()["frames"][0]
        self.assertEqual(frame["classification"], "committed")
        self.assertEqual(frame["attempts"][0]["response"], "lost")

    def test_projection_is_defensively_immutable_and_release_gated(self):
        from types import SimpleNamespace

        from robot_factory_arena.evidence.visualizer_projection import build_projection

        source = projection()
        parsed = validate_projection(source)
        source["frames"][0]["reason"] = "changed-after-validation"
        self.assertEqual(parsed.to_mapping()["frames"][0]["reason"], "malformed_call")
        with self.assertRaises(ValueError):
            build_projection(
                SimpleNamespace(verified=False, projection_mapping=projection())
            )
        built = build_projection(
            SimpleNamespace(verified=True, projection_mapping=projection())
        )
        self.assertEqual(built.to_mapping()["projection_type"], "VisualizerProjection")


if __name__ == "__main__":
    unittest.main()
