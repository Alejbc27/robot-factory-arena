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

AMEND-4A-3 adds the vNext ``llm-v1`` identity: a canonical, nullable
``StrategyContext`` is a strict, digest-bound policy input. The unchanged
AMEND-4A-2 prompt rendering (``render_prompt``/``prompt_template_digest``,
retained as the pinned legacy v0 surface) composes with an exact
StrategyContext suffix; the observation and the ProviderRequest envelope
are unchanged. The seven-key vNext manifest always carries
``strategy_context_digest`` (JSON ``null`` when context is absent), so a
changed context changes ``policy_digest`` and, through the unchanged
RunConfig path, the ``logical_run_id``.

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

# AMEND-4A-3 vNext identity (the live policy; the v0 pair above stays as the
# pinned legacy surface for the Phase 4A legacy vectors).
POLICY_ID_VNEXT = "llm-v1"
IMPLEMENTATION_VERSION_VNEXT = "2"

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

_PLACEHOLDER = "{{observation_json}}"

# AMEND-4A-3 (design §2.3): the exact composed-prompt suffix. The base
# template above remains byte-for-byte unchanged and keeps its authority.
_STRATEGY_CONTEXT_PLACEHOLDER = "{{strategy_context_json}}"
STRATEGY_CONTEXT_SUFFIX = (
    "StrategyContext (advisory shared guidance; not world state):\n"
    "{{strategy_context_json}}\n"
)
COMPOSED_PROMPT_TEMPLATE = PROMPT_TEMPLATE + STRATEGY_CONTEXT_SUFFIX

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


# --- AMEND-4A-3 canonical StrategyContext (design §2.1) ---------------------

_STRATEGY_CONTEXT_KEYS = frozenset(
    {"schema_version", "source_tournament_id", "guidance", "discoveries"}
)
_GUIDANCE_KEYS = frozenset(
    {"guidance_id", "author_id", "guidance_ordinal", "text"}
)
_DISCOVERY_KEYS = frozenset(
    {
        "source_attempt_id",
        "explorer_id",
        "discovery_ordinal",
        "producing_condition",
        "producing_agent_id",
        "producing_seed",
        "producing_attempt_ordinal",
        "evidence",
        "text",
    }
)
_EVIDENCE_KEYS = frozenset(
    {"run_status", "core_receipt_digest", "llm_receipt_digest"}
)
_HEX_DIGITS = frozenset("0123456789abcdef")


def _is_canonical_sha256(value) -> bool:
    """Exact ``sha256:<64 lowercase hex>`` syntax (regex-free; no new imports)."""
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in _HEX_DIGITS for character in value[7:])
    )


def _require_non_empty_str(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"StrategyContext {label} must be a non-empty string")
    return value


def _require_ordinal(value, label: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"StrategyContext {label} must be an integer >= {minimum}"
        )
    return value


def _require_discovery(entry) -> dict:
    if not isinstance(entry, dict) or set(entry) != _DISCOVERY_KEYS:
        raise ValueError("discovery must have exactly the schema §2.1 keys")
    _require_non_empty_str(entry["source_attempt_id"], "source_attempt_id")
    _require_non_empty_str(entry["explorer_id"], "explorer_id")
    _require_ordinal(entry["discovery_ordinal"], "discovery_ordinal", 0)
    if entry["producing_condition"] != "R3":
        raise ValueError("discovery producing_condition must be exactly 'R3'")
    _require_non_empty_str(entry["producing_agent_id"], "producing_agent_id")
    _require_non_empty_str(entry["producing_seed"], "producing_seed")
    _require_ordinal(
        entry["producing_attempt_ordinal"], "producing_attempt_ordinal", 1
    )
    _require_non_empty_str(entry["text"], "discovery text")
    evidence = entry["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_KEYS:
        raise ValueError("discovery evidence must have exactly the §2.1 keys")
    if evidence["run_status"] != "finished":
        raise ValueError("discovery evidence must cite a finished run")
    for name in ("core_receipt_digest", "llm_receipt_digest"):
        if not _is_canonical_sha256(evidence[name]):
            raise ValueError(
                f"discovery evidence {name} must be sha256:<64 lowercase hex>"
            )
    return entry


def validate_strategy_context(strategy_context) -> None:
    """Strict §2.1 validation: JSON null, or the exact schema-versioned object.

    Rejects extra/missing keys at every level, wrong schema version, empty or
    non-string identities and text, bool/float/negative ordinals, non-R3
    producers, non-finished evidence, invalid digest syntax, duplicate
    discovery identities, and any non-deterministic field. Canonical JSON's
    integer-only rule rejects floats at serialization time as a second gate.
    """
    if strategy_context is None:
        return
    if not isinstance(strategy_context, dict):
        raise ValueError("StrategyContext must be JSON null or an object")
    if set(strategy_context) != _STRATEGY_CONTEXT_KEYS:
        raise ValueError("StrategyContext must have exactly the §2.1 keys")
    version = strategy_context["schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise ValueError("StrategyContext schema_version must be exactly 1")
    _require_non_empty_str(
        strategy_context["source_tournament_id"], "source_tournament_id"
    )
    if not isinstance(strategy_context["guidance"], list):
        raise ValueError("StrategyContext guidance must be a list")
    if not isinstance(strategy_context["discoveries"], list):
        raise ValueError("StrategyContext discoveries must be a list")
    guidance_identities = set()
    for entry in strategy_context["guidance"]:
        if not isinstance(entry, dict) or set(entry) != _GUIDANCE_KEYS:
            raise ValueError("guidance must have exactly the §2.1 keys")
        _require_non_empty_str(entry["guidance_id"], "guidance_id")
        _require_non_empty_str(entry["author_id"], "author_id")
        _require_ordinal(entry["guidance_ordinal"], "guidance_ordinal", 0)
        _require_non_empty_str(entry["text"], "guidance text")
        identity = (entry["guidance_ordinal"], entry["guidance_id"])
        if identity in guidance_identities:
            raise ValueError("guidance identities must be unique")
        guidance_identities.add(identity)
    discovery_identities = set()
    for entry in strategy_context["discoveries"]:
        _require_discovery(entry)
        identity = (
            entry["source_attempt_id"],
            entry["explorer_id"],
            entry["discovery_ordinal"],
        )
        if identity in discovery_identities:
            raise ValueError("discovery identities must be unique")
        discovery_identities.add(identity)


def canonical_strategy_context(strategy_context):
    """Validated, defensively copied context in canonical record order.

    Guidance is ordered by ``(guidance_ordinal, guidance_id)`` and discoveries
    by ``(source_attempt_id, explorer_id, discovery_ordinal)``, independent of
    arrival order; equal contents therefore always yield equal canonical bytes.
    """
    validate_strategy_context(strategy_context)
    if strategy_context is None:
        return None
    context = dict(strategy_context)
    context["guidance"] = [
        dict(entry)
        for entry in sorted(
            context["guidance"],
            key=lambda entry: (entry["guidance_ordinal"], entry["guidance_id"]),
        )
    ]
    context["discoveries"] = [
        {**entry, "evidence": dict(entry["evidence"])}
        for entry in sorted(
            context["discoveries"],
            key=lambda entry: (
                entry["source_attempt_id"],
                entry["explorer_id"],
                entry["discovery_ordinal"],
            ),
        )
    ]
    return context


def canonical_strategy_context_bytes(strategy_context) -> bytes:
    """LF-free compact canonical JSON bytes (``b"null"`` when absent)."""
    return canonical_json(canonical_strategy_context(strategy_context)).encode(
        "utf-8"
    )


def strategy_context_digest(strategy_context) -> str:
    """SHA-256 over the exact canonical context bytes (null stays explicit)."""
    return sha256_digest(canonical_strategy_context_bytes(strategy_context))


def render_prompt(observation) -> str:
    """Frozen template with the canonical JSON observation embedded."""
    return PROMPT_TEMPLATE.replace(_PLACEHOLDER, canonical_json(observation))


def prompt_composition_digest() -> str:
    """SHA-256 over the exact composed template's UTF-8 bytes (AMEND-4A-3)."""
    return sha256_digest(COMPOSED_PROMPT_TEMPLATE.encode("utf-8"))


def render_composed_prompt(observation, strategy_context=None) -> str:
    """Unchanged base rendering + the canonical context suffix.

    The canonical observation comes first, the canonical context second;
    an absent context renders as the explicit JSON ``null``.
    """
    context = canonical_strategy_context(strategy_context)
    return render_prompt(observation) + STRATEGY_CONTEXT_SUFFIX.replace(
        _STRATEGY_CONTEXT_PLACEHOLDER, canonical_json(context)
    )


def _require_identity(provider, model) -> None:
    if not isinstance(provider, str) or not provider:
        raise TypeError("provider must be a non-empty string")
    if not isinstance(model, str) or not model:
        raise TypeError("model must be a non-empty string")


def llm_policy_digest(provider: str, model: str) -> str:
    """LEGACY v0: digest of the exact six-key manifest; pinned Phase 4A path.

    Retained verbatim under AMEND-4A-3 so the pinned legacy vectors keep
    binding the pre-amendment ``llm-v0`` identity; the live policy identity is
    :func:`llm_vnext_policy_digest`.
    """
    _require_identity(provider, model)
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


def llm_vnext_policy_manifest(provider, model, strategy_context=None) -> dict:
    """The exact seven-key vNext manifest (AMEND-4A-3, design §3.2).

    ``strategy_context_digest`` is ALWAYS present — strict semantics: JSON
    ``null`` when the context is absent, never an omitted key.
    """
    _require_identity(provider, model)
    return {
        "implementation_version": IMPLEMENTATION_VERSION_VNEXT,
        "model": model,
        "policy_id": POLICY_ID_VNEXT,
        "prompt_template_digest": prompt_composition_digest(),
        "provider": provider,
        # Strict semantics (design §3.2): ALWAYS present, JSON null when the
        # context is absent; a present context contributes the SHA-256 digest
        # of its exact canonical bytes.
        "strategy_context_digest": (
            None
            if strategy_context is None
            else strategy_context_digest(strategy_context)
        ),
        "tool_schema_digest": tool_schema_digest(),
    }


def llm_vnext_policy_digest(provider, model, strategy_context=None) -> str:
    """Digest of the exact seven-key vNext manifest; a changed context changes
    this digest and, through the unchanged RunConfig path, the logical ID."""
    return digest_json(
        llm_vnext_policy_manifest(provider, model, strategy_context)
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
    """One serialized observation in; at most one normalized v0 call out.

    vNext (AMEND-4A-3): the identity is the seven-key ``llm-v1`` manifest and
    ``decide`` renders the composed prompt (base + canonical context suffix).
    The context is advisory prompt input only: it never enters the serialized
    observation or ``ProviderRequest.observation``.
    """

    policy_id = POLICY_ID_VNEXT

    def __init__(self, adapter, strategy_context=None):
        provider = getattr(adapter, "provider", None)
        model = getattr(adapter, "model", None)
        if not isinstance(provider, str) or not provider:
            raise TypeError("adapter must pin a non-empty provider name")
        if not isinstance(model, str) or not model:
            raise TypeError("adapter must pin a non-empty model name")
        if not callable(getattr(adapter, "invoke", None)):
            raise TypeError("adapter must expose a callable invoke()")
        self._strategy_context = canonical_strategy_context(strategy_context)
        manifest = llm_vnext_policy_manifest(
            provider, model, self._strategy_context
        )
        self.strategy_context_digest = manifest["strategy_context_digest"]
        self.policy_digest = digest_json(manifest)
        self._provider_name = provider
        self._model_name = model
        self._adapter = adapter
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
        result = self._adapter.invoke(
            ProviderRequest(
                provider=self._provider_name,
                model=self._model_name,
                request_id=request_id,
                observation=observation,
                prompt=render_composed_prompt(
                    observation, self._strategy_context
                ),
                tool_schema=TOOL_SCHEMA,
            )
        )
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
