"""Phase 4A LLMPolicy tests (T4A-04/T4A-05, P4A-REQ-02/03/04).

One frozen English prompt template and one frozen JSON tool schema with
golden digest vectors; one provider adapter invocation per decide; exact
move/pick/place normalization into one v0 ToolCall; deterministic rejection
candidates carrying the current-run ``intent-llm-reject`` key for
malformed_call / unsupported_tool / invalid_arguments without raising and
without a second repair call.
"""

import hashlib
import unittest

from robot_factory_arena.contracts.tool_call import (
    normalize_tool_call,
    validate_operation_key,
)
from robot_factory_arena.evidence.canonical import canonical_json, sha256_digest
from robot_factory_arena.policies.llm import (
    POLICY_ID,
    PROMPT_TEMPLATE,
    TOOL_SCHEMA,
    LLMPolicy,
    llm_policy_digest,
    prompt_template_digest,
    render_prompt,
    tool_schema_digest,
)
from robot_factory_arena.providers.adapter import ProviderError
from robot_factory_arena.providers.fake import FakeProvider

LOGICAL_RUN_ID = "lr_" + "ab" * 32

# The frozen template is re-declared here byte-for-byte; the policy module
# must match it exactly (P4A-REQ-04: wording and line endings are frozen).
FROZEN_PROMPT = (
    "You are the bounded controller for Robot Factory & Arena warehouse-v0.\n"
    "\n"
    "Read the serialized observation below. Choose exactly one next warehouse action.\n"
    "You may call exactly one tool: move, pick, or place. Do not call tools in\n"
    "parallel. Use only the arguments defined by the tool schema. Do not invent\n"
    "items, shelves, orders, directions, operation keys, grants, or state changes.\n"
    "The host will add the operation key and will validate the action. If no legal\n"
    "action is possible, still select the single best action supported by the\n"
    "observation.\n"
    "\n"
    "Operating procedure:\n"
    "Coordinate convention: east x+1, west x-1, north y+1, south y-1.\n"
    "If carrying an item, find its pending matching order, move one\n"
    "Manhattan-reducing step toward base_position, then call place only at base.\n"
    "If empty, select the first pending order in list order, find the shelf with\n"
    "its item_id, move one Manhattan-reducing step toward that shelf, then call\n"
    "pick only when at that shelf.\n"
    "After every action, re-read the current observation and last_result. Do not\n"
    "repeat a rejected action. Never target completed or non-pending orders/items.\n"
    "\n"
    "Serialized observation:\n"
    "{{observation_json}}\n"
)


def _observation(used=2):
    return {
        "schema_version": 1,
        "logical_run_id": LOGICAL_RUN_ID,
        "world_state": {"step": used, "robot": {"position": [0, 0]}},
        "grants": ["move", "pick", "place"],
        "policy_steps": {"used": used, "remaining": 100 - used, "maximum": 100},
        "last_result": None,
    }


def _success(tool_call, request_id, raw="choosing"):
    return _script_entry(tool_call=tool_call, request_id=request_id, raw=raw)


def _script_entry(*, tool_call, request_id, raw):
    from robot_factory_arena.providers.adapter import (
        ProviderResult,
        ToolCallPayload,
        Usage,
    )

    return ProviderResult(
        provider="fake",
        model="fake-v1",
        request_id=request_id,
        usage=Usage(4, 6, 10),
        latency_ms=3,
        raw_visible_response=raw,
        tool_call=None if tool_call is None else ToolCallPayload(**tool_call),
        error=None,
    )


def _error_result(code, message, request_id):
    from robot_factory_arena.providers.adapter import ProviderResult, Usage

    return ProviderResult(
        provider="fake",
        model="fake-v1",
        request_id=request_id,
        usage=Usage(4, 0, 4),
        latency_ms=1,
        raw_visible_response=None,
        tool_call=None,
        error=ProviderError(code, message),
    )


def _policy(*script):
    return LLMPolicy(FakeProvider(script=list(script)))


class TestFrozenPromptTemplate(unittest.TestCase):
    """T4A-04: byte-exact prompt freeze, stable UTF-8 digest, canonical embed."""

    def test_template_matches_the_frozen_wording_exactly(self):
        self.assertEqual(PROMPT_TEMPLATE, FROZEN_PROMPT)

    def test_template_has_no_carriage_returns_and_ends_with_one_lf(self):
        self.assertNotIn("\r", PROMPT_TEMPLATE)
        self.assertTrue(PROMPT_TEMPLATE.endswith("{{observation_json}}\n"))

    def test_operating_procedure_is_present_and_digest_is_deterministic(self):
        for guidance in (
            "Coordinate convention: east x+1, west x-1, north y+1, south y-1.",
            "If carrying an item, find its pending matching order",
            "Manhattan-reducing step toward base_position",
            "call place only at base.",
            "first pending order in list order",
            "find the shelf with",
            "its item_id",
            "pick only when at that shelf.",
            "After every action, re-read the current observation and last_result.",
            "Do not\nrepeat a rejected action.",
            "Never target completed or non-pending orders/items.",
        ):
            self.assertIn(guidance, PROMPT_TEMPLATE)
        first = prompt_template_digest()
        self.assertEqual(first, prompt_template_digest())
        self.assertEqual(first, sha256_digest(PROMPT_TEMPLATE.encode("utf-8")))

    def test_prompt_template_digest_is_the_sha256_of_the_utf8_bytes(self):
        expected = (
            "sha256:" + hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
        )
        self.assertEqual(prompt_template_digest(), expected)
        self.assertRegex(prompt_template_digest(), r"sha256:[0-9a-f]{64}")

    def test_rendered_prompt_replaces_the_placeholder_with_canonical_json(self):
        observation = _observation(used=2)
        rendered = render_prompt(observation)
        self.assertEqual(
            rendered,
            FROZEN_PROMPT.replace("{{observation_json}}", canonical_json(observation)),
        )
        self.assertIn('{"grants":["move","pick","place"]', rendered)
        self.assertNotIn("{{observation_json}}", rendered)

    def test_rendering_is_stable_across_repeated_calls(self):
        first = render_prompt(_observation())
        second = render_prompt(_observation())
        self.assertEqual(first, second)
        self.assertEqual(first.encode("utf-8"), first.encode("utf-8"))


class TestFrozenToolSchema(unittest.TestCase):
    """T4A-04: one common JSON tool schema; single-call allowance is exact."""

    def test_schema_has_exactly_the_frozen_content(self):
        self.assertEqual(
            TOOL_SCHEMA,
            {
                "schema_version": 1,
                "max_tool_calls": 1,
                "parallel_tool_calls": False,
                "tools": [
                    {
                        "name": "move",
                        "description": "Move one cardinal grid step.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["direction"],
                            "properties": {
                                "direction": {
                                    "type": "string",
                                    "enum": ["north", "east", "south", "west"],
                                }
                            },
                        },
                    },
                    {
                        "name": "pick",
                        "description": "Pick the named item from the named shelf.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["shelf_id", "item_id"],
                            "properties": {
                                "shelf_id": {
                                    "type": "string",
                                    "pattern": "^shelf-(0[0-9])$",
                                },
                                "item_id": {
                                    "type": "string",
                                    "pattern": "^item-(0[0-9])$",
                                },
                            },
                        },
                    },
                    {
                        "name": "place",
                        "description": "Place the carried item into the named order.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["order_id"],
                            "properties": {
                                "order_id": {
                                    "type": "string",
                                    "pattern": "^order-(0[0-9])$",
                                },
                            },
                        },
                    },
                ],
            },
        )

    def test_schema_allows_exactly_one_non_parallel_tool_call(self):
        self.assertEqual(TOOL_SCHEMA["max_tool_calls"], 1)
        self.assertIs(TOOL_SCHEMA["parallel_tool_calls"], False)
        self.assertEqual(len(TOOL_SCHEMA["tools"]), 3)

    def test_tool_schema_digest_is_the_canonical_json_digest(self):
        expected = sha256_digest(canonical_json(TOOL_SCHEMA).encode("utf-8"))
        self.assertEqual(tool_schema_digest(), expected)
        self.assertRegex(tool_schema_digest(), r"sha256:[0-9a-f]{64}")


class TestGoldenDigestVectors(unittest.TestCase):
    """T4A-04: golden digests pin the frozen bytes against any drift."""

    GOLDEN_PROMPT_DIGEST = (
        "sha256:3300463634070efc2d30f915019612a6a99b92a13cc394401a1b9dbcbb831163"
    )
    GOLDEN_TOOL_SCHEMA_DIGEST = (
        "sha256:1dfd69cb33d19e6b24f3e9129f0dbd72e7c0fc9cd6df2ae6e76e2fce0b6b42c4"
    )
    GOLDEN_MANIFEST_DIGEST = (
        "sha256:89fbce88c3b98dcc6ec915a86de8c89b088667b8ef915112b458a19b61eef97c"
    )

    def test_golden_prompt_template_digest(self):
        self.assertEqual(prompt_template_digest(), self.GOLDEN_PROMPT_DIGEST)

    def test_golden_tool_schema_digest(self):
        self.assertEqual(tool_schema_digest(), self.GOLDEN_TOOL_SCHEMA_DIGEST)

    def test_golden_policy_manifest_digest_for_the_pinned_fake_identity(self):
        self.assertEqual(
            llm_policy_digest("fake", "fake-v1"), self.GOLDEN_MANIFEST_DIGEST
        )


class TestLLMPolicyIdentity(unittest.TestCase):
    """T4A-04/P4A-REQ-03: digest of the exact six-key manifest; no extras."""

    def test_policy_id_is_llm_v0(self):
        self.assertEqual(POLICY_ID, "llm-v0")
        self.assertEqual(_policy().policy_id, "llm-v0")

    def test_policy_digest_is_the_digest_of_the_exact_manifest(self):
        manifest = {
            "implementation_version": "1",
            "model": "fake-v1",
            "policy_id": "llm-v0",
            "prompt_template_digest": prompt_template_digest(),
            "provider": "fake",
            "tool_schema_digest": tool_schema_digest(),
        }
        self.assertEqual(
            _policy().policy_digest,
            sha256_digest(canonical_json(manifest).encode("utf-8")),
        )
        self.assertEqual(_policy().policy_digest, llm_policy_digest("fake", "fake-v1"))

    def test_digest_excludes_everything_but_the_manifest(self):
        # Same pinned identity -> same digest regardless of adapter instance.
        first = LLMPolicy(FakeProvider()).policy_digest
        second = LLMPolicy(FakeProvider(script=[])).policy_digest
        self.assertEqual(first, second)

    def test_provider_and_model_enter_the_digest(self):
        other_model = llm_policy_digest("fake", "model-b")
        other_provider = llm_policy_digest("openai", "fake-v1")
        base = llm_policy_digest("fake", "fake-v1")
        self.assertNotEqual(base, other_model)
        self.assertNotEqual(base, other_provider)


class TestLLMPolicyNormalization(unittest.TestCase):
    """T4A-05: one request, one adapter call, exact normalization semantics."""

    def test_valid_move_becomes_one_toolcall_with_step_used_plus_one(self):
        script = [
            _success(
                {"tool": "move", "arguments": {"direction": "north"}},
                "request-3",
                raw="move north",
            )
        ]
        candidate = _policy(*script).decide(_observation(used=2))
        parse = normalize_tool_call(candidate)
        self.assertTrue(parse.ok)
        call = parse.tool_call
        assert call is not None
        self.assertEqual(call.schema_version, 1)
        self.assertEqual(call.tool, "move")
        self.assertEqual(dict(call.arguments), {"direction": "north"})
        self.assertEqual(
            call.operation_key,
            f"op/{LOGICAL_RUN_ID}/step-3/intent-llm-move-north",
        )
        self.assertTrue(validate_operation_key(call.operation_key, LOGICAL_RUN_ID))

    def test_valid_pick_and_place_intents_follow_the_frozen_labels(self):
        pick_parse = normalize_tool_call(
            _policy(
                _success(
                    {
                        "tool": "pick",
                        "arguments": {"shelf_id": "shelf-03", "item_id": "item-04"},
                    },
                    "request-3",
                )
            ).decide(_observation(used=2))
        )
        self.assertTrue(pick_parse.ok)
        pick = pick_parse.tool_call
        assert pick is not None
        self.assertEqual(
            pick.operation_key,
            f"op/{LOGICAL_RUN_ID}/step-3/intent-llm-pick-shelf-03",
        )
        self.assertEqual(
            dict(pick.arguments), {"shelf_id": "shelf-03", "item_id": "item-04"}
        )
        place_parse = normalize_tool_call(
            _policy(
                _success(
                    {"tool": "place", "arguments": {"order_id": "order-03"}},
                    "request-4",
                )
            ).decide(_observation(used=3))
        )
        self.assertTrue(place_parse.ok)
        place = place_parse.tool_call
        assert place is not None
        self.assertEqual(
            place.operation_key,
            f"op/{LOGICAL_RUN_ID}/step-4/intent-llm-place-order-03",
        )
        self.assertEqual(dict(place.arguments), {"order_id": "order-03"})

    def test_request_preserves_the_observation_and_frozen_prompt_schema(self):
        observation = _observation(used=2)
        policy = _policy(
            _success({"tool": "move", "arguments": {"direction": "north"}}, "request-3")
        )
        policy.decide(observation)
        sent = policy._adapter.requests[0]
        self.assertEqual(sent.observation, observation)
        self.assertEqual(sent.prompt, render_prompt(observation))
        self.assertEqual(sent.tool_schema, TOOL_SCHEMA)
        self.assertEqual(sent.request_id, "request-3")
        self.assertEqual(sent.provider, "fake")
        self.assertEqual(sent.model, "fake-v1")

    def test_exactly_one_adapter_call_and_no_repair_call(self):
        script = [_error_result("no_tool_call", "prose", "request-3")]
        policy = _policy(*script)
        policy.decide(_observation(used=2))
        self.assertEqual(len(policy._adapter.requests), 1)
        self.assertEqual(len(policy_decisions(policy)), 1)

    def _rejection_reason(self, script, used=2):
        policy = _policy(*script)
        candidate = policy.decide(_observation(used=used))
        assert isinstance(candidate, dict)
        parse = normalize_tool_call(candidate)
        self.assertFalse(parse.ok)
        return parse.reason, candidate["operation_key"], candidate

    def test_error_result_rejects_as_malformed_call_with_llm_reject_key(self):
        reason, key, _ = self._rejection_reason(
            [_error_result("no_tool_call", "prose", "request-8")], used=7
        )
        self.assertEqual(reason, "malformed_call")
        self.assertEqual(key, f"op/{LOGICAL_RUN_ID}/step-8/intent-llm-reject")

    def test_absent_tool_call_via_error_rejects_deterministically(self):
        reason, key, _ = self._rejection_reason(
            [_error_result("transport_error", "down", "request-3")]
        )
        self.assertEqual(reason, "malformed_call")
        self.assertEqual(key, f"op/{LOGICAL_RUN_ID}/step-3/intent-llm-reject")

    def test_unsupported_tool_name_becomes_unsupported_tool(self):
        reason, key, candidate = self._rejection_reason(
            [_success({"tool": "assemble", "arguments": {}}, "request-3")]
        )
        self.assertEqual(reason, "unsupported_tool")
        self.assertEqual(key, f"op/{LOGICAL_RUN_ID}/step-3/intent-llm-reject")
        self.assertNotIn("assemble", repr(candidate["arguments"]))

    def test_wrong_argument_keys_or_values_become_invalid_arguments(self):
        reason, key, _ = self._rejection_reason(
            [_success({"tool": "move", "arguments": {"direction": "up"}}, "request-3")]
        )
        self.assertEqual(reason, "invalid_arguments")
        reason, key, _ = self._rejection_reason(
            [
                _success(
                    {
                        "tool": "pick",
                        "arguments": {"shelf_id": "shelf-3", "item_id": "item-04"},
                    },
                    "request-3",
                )
            ]
        )
        self.assertEqual(reason, "invalid_arguments")
        reason, key, _ = self._rejection_reason(
            [
                _success(
                    {"tool": "move", "arguments": {"direction": "north", "extra": 1}},
                    "request-3",
                )
            ]
        )
        self.assertEqual(reason, "invalid_arguments")

    def test_multiple_tool_calls_reported_by_the_adapter_reject_as_malformed(self):
        # A multi-call provider response is folded into the deterministic
        # multiple_tool_calls error by the adapter layer; the policy treats it
        # like every provider error: malformed_call, one consumed attempt.
        reason, key, _ = self._rejection_reason(
            [_error_result("multiple_tool_calls", "two calls", "request-3")]
        )
        self.assertEqual(reason, "malformed_call")
        self.assertEqual(key, f"op/{LOGICAL_RUN_ID}/step-3/intent-llm-reject")

    def test_rejection_candidates_carry_no_provider_payload(self):
        _, _, candidate = self._rejection_reason(
            [_success({"tool": "assemble", "arguments": {}}, "request-3")]
        )
        flat = repr(candidate)
        self.assertNotIn("raw_visible_response", flat)
        self.assertNotIn("choosing", flat)
        self.assertNotIn("usage", flat)

    def test_decide_does_not_mutate_the_observation(self):
        observation = _observation(used=2)
        frozen = {
            key: (dict(value) if isinstance(value, dict) else value)
            for key, value in observation.items()
        }
        _policy(
            _success({"tool": "move", "arguments": {"direction": "north"}}, "request-3")
        ).decide(observation)
        self.assertEqual(observation, frozen)

    def test_decisions_are_logged_in_order_for_external_evidence(self):
        policy = _policy(
            _success(
                {"tool": "move", "arguments": {"direction": "north"}}, "request-1"
            ),
            _error_result("no_tool_call", "prose", "request-2"),
        )
        policy.decide(_observation(used=0))
        policy.decide(_observation(used=1))
        records = policy_decisions(policy)
        self.assertEqual([r.policy_step for r in records], [1, 2])
        self.assertEqual([r.request_id for r in records], ["request-1", "request-2"])
        self.assertIsNone(records[1].result.tool_call)


def policy_decisions(policy):
    return policy.decisions


if __name__ == "__main__":
    unittest.main()
