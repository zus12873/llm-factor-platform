"""Tests for the isolated worker.

The worker's security property is negative and therefore easy to lose silently:
it must hold no credential that reaches Wind or a model provider. Nothing fails
when that stops being true — the job still runs, the factor still computes — so
it needs a test that asserts the absence directly.

The rest is about not producing misleading artifacts: a cancelled run writes
nothing, and a manifest that fails verification never reaches the compute path.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

from factor_platform.domain.models import ErrorCategory
from factor_platform.execution import worker as worker_module
from factor_platform.execution.job_store import JobStatus, JobStore, ManualClock
from factor_platform.execution.manifest import InputArtifact, sign
from factor_platform.execution.runtime import ManifestRuntime, RuntimeError_
from factor_platform.execution.worker import Worker
from tests.execution.test_manifest import SIGNING_KEY, build

DATES = pd.date_range("2024-01-01", periods=30, freq="B")
CODES = ["600519.SH", "000001.SZ"]


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(start=1_700_000_000.0)


@pytest.fixture
def store(tmp_path: Path, clock: ManualClock) -> JobStore:
    return JobStore(tmp_path / "jobs", clock=clock)


@pytest.fixture
def worker(tmp_path: Path, store: JobStore) -> Worker:
    return Worker(
        store,
        signing_key=SIGNING_KEY,
        artifact_root=tmp_path / "artifacts",
        input_root=tmp_path / "inputs",
    )


def queue_job(store: JobStore, tmp_path: Path, *, key: str = SIGNING_KEY):
    """Write inputs, hash them, then build the manifest around those hashes.

    This is also the production order. Building the manifest first and hashing
    later would let the two drift, and the whole point of pinning inputs by
    content is that they cannot.
    """
    job_dir = tmp_path / "inputs" / "staging"
    job_dir.mkdir(parents=True, exist_ok=True)
    prices = pd.DataFrame(
        [[100.0 + i, 50.0 + i * 0.5] for i in range(len(DATES))],
        index=DATES,
        columns=CODES,
    )
    staged = job_dir / "close.parquet"
    prices.to_parquet(staged)
    digest = hashlib.sha256(staged.read_bytes()).hexdigest()

    manifest = build(inputs=[InputArtifact(uri=staged.as_uri(), sha256=digest, rows=len(DATES))])
    signed = sign(manifest, key=key)
    job_id = store.enqueue(
        session_id="s1",
        session_version=1,
        manifest_sha256=manifest.sha256,
        input_sha256=digest,
        signed_payload=signed.payload,
        signature=signed.signature,
    )
    inputs = tmp_path / "inputs" / job_id
    inputs.mkdir(parents=True, exist_ok=True)
    shutil.copy(staged, inputs / "close.parquet")
    return job_id, manifest


# --------------------------------------------------------------------------- secrets


def test_the_worker_environment_contains_no_credentials(worker: Worker) -> None:
    """Nothing breaks when this stops being true, so assert it directly."""
    environment = worker.clean_environment()
    joined = " ".join(f"{k}={v}" for k, v in environment.items()).lower()
    for forbidden in ("password", "api_key", "secret", "token", "wind_"):
        assert forbidden not in joined


def test_the_environment_is_built_from_an_allowlist(
    worker: Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filtered copy would leak every variable nobody remembered to deny."""
    monkeypatch.setenv("WIND_PASSWORD", "unit-test-only")  # pragma: allowlist secret
    monkeypatch.setenv("SOME_FUTURE_SECRET", "unit-test-only")  # pragma: allowlist secret
    assert "WIND_PASSWORD" not in worker.clean_environment()
    assert "SOME_FUTURE_SECRET" not in worker.clean_environment()


def test_runtime_cannot_read_parent_wind_or_model_credentials(
    tmp_path: Path,
    store: JobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InspectingRuntime:
        def execute(self, manifest, input_dir, output_dir):  # noqa: ANN001
            for forbidden in ("WIND_PASSWORD", "KIMI_CODING_API_KEY"):
                assert forbidden not in os.environ
            return worker_module.ManifestRuntime().execute(manifest, input_dir, output_dir)

    monkeypatch.setenv("WIND_PASSWORD", "unit-test-only")  # pragma: allowlist secret
    monkeypatch.setenv("KIMI_CODING_API_KEY", "unit-test-only")  # pragma: allowlist secret
    isolated = Worker(
        store,
        signing_key=SIGNING_KEY,
        artifact_root=tmp_path / "artifacts",
        input_root=tmp_path / "inputs",
        runtime=InspectingRuntime(),  # type: ignore[arg-type]
    )
    queue_job(store, tmp_path)
    assert isolated.run_once().status == "completed"


def test_resource_limits_are_optional_on_non_posix_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows has no ``resource`` module; importing or running a worker must work."""
    monkeypatch.setattr(worker_module, "_RESOURCE", None)
    worker_module._apply_resource_limits()


# --------------------------------------------------------------------------- happy path


def test_a_signed_job_runs_to_completion(worker: Worker, store: JobStore, tmp_path: Path) -> None:
    job_id, _ = queue_job(store, tmp_path)
    result = worker.run_once()

    assert result.status == "completed"
    assert result.job_id == job_id
    assert store.status_of(job_id) is JobStatus.COMPLETED
    assert (tmp_path / "artifacts" / job_id / "result.parquet").exists()
    assert (tmp_path / "artifacts" / job_id / "result.json").exists()


def test_runtime_uses_warmup_for_compute_but_trims_it_from_output(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    dates = pd.to_datetime(["2023-12-29", "2024-01-02", "2024-06-28", "2024-07-01"])
    prices = pd.DataFrame([[1.0], [2.0], [3.0], [4.0]], index=dates, columns=CODES[:1])
    path = input_dir / "close.parquet"
    prices.to_parquet(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = build(inputs=[InputArtifact(uri=path.as_uri(), sha256=digest, rows=len(prices))])

    result = ManifestRuntime().execute(manifest, input_dir, tmp_path / "output")
    factor = pd.read_parquet(tmp_path / "output" / "result.parquet")

    assert result.rows == 2
    assert list(factor.index) == list(pd.to_datetime(["2024-01-02", "2024-06-28"]))


def test_relative_roots_produce_an_absolute_result_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    relative_store = JobStore("jobs")
    relative_worker = Worker(
        relative_store,
        signing_key=SIGNING_KEY,
        artifact_root="artifacts",
        input_root="inputs",
    )
    queue_job(relative_store, tmp_path)
    result = relative_worker.run_once()
    assert result.status == "completed"
    assert result.result_uri is not None
    assert result.result_uri.startswith("file:///")


def test_the_result_records_the_manifest_it_came_from(
    worker: Worker, store: JobStore, tmp_path: Path
) -> None:
    _, manifest = queue_job(store, tmp_path)
    result = worker.run_once()
    assert result.runtime is not None
    assert result.runtime.manifest_sha256 == manifest.sha256


def test_an_empty_queue_is_idle_not_an_error(worker: Worker) -> None:
    assert worker.run_once().status == "idle"


# --------------------------------------------------------------------------- refusals


def test_a_tampered_manifest_never_reaches_the_compute_path(
    worker: Worker, store: JobStore, tmp_path: Path
) -> None:
    job_id, _ = queue_job(store, tmp_path, key="a-different-key")  # pragma: allowlist secret
    result = worker.run_once()

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category is ErrorCategory.INPUT
    assert result.error.code == "manifest_verification_failed"
    assert not (tmp_path / "artifacts" / job_id / "result.parquet").exists()


def test_a_cancelled_job_writes_no_result(worker: Worker, store: JobStore, tmp_path: Path) -> None:
    """A partial artifact is worse than none: it looks like output."""
    job_id, _ = queue_job(store, tmp_path)
    claimed = store.claim_next("other-worker")
    assert claimed is not None
    store.cancel(job_id)
    store.requeue(job_id, reason="test setup")

    result = worker.run_once()
    assert result.status == "cancelled"
    assert store.status_of(job_id) is JobStatus.CANCELLED
    assert not (tmp_path / "artifacts" / job_id / "result.parquet").exists()


def test_missing_inputs_fail_the_job_rather_than_producing_an_empty_factor(
    worker: Worker, store: JobStore, tmp_path: Path
) -> None:
    manifest = build()
    signed = sign(manifest, key=SIGNING_KEY)
    store.enqueue(
        session_id="s2",
        session_version=1,
        manifest_sha256=manifest.sha256,
        input_sha256="i" * 64,
        signed_payload=signed.payload,
        signature=signed.signature,
    )
    result = worker.run_once()
    assert result.status == "failed"
    assert result.error is not None
    assert "missing" in result.error.message.lower()


def test_every_manifest_input_hash_must_match_not_merely_one(
    tmp_path: Path,
) -> None:
    """Regression: one matching hash must not mask another missing artifact."""
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    prices = pd.DataFrame([[1.0], [2.0]], index=DATES[:2], columns=CODES[:1])
    path = input_dir / "close.parquet"
    prices.to_parquet(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = build(
        inputs=[
            InputArtifact(uri=path.as_uri(), sha256=digest, rows=2),
            InputArtifact(uri="file:///missing.parquet", sha256="b" * 64, rows=2),
        ]
    )

    with pytest.raises(RuntimeError_, match="hashes do not match"):
        ManifestRuntime().execute(manifest, input_dir, tmp_path / "output")
