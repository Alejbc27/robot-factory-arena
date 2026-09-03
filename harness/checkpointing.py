"""Durable checkpoints at the design §8 recovery boundaries.

The five R1/launch/receipt boundary hooks (``pre_prompt`` .. ``post_binding``)
are exposed now so Units 3-4 persist at exactly those points; recovery reloads
the latest checkpoint and fails closed on a corrupt one.
"""

import json
from pathlib import Path

from . import versions
from .strategy_context import canonical_json_bytes

PRE_PROMPT = "pre_prompt"
POST_ANSWER = "post_answer"
PRE_LAUNCH = "pre_launch"
POST_REGISTRATION = "post_registration"
POST_BINDING = "post_binding"
BOUNDARIES = (PRE_PROMPT, POST_ANSWER, PRE_LAUNCH, POST_REGISTRATION, POST_BINDING)


class CheckpointError(ValueError):
    """A checkpoint file is unreadable; recovery must fail closed."""


class CheckpointStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _paths(self):
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("checkpoint-*.json"))

    def write(self, boundary, state_snapshot):
        if boundary not in BOUNDARIES:
            raise CheckpointError(f"unknown checkpoint boundary: {boundary}")
        seq = len(self._paths())
        record = {
            "schema_version": versions.CHECKPOINT_SCHEMA_VERSION,
            "seq": seq,
            "boundary": boundary,
            "state": state_snapshot,
        }
        path = self.directory / f"checkpoint-{seq:03d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(record))
        return path

    def latest(self):
        paths = self._paths()
        if not paths:
            return None
        try:
            return json.loads(paths[-1].read_bytes())
        except (json.JSONDecodeError, OSError) as exc:
            raise CheckpointError(f"latest checkpoint unreadable: {paths[-1]}") from exc
