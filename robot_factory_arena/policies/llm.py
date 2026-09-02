"""LLMPolicy: the frozen Phase-4A policy adapter over one provider (P4A-REQ-02..04).

Implements the existing ``Policy`` protocol exactly: ``policy_id``,
``policy_digest``, and ``decide(observation) -> candidate``. ``decide``
receives only the serialized v0 observation, renders the FROZEN English prompt
with the canonical JSON of that observation, builds exactly one
``ProviderRequest``, invokes the pinned adapter EXACTLY once (never a second
repair call), and returns at most one normalized v0 candidate.

Normalization (P4A-REQ-02) is deterministic and delegates final argument
validation to the frozen contract authority (``contracts.tool_call``):

- provider ``error`` (including adapter-folded ``multiple_tool_calls``) or an
  absent tool call -> ``malformed_call`` rejection;
- unsupported tool names -> ``unsupported_tool`` rejection;
- wrong argument keys/values -> ``invalid_arguments`` rejection;
- a legal move/pick/place -> one ``ToolCall`` at step ``used + 1`` with intent
  labels ``llm-move-D`` / ``llm-pick-S`` / ``llm-place-O``.

Rejection candidates always carry the current-run key with intent
``llm-reject`` and canonical-safe arguments (``{}``), so no raw provider
payload ever crosses into the core boundary (the runner's parse-rejection
ledger path canonicalizes candidate arguments). The provider result never
enters the returned candidate; it is retained as external evidence on the
policy instance (``decisions``) for the scrubbed sidecar export path.

The module imports ONLY the tool-call contract, the canonical digest
authority, and the provider request envelope (AST-enforced): no runtime,
verifier, world state, or mutable runtime-state access.
"""
from dataclasses import dataclass

from robot_factory_arena.contracts.tool_call import (
    expand_operation_key,
    normalize_tool_call,
)
from robot_factory_arena.evidence.canonical import (
    canonical_json,
    digest_json,
    sha256_digest,
)
from robot_factory_arena.providers.adapter import ProviderRequest, ProviderResult

POLICY_ID = "llm-v0"
IMPLEMENTATION_VERSION = "1"
INTENT_REJECT = "llm-reject"

# Exact supported-tool set (final validation is delegated to the contract).
_SUPPORTED_TOOLS = frozenset({"move", "pick", "place"})

# P4A-REQ-04: exactly one frozen English prompt template (LF endings).
PROMPT_TEMPLATE = (
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
    "Serialized observation:\n"
    "{{observation_json}}\n"
)

_PLACEHOLDER = "{{observation_json}}"

# P4A-REQ-04: exactly one frozen common JSON tool schema.
TOOL_SCHEMA = {
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
                    "shelf_id": {"type": "string", "pattern": "^shelf-(0[0-9])$"},
                    "item_id": {"type": "string", "pattern": "^item-(0[0-9])$"},
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
                    "order_id": {"type": "string", "pattern": "^order-(0[0-9])$"},
                },
            },
        },
    ],
}


def prompt_template_digest() -> str:
    """SHA-256 over the frozen template's UTF-8 bytes (P4A-REQ-03)."""
    return sha256_digest(PROMPT_TEMPLATE.encode("utf-8"))


def tool_schema_digest() -> str:
    """Canonical-JSON digest of the exact frozen tool-schema object."""
    return digest_json(TOOL_SCHEMA)


def render_prompt(observation) -> str:
    """Frozen template with the canonical JSON observation embedded."""
    return PROMPT_TEMPLATE.replace(_PLACEHOLDER, canonical_json(observation))


def llm_policy_digest(provider: str, model: str) -> str:
    """Digest of the exact six-key manifest (P4A-REQ-03); nothing else enters."""
    if not isinstance(provider, str) or not provider:
        raise TypeError("provider must be a non-empty string")
    if not isinstance(model, str) or not model:
        raise TypeError("model must be a non-empty string")
    return digest_json(
        {
            "implementation_version": IMPLEMENTATION_VERSION,
            "model": model,
            "policy_id": POLICY_ID,
            "prompt_template_digest": prompt_template_digest(),
            "provider": provider,
            "tool_schema_digest": tool_schema_digest(),
        }
    )


@dataclass(frozen=True)
class DecisionRecord:
    """One LLM decision attempt, retained for the external evidence path."""

    policy_step: int
    request_id: str
    result: ProviderResult


def _argument_key(tool: str) -> str:
    """The named v0 argument that labels the intent for this tool."""
    if tool == "move":
        return "direction"
    if tool == "pick":
        return "shelf_id"
    return "order_id"


def _intent_label(tool: str, named_value: str) -> str:
    """Frozen deterministic intent label (P4A-REQ-02 table)."""
    if tool == "move":
        return f"llm-move-{named_value}"
    if tool == "pick":
        return f"llm-pick-{named_value}"
    return f"llm-place-{named_value}"


class LLMPolicy:
    """One serialized observation in; at most one normalized v0 call out."""

    policy_id = POLICY_ID

    def __init__(self, adapter):
        provider = getattr(adapter, "provider", None)
        model = getattr(adapter, "model", None)
        if not isinstance(provider, str) or not provider:
            raise TypeError("adapter must pin a non-empty provider name")
        if not isinstance(model, str) or not model:
            raise TypeError("adapter must pin a non-empty model name")
        if not callable(getattr(adapter, "invoke", None)):
            raise TypeError("adapter must expose a callable invoke()")
        self._provider_name = provider
        self._model_name = model
        self._adapter = adapter
        self.policy_digest = llm_policy_digest(provider, model)
        self._decisions = []

    @property
    def provider(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def decisions(self):
        """The retained decision attempts, in order (external evidence)."""
        return tuple(self._decisions)

    def decide(self, observation) -> object:
        if not isinstance(observation, dict):
            raise TypeError("decide expects the serialized observation mapping")
        policy_step = observation["policy_steps"]["used"] + 1
        logical_run_id = observation["logical_run_id"]
        request_id = f"request-{policy_step}"
        request = ProviderRequest(
            provider=self._provider_name,
            model=self._model_name,
            request_id=request_id,
            observation=observation,
            prompt=render_prompt(observation),
            tool_schema=TOOL_SCHEMA,
        )
        result = self._adapter.invoke(request)
        self._decisions.append(DecisionRecord(policy_step, request_id, result))
        return self._normalize(result, logical_run_id, policy_step)

    def _normalize(self, result, logical_run_id: str, policy_step: int) -> object:
        reject_key = expand_operation_key(logical_run_id, policy_step, INTENT_REJECT)

        def malformed():
            # A candidate whose key set is not exactly the four contract keys:
            # normalize_tool_call assigns malformed_call deterministically while
            # the runner still extracts the current-run operation key.
            return {
                "tool": "move",
                "arguments": {},
                "operation_key": reject_key,
            }

        def rejected(tool):
            # The exact four contract keys with canonical-safe {} arguments:
            # the closed-taxonomy reason (unsupported_tool for unknown tool
            # names, invalid_arguments for wrong shapes) comes from the frozen
            # contract authority; no raw provider payload crosses.
            return {
                "schema_version": 1,
                "tool": tool,
                "arguments": {},
                "operation_key": reject_key,
            }

        if result.error is not None or result.tool_call is None:
            return malformed()
        tool = result.tool_call.tool
        arguments = result.tool_call.arguments
        if tool not in _SUPPORTED_TOOLS:
            return rejected(tool)
        named = arguments.get(_argument_key(tool))
        if not isinstance(named, str):
            return rejected(tool)
        intent_key = expand_operation_key(
            logical_run_id, policy_step, _intent_label(tool, named)
        )
        parse = normalize_tool_call(
            {
                "schema_version": 1,
                "tool": tool,
                "arguments": dict(arguments),
                "operation_key": intent_key,
            }
        )
        if parse.ok:
            return parse.tool_call
        return rejected(tool)
