"""Verifier-owned official integer scoring (REQ-06, REQ-09, T-19).

Only the verifier may produce ``verified_score`` (REQ-06): runtime and policy
claims are structurally non-authoritative (the receipt candidate carries no
score field at all). The official v0 score is the integer

    floor(100 * unique_valid_completed_orders / total_orders)

computed here with integer arithmetic only — no floating-point value can enter
a score or a digest input (REQ-07). The raw numerator/denominator are retained
on ``ScoreResult`` so the static report can show the unrounded completion
ratio as its own labeled signal without ever normalizing it into the score.
"""

from dataclasses import dataclass

SCORE_NUMERATOR = 100

__all__ = ["SCORE_NUMERATOR", "ScoreResult", "compute_official_score"]


@dataclass(frozen=True)
class ScoreResult:
    """The official integer score plus its raw numerator and denominator."""

    score: int
    completed: int
    total: int


def _require_count(value, label) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def compute_official_score(completed: int, total: int) -> ScoreResult:
    """Integer floor score over exact counts; floats and bools are rejected.

    ``completed`` counts unique valid completed orders; ``total`` is the
    task's order count (always positive for a generated task). ``completed``
    may never exceed ``total``.
    """
    _require_count(completed, "completed")
    _require_count(total, "total")
    if total == 0:
        raise ValueError("total must be positive")
    if completed > total:
        raise ValueError("completed cannot exceed total")
    return ScoreResult(
        score=(SCORE_NUMERATOR * completed) // total,
        completed=completed,
        total=total,
    )
