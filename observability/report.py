"""Redacted machine-readable export report (Unit B, task 4).

The final report is emitted only after finite processing and contains the
schema version, the seven exact counters, the deterministic trace IDs of every
exported item, and safe per-item ``{path, reason}`` skips. It never carries
request bodies, prompt/response text, tool arguments, authorization data,
environment values, or secret substrings — the composition and guard layers
make text unreachable here, and this module adds no free-text field at all.
Rendering uses the isolated canonical JSON rule, so identical runs of the same
artifact root produce byte-identical reports across machines.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .artifacts import canonical_json_bytes

SCHEMA_VERSION = 1

COUNT_KEYS = (
    "processed",
    "exported",
    "created",
    "updated_or_replayed",
    "unchanged",
    "failed",
    "skipped",
)


@dataclass(frozen=True)
class ExportedItem:
    """One accepted item: its deterministic trace ID plus per-outcome counts."""

    trace_id: str
    created: int
    updated_or_replayed: int
    unchanged: int


@dataclass(frozen=True)
class ExportReport:
    """Aggregate of one finite export run.

    Counter semantics: ``processed`` is the full accounted item set
    (validated candidates plus validation skips), ``exported + skipped ==
    processed``, and ``failed`` (item-level ingestion failures) is a subset of
    ``skipped``. Validation skips, endpoint-guard skips, and ingestion failures
    all appear in ``skipped`` with their stable reason code.
    """

    counts: Mapping
    exported: tuple
    skipped: tuple
    error: str | None = None

    def to_dict(self):
        document = {
            "schema_version": SCHEMA_VERSION,
            "counts": {key: self.counts[key] for key in COUNT_KEYS},
            "exported": [asdict(entry) for entry in self.exported],
            "skipped": [
                {"path": skip.path, "reason": skip.reason} for skip in self.skipped
            ],
        }
        if self.error is not None:
            document["error"] = self.error
        return document

    def render(self):
        """Deterministic canonical JSON text of the whole report."""
        return canonical_json_bytes(self.to_dict()).decode("utf-8")
