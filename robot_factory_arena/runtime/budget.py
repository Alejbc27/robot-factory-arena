"""Runner-owned hard policy budget and the six separate counters (REQ-02).

The budget is the kill switch. Exactly one policy step is reserved per policy
action attempt BEFORE parse/validation/dispatch, so malformed, invalid, and
unauthorized outputs all consume the reserved step. Internal retries never
consume policy steps: they only increment ``tool_attempts`` and
``internal_retries``. Terminal checks are free — ``record_terminal_check()``
touches nothing else. ``policy_steps`` has the hard maximum 100; the runner
(B3) always constructs the budget at that locked maximum (RunConfig constant).
"""
from robot_factory_arena.contracts.outcomes import BudgetView, Counters

DEFAULT_MAXIMUM = 100


class BudgetExhaustedError(RuntimeError):
    """Raised once the policy budget is exhausted (closed reason
    ``budget_exhausted``); the runner classifies the known, non-ambiguous
    abort as ``safe_abort``."""


class Budget:
    """Mutable runtime cursor exposing only frozen snapshots (design §2).

    The six counters are separate integers and every ``record_*`` mutates
    exactly one of them. ``view()`` always satisfies the BudgetView invariant
    ``used + remaining == maximum``.
    """

    def __init__(self, maximum: int = DEFAULT_MAXIMUM):
        if isinstance(maximum, bool) or not isinstance(maximum, int):
            raise TypeError("maximum must be an int")
        if maximum <= 0:
            raise ValueError("maximum must be positive")
        self._maximum = maximum
        self._used = 0
        self._tool_attempts = 0
        self._committed_effects = 0
        self._internal_retries = 0
        self._contract_violations = 0
        self._terminal_checks = 0

    @property
    def maximum(self) -> int:
        return self._maximum

    @property
    def exhausted(self) -> bool:
        return self._used >= self._maximum

    def view(self) -> BudgetView:
        """Frozen policy-boundary view; used + remaining == maximum."""
        return BudgetView(self._used, self._maximum - self._used, self._maximum)

    def counters(self) -> Counters:
        """Frozen snapshot of all six separate counter integers."""
        return Counters(
            policy_steps=self._used,
            tool_attempts=self._tool_attempts,
            committed_effects=self._committed_effects,
            internal_retries=self._internal_retries,
            contract_violations=self._contract_violations,
            terminal_checks=self._terminal_checks,
        )

    def require_available(self) -> None:
        """Preflight before observation; raises once the budget is spent."""
        if self.exhausted:
            raise BudgetExhaustedError("budget_exhausted")

    def reserve_policy_attempt(self) -> BudgetView:
        """Reserve exactly one policy step before parse/validation/dispatch."""
        self.require_available()
        self._used += 1
        return self.view()

    def record_tool_attempt(self) -> None:
        """One initial ToolBay submission or one internal retry submission."""
        self._tool_attempts += 1

    def record_commit(self) -> None:
        """One successful world effect, counted once at the commit boundary."""
        self._committed_effects += 1

    def record_retry(self) -> None:
        """One internal retry after the initial submission of an operation."""
        self._internal_retries += 1

    def record_violation(self) -> None:
        """One contract violation (invalid, malformed, unauthorized, ...)."""
        self._contract_violations += 1

    def record_terminal_check(self) -> None:
        """One free terminal check: consumes no step, attempt, or effect."""
        self._terminal_checks += 1
