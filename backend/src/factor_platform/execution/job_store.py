"""File-backed job queue: four directories and atomic renames.

There is no broker here, and that is a design choice rather than a shortcut. On a
single filesystem a rename either succeeds or raises; two workers racing for the
same job resolve without a lock, a heartbeat protocol, or a service to keep
running. For a single-host prototype that is the whole coordination problem.

What renames cannot solve is a worker that dies holding a job: the file stays in
``running`` and nothing ever retries it. So a claim carries a **lease** with an
expiry, and :mod:`factor_platform.execution.recovery` decides what to do once it
passes. That decision is deliberately not "retry" — a worker can die *after*
writing a complete result, and blindly re-running would duplicate the work and
overwrite an artifact someone may already have read.

The queue holds no credentials. A job carries a signed manifest and input hashes;
the worker needs the signing key to verify, and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

#: How long a claim is valid before recovery may reclaim the job.
DEFAULT_LEASE_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_MAX_ATTEMPTS = 3


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_DIRECTORIES: tuple[JobStatus, ...] = (
    JobStatus.PENDING,
    JobStatus.RUNNING,
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
)


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class ManualClock:
    """Injectable clock so lease expiry is testable without sleeping."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class Job(BaseModel):
    """One queued computation, and everything needed to recover it."""

    schema_version: int = 1
    job_id: str
    session_id: str
    session_version: int
    idempotency_key: str
    manifest_sha256: str
    input_sha256: str
    signed_payload: str
    signature: str

    claimed_by: str | None = None
    claimed_at: float | None = None
    lease_expires_at: float | None = None
    attempt: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    cancel_requested: bool = False

    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    artifact_retention_until: float | None = None

    result_uri: str | None = None
    failure_reason: str | None = None
    recovery_log: list[str] = Field(default_factory=list)


def idempotency_key(
    session_id: str, session_version: int, manifest_sha256: str, input_sha256: str
) -> str:
    """Identity of a computation, independent of when it was requested.

    Two clicks on Run produce the same key; a revision bumps ``session_version``
    and therefore produces a different one.
    """
    raw = f"{session_id}|{session_version}|{manifest_sha256}|{input_sha256}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class JobStore:
    """Enqueue, claim and settle jobs on the filesystem."""

    def __init__(
        self,
        root: Path | str,
        *,
        clock: Clock | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self._root = Path(root)
        self._clock = clock or SystemClock()
        self._lease_seconds = lease_seconds
        for status in _DIRECTORIES:
            (self._root / status.value).mkdir(parents=True, exist_ok=True)
        (self._root / "tmp").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ enqueue

    def enqueue(
        self,
        *,
        session_id: str,
        session_version: int,
        manifest_sha256: str,
        input_sha256: str,
        signed_payload: str,
        signature: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        """Queue a computation, returning the existing job id if already queued."""
        key = idempotency_key(session_id, session_version, manifest_sha256, input_sha256)
        existing = self._find_by_key(key)
        if existing is not None:
            return existing

        job = Job(
            job_id=uuid.uuid4().hex,
            session_id=session_id,
            session_version=session_version,
            idempotency_key=key,
            manifest_sha256=manifest_sha256,
            input_sha256=input_sha256,
            signed_payload=signed_payload,
            signature=signature,
            max_attempts=max_attempts,
            timeout_seconds=timeout_seconds,
            created_at=self._clock.now(),
        )
        self._write_atomic(JobStatus.PENDING, job)
        return job.job_id

    # ------------------------------------------------------------------ claim

    def claim_next(self, worker_id: str) -> Job | None:
        """Atomically take the oldest pending job, or return ``None``.

        The rename is the entire mutual exclusion: a worker that loses the race
        gets ``FileNotFoundError`` and moves on to the next candidate.
        """
        for path in self._sorted_pending():
            job = _load(path)
            job.claimed_by = worker_id
            job.claimed_at = self._clock.now()
            job.lease_expires_at = job.claimed_at + self._lease_seconds
            job.attempt += 1
            job.started_at = job.started_at or job.claimed_at
            target = self._path(JobStatus.RUNNING, job.job_id)
            try:
                os.rename(path, target)
            except FileNotFoundError:
                continue  # another worker won the race
            target.write_text(job.model_dump_json(indent=2), encoding="utf-8")
            return job
        return None

    def renew_lease(self, job_id: str) -> None:
        job = self.load(job_id)
        job.lease_expires_at = self._clock.now() + self._lease_seconds
        self._overwrite(JobStatus.RUNNING, job)

    # ------------------------------------------------------------------ settle

    def complete(self, job_id: str, *, result_uri: str) -> None:
        job = self.load(job_id)
        job.result_uri = result_uri
        job.finished_at = self._clock.now()
        self._move(job, JobStatus.COMPLETED)

    def fail(self, job_id: str, *, reason: str) -> None:
        job = self.load(job_id)
        job.failure_reason = reason
        job.finished_at = self._clock.now()
        self._move(job, JobStatus.FAILED)

    def requeue(self, job_id: str, *, reason: str) -> None:
        """Return a job to pending after a lease expiry, keeping the attempt count."""
        job = self.load(job_id)
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.recovery_log.append(reason)
        self._move(job, JobStatus.PENDING)

    def settle_cancelled(self, job_id: str, *, reason: str) -> None:
        """Move a running job straight to cancelled.

        Distinct from :meth:`fail`: a cancellation is a decision, not a defect,
        and filing it under failures would make deliberate stops look like an
        unreliable worker in every report that counts them.
        """
        job = self.load(job_id)
        job.cancel_requested = True
        job.finished_at = self._clock.now()
        job.recovery_log.append(reason)
        self._move(job, JobStatus.CANCELLED)

    def cancel(self, job_id: str) -> None:
        """Cancel a pending job outright; flag a running one for its next checkpoint."""
        status = self.status_of(job_id)
        job = self.load(job_id)
        job.cancel_requested = True
        if status is JobStatus.PENDING:
            job.finished_at = self._clock.now()
            self._move(job, JobStatus.CANCELLED)
        else:
            self._overwrite(status, job)

    # ------------------------------------------------------------------ queries

    def load(self, job_id: str) -> Job:
        for status in _DIRECTORIES:
            path = self._path(status, job_id)
            if path.exists():
                return _load(path)
        raise KeyError(f"unknown job: {job_id}")

    def status_of(self, job_id: str) -> JobStatus:
        for status in _DIRECTORIES:
            if self._path(status, job_id).exists():
                return status
        raise KeyError(f"unknown job: {job_id}")

    def pending_count(self) -> int:
        return len(list((self._root / JobStatus.PENDING.value).glob("*.json")))

    def running_count(self) -> int:
        return len(list((self._root / JobStatus.RUNNING.value).glob("*.json")))

    def running_jobs(self) -> list[Job]:
        return [
            _load(path)
            for path in sorted((self._root / JobStatus.RUNNING.value).glob("*.json"))
        ]

    # ------------------------------------------------------------------ internals

    def _sorted_pending(self) -> list[Path]:
        paths = list((self._root / JobStatus.PENDING.value).glob("*.json"))
        return sorted(paths, key=lambda p: (_load(p).created_at, p.name))

    def _path(self, status: JobStatus, job_id: str) -> Path:
        return self._root / status.value / f"{job_id}.json"

    def _write_atomic(self, status: JobStatus, job: Job) -> None:
        """Write to a temp directory first so the queue never shows a partial file."""
        tmp = self._root / "tmp" / f"{job.job_id}.json"
        tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self._path(status, job.job_id))

    def _overwrite(self, status: JobStatus, job: Job) -> None:
        self._path(status, job.job_id).write_text(
            job.model_dump_json(indent=2), encoding="utf-8"
        )

    def _move(self, job: Job, target: JobStatus) -> None:
        current = self.status_of(job.job_id)
        self._write_atomic(target, job)
        if current is not target:
            self._path(current, job.job_id).unlink(missing_ok=True)

    def _find_by_key(self, key: str) -> str | None:
        for status in _DIRECTORIES:
            for path in (self._root / status.value).glob("*.json"):
                if _load(path).idempotency_key == key:
                    return path.stem
        return None


def _load(path: Path) -> Job:
    return Job.model_validate(json.loads(path.read_text(encoding="utf-8")))


def as_dict(job: Job) -> dict[str, Any]:
    return job.model_dump(mode="json")


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "Clock",
    "Job",
    "JobStatus",
    "JobStore",
    "ManualClock",
    "SystemClock",
    "idempotency_key",
]
