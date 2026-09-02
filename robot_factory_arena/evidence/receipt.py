"""Receipt candidate and verified receipt release (REQ-03, REQ-06, REQ-09).

The receipt is the verified binding of one run's evidence set. A frozen
``ReceiptCandidate`` is created in memory AFTER trace closure (locked
decision 3: all run events -> ``run_finished`` -> close -> digest -> receipt)
carrying the run identity and every declared artifact digest. It contains NO
score: only the verifier may produce ``verified_score`` (REQ-06), so runtime
and policy claims structurally cannot enter the receipt.

``ReceiptWriter.write_final(candidate, verification)`` composes the exact
canonical receipt only from a real ``VerificationResult`` (B5 supplies the
authoritative one; tests use stubs). An invalid/tampered verification
releases NO receipt at all (``ReceiptNotReleasedError``), and the released
outcome is the verifier's classification — the candidate's claimed outcome
is never substituted. The exact ``verification`` block carries status,
verifier version, verified score, completion counts, sorted structured
violations, and the post-verification ``verification_finished`` AUXILIARY
record — which never becomes a trace record (locked decision 3).

This module knows no runtime, trace-writer, store, or replay code; it only
validates and composes value mappings through the shared canonical rules.
"""
import re
from dataclasses import dataclass

from robot_factory_arena.contracts.outcomes import (
    OUTCOMES,
    SUPPORTED_TOOLS,
    Counters,
    VerificationResult,
)

RECEIPT_SCHEMA_VERSION = 1
TASK_VERSION = "warehouse-v0"
BUDGET_MAXIMUM = 100

# Exactly the REQ-03 receipt keys plus the T-15 ``verification`` block.
RECEIPT_KEYS = (
    "budget",
    "config_digest",
    "counters",
    "execution_id",
    "fault_tape_digest",
    "final_state_digest",
    "grants",
    "logical_run_id",
    "operation_log_digest",
    "outcome",
    "policy_digest",
    "policy_id",
    "schema_version",
    "seed",
    "task_version",
    "trace_digest",
    "verifier_version",
    "verified_score",
    "verification",
)

# Exactly the T-15 verification-block keys.
VERIFICATION_BLOCK_KEYS = (
    "completed_count",
    "status",
    "total_count",
    "verification_finished",
    "verified_score",
    "verifier_version",
    "violations",
)

VERIFICATION_STATUS_VALID = "valid"
VERIFICATION_FINISHED_EVENT = "verification_finished"

_LOGICAL_RUN_ID_RE = re.compile(r"lr_[0-9a-f]{64}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SEED_TEXT_RE = re.compile(r"(0|[1-9][0-9]*)")


class ReceiptNotReleasedError(ValueError):
    """Invalid/tampered verification: no receipt is released (REQ-06)."""


def _is_digest(value) -> bool:
    return (
        isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None
    )


def _require_digest(value, label) -> str:
    if not _is_digest(value):
        raise ValueError(f"{label} must be a sha256:<64 hex> digest")
    return value


@dataclass(frozen=True)
class ReceiptCandidate:
    """In-memory pre-verification binding; contains NO score claim.

    Built after trace closure with the run identity, the locked constants,
    and every declared artifact digest. ``outcome`` is the RUNTIME claim
    (runner-producible classification) kept for composition completeness;
    the released receipt's outcome is always the verifier's.
    """

    schema_version: int
    task_version: str
    logical_run_id: str
    execution_id: str
    seed: str
    config_digest: str
    policy_id: str
    policy_digest: str
    budget: int
    grants: tuple[str, ...]
    fault_tape_digest: str | None
    trace_digest: str
    final_state_digest: str
    operation_log_digest: str
    counters: Counters
    outcome: str

    def __post_init__(self):
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError("schema_version must be 1")
        if self.task_version != TASK_VERSION:
            raise ValueError(f"task_version must be {TASK_VERSION!r}")
        if (
            not isinstance(self.logical_run_id, str)
            or _LOGICAL_RUN_ID_RE.fullmatch(self.logical_run_id) is None
        ):
            raise ValueError(
                "logical_run_id must be lr_ plus 64 hex characters"
            )
        if not isinstance(self.execution_id, str) or not self.execution_id:
            raise ValueError("execution_id must be a non-empty str")
        if (
            not isinstance(self.seed, str)
            or _SEED_TEXT_RE.fullmatch(self.seed) is None
        ):
            raise ValueError("seed must be canonical decimal text")
        _require_digest(self.config_digest, "config_digest")
        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise ValueError("policy_id must be a non-empty str")
        _require_digest(self.policy_digest, "policy_digest")
        if (
            isinstance(self.budget, bool)
            or not isinstance(self.budget, int)
            or self.budget != BUDGET_MAXIMUM
        ):
            raise ValueError(f"budget is locked at {BUDGET_MAXIMUM} in v0")
        if not isinstance(self.grants, tuple) or not self.grants:
            raise ValueError("grants must be a non-empty tuple of capabilities")
        for capability in self.grants:
            if capability not in SUPPORTED_TOOLS:
                raise ValueError(f"unsupported capability: {capability!r}")
        if any(a >= b for a, b in zip(self.grants, self.grants[1:])):
            raise ValueError(
                f"grants must be strictly ascending: {self.grants!r}"
            )
        if self.fault_tape_digest is not None:
            _require_digest(self.fault_tape_digest, "fault_tape_digest")
        _require_digest(self.trace_digest, "trace_digest")
        _require_digest(self.final_state_digest, "final_state_digest")
        _require_digest(self.operation_log_digest, "operation_log_digest")
        if not isinstance(self.counters, Counters):
            raise TypeError("counters must be a Counters snapshot")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")

    def to_mapping(self) -> dict:
        """The candidate's share of the receipt; grants/counters expanded."""
        return {
            "schema_version": self.schema_version,
            "task_version": self.task_version,
            "logical_run_id": self.logical_run_id,
            "execution_id": self.execution_id,
            "seed": self.seed,
            "config_digest": self.config_digest,
            "policy_id": self.policy_id,
            "policy_digest": self.policy_digest,
            "budget": self.budget,
            "grants": list(self.grants),
            "fault_tape_digest": self.fault_tape_digest,
            "trace_digest": self.trace_digest,
            "final_state_digest": self.final_state_digest,
            "operation_log_digest": self.operation_log_digest,
            "counters": {
                "policy_steps": self.counters.policy_steps,
                "tool_attempts": self.counters.tool_attempts,
                "committed_effects": self.counters.committed_effects,
                "internal_retries": self.counters.internal_retries,
                "contract_violations": self.counters.contract_violations,
                "terminal_checks": self.counters.terminal_checks,
            },
            "outcome": self.outcome,
        }


def verification_finished_record(
    verification: VerificationResult,
) -> dict:
    """The post-verification AUXILIARY record (locked decision 3).

    Lives only inside the receipt's verification block (and, in B5, the
    verification result); it is never appended to the closed trace.
    """
    if not isinstance(verification, VerificationResult):
        raise TypeError("verification must be a VerificationResult")
    return {
        "event": VERIFICATION_FINISHED_EVENT,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "payload": {
            "outcome": verification.outcome,
            "verified_score": verification.verified_score,
            "verifier_version": verification.verifier_version,
            "violations_count": len(verification.violations),
        },
    }


def _verification_block(verification: VerificationResult) -> dict:
    """Exact T-15 block for a VALID verification (invalid never releases)."""
    return {
        "status": VERIFICATION_STATUS_VALID,
        "verifier_version": verification.verifier_version,
        "verified_score": verification.verified_score,
        "completed_count": verification.completed_count,
        "total_count": verification.total_count,
        "violations": [
            {
                "code": violation.code,
                "location": violation.location,
                "detail": violation.detail,
            }
            for violation in verification.violations
        ],
        "verification_finished": verification_finished_record(verification),
    }


class ReceiptWriter:
    """Composes the canonical receipt; release requires verification (REQ-06)."""

    @staticmethod
    def write_final(
        candidate: ReceiptCandidate,
        verification: VerificationResult,
    ) -> dict:
        """Bind the verified evidence set into the exact receipt mapping.

        - ``candidate`` must be a ReceiptCandidate, ``verification`` a real
          VerificationResult: without one there is no release (TypeError).
        - An INVALID verification releases nothing (ReceiptNotReleasedError):
          tampered artifacts never become a valid receipt.
        - A valid verification must carry an integer verified_score.
        - The receipt's outcome is ``verification.outcome`` (verifier-owned).
        """
        if not isinstance(candidate, ReceiptCandidate):
            raise TypeError("write_final requires a ReceiptCandidate")
        if not isinstance(verification, VerificationResult):
            raise TypeError("write_final requires a VerificationResult")
        if not verification.valid:
            raise ReceiptNotReleasedError(
                "invalid verification releases no receipt (REQ-06)"
            )
        score = verification.verified_score
        if (
            isinstance(score, bool)
            or not isinstance(score, int)
        ):
            raise ValueError(
                "a valid verification must carry an integer verified_score"
            )
        receipt = candidate.to_mapping()
        receipt["verifier_version"] = verification.verifier_version
        receipt["verified_score"] = score
        receipt["outcome"] = verification.outcome
        receipt["verification"] = _verification_block(verification)
        if set(receipt) != set(RECEIPT_KEYS):
            raise ValueError("receipt key drift")  # fail-closed; unreachable
        return receipt
