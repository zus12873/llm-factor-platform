"""Executes a verified manifest against local Parquet inputs.

This is the only code that computes a factor value, and it reaches nothing
outside the job directory: no database, no network, no model. Everything it needs
arrived as a signed manifest plus Parquet files whose hashes the manifest pins.

The order below is the manifest's order, not a convention chosen here:
``variables``-targeted preprocessing runs on the raw inputs, the formula is
evaluated, then ``factor``-targeted preprocessing runs on the result. Getting that
sequence from the manifest rather than from code is what makes two runs of the
same manifest produce the same numbers.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from factor_platform.domain.errors import DomainError
from factor_platform.execution.manifest import Manifest
from factor_platform.factor.compiler import FormulaCompiler
from factor_platform.factor.pipeline_executor import PipelineContext, PipelineExecutor


class RuntimeError_(DomainError):
    """Raised when a manifest cannot be executed against the inputs supplied."""


class RuntimeResult(BaseModel):
    """What one execution produced, plus the hashes that pin it."""

    rows: int
    columns: int
    result_sha256: str
    manifest_sha256: str
    input_sha256: list[str] = Field(default_factory=list)
    pipeline_trace: list[dict[str, object]] = Field(default_factory=list)
    non_null_rate: float = 0.0


class ManifestRuntime:
    """Runs a verified manifest and writes its result artifacts."""

    def __init__(
        self,
        compiler: FormulaCompiler | None = None,
        pipeline: PipelineExecutor | None = None,
    ) -> None:
        self._compiler = compiler or FormulaCompiler()
        self._pipeline = pipeline or PipelineExecutor()

    def execute(
        self,
        manifest: Manifest,
        input_dir: Path,
        output_dir: Path,
        *,
        industries: dict[str, str] | None = None,
    ) -> RuntimeResult:
        variables = self._load_inputs(manifest, input_dir)

        processed, _, pre_trace = self._pipeline.apply(
            _variables_only(manifest),
            variables,
            _empty_like(variables),
            PipelineContext(industries=industries or {}),
        )
        factor = self._compiler.evaluate(manifest.factor_spec.formula_ast, processed)
        _, factor, post_trace = self._pipeline.apply(
            _factor_only(manifest),
            {},
            factor,
            PipelineContext(industries=industries or {}),
        )
        factor = _trim_to_requested_range(factor, manifest)

        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "result.parquet"
        factor.to_parquet(result_path)

        return RuntimeResult(
            rows=int(factor.shape[0]),
            columns=int(factor.shape[1]),
            result_sha256=_sha256(result_path),
            manifest_sha256=manifest.sha256,
            input_sha256=[artifact.sha256 for artifact in manifest.input_artifacts],
            pipeline_trace=[
                {"order": e.order, "operation": e.operation, "target": e.target}
                for e in (*pre_trace, *post_trace)
            ],
            non_null_rate=float(factor.notna().to_numpy().mean()) if factor.size else 0.0,
        )

    def _load_inputs(self, manifest: Manifest, input_dir: Path) -> dict[str, pd.DataFrame]:
        """Load one Parquet per confirmed variable, verifying each hash.

        A mismatch is fatal rather than a warning: the manifest promised a
        specific input, and computing from a different one produces a result that
        claims a provenance it does not have.
        """
        variables: dict[str, pd.DataFrame] = {}
        for selection in manifest.field_selections:
            path = input_dir / f"{selection.logical_name}.parquet"
            if not path.exists():
                raise RuntimeError_(f"input for {selection.logical_name!r} is missing at {path}")
            variables[selection.logical_name] = pd.read_parquet(path)

        expected = sorted(artifact.sha256 for artifact in manifest.input_artifacts)
        actual = sorted(
            _sha256(input_dir / f"{s.logical_name}.parquet") for s in manifest.field_selections
        )
        if expected != actual:
            raise RuntimeError_(
                "input artifact hashes do not match the manifest; the inputs on "
                "disk are not the ones this manifest was built for"
            )
        return variables


def _variables_only(manifest: Manifest):
    pipeline = manifest.preprocessing.model_copy(deep=True)
    pipeline.steps = manifest.preprocessing.steps_for("variables")
    return pipeline


def _factor_only(manifest: Manifest):
    pipeline = manifest.preprocessing.model_copy(deep=True)
    pipeline.steps = manifest.preprocessing.steps_for("factor")
    return pipeline


def _empty_like(variables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not variables:
        return pd.DataFrame()
    template = next(iter(variables.values()))
    return pd.DataFrame(index=template.index, columns=template.columns, dtype=float)


def _trim_to_requested_range(factor: pd.DataFrame, manifest: Manifest) -> pd.DataFrame:
    """Keep warm-up rows as inputs but never publish them as requested output."""
    start = manifest.execution_plan.metadata.get("start_date")
    end = manifest.execution_plan.metadata.get("end_date")
    if not isinstance(start, str) or not isinstance(end, str) or factor.empty:
        return factor
    dates = pd.to_datetime(factor.index)
    mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    return factor.loc[mask]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = ["ManifestRuntime", "RuntimeResult", "RuntimeError_"]
