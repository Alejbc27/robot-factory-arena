"""Run-scoped capability grants and the authorization gate (REQ-03, REQ-04).

The runner issues one explicit sorted GrantSet per logical run. Grant IDs are
deterministic from (logical_run_id, capability), so authorization can prove a
grant belongs to THIS run: authorize() recomputes the expected id and rejects
out-of-run or out-of-scope grants as ``grant_scope_mismatch``. Grants are
immutable value objects; the observation and receipt expose the sorted
capability names only (design §2). ``assemble`` and every other tool are
unsupported and unauthorized in v0.
"""
import re
from dataclasses import dataclass

from robot_factory_arena.contracts.outcomes import SUPPORTED_TOOLS
from robot_factory_arena.evidence.canonical import digest_json

SCOPE_RUN = "run"

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")


def _require_logical_run_id(logical_run_id):
    if (
        not isinstance(logical_run_id, str)
        or _LOGICAL_RUN_ID_RE.fullmatch(logical_run_id) is None
    ):
        raise ValueError("logical_run_id must be lr_ plus 64 hex characters")
    return logical_run_id


def grant_id_for(logical_run_id: str, capability: str) -> str:
    """Deterministic grant id: ``grant-`` plus 16 hex of the canonical identity."""
    _require_logical_run_id(logical_run_id)
    if capability not in SUPPORTED_TOOLS:
        raise ValueError(f"unsupported capability: {capability!r}")
    digest = digest_json(
        {
            "capability": capability,
            "logical_run_id": logical_run_id,
            "scope": SCOPE_RUN,
        }
    )
    return "grant-" + digest.removeprefix("sha256:")[:16]


@dataclass(frozen=True)
class Grant:
    """The immutable run-scoped capability {grant_id, capability, scope}."""

    grant_id: str
    capability: str
    scope: str

    def __post_init__(self):
        if not isinstance(self.grant_id, str) or not self.grant_id:
            raise ValueError("grant_id must be a non-empty str")
        if self.capability not in SUPPORTED_TOOLS:
            raise ValueError(f"capability must be one of {SUPPORTED_TOOLS}")
        if self.scope != SCOPE_RUN:
            raise ValueError("v0 grants are run-scoped")

    def to_mapping(self):
        return {
            "grant_id": self.grant_id,
            "capability": self.capability,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class GrantSet:
    """Capability-sorted, duplicate-free grants issued for one logical run."""

    grants: tuple[Grant, ...]

    def __post_init__(self):
        if not isinstance(self.grants, tuple) or any(
            not isinstance(grant, Grant) for grant in self.grants
        ):
            raise TypeError("grants must be a tuple of Grant")
        capabilities = [grant.capability for grant in self.grants]
        if any(a >= b for a, b in zip(capabilities, capabilities[1:])):
            raise ValueError(
                "grants must be strictly sorted by capability: "
                f"{capabilities!r}"
            )


@dataclass(frozen=True)
class AuthorizationResult:
    """``ok`` exactly when ``reason`` is None (closed-taxonomy reason)."""

    ok: bool
    reason: str | None

    def __post_init__(self):
        if not isinstance(self.ok, bool):
            raise TypeError("ok must be a bool")
        if self.ok:
            if self.reason is not None:
                raise ValueError("an authorized result carries no reason")
            return
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("an unauthorized result carries a reason")


def issue_grants(logical_run_id: str, capabilities) -> GrantSet:
    """Issue one deterministic grant per distinct supported capability."""
    _require_logical_run_id(logical_run_id)
    try:
        distinct = set(capabilities)
    except TypeError:
        raise TypeError("capabilities must be an iterable of capability names")
    if not distinct:
        raise ValueError("at least one capability is required")
    for capability in distinct:
        if capability not in SUPPORTED_TOOLS:
            raise ValueError(f"unsupported capability: {capability!r}")
    return GrantSet(
        tuple(
            Grant(grant_id_for(logical_run_id, capability), capability, SCOPE_RUN)
            for capability in sorted(distinct)
        )
    )


def authorize(call, grant_set: GrantSet, logical_run_id: str) -> AuthorizationResult:
    """The grant check performed before any domain transition (REQ-04).

    The requested tool must be supported, must hold a grant, and that grant
    must be run-scoped for THIS logical run — proven by recomputing the
    deterministic grant id. Unsupported tools reject as ``unsupported_tool``,
    absent capabilities as ``missing_grant``, and foreign/out-of-scope grants
    as ``grant_scope_mismatch``.
    """
    _require_logical_run_id(logical_run_id)
    tool = getattr(call, "tool", None)
    if tool not in SUPPORTED_TOOLS:
        return AuthorizationResult(False, "unsupported_tool")
    for grant in grant_set.grants:
        if grant.capability == tool:
            if (
                grant.scope != SCOPE_RUN
                or grant.grant_id != grant_id_for(logical_run_id, tool)
            ):
                return AuthorizationResult(False, "grant_scope_mismatch")
            return AuthorizationResult(True, None)
    return AuthorizationResult(False, "missing_grant")
