"""Verified, additive release of the optional offline visualizer."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from robot_factory_arena.evidence.artifact_store import atomic_write_bytes
from robot_factory_arena.evidence.visualizer_projection import (
    VisualizerLogicalMetadata,
    build_projection,
)
from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

VISUALIZER_NAME = "visualizer.html"


class VisualizerGenerationError(RuntimeError):
    """A downstream visualizer failure; core evidence remains authoritative."""


@dataclass(frozen=True)
class VerifiedRelease:
    """Immutable handoff created only after the core receipt/report recheck."""

    directory: Path
    artifacts: object
    receipt: Mapping
    verification: object
    replay_result: object
    report_path: Path
    report_bytes: bytes
    logical_metadata: VisualizerLogicalMetadata

    def __post_init__(self) -> None:
        directory = Path(self.directory)
        report_path = Path(self.report_path)
        if not getattr(self.verification, "valid", False):
            raise VisualizerGenerationError("verification is not valid")
        if not getattr(self.replay_result, "matches_receipt", False):
            raise VisualizerGenerationError(
                "operation-only replay does not match receipt"
            )
        artifact_receipt = getattr(self.artifacts, "receipt", None)
        if not isinstance(self.receipt, Mapping) or artifact_receipt != self.receipt:
            raise VisualizerGenerationError(
                "released receipt is absent or inconsistent"
            )
        if report_path != directory / "report.html" or not report_path.is_file():
            raise VisualizerGenerationError("verified report is absent")
        if (
            not isinstance(self.report_bytes, bytes)
            or report_path.read_bytes() != self.report_bytes
        ):
            raise VisualizerGenerationError("verified report bytes are inconsistent")
        if not isinstance(self.logical_metadata, VisualizerLogicalMetadata):
            raise TypeError("logical_metadata must be VisualizerLogicalMetadata")
        object.__setattr__(self, "directory", directory)
        object.__setattr__(self, "report_path", report_path)

    @property
    def verified(self) -> bool:
        """Compatibility gate consumed by the projection's approved handoff."""
        return True


def _remove_incomplete_output(directory: Path) -> None:
    for path in (directory / VISUALIZER_NAME, directory / f"{VISUALIZER_NAME}.tmp"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def release_visualizer(release: VerifiedRelease) -> Path:
    """Build and durably release the optional sibling, or remove it and fail open."""
    if not isinstance(release, VerifiedRelease):
        raise TypeError("release must be a VerifiedRelease")
    target = release.directory / VISUALIZER_NAME
    try:
        projection = build_projection(release)
        html = render_visualizer(projection)
        atomic_write_bytes(target, html)
    except Exception as error:
        _remove_incomplete_output(release.directory)
        raise VisualizerGenerationError(
            f"non-authoritative visualizer generation failed: {error}"
        ) from error
    return target
