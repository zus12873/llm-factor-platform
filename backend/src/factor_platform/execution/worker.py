"""The isolated worker: claim, verify, execute, settle.

The worker holds exactly one secret — the manifest signing key — and it holds that
only to *verify*, not to sign. It has no database credential and no model key, so
compromising it yields nothing that reaches Wind or an external provider.

Three habits keep that true:

* **The environment is built from an allowlist, not filtered.** Copying the parent
  environment and deleting known secrets fails the moment someone adds a new one;
  starting empty and adding what is needed fails safe by construction.
* **The manifest is verified here, independently.** The orchestrator already
  verified it, but "someone upstream checked" is not a property the worker can
  observe — and the job file sat on disk in between.
* **Cancellation is checked at explicit points**, and a cancelled job writes no
  result. A half-written artifact from an abandoned run is worse than none: it
  looks like output.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel

from factor_platform.domain.models import ErrorCategory, StructuredError
from factor_platform.execution.job_store import Job, JobStore
from factor_platform.execution.manifest import (
    ManifestVerificationError,
    SignedManifest,
    verify,
)
from factor_platform.execution.runtime import ManifestRuntime, RuntimeResult

#: Resource ceilings applied before any manifest runs.
DEFAULT_CPU_SECONDS = 900
DEFAULT_MEMORY_BYTES = 4 * 1024**3
DEFAULT_FILE_SIZE_BYTES = 2 * 1024**3


def _load_resource_module() -> ModuleType | None:
    """Return the POSIX resource module when this platform provides it."""
    try:
        return importlib.import_module("resource")
    except ModuleNotFoundError:
        return None


_RESOURCE = _load_resource_module()


class WorkerResult(BaseModel):
    """Outcome of one ``run_once``, including what the environment contained."""

    status: str
    job_id: str | None = None
    result_uri: str | None = None
    error: StructuredError | None = None
    environment_keys: list[str] = []
    runtime: RuntimeResult | None = None


class Worker:
    """Executes one queued manifest per :meth:`run_once`."""

    def __init__(
        self,
        store: JobStore,
        *,
        signing_key: str,
        artifact_root: Path | str,
        input_root: Path | str,
        worker_id: str = "worker-1",
        runtime: ManifestRuntime | None = None,
        app_env: str = "production",
    ) -> None:
        self._store = store
        self._key = signing_key
        self._artifacts = Path(artifact_root)
        self._inputs = Path(input_root)
        self._worker_id = worker_id
        self._runtime = runtime or ManifestRuntime()
        self._app_env = app_env

    # ------------------------------------------------------------------ environment

    def clean_environment(self) -> dict[str, str]:
        """Build the execution environment from an allowlist.

        Deliberately not ``os.environ.copy()`` minus known secrets: that approach
        leaks every variable nobody thought to add to the deny list, and the list
        is only ever updated after something leaks.
        """
        return {
            "PYTHONUNBUFFERED": "1",
            "PYTHONHASHSEED": "0",  # keep any dict-order-sensitive output stable
            "APP_ENV": self._app_env,
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(self._artifacts / "tmp"),
        }

    # ------------------------------------------------------------------ run

    def run_once(self) -> WorkerResult:
        job = self._store.claim_next(self._worker_id)
        if job is None:
            return WorkerResult(status="idle", environment_keys=[])

        environment = self.clean_environment()

        if job.cancel_requested:
            return self._cancelled(job, environment)

        try:
            manifest = verify(
                SignedManifest(
                    payload=job.signed_payload,
                    signature=job.signature,
                    sha256=job.manifest_sha256,
                ),
                key=self._key,
            )
        except ManifestVerificationError as exc:
            return self._failed(
                job,
                environment,
                StructuredError(
                    category=ErrorCategory.INPUT,
                    code="manifest_verification_failed",
                    message=str(exc),
                ),
            )

        # Second checkpoint: verification is the slowest step before compute, and
        # a cancel arriving during it should still stop the run.
        if self._store.load(job.job_id).cancel_requested:
            return self._cancelled(job, environment)

        _apply_resource_limits()
        output_dir = self._artifacts / job.job_id
        try:
            with _isolated_environment(environment):
                runtime_result = self._runtime.execute(
                    manifest, self._inputs / job.job_id, output_dir
                )
        except Exception as exc:  # noqa: BLE001 - classified and reported, not swallowed
            return self._failed(
                job,
                environment,
                StructuredError(
                    category=ErrorCategory.INFRASTRUCTURE,
                    code="execution_failed",
                    message=str(exc),
                ),
            )

        (output_dir / "result.json").write_text(
            runtime_result.model_dump_json(indent=2), encoding="utf-8"
        )
        result_uri = (output_dir / "result.parquet").resolve().as_uri()
        self._store.complete(job.job_id, result_uri=result_uri)
        return WorkerResult(
            status="completed",
            job_id=job.job_id,
            result_uri=result_uri,
            environment_keys=sorted(environment),
            runtime=runtime_result,
        )

    # ------------------------------------------------------------------ outcomes

    def _cancelled(self, job: Job, environment: Mapping[str, str]) -> WorkerResult:
        """Settle without writing a result.

        A half-written artifact from an abandoned run is worse than no artifact:
        downstream code cannot tell it apart from a finished one.
        """
        self._store.settle_cancelled(
            job.job_id, reason="worker observed cancel_requested at a checkpoint"
        )
        return WorkerResult(
            status="cancelled", job_id=job.job_id, environment_keys=sorted(environment)
        )

    def _failed(
        self, job: Job, environment: Mapping[str, str], error: StructuredError
    ) -> WorkerResult:
        self._store.fail(job.job_id, reason=error.code)
        return WorkerResult(
            status="failed",
            job_id=job.job_id,
            error=error,
            environment_keys=sorted(environment),
        )


def _apply_resource_limits() -> None:
    """Cap CPU, address space and file size before running anything.

    Best-effort: a platform that refuses a limit should not stop the job, but the
    limits that do apply bound a runaway computation to this process.
    """
    if _RESOURCE is None:
        return

    getrlimit = getattr(_RESOURCE, "getrlimit", None)
    setrlimit = getattr(_RESOURCE, "setrlimit", None)
    infinity = getattr(_RESOURCE, "RLIM_INFINITY", None)
    limits = (
        (getattr(_RESOURCE, "RLIMIT_CPU", None), DEFAULT_CPU_SECONDS),
        (getattr(_RESOURCE, "RLIMIT_AS", None), DEFAULT_MEMORY_BYTES),
        (getattr(_RESOURCE, "RLIMIT_FSIZE", None), DEFAULT_FILE_SIZE_BYTES),
    )
    if (
        not callable(getrlimit)
        or not callable(setrlimit)
        or infinity is None
        or any(which is None for which, _ in limits)
    ):
        return

    for which, limit in limits:
        assert which is not None
        try:
            soft, hard = getrlimit(which)
            ceiling = limit if hard == infinity else min(limit, hard)
            setrlimit(which, (ceiling, hard))
        except (ValueError, OSError):
            continue


@contextlib.contextmanager
def _isolated_environment(environment: Mapping[str, str]):
    """Expose only the worker allowlist while executing the manifest runtime.

    Production workers already run in their own process/container. Applying the
    same boundary here also protects the single-process CLI acceptance path,
    where the backend fetched Wind data immediately before invoking a worker.
    """
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def read_result(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["Worker", "WorkerResult", "read_result"]
