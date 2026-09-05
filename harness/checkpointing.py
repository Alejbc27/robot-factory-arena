"""Durable checkpoints at the design §8 recovery boundaries.

The five R1/launch/receipt boundary hooks (``pre_prompt`` .. ``post_binding``)
are exposed so Units 3-4 persist at exactly those points; recovery reloads
the latest checkpoint and fails closed on a corrupt one.

PR #18 blocker 4: the store is gap-fail-closed. Full 0..n filename and
record continuity is validated before ANY read or write; ``next_seq`` is
``last_seq + 1`` (never ``len``); an existing destination is never
overwritten; and writes stay atomic.
"""

import json
import re
from pathlib import Path

from . import versions
from .strategy_context import atomic_write_bytes, canonical_json_bytes

PRE_PROMPT = "pre_prompt"
POST_ANSWER = "post_answer"
PRE_LAUNCH = "pre_launch"
POST_REGISTRATION = "post_registration"
POST_BINDING = "post_binding"
BOUNDARIES = (PRE_PROMPT, POST_ANSWER, PRE_LAUNCH, POST_REGISTRATION, POST_BINDING)

_CHECKPOINT_NAME = re.compile(r"checkpoint-(\d+)\.json")


class CheckpointError(ValueError):
    """A checkpoint is unreadable, discontinuous, or already exists;
    recovery must fail closed."""


class CheckpointStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _paths(self):
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("checkpoint-*.json"))

    def _validated_records(self):
        """Full 0..n continuity check before ANY read or write (blocker 4)."""
        records = []
        for index, path in enumerate(self._paths()):
            match = _CHECKPOINT_NAME.fullmatch(path.name)
            if match is None:
                raise CheckpointError(f"unexpected checkpoint filename: {path.name}")
            try:
                seq = int(match.group(1))
            except ValueError as exc:  # e.g. CPython's int-str digit limit
                raise CheckpointError(
                    f"unparsable checkpoint sequence number: {path.name}"
                ) from exc
            if seq != index:
                raise CheckpointError(
                    f"checkpoint gap or discontinuity at {path.name}: "
                    f"expected seq {index}"
                )
            try:
                record = json.loads(path.read_bytes())
            except (json.JSONDecodeError, OSError) as exc:
                raise CheckpointError(f"checkpoint unreadable: {path}") from exc
            if not isinstance(record, dict) or record.get("seq") != seq:
                raise CheckpointError(
                    f"checkpoint seq field disagrees with its filename: {path.name}"
                )
            records.append(record)
        return records

    def write(self, boundary, state_snapshot):
        if boundary not in BOUNDARIES:
            raise CheckpointError(f"unknown checkpoint boundary: {boundary}")
        records = self._validated_records()
        seq = records[-1]["seq"] + 1 if records else 0
        path = self.directory / f"checkpoint-{seq:03d}.json"
        if path.exists():
            raise CheckpointError(
                f"refusing to overwrite an existing checkpoint: {path.name}"
            )
        record = {
            "schema_version": versions.CHECKPOINT_SCHEMA_VERSION,
            "seq": seq,
            "boundary": boundary,
            "state": state_snapshot,
        }
        atomic_write_bytes(path, canonical_json_bytes(record))
        return path

    def records(self):
        """All checkpoint records in sequence order (public read access for
        the restart replay); full continuity is validated before any read.
        """
        return self._validated_records()

    def latest(self):
        records = self._validated_records()
        if not records:
            return None
        return records[-1]
