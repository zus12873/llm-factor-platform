"""Artifact retention and the disk-pressure rule.

Different artifacts deserve different lifetimes, and the distinction that matters
is not age but whether anything else references them. A temporary Parquet from a
failed run is disposable. A published factor's output is what a saved factor
*points at* — deleting it turns a library entry into a dangling reference, and the
user finds out when they open a factor they saved months ago.

So retention has a floor, not just a ceiling: under disk pressure the policy stops
accepting new work and clears expendable classes, but immutable published
artifacts are never in the sweep. Refusing to start a job is recoverable; deleting
the evidence behind a published factor is not.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

DAY = 86_400.0


class ArtifactClass(StrEnum):
    UPLOADED_PDF = "uploaded_pdf"
    EXTRACTED_TEXT = "extracted_text"
    INPUT_PARQUET = "input_parquet"
    OUTPUT_PARQUET = "output_parquet"
    PUBLISHED_OUTPUT = "published_output"
    LOG = "log"
    ERROR_ARTIFACT = "error_artifact"
    TEMPORARY = "temporary"
    EXPORTED_CODE = "exported_code"


#: Retention in seconds. ``None`` means never swept.
RETENTION: dict[ArtifactClass, float | None] = {
    ArtifactClass.TEMPORARY: 1 * DAY,
    ArtifactClass.EXTRACTED_TEXT: 7 * DAY,
    ArtifactClass.INPUT_PARQUET: 14 * DAY,
    ArtifactClass.OUTPUT_PARQUET: 30 * DAY,
    ArtifactClass.LOG: 30 * DAY,
    ArtifactClass.ERROR_ARTIFACT: 30 * DAY,
    ArtifactClass.UPLOADED_PDF: 90 * DAY,
    ArtifactClass.EXPORTED_CODE: 90 * DAY,
    # A published factor's output is referenced by a library entry. Sweeping it
    # would leave a saved factor pointing at nothing.
    ArtifactClass.PUBLISHED_OUTPUT: None,
}

#: Below this fraction of free disk, stop accepting new jobs.
FREE_SPACE_FLOOR = 0.10


class SweepEntry(BaseModel):
    path: str
    artifact_class: ArtifactClass
    age_seconds: float


class SweepReport(BaseModel):
    """What a sweep removed, and whether the host is still accepting work."""

    removed: list[SweepEntry] = []
    bytes_reclaimed: int = 0
    free_fraction: float = 1.0
    accepting_new_jobs: bool = True


class RetentionPolicy:
    """Decides what may be deleted, and when to stop taking work."""

    def __init__(
        self,
        root: Path | str,
        *,
        now: float | None = None,
        free_fraction_source: Callable[[], float] | None = None,
    ) -> None:
        self._root = Path(root)
        self._now = now
        # Injectable so the intake threshold can be tested without depending on
        # how full the developer's disk happens to be.
        self._free_fraction_source = free_fraction_source

    def is_expired(self, path: Path, artifact_class: ArtifactClass) -> bool:
        limit = RETENTION[artifact_class]
        if limit is None:
            return False
        return self._age(path) > limit

    def sweep(self, candidates: Iterable[tuple[Path, ArtifactClass]]) -> SweepReport:
        """Delete expired artifacts, never touching the immutable classes."""
        removed: list[SweepEntry] = []
        reclaimed = 0

        for path, artifact_class in candidates:
            if RETENTION[artifact_class] is None or not path.exists():
                continue
            if not self.is_expired(path, artifact_class):
                continue
            size = _size_of(path)
            _remove(path)
            reclaimed += size
            removed.append(
                SweepEntry(
                    path=str(path),
                    artifact_class=artifact_class,
                    age_seconds=self._age(path),
                )
            )

        free = self.free_fraction()
        return SweepReport(
            removed=removed,
            bytes_reclaimed=reclaimed,
            free_fraction=free,
            accepting_new_jobs=free >= FREE_SPACE_FLOOR,
        )

    def free_fraction(self) -> float:
        if self._free_fraction_source is not None:
            return self._free_fraction_source()
        usage = shutil.disk_usage(self._root)
        return usage.free / usage.total if usage.total else 1.0

    def accepting_new_jobs(self) -> bool:
        """Refuse new work before the disk fills.

        Stopping intake is recoverable; a job that dies halfway through writing
        an artifact leaves something that looks like output.
        """
        return self.free_fraction() >= FREE_SPACE_FLOOR

    def _age(self, path: Path) -> float:
        now = self._now if self._now is not None else time.time()
        try:
            return now - path.stat().st_mtime
        except OSError:
            return 0.0


def _size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


__all__ = [
    "FREE_SPACE_FLOOR",
    "RETENTION",
    "ArtifactClass",
    "RetentionPolicy",
    "SweepEntry",
    "SweepReport",
]
