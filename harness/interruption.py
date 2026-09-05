"""R1 human-interruption seam: approval answers enter as controlled data only.

The approval terminal is injected through the graph deps (design §6/§8); its
answers are validated against an exact-key contract before any side effect
beyond the pre-prompt checkpoint, so guidance can enter the pipeline ONLY as
text that the canonical StrategyContext
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
    """Exact-key answer contract; any other shape is rejected as a side channel.

    ``yes`` and ``no`` carry exactly ``{"decision"}``. ``edit`` carries
    exactly ``{"decision", "guidance"}`` where guidance is a list of
    non-empty strings — a REPLACEMENT (revision) of the pending guidance
    list for the proposal, never a direct prompt fragment. ``[]`` is legal
    and clears the pending guidance; a missing or null guidance on an edit
    answer is rejected here so an incomplete answer can never reach the
    graph node and crash it outside this contract, and ``guidance`` exists
    ONLY with ``edit`` — even a null guidance alongside ``yes``/``no`` is
    rejected (PR #22 blocker 1).
    """
    if not isinstance(answer, dict):
        raise InterruptionError("approval answer must be a JSON object")
    if "decision" not in answer:
        raise InterruptionError("approval answer requires 'decision'")
    decision = answer["decision"]
    if decision not in DECISIONS:
        raise InterruptionError(f"decision must be one of {DECISIONS}")
    if decision == "edit":
        if "guidance" not in answer:
            raise InterruptionError(
                "an edit answer requires 'guidance' (use [] to clear it)"
            )
        if set(answer) != {"decision", "guidance"}:
            raise InterruptionError(
                "an edit answer has non-contract keys (side channel): "
                f"{sorted(set(answer) - ANSWER_KEYS)}"
            )
        guidance = answer["guidance"]
        if not isinstance(guidance, list) or not all(
            _nonempty_text(text) for text in guidance
        ):
            raise InterruptionError("guidance must be a list of non-empty strings")
    elif set(answer) != {"decision"}:
        raise InterruptionError(
            f"a {decision!r} answer has non-contract keys (side channel): "
            f"{sorted(set(answer) - ANSWER_KEYS)}; guidance exists only "
            "with an 'edit' decision"
        )
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
