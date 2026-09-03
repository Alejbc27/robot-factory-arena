"""LF-terminated append-only JSONL with digest-chained integrity (design §7).

Records carry a deterministic sequence identity (``seq``) and a ``prev_digest``
link; tamper evidence comes from sequence/continuity checks plus an externally
anchored tail digest (e.g. persisted in a checkpoint). Not cryptographically
authentic by design.
"""

import json
from pathlib import Path

from . import versions
from .strategy_context import canonical_json_bytes, sha256_digest


class LogIntegrityError(ValueError):
    """Truncation, overwrite, or sequence break detected."""


class AppendOnlyLog:
    def __init__(self, path):
        self.path = Path(path)

    def _lines(self):
        if not self.path.exists():
            return []
        return self.path.read_bytes().splitlines(keepends=True)

    def append(self, record):
        lines = self._lines()
        prev = sha256_digest(lines[-1].rstrip(b"\n")) if lines else None
        stored = {
            **record,
            "schema_version": versions.LOG_SCHEMA_VERSION,
            "seq": len(lines),
            "prev_digest": prev,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "ab") as handle:
                handle.write(canonical_json_bytes(stored) + b"\n")
        except OSError as exc:
            raise LogIntegrityError(f"append failed for {self.path}") from exc
        return stored

    def records(self):
        try:
            return [json.loads(line) for line in self._lines()]
        except json.JSONDecodeError as exc:
            raise LogIntegrityError(f"log contains a non-JSON line: {self.path}") from exc

    def tail_digest(self):
        lines = self._lines()
        return sha256_digest(lines[-1].rstrip(b"\n")) if lines else None

    def verify(self, expected_tail_digest=None):
        lines = self._lines()
        if lines and not lines[-1].endswith(b"\n"):
            raise LogIntegrityError("log does not end with LF")
        prev = None
        for index, line in enumerate(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LogIntegrityError(f"line {index} is not JSON") from exc
            if not isinstance(record, dict):
                raise LogIntegrityError(f"line {index} is not an object")
            if record.get("seq") != index or record.get("prev_digest") != prev:
                raise LogIntegrityError(f"sequence/integrity break at line {index}")
            prev = sha256_digest(line.rstrip(b"\n"))
        if expected_tail_digest is not None and prev != expected_tail_digest:
            raise LogIntegrityError(
                "log tail disagrees with the recorded anchor (truncation/overwrite)"
            )
        return len(lines)
