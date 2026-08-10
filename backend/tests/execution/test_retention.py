"""Tests for artifact retention.

The rule that matters is not "delete old things". It is that some artifacts are
referenced by something else: a published factor's output is what a library entry
points at, and sweeping it turns a saved factor into a dangling reference. The
user discovers that months later, opening a factor they trusted.

So the policy has a floor as well as a ceiling, and disk pressure stops intake
rather than reaching further into the immutable classes.
"""

from __future__ import annotations

from pathlib import Path

from factor_platform.execution.retention import (
    DAY,
    ArtifactClass,
    RetentionPolicy,
)


def aged_file(root: Path, name: str, *, days_old: float) -> Path:
    path = root / name
    path.write_bytes(b"x" * 128)
    import os

    stamp = 1_700_000_000.0 - days_old * DAY
    os.utime(path, (stamp, stamp))
    return path


def policy(root: Path) -> RetentionPolicy:
    return RetentionPolicy(root, now=1_700_000_000.0)


# --------------------------------------------------------------------------- expiry


def test_a_temporary_artifact_expires_after_a_day(tmp_path: Path) -> None:
    path = aged_file(tmp_path, "tmp.parquet", days_old=2)
    assert policy(tmp_path).is_expired(path, ArtifactClass.TEMPORARY)


def test_a_fresh_artifact_is_not_expired(tmp_path: Path) -> None:
    path = aged_file(tmp_path, "tmp.parquet", days_old=0.1)
    assert not policy(tmp_path).is_expired(path, ArtifactClass.TEMPORARY)


def test_an_uploaded_pdf_outlives_a_temporary_file(tmp_path: Path) -> None:
    path = aged_file(tmp_path, "report.pdf", days_old=30)
    assert not policy(tmp_path).is_expired(path, ArtifactClass.UPLOADED_PDF)
    assert policy(tmp_path).is_expired(path, ArtifactClass.TEMPORARY)


# --------------------------------------------------------------------------- immutability


def test_a_published_output_never_expires(tmp_path: Path) -> None:
    """A library entry points at it; deleting it dangles the reference."""
    path = aged_file(tmp_path, "published.parquet", days_old=3650)
    assert not policy(tmp_path).is_expired(path, ArtifactClass.PUBLISHED_OUTPUT)


def test_a_sweep_never_removes_a_published_output(tmp_path: Path) -> None:
    published = aged_file(tmp_path, "published.parquet", days_old=3650)
    temporary = aged_file(tmp_path, "scratch.parquet", days_old=3650)

    report = policy(tmp_path).sweep(
        [
            (published, ArtifactClass.PUBLISHED_OUTPUT),
            (temporary, ArtifactClass.TEMPORARY),
        ]
    )
    assert published.exists()
    assert not temporary.exists()
    assert [entry.artifact_class for entry in report.removed] == [ArtifactClass.TEMPORARY]


def test_sweep_reports_what_it_reclaimed(tmp_path: Path) -> None:
    aged_file(tmp_path, "a.parquet", days_old=10)
    report = policy(tmp_path).sweep(
        [(tmp_path / "a.parquet", ArtifactClass.TEMPORARY)]
    )
    assert report.bytes_reclaimed == 128


def test_sweeping_a_missing_path_is_not_an_error(tmp_path: Path) -> None:
    report = policy(tmp_path).sweep(
        [(tmp_path / "gone.parquet", ArtifactClass.TEMPORARY)]
    )
    assert report.removed == []


# --------------------------------------------------------------------------- pressure


def test_intake_stops_when_free_space_falls_below_the_floor(tmp_path: Path) -> None:
    """Refusing work is recoverable; a job dying mid-write leaves fake output.

    The free fraction is injected rather than measured: a test that reads the
    real disk passes or fails according to how full the developer's machine is,
    which tells you nothing about the threshold logic.
    """
    roomy = RetentionPolicy(tmp_path, now=0.0, free_fraction_source=lambda: 0.50)
    tight = RetentionPolicy(tmp_path, now=0.0, free_fraction_source=lambda: 0.02)
    assert roomy.accepting_new_jobs() is True
    assert tight.accepting_new_jobs() is False


def test_a_sweep_reports_that_intake_is_suspended(tmp_path: Path) -> None:
    tight = RetentionPolicy(tmp_path, now=0.0, free_fraction_source=lambda: 0.02)
    assert tight.sweep([]).accepting_new_jobs is False


def test_every_artifact_class_has_a_declared_retention() -> None:
    """A class without a policy would be swept by accident or never at all."""
    from factor_platform.execution.retention import RETENTION

    assert set(RETENTION) == set(ArtifactClass)
