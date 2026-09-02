"""Bounded v0 enums, counters, and result value objects (REQ-03, REQ-06, REQ-09).

Runtime result statuses and final receipt outcomes are distinct; an invalid
or tampered artifact set is never released as a valid receipt, and ambiguity
is represented as ``unresolved_outcome``. The rejection/verifier reason
taxonomy is closed: unknown internal failures are invalid evidence, not new
public reasons (design §7).
"""
import re
from dataclasses import dataclass

OUTCOMES = ("completed", "duplicate_effect", "safe_abort", "unresolved_outcome")
RESULT_STATUSES = ("committed", "idempotent_replay", "rejected")
SUPPORTED_TOOLS = ("move", "pick", "place")
DIRECTIONS = ("north", "east", "south", "west")

# Closed lowercase taxonomy (sorted); frozen for v0.
CLOSED_REASONS = (
    "artifact_mismatch", "budget_exhausted", "commit_boundary_unproven",
    "destination_out_of_grid", "grant_scope_mismatch", "invalid_arguments",
    "inventory_empty", "inventory_not_empty", "item_not_pending",
    "malformed_call", "missing_grant", "not_at_base",
    "operation_key_conflict", "operation_key_invalid",
    "order_already_completed", "order_item_mismatch", "response_lost",
    "robot_not_at_shelf", "score_mismatch", "shelf_item_mismatch",
    "trace_chain_invalid", "unsupported_tool",
)

_ASCII_RE = re.compile(r"[\x00-\x7f]*")


def _require_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an int, got {type(value).__name__}")
    return value


def _require_non_negative(value, label):
    _require_int(value, label)
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def _require_non_empty_ascii(value, label):
    if not isinstance(value, str) or not value or not _ASCII_RE.fullmatch(value):
        raise ValueError(f"{label} must be a non-empty ASCII string")
    return value


@dataclass(frozen=True)
class BudgetView:
    """Runner-owned policy budget as exposed at the policy boundary."""

    used: int
    remaining: int
    maximum: int

    def __post_init__(self):
        _require_non_negative(self.used, "used")
        _require_non_negative(self.remaining, "remaining")
        _require_non_negative(self.maximum, "maximum")
        if self.used + self.remaining != self.maximum:
            raise ValueError("used + remaining must equal maximum")


@dataclass(frozen=True)
class LastResult:
    """The single prior ToolBay result carried in an observation (REQ-03)."""

    status: str
    operation_key: str
    reason: str | None

    def __post_init__(self):
        if self.status not in RESULT_STATUSES:
            raise ValueError(f"status must be one of {RESULT_STATUSES}")
        if not isinstance(self.operation_key, str):
            raise TypeError("operation_key must be a str")
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError("reason must be a str or None")


@dataclass(frozen=True)
class Counters:
    """The six separate integer counters owned by the runner (REQ-02)."""

    policy_steps: int
    tool_attempts: int
    committed_effects: int
    internal_retries: int
    contract_violations: int
    terminal_checks: int

    def __post_init__(self):
        for field_name in (
            "policy_steps", "tool_attempts", "committed_effects",
            "internal_retries", "contract_violations", "terminal_checks",
        ):
            _require_non_negative(getattr(self, field_name), field_name)


@dataclass(frozen=True)
class VerifierViolation:
    """Locked violation object: exactly {code, location, detail} (decision 5).

    Non-empty ASCII fields; ``code`` comes from the closed taxonomy;
    violations sort by ``(code, location, detail)``.
    """

    code: str
    location: str
    detail: str

    def __post_init__(self):
        _require_non_empty_ascii(self.code, "code")
        if self.code not in CLOSED_REASONS:
            raise ValueError(f"code {self.code!r} is outside the closed taxonomy")
        _require_non_empty_ascii(self.location, "location")
        _require_non_empty_ascii(self.detail, "detail")

    @property
    def sort_key(self):
        return (self.code, self.location, self.detail)


def sort_violations(violations):
    """Deterministically ordered violation tuple per locked decision 5."""
    return tuple(sorted(violations, key=lambda violation: violation.sort_key))


@dataclass(frozen=True)
class VerificationResult:
    """Verifier-authoritative result; runtime claims stay non-authoritative."""

    valid: bool
    outcome: str
    verified_score: int | None
    completed_count: int
    total_count: int
    violations: tuple[VerifierViolation, ...]
    verifier_version: str

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        if self.verified_score is not None:
            _require_int(self.verified_score, "verified_score")
        _require_non_negative(self.completed_count, "completed_count")
        _require_non_negative(self.total_count, "total_count")
        if not isinstance(self.violations, tuple):
            raise TypeError("violations must be a tuple of VerifierViolation")
        for violation in self.violations:
            if not isinstance(violation, VerifierViolation):
                raise TypeError("violations must contain only VerifierViolation")
        if [
            violation.sort_key for violation in self.violations
        ] != sorted(violation.sort_key for violation in self.violations):
            raise ValueError("violations must be sorted by (code, location, detail)")
        _require_non_empty_ascii(self.verifier_version, "verifier_version")
