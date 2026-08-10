"""Tests for the file-backed job queue.

The queue is four directories and atomic renames. That choice is deliberate: on
one filesystem, a rename either succeeds or fails, so two workers racing for the
same job resolve without a lock, a heartbeat, or a broker.

What renames cannot solve is a worker that dies holding a job — the file sits in
``running`` forever and nothing retries it. Hence leases: a claim carries an
expiry, and recovery decides what to do when it passes. The decision is not
"retry": a worker can die *after* writing a complete result, and re-running that
job would duplicate work and overwrite an artifact someone may already have read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.execution.job_store import JobStatus, JobStore, ManualClock


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(start=1_700_000_000.0)


@pytest.fixture
def store(tmp_path: Path, clock: ManualClock) -> JobStore:
    return JobStore(tmp_path / "jobs", clock=clock)


def enqueue(store: JobStore, *, version: int = 1, manifest: str = "m" * 64) -> str:
    return store.enqueue(
        session_id="s1",
        session_version=version,
        manifest_sha256=manifest,
        input_sha256="i" * 64,
        signed_payload='{"stub":true}',
        signature="sig",
    )


# --------------------------------------------------------------------------- claiming


def test_a_job_can_be_claimed_only_once(store: JobStore) -> None:
    job_id = enqueue(store)
    claimed = store.claim_next("worker-1")
    assert claimed is not None
    assert claimed.job_id == job_id
    assert store.claim_next("worker-2") is None


def test_claiming_moves_the_job_out_of_pending(store: JobStore) -> None:
    enqueue(store)
    assert store.pending_count() == 1
    store.claim_next("worker-1")
    assert store.pending_count() == 0
    assert store.running_count() == 1


def test_claim_records_the_worker_and_a_lease(store: JobStore) -> None:
    enqueue(store)
    job = store.claim_next("worker-1")
    assert job is not None
    assert job.claimed_by == "worker-1"
    assert job.lease_expires_at > job.claimed_at


def test_claiming_an_empty_queue_returns_none(store: JobStore) -> None:
    assert store.claim_next("worker-1") is None


def test_jobs_are_claimed_oldest_first(store: JobStore, clock: ManualClock) -> None:
    first = enqueue(store, version=1)
    clock.advance(1.0)
    enqueue(store, version=2)
    assert store.claim_next("worker-1").job_id == first


# --------------------------------------------------------------------------- idempotency


def test_an_identical_request_reuses_the_existing_job(store: JobStore) -> None:
    """Two clicks on Run must not queue the same computation twice."""
    first = enqueue(store)
    second = enqueue(store)
    assert first == second
    assert store.pending_count() == 1


def test_a_new_session_version_is_a_different_job(store: JobStore) -> None:
    """A revision produces a genuinely different computation."""
    assert enqueue(store, version=1) != enqueue(store, version=2)


def test_a_different_manifest_is_a_different_job(store: JobStore) -> None:
    assert enqueue(store, manifest="a" * 64) != enqueue(store, manifest="b" * 64)


# --------------------------------------------------------------------------- lifecycle


def test_completing_moves_the_job_to_completed(store: JobStore) -> None:
    enqueue(store)
    job = store.claim_next("worker-1")
    store.complete(job.job_id, result_uri="file:///out.parquet")
    assert store.running_count() == 0
    assert store.status_of(job.job_id) is JobStatus.COMPLETED


def test_failing_records_the_reason(store: JobStore) -> None:
    enqueue(store)
    job = store.claim_next("worker-1")
    store.fail(job.job_id, reason="manifest_verification_failed")
    assert store.status_of(job.job_id) is JobStatus.FAILED
    assert store.load(job.job_id).failure_reason == "manifest_verification_failed"


def test_renewing_extends_the_lease(store: JobStore, clock: ManualClock) -> None:
    enqueue(store)
    job = store.claim_next("worker-1")
    original = job.lease_expires_at
    clock.advance(10.0)
    store.renew_lease(job.job_id)
    assert store.load(job.job_id).lease_expires_at > original


# --------------------------------------------------------------------------- cancellation


def test_cancelling_a_pending_job_removes_it_from_the_queue(store: JobStore) -> None:
    job_id = enqueue(store)
    store.cancel(job_id)
    assert store.pending_count() == 0
    assert store.status_of(job_id) is JobStatus.CANCELLED


def test_cancelling_a_running_job_only_flags_it(store: JobStore) -> None:
    """A running job is mid-flight; the worker stops at its next checkpoint."""
    enqueue(store)
    job = store.claim_next("worker-1")
    store.cancel(job.job_id)
    assert store.status_of(job.job_id) is JobStatus.RUNNING
    assert store.load(job.job_id).cancel_requested is True


# --------------------------------------------------------------------------- durability


def test_an_enqueued_job_is_never_visible_half_written(
    tmp_path: Path, clock: ManualClock
) -> None:
    """Workers scan the directory; a partially written file would be claimed."""
    store = JobStore(tmp_path / "jobs", clock=clock)
    enqueue(store)
    for path in (tmp_path / "jobs" / "pending").glob("*"):
        assert path.suffix == ".json", f"non-atomic temp file left behind: {path}"


def test_the_queue_survives_a_restart(tmp_path: Path, clock: ManualClock) -> None:
    root = tmp_path / "jobs"
    job_id = enqueue(JobStore(root, clock=clock))
    assert JobStore(root, clock=clock).claim_next("worker-2").job_id == job_id
