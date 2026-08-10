"""Tests for lease recovery.

An expired lease means the worker stopped reporting. It does not mean the work
was not done — a worker can die between writing a complete result and moving the
job file. Re-running that job would duplicate the computation and overwrite an
artifact a user may already have opened.

So recovery inspects the result before deciding, and it has three outcomes rather
than one. The attempt ceiling matters for the same reason: a job that fails the
same way three times is not going to succeed on the fourth, and an unbounded retry
loop hides the real error behind an endless queue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.execution.job_store import JobStatus, JobStore, ManualClock
from factor_platform.execution.recovery import RecoveryScanner


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(start=1_700_000_000.0)


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


@pytest.fixture
def store(tmp_path: Path, clock: ManualClock) -> JobStore:
    return JobStore(tmp_path / "jobs", clock=clock, lease_seconds=60.0)


@pytest.fixture
def scanner(store: JobStore, artifacts: Path, clock: ManualClock) -> RecoveryScanner:
    return RecoveryScanner(store, artifact_root=artifacts, clock=clock)


def enqueue_and_claim(store: JobStore, *, version: int = 1):
    store.enqueue(
        session_id="s1",
        session_version=version,
        manifest_sha256="m" * 64,
        input_sha256="i" * 64,
        signed_payload="{}",
        signature="sig",
    )
    return store.claim_next("worker-1")


def write_result(artifacts: Path, job_id: str) -> None:
    (artifacts / job_id).mkdir(parents=True, exist_ok=True)
    (artifacts / job_id / "result.parquet").write_bytes(b"PAR1")
    (artifacts / job_id / "result.json").write_text('{"status":"completed"}', encoding="utf-8")


# --------------------------------------------------------------------------- outcomes


def test_a_live_lease_is_left_alone(
    scanner: RecoveryScanner, store: JobStore
) -> None:
    enqueue_and_claim(store)
    assert scanner.scan() == []


def test_an_expired_lease_without_a_result_is_requeued(
    scanner: RecoveryScanner, store: JobStore, clock: ManualClock
) -> None:
    job = enqueue_and_claim(store)
    clock.advance(61.0)
    outcomes = scanner.scan()
    assert [o.outcome for o in outcomes] == ["requeued"]
    assert store.status_of(job.job_id) is JobStatus.PENDING


def test_an_expired_lease_with_a_complete_result_is_completed(
    scanner: RecoveryScanner, store: JobStore, artifacts: Path, clock: ManualClock
) -> None:
    """The worker finished and then died. Re-running would overwrite its output."""
    job = enqueue_and_claim(store)
    write_result(artifacts, job.job_id)
    clock.advance(61.0)
    outcomes = scanner.scan()
    assert [o.outcome for o in outcomes] == ["completed"]
    assert store.status_of(job.job_id) is JobStatus.COMPLETED


def test_a_partial_result_is_not_treated_as_complete(
    scanner: RecoveryScanner, store: JobStore, artifacts: Path, clock: ManualClock
) -> None:
    """Parquet present but no result JSON: the worker died mid-write."""
    job = enqueue_and_claim(store)
    (artifacts / job.job_id).mkdir(parents=True)
    (artifacts / job.job_id / "result.parquet").write_bytes(b"PAR1")
    clock.advance(61.0)
    assert [o.outcome for o in scanner.scan()] == ["requeued"]


def test_exhausted_attempts_fail_instead_of_looping(
    scanner: RecoveryScanner, store: JobStore, clock: ManualClock
) -> None:
    """An unbounded retry hides the real error behind an endless queue."""
    job = enqueue_and_claim(store)  # attempt 1
    for _ in range(job.max_attempts - 1):
        clock.advance(61.0)
        scanner.scan()  # requeue
        store.claim_next("worker-1")  # next attempt
    clock.advance(61.0)
    outcomes = scanner.scan()
    assert outcomes[0].outcome == "failed"
    assert outcomes[0].reason == "max_attempts_exceeded"
    assert store.status_of(job.job_id) is JobStatus.FAILED


def test_a_cancelled_running_job_is_settled_as_cancelled(
    scanner: RecoveryScanner, store: JobStore, clock: ManualClock
) -> None:
    job = enqueue_and_claim(store)
    store.cancel(job.job_id)
    clock.advance(61.0)
    assert [o.outcome for o in scanner.scan()] == ["cancelled"]
    assert store.status_of(job.job_id) is JobStatus.CANCELLED


# --------------------------------------------------------------------------- audit


def test_every_recovery_is_recorded_on_the_job(
    scanner: RecoveryScanner, store: JobStore, clock: ManualClock
) -> None:
    """Silent requeues make a flaky worker look like a slow one."""
    job = enqueue_and_claim(store)
    clock.advance(61.0)
    scanner.scan()
    assert store.load(job.job_id).recovery_log


def test_scan_reports_the_job_it_acted_on(
    scanner: RecoveryScanner, store: JobStore, clock: ManualClock
) -> None:
    job = enqueue_and_claim(store)
    clock.advance(61.0)
    assert scanner.scan()[0].job_id == job.job_id
