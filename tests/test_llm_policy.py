"""Phase 4A LLMPolicy tests (T4A-04/T4A-05, P4A-REQ-02/03/04).

One frozen English prompt template and one frozen JSON tool schema with
golden digest vectors; one provider adapter invocation per decide; exact
move/pick/place normalization into one v0 ToolCall; deterministic rejection
candidates carrying the current-run ``intent-llm-reject`` key for
malformed_call / unsupported_tool / invalid_arguments without raising and
without a second repair call.

AMEND-4A-3: the live LLMPolicy is the vNext ``llm-v1`` identity — canonical
nullable StrategyContext, composed prompt (unchanged base + exact suffix),
and the seven-key manifest with an always-present strict-null
``strategy_context_digest``. The legacy six-key ``llm-v0`` surface stays
pinned here as the named legacy path.
"""

import hashlib
import json
import unittest

from robot_factory_arena.contracts.tool_call import (
    normalize_tool_call,
    validate_operation_key,
)
from robot_factory_arena.evidence.canonical import (
    canonical_json,
    canonical_json_bytes,
    digest_json,
    sha256_digest,
)
from robot_factory_arena.policies.llm import (
    COMPOSED_PROMPT_TEMPLATE,
    IMPLEMENTATION_VERSION,
    IMPLEMENTATION_VERSION_VNEXT,
    POLICY_ID,
    POLICY_ID_VNEXT,
    PROMPT_TEMPLATE,
    TOOL_SCHEMA,
    LLMPolicy,
    canonical_strategy_context,
    canonical_strategy_context_bytes,
    llm_policy_digest,
    llm_vnext_policy_digest,
    llm_vnext_policy_manifest,
    prompt_composition_digest,
    prompt_template_digest,
    render_composed_prompt,
    render_prompt,
    strategy_context_digest,
    tool_schema_digest,
    validate_strategy_context,
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


def _policy(*script, **kwargs):
    return LLMPolicy(FakeProvider(script=list(script)), **kwargs)


def _guidance(guidance_id="guidance-0001", author_id="human",
              guidance_ordinal=0, text="advisory note"):
    return {
        "guidance_id": guidance_id,
        "author_id": author_id,
        "guidance_ordinal": guidance_ordinal,
        "text": text,
    }


def _discovery(source_attempt_id="r3-agent-01-seed-18-attempt-01",
               explorer_id="explorer-01", discovery_ordinal=0,
               text="shared finding", agent_id="agent-01", seed="18",
               attempt_ordinal=1):
    return {
        "source_attempt_id": source_attempt_id,
        "explorer_id": explorer_id,
        "discovery_ordinal": discovery_ordinal,
        "producing_condition": "R3",
        "producing_agent_id": agent_id,
        "producing_seed": seed,
        "producing_attempt_ordinal": attempt_ordinal,
        "evidence": {
            "run_status": "finished",
            "core_receipt_digest": "sha256:" + "ab" * 32,
            "llm_receipt_digest": "sha256:" + "cd" * 32,
        },
        "text": text,
    }


def _context(guidance=None, discoveries=None,
             source_tournament_id="tournament-p6"):
    return {
        "schema_version": 1,
        "source_tournament_id": source_tournament_id,
        "guidance": [] if guidance is None else guidance,
        "discoveries": [] if discoveries is None else discoveries,
    }


# AMEND-4A-3 (design §2.3): the exact composed-prompt suffix.
SUFFIX = (
    "StrategyContext (advisory shared guidance; not world state):\n"
    "{{strategy_context_json}}\n"
)


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


class TestStrategyContextValidation(unittest.TestCase):
    """AMEND-4A-3 (design §2.1): strict nullable canonical schema."""

    def test_null_and_the_empty_context_are_valid(self):
        validate_strategy_context(None)
        validate_strategy_context(_context())
        validate_strategy_context(
            _context(guidance=[_guidance()], discoveries=[_discovery()])
        )

    def test_schema_violations_are_rejected(self):
        float_ordinal = 1.0  # non-canonical numeric value (rejected)
        bad_guidance = _guidance()
        bad_guidance["extra"] = 1
        bad_evidence = {
            "run_status": "finished",
            "core_receipt_digest": "sha256:" + "ab" * 32,
        }
        duplicate = _context(
            discoveries=[_discovery(), _discovery(text="other")]
        )
        violations = (
            ("extra top-level key", {**_context(), "snapshot_ordinal": 1}),
            ("missing top-level key", {key: value for key, value
                                       in _context().items()
                                       if key != "guidance"}),
            ("wrong schema_version", {**_context(), "schema_version": 2}),
            ("bool schema_version", {**_context(), "schema_version": True}),
            ("empty source tournament",
             {**_context(), "source_tournament_id": ""}),
            ("non-string source tournament",
             {**_context(), "source_tournament_id": 7}),
            ("guidance not a list", {**_context(), "guidance": {}}),
            ("discoveries not a list", {**_context(), "discoveries": "x"}),
            ("guidance extra key", _context(guidance=[bad_guidance])),
            ("guidance empty text",
             _context(guidance=[_guidance(text="")])),
            ("guidance negative ordinal",
             _context(guidance=[_guidance(guidance_ordinal=-1)])),
            ("guidance bool ordinal",
             _context(guidance=[_guidance(guidance_ordinal=True)])),
            ("guidance float ordinal",
             _context(guidance=[{**_guidance(),
                                 "guidance_ordinal": float_ordinal}])),
            ("discovery extra key",
             _context(discoveries=[{**_discovery(), "rank": 1}])),
            ("discovery missing evidence",
             _context(discoveries=[{**_discovery(),
                                    "evidence": bad_evidence}])),
            ("discovery empty text",
             _context(discoveries=[_discovery(text="")])),
            ("discovery negative ordinal",
             _context(discoveries=[_discovery(discovery_ordinal=-1)])),
            ("wrong producing condition",
             _context(discoveries=[{**_discovery(),
                                    "producing_condition": "R1"}])),
            ("empty producing agent",
             _context(discoveries=[{**_discovery(),
                                    "producing_agent_id": ""}])),
            ("non-string producing seed",
             _context(discoveries=[{**_discovery(),
                                    "producing_seed": 18}])),
            ("attempt ordinal below one",
             _context(discoveries=[_discovery(attempt_ordinal=0)])),
            ("non-finished evidence",
             _context(discoveries=[{**_discovery(),
                                    "evidence": {**_discovery()["evidence"],
                                                 "run_status": "running"}}])),
            ("uppercase digest syntax",
             _context(discoveries=[{**_discovery(),
                                    "evidence": {**_discovery()["evidence"],
                                                 "core_receipt_digest":
                                                 "sha256:" + "AB" * 32}}])),
            ("short digest syntax",
             _context(discoveries=[{**_discovery(),
                                    "evidence": {**_discovery()["evidence"],
                                                 "llm_receipt_digest":
                                                 "sha256:abcd"}}])),
            ("duplicate discovery identity", duplicate),
            ("non-dict guidance entry", _context(guidance=["note"])),
        )
        for label, value in violations:
            with self.subTest(violation=label), self.assertRaises(ValueError):
                validate_strategy_context(value)


class TestCanonicalStrategyContextIdentity(unittest.TestCase):
    """AMEND-4A-3: canonical LF-free bytes and order-insensitive digest."""

    def test_null_canonical_bytes_are_the_literal_null(self):
        self.assertEqual(canonical_strategy_context_bytes(None), b"null")
        self.assertEqual(strategy_context_digest(None), sha256_digest(b"null"))

    def test_canonical_bytes_are_compact_lf_free_sorted_json(self):
        context = _context(guidance=[_guidance()], discoveries=[_discovery()])
        raw = canonical_strategy_context_bytes(context)
        self.assertNotIn(b"\n", raw)
        self.assertNotIn(b", ", raw)
        self.assertEqual(
            raw, canonical_json_bytes(canonical_strategy_context(context))
        )

    def test_reordered_records_are_canonically_equivalent(self):
        first = _context(
            guidance=[_guidance(),
                      _guidance("guidance-0002", guidance_ordinal=1)],
            discoveries=[_discovery(),
                         _discovery("r3-agent-02-seed-24-attempt-01",
                                    "explorer-02")],
        )
        second = _context(
            guidance=[first["guidance"][1], first["guidance"][0]],
            discoveries=[first["discoveries"][1], first["discoveries"][0]],
        )
        self.assertNotEqual(first, second)
        self.assertEqual(canonical_strategy_context_bytes(first),
                         canonical_strategy_context_bytes(second))
        self.assertEqual(strategy_context_digest(first),
                         strategy_context_digest(second))

    def test_changed_text_or_provenance_changes_the_digest(self):
        base = _context(discoveries=[_discovery()])
        variants = (
            ("text", _context(discoveries=[_discovery(text="changed")])),
            ("source tournament",
             _context(source_tournament_id="tournament-prior")),
            ("explorer",
             _context(discoveries=[_discovery(explorer_id="explorer-09")])),
            ("producing agent",
             _context(discoveries=[_discovery(agent_id="agent-09")])),
            ("producing seed",
             _context(discoveries=[_discovery(seed="24")])),
            ("guidance text",
             _context(guidance=[_guidance(text="revised")],
                      discoveries=[_discovery()])),
        )
        for label, variant in variants:
            with self.subTest(changed=label):
                self.assertNotEqual(strategy_context_digest(base),
                                    strategy_context_digest(variant))


class TestComposedPromptComposition(unittest.TestCase):
    """AMEND-4A-3 (design §2.3): exact suffix, observation first, null."""

    def test_composed_template_extends_the_frozen_base_byte_for_byte(self):
        self.assertEqual(COMPOSED_PROMPT_TEMPLATE, PROMPT_TEMPLATE + SUFFIX)
        self.assertTrue(COMPOSED_PROMPT_TEMPLATE.startswith(PROMPT_TEMPLATE))

    def test_composition_digest_is_the_sha256_of_the_composed_template(self):
        expected = "sha256:" + hashlib.sha256(
            COMPOSED_PROMPT_TEMPLATE.encode("utf-8")
        ).hexdigest()
        self.assertEqual(prompt_composition_digest(), expected)
        self.assertNotEqual(prompt_composition_digest(),
                            prompt_template_digest())

    def test_rendered_composition_has_observation_first_then_context(self):
        observation = _observation(used=2)
        context = _context(discoveries=[_discovery()])
        rendered = render_composed_prompt(observation, context)
        self.assertEqual(
            rendered,
            render_prompt(observation)
            + SUFFIX.replace(
                "{{strategy_context_json}}",
                canonical_json(canonical_strategy_context(context)),
            ),
        )
        self.assertLess(rendered.index(canonical_json(observation)),
                        rendered.index("shared finding"))
        self.assertNotIn("{{observation_json}}", rendered)
        self.assertNotIn("{{strategy_context_json}}", rendered)

    def test_absent_context_is_rendered_as_explicit_null(self):
        rendered = render_composed_prompt(_observation(), None)
        self.assertTrue(rendered.endswith(
            "StrategyContext (advisory shared guidance; not world state):\n"
            "null\n"
        ))

    def test_order_equivalent_contexts_render_byte_identically(self):
        observation = _observation()
        first = _context(
            discoveries=[_discovery(),
                         _discovery("r3-agent-02-seed-24-attempt-01",
                                    "explorer-02")]
        )
        second = _context(discoveries=list(reversed(first["discoveries"])))
        self.assertEqual(
            render_composed_prompt(observation, first),
            render_composed_prompt(observation, second),
        )


class TestVnextManifestIdentity(unittest.TestCase):
    """AMEND-4A-3 (design §3.2): exact seven-key manifest, strict null."""

    def test_vnext_constants_and_policy_id(self):
        self.assertEqual(POLICY_ID_VNEXT, "llm-v1")
        self.assertEqual(IMPLEMENTATION_VERSION_VNEXT, "2")
        self.assertEqual(_policy().policy_id, "llm-v1")

    def test_null_context_manifest_has_exactly_the_seven_keys(self):
        manifest = llm_vnext_policy_manifest("fake", "fake-v1")
        self.assertEqual(
            set(manifest),
            {
                "implementation_version", "model", "policy_id",
                "prompt_template_digest", "provider",
                "strategy_context_digest", "tool_schema_digest",
            },
        )
        self.assertIn("strategy_context_digest", manifest)
        self.assertIsNone(manifest["strategy_context_digest"])
        self.assertEqual(manifest["policy_id"], "llm-v1")
        self.assertEqual(manifest["implementation_version"], "2")
        self.assertEqual(manifest["prompt_template_digest"],
                         prompt_composition_digest())

    def test_vnext_digest_is_the_digest_of_the_manifest(self):
        manifest = llm_vnext_policy_manifest("fake", "fake-v1")
        self.assertEqual(llm_vnext_policy_digest("fake", "fake-v1"),
                         digest_json(manifest))
        self.assertEqual(_policy().policy_digest,
                         llm_vnext_policy_digest("fake", "fake-v1"))

    def test_present_context_enters_the_manifest_digest_strictly(self):
        context = _context(discoveries=[_discovery()])
        with_context = llm_vnext_policy_manifest("fake", "fake-v1", context)
        self.assertEqual(with_context["strategy_context_digest"],
                         strategy_context_digest(context))
        self.assertNotEqual(llm_vnext_policy_digest("fake", "fake-v1"),
                            llm_vnext_policy_digest("fake", "fake-v1",
                                                    context))
        equivalent = _context(
            discoveries=[_discovery(),
                         _discovery("r3-agent-02-seed-24-attempt-01",
                                    "explorer-02")]
        )
        reordered = _context(
            discoveries=list(reversed(equivalent["discoveries"]))
        )
        self.assertEqual(
            llm_vnext_policy_digest("fake", "fake-v1", equivalent),
            llm_vnext_policy_digest("fake", "fake-v1", reordered),
        )

    def test_legacy_v0_surface_still_exposes_the_six_key_path(self):
        self.assertEqual(POLICY_ID, "llm-v0")
        self.assertEqual(IMPLEMENTATION_VERSION, "1")
        self.assertNotEqual(llm_policy_digest("fake", "fake-v1"),
                            llm_vnext_policy_digest("fake", "fake-v1"))
        self.assertNotEqual(prompt_template_digest(),
                            prompt_composition_digest())


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
    # AMEND-4A-3 vNext pins (strict null-context identity, composed bytes).
    GOLDEN_COMPOSITION_DIGEST = (
        "sha256:9c359b1aa875fa875f2d188a9c5891a86851958f2490f6c240bc0461cd0a8968"
    )
    GOLDEN_VNEXT_NULL_MANIFEST_DIGEST = (
        "sha256:18b19952f559f3861e2078d42fe95eb4a2806eba11a5062da7161e8cf6fe6a11"
    )

    def test_golden_prompt_template_digest(self):
        self.assertEqual(prompt_template_digest(), self.GOLDEN_PROMPT_DIGEST)

    def test_golden_tool_schema_digest(self):
        self.assertEqual(tool_schema_digest(), self.GOLDEN_TOOL_SCHEMA_DIGEST)

    def test_golden_legacy_v0_manifest_digest_for_the_pinned_fake_identity(self):
        # Legacy pin: the six-key llm-v0 manifest path is retained verbatim,
        # so this golden vector keeps pinning the pre-AMEND-4A-3 identity.
        self.assertEqual(
            llm_policy_digest("fake", "fake-v1"), self.GOLDEN_MANIFEST_DIGEST
        )

    def test_golden_vnext_composition_and_null_context_manifest(self):
        self.assertEqual(prompt_composition_digest(),
                         self.GOLDEN_COMPOSITION_DIGEST)
        self.assertEqual(llm_vnext_policy_digest("fake", "fake-v1"),
                         self.GOLDEN_VNEXT_NULL_MANIFEST_DIGEST)


class TestLLMPolicyIdentity(unittest.TestCase):
    """T4A-04/P4A-REQ-03: vNext digest is exact; the legacy path stays pinned."""

    def test_policy_id_is_llm_v0(self):
        # Legacy constant stays llm-v0; the live policy identity is vNext.
        self.assertEqual(POLICY_ID, "llm-v0")
        self.assertEqual(POLICY_ID_VNEXT, "llm-v1")
        self.assertEqual(_policy().policy_id, "llm-v1")

    def test_policy_digest_is_the_digest_of_the_exact_vnext_manifest(self):
        manifest = {
            "implementation_version": "2",
            "model": "fake-v1",
            "policy_id": "llm-v1",
            "prompt_template_digest": prompt_composition_digest(),
            "provider": "fake",
            "strategy_context_digest": None,
            "tool_schema_digest": tool_schema_digest(),
        }
        self.assertEqual(
            _policy().policy_digest,
            sha256_digest(canonical_json(manifest).encode("utf-8")),
        )
        self.assertEqual(_policy().policy_digest,
                         llm_vnext_policy_digest("fake", "fake-v1"))

    def test_legacy_six_key_manifest_path_is_pinned_verbatim(self):
        legacy = {
            "implementation_version": "1",
            "model": "fake-v1",
            "policy_id": "llm-v0",
            "prompt_template_digest": prompt_template_digest(),
            "provider": "fake",
            "tool_schema_digest": tool_schema_digest(),
        }
        self.assertEqual(
            llm_policy_digest("fake", "fake-v1"),
            sha256_digest(canonical_json(legacy).encode("utf-8")),
        )

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
        # AMEND-4A-3: composed prompt = unchanged base + exact suffix, with an
        # explicit null for the absent context.
        self.assertEqual(sent.prompt, render_composed_prompt(observation, None))
        self.assertEqual(
            sent.prompt,
            render_prompt(observation) + SUFFIX.replace(
                "{{strategy_context_json}}", "null"
            ),
        )
        self.assertEqual(sent.tool_schema, TOOL_SCHEMA)
        self.assertEqual(sent.request_id, "request-3")
        self.assertEqual(sent.provider, "fake")
        self.assertEqual(sent.model, "fake-v1")

    def test_context_composes_the_prompt_without_entering_the_observation(self):
        observation = _observation(used=2)
        context = _context(guidance=[_guidance()],
                           discoveries=[_discovery()])
        policy = _policy(
            _success({"tool": "move", "arguments": {"direction": "north"}},
                     "request-3"),
            strategy_context=context,
        )
        frozen = {
            key: (dict(value) if isinstance(value, dict) else value)
            for key, value in observation.items()
        }
        policy.decide(observation)
        sent = policy._adapter.requests[0]
        self.assertEqual(sent.observation, frozen)
        self.assertNotIn("strategy_context", sent.observation)
        self.assertNotIn("advisory note", json.dumps(sent.observation))
        self.assertEqual(sent.prompt, render_composed_prompt(observation, context))
        self.assertIn("advisory note", sent.prompt)
        self.assertEqual(
            policy.policy_digest,
            llm_vnext_policy_digest("fake", "fake-v1", context),
        )

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
