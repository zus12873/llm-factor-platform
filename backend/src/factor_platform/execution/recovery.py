"""Lease recovery: deciding what an expired claim actually means.

An expired lease says the worker stopped reporting. It does not say the work was
not done. A worker can die between writing a complete result and moving the job
file, and re-running that job would repeat the computation and overwrite an
artifact a user may already have opened.

So recovery looks at the result before deciding, and has three outcomes:

* a **complete** result on disk — settle the job as completed;
* no result, attempts remaining — return it to the queue;
* no result, attempts exhausted — fail it.

The ceiling matters as much as the retry. A job that fails the same way three
times will not succeed on the fourth, and an unbounded loop turns a specific error
into an endlessly busy queue with no visible cause.

"Complete" means both the data artifact and its result JSON are present. Parquet
alone is a worker that died mid-write, and treating it as finished would publish a
truncated factor.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from factor_platform.execution.job_store import Clock, JobStore, SystemClock


class RecoveryOutcome(BaseModel):
    """What recovery did to one job, and why."""

    job_id: str
    outcome: str
    reason: str


class RecoveryScanner:
    """Scans running jobs and settles the ones whose lease has expired."""

    def __init__(
        self,
        store: JobStore,
        *,
        artifact_root: Path | str,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._artifacts = Path(artifact_root)
        self._clock = clock or SystemClock()

    def scan(self) -> list[RecoveryOutcome]:
        now = self._clock.now()
        outcomes: list[RecoveryOutcome] = []

        for job in self._store.running_jobs():
            if job.lease_expires_at is None or job.lease_expires_at > now:
                continue

            if job.cancel_requested:
                self._store.settle_cancelled(
                    job.job_id, reason="cancel requested before the lease expired"
                )
                outcomes.append(
                    RecoveryOutcome(
                        job_id=job.job_id,
                        outcome="cancelled",
                        reason="cancel was requested before the lease expired",
                    )
                )
                continue

            if self._has_complete_result(job.job_id):
                self._store.complete(
                    job.job_id,
                    result_uri=(self._artifacts / job.job_id / "result.parquet").as_uri(),
                )
                outcomes.append(
                    RecoveryOutcome(
                        job_id=job.job_id,
                        outcome="completed",
                        reason="lease expired but a complete result was already written",
                    )
                )
                continue

            if job.attempt >= job.max_attempts:
                self._store.fail(job.job_id, reason="max_attempts_exceeded")
                outcomes.append(
                    RecoveryOutcome(
                        job_id=job.job_id,
                        outcome="failed",
                        reason="max_attempts_exceeded",
                    )
                )
                continue

            self._store.requeue(
                job.job_id,
                reason=(
                    f"lease expired at {job.lease_expires_at} with no result; "
                    f"requeued after attempt {job.attempt}"
                ),
            )
            outcomes.append(
                RecoveryOutcome(
                    job_id=job.job_id,
                    outcome="requeued",
                    reason="lease expired with no result on disk",
                )
            )

        return outcomes

    def _has_complete_result(self, job_id: str) -> bool:
        """Both artifacts must exist; Parquet alone is a half-finished write."""
        directory = self._artifacts / job_id
        return (directory / "result.parquet").exists() and (
            directory / "result.json"
        ).exists()


__all__ = ["RecoveryOutcome", "RecoveryScanner"]
