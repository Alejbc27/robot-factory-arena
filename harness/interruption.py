"""R1 human-interruption seam: approval answers enter as controlled data only.

The approval terminal is injected through the graph deps (design §6/§8); its
answers are validated against an exact-key contract BEFORE any side effect, so
guidance can enter the pipeline ONLY as text that the canonical StrategyContext
builder (``harness.strategy_context``, the AMEND-4A-3 mirror) later validates,
normalizes, freezes, and digests. There is no field a terminal could set to
reach the prompt, the observation, the provider request, or the score path
directly: launch-field edits (condition, seed, provider/model, K, generation
config, and every other frozen tournament identity) are rejected as side
channels by the exact-key answer contract.
"""

from . import strategy_context, versions

DECISIONS = ("yes", "no", "edit")
ANSWER_KEYS = frozenset({"decision", "guidance"})
GUIDANCE_AUTHOR = "human"


class InterruptionError(ValueError):
    """An approval answer or guidance payload violates the interruption contract."""


def _nonempty_text(value):
    return isinstance(value, str) and value != ""


def validate_answer(answer):
    """Exact-key answer contract; unknown keys are rejected as side channels.

    ``guidance`` is only accepted with an ``edit`` decision and must be a list
    of non-empty strings. It is a REPLACEMENT (revision) of the pending
    guidance list for the proposal — never a direct prompt fragment.
    """
    if not isinstance(answer, dict):
        raise InterruptionError("approval answer must be a JSON object")
    unknown = sorted(set(answer) - ANSWER_KEYS)
    if unknown:
        raise InterruptionError(
            f"approval answer has non-contract keys (side channel): {unknown}"
        )
    if "decision" not in answer:
        raise InterruptionError("approval answer requires 'decision'")
    decision = answer["decision"]
    if decision not in DECISIONS:
        raise InterruptionError(f"decision must be one of {DECISIONS}")
    guidance = answer.get("guidance")
    if guidance is not None:
        if decision != "edit":
            raise InterruptionError("guidance is only accepted with an 'edit' decision")
        if not isinstance(guidance, list) or not all(
            _nonempty_text(text) for text in guidance
        ):
            raise InterruptionError("guidance must be a list of non-empty strings")
    return decision


def guidance_records(texts):
    """Deterministic canonical guidance records; ordinals follow list order
    and record ids are 1-based (design §2.1 example: guidance-0001)."""
    return [
        {
            "guidance_id": f"guidance-{index + 1:04d}",
            "author_id": GUIDANCE_AUTHOR,
            "guidance_ordinal": index,
            "text": text,
        }
        for index, text in enumerate(texts)
    ]


def context_from_guidance_records(tournament_id, records):
    """Canonical StrategyContext from pre-built records; mirror-validator gate.

    This is the validation door on the R1 guidance path: an invalid canonical
    shape (extra keys, bad ordinals, empty text, wrong identity types) raises
    ``ContextError`` here and can never be frozen, digested, or handed to a
    launch.
    """
    context = {
        "schema_version": versions.CONTEXT_SCHEMA_VERSION,
        "source_tournament_id": tournament_id,
        "guidance": list(records),
        "discoveries": [],
    }
    strategy_context.validate_context(context)
    return context


def build_guidance_context(tournament_id, texts):
    """Canonical StrategyContext from approved guidance texts (§2.1/§6).

    Strict absence semantics (design decision: obs 6410): no guidance at all
    is canonical JSON ``null`` — never an empty-guidance object.
    """
    if not texts:
        return None
    return context_from_guidance_records(tournament_id, guidance_records(texts))


def auto_decline(view):
    """Deterministic terminal for unattended runs: always declines, so R1
    stays an explicit logged decline event rather than a silent skip."""
    return {"decision": "no"}
