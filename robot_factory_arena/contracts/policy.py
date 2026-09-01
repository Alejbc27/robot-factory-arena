"""Pure policy boundary: untrusted candidate in, one ToolCall intent out (REQ-01..REQ-03).

Policies receive only the serialized observation and return one candidate
intended as one ToolCall per policy step. The runner treats the output as
untrusted data. ``policy_digest`` is a digest of a stable canonical policy
manifest — never a path or execution metadata.
"""
from collections.abc import Mapping
from typing import Protocol

from robot_factory_arena.evidence.canonical import digest_json

POLICY_MANIFEST_VERSION = "1"
CONTRACT_VERSION = "1"


class Policy(Protocol):
    """Runtime contract every policy implementation must satisfy."""

    policy_id: str
    policy_digest: str

    def decide(self, observation: Mapping) -> object:
        """Return one candidate ToolCall for the serialized observation."""
        ...


def policy_manifest_digest(policy_id: str) -> str:
    """Digest of the stable canonical policy manifest for ``policy_id``."""
    return digest_json(
        {
            "contract_version": CONTRACT_VERSION,
            "implementation_version": POLICY_MANIFEST_VERSION,
            "policy_id": policy_id,
        }
    )
