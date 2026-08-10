"""``factor-worker`` — the entry point that runs on the isolated host.

Separate from the main CLI on purpose. That one loads ``Settings``, which requires
the Wind and model credentials; this one must start on a host that has none of
them. Sharing an entry point would mean the worker imports a settings object it is
not allowed to populate, and the first symptom would be a startup failure on the
machine that is *correctly* configured — with no secrets.

The only secret this reads is the manifest signing key, and only to verify.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from factor_platform.execution.job_store import JobStore
from factor_platform.execution.recovery import RecoveryScanner
from factor_platform.execution.worker import Worker

app = typer.Typer(add_completion=False, help="Isolated factor execution worker.")

_KEY_ENV = "MANIFEST_SIGNING_KEY"


def _signing_key() -> str:
    key = os.environ.get(_KEY_ENV)
    if not key:
        typer.echo(
            f"error: {_KEY_ENV} is not set. The worker needs it to verify manifests; "
            "it is the only secret this process should ever hold.",
            err=True,
        )
        raise typer.Exit(code=3)
    return key


@app.command("run-once")
def run_once(
    job_root: Annotated[Path, typer.Option(help="Queue directory")],
    artifact_root: Annotated[Path, typer.Option(help="Where results are written")],
    input_root: Annotated[Path, typer.Option(help="Where input Parquet lives")],
    worker_id: Annotated[str, typer.Option(help="Identity recorded on the lease")] = "worker-1",
) -> None:
    """Claim and execute one queued manifest, then exit."""
    worker = Worker(
        JobStore(job_root),
        signing_key=_signing_key(),
        artifact_root=artifact_root,
        input_root=input_root,
        worker_id=worker_id,
    )
    result = worker.run_once()
    typer.echo(f"{result.status}" + (f" job={result.job_id}" if result.job_id else ""))
    if result.error is not None:
        typer.echo(f"  {result.error.category.value}/{result.error.code}: {result.error.message}")
    if result.status == "failed":
        raise typer.Exit(code=1)


@app.command("recover")
def recover(
    job_root: Annotated[Path, typer.Option(help="Queue directory")],
    artifact_root: Annotated[Path, typer.Option(help="Where results are written")],
) -> None:
    """Settle jobs whose lease expired.

    Reports every decision. A silent requeue makes a worker that keeps dying look
    like one that is merely slow.
    """
    outcomes = RecoveryScanner(JobStore(job_root), artifact_root=artifact_root).scan()
    if not outcomes:
        typer.echo("no expired leases")
        return
    for outcome in outcomes:
        typer.echo(f"{outcome.outcome}\t{outcome.job_id}\t{outcome.reason}")


@app.command("serve")
def serve(
    job_root: Annotated[Path, typer.Option(help="Queue directory")],
    artifact_root: Annotated[Path, typer.Option(help="Where results are written")],
    input_root: Annotated[Path, typer.Option(help="Where input Parquet lives")],
    max_jobs: Annotated[int, typer.Option(help="Stop after this many jobs")] = 0,
) -> None:
    """Drain the queue, then exit. ``max_jobs=0`` means until empty."""
    worker = Worker(
        JobStore(job_root),
        signing_key=_signing_key(),
        artifact_root=artifact_root,
        input_root=input_root,
    )
    processed = 0
    while max_jobs == 0 or processed < max_jobs:
        result = worker.run_once()
        if result.status == "idle":
            break
        processed += 1
        typer.echo(f"{result.status}\t{result.job_id}")
    typer.echo(f"processed {processed} job(s)")


if __name__ == "__main__":  # pragma: no cover
    app()
