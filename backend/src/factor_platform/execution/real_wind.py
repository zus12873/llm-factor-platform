"""Backend-owned Wind retrieval followed by isolated manifest execution.

The database boundary ends before the worker begins. This module resolves the
historical universe, fetches only planner-approved shapes through the Wind
adapter, aligns the inputs, pins their Parquet hashes in a manifest, closes the
Wind connection, and only then queues the no-database worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from factor_platform.domain.models import (
    ExecutionPlan,
    FactorSpec,
    FieldSelection,
    FieldTimeRole,
    ResearchRequest,
    ValidationSeverity,
)
from factor_platform.execution.job_store import JobStore
from factor_platform.execution.manifest import InputArtifact, ManifestBuilder, sign
from factor_platform.execution.worker import Worker
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.settings import Settings
from factor_platform.validation.data import DataValidator
from factor_platform.validation.formula import FormulaValidator
from factor_platform.validation.result import ResultValidator
from factor_platform.wind import adapter
from factor_platform.wind.connection import WindConnectionFactory


class RealWindExecutionError(RuntimeError):
    """Raised when a live run cannot preserve the verified execution contract."""


class RealWindRunResult(BaseModel):
    run_dir: str
    job_id: str
    manifest_sha256: str
    result_rows: int
    result_columns: int
    result_non_null_rate: float
    input_rows: dict[str, int] = Field(default_factory=dict)
    input_securities: dict[str, int] = Field(default_factory=dict)
    worker_environment_keys: list[str] = Field(default_factory=list)
    validation_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    metric_review_status: dict[str, str] = Field(default_factory=dict)


def complete_registered_selections(
    selections: list[FieldSelection], registry: MetricRegistry
) -> list[FieldSelection]:
    """Attach time roles already registered for exact table/field bindings."""
    completed: list[FieldSelection] = []
    for selection in selections:
        definition = registry.get(selection.logical_name)
        if (
            definition is None
            or definition.wind_table.lower() != selection.table.lower()
            or definition.wind_field.lower() != selection.field.lower()
        ):
            completed.append(selection)
            continue
        role = FieldTimeRole(definition.time_role) if definition.time_role else None
        completed.append(
            selection.model_copy(
                update={
                    "time_role": selection.time_role or role,
                    "point_in_time": selection.point_in_time or role is FieldTimeRole.REPORT_PERIOD,
                    "announcement_date_field": selection.announcement_date_field
                    or definition.announcement_field,
                    "report_period_field": selection.report_period_field
                    or ("report_period" if role is FieldTimeRole.REPORT_PERIOD else None),
                }
            )
        )
    return completed


def apply_confirmed_announcement_requirements(
    spec: FactorSpec, selections: list[FieldSelection]
) -> FactorSpec:
    """Record announcement availability only after a field binding proves it."""
    confirmed = {
        selection.logical_name
        for selection in selections
        if selection.point_in_time and selection.announcement_date_field
    }
    updated = spec.model_copy(deep=True)
    for variable in updated.variables:
        if variable.point_in_time_required and variable.logical_name in confirmed:
            variable.announcement_date_required = True
    return updated


class RealWindCaseRunner:
    """Fetch real inputs in the backend and execute only their signed manifest."""

    def __init__(self, settings: Settings, *, registry: MetricRegistry | None = None) -> None:
        self._settings = settings
        self._registry = registry or MetricRegistry.load()

    def run(
        self,
        *,
        case_id: str,
        spec: FactorSpec,
        request: ResearchRequest,
        selections: list[FieldSelection],
        plan: ExecutionPlan,
    ) -> RealWindRunResult:
        signing_secret = os.environ.get("MANIFEST_SIGNING_KEY")
        if not signing_secret:
            raise RealWindExecutionError(
                "MANIFEST_SIGNING_KEY must be present for a real execution"
            )

        run_root = self._run_root(case_id)
        staging = run_root / "inputs" / "staging"
        staging.mkdir(parents=True, exist_ok=False)
        (run_root / "artifacts").mkdir(parents=True, exist_ok=True)

        adapter.configure_factory(WindConnectionFactory(self._settings))
        try:
            membership = self._membership(plan)
            universe_mask = _membership_mask(membership)
            if universe_mask.empty or not len(universe_mask.columns):
                raise RealWindExecutionError("historical universe resolved to no securities")
            codes = list(universe_mask.columns)
            st_mask, suspended_mask = self._filter_masks(plan, codes)
            variables, raw_frames = self._variables(
                plan,
                selections,
                codes,
                universe_mask,
                st_mask,
                suspended_mask,
            )
        finally:
            # The backend connection is closed before manifest signing/queueing;
            # the worker never calls or receives the adapter.
            adapter.close_wind_conn()

        self._write_audit_parquets(
            run_root,
            raw_frames,
            variables,
            universe_mask,
            st_mask,
            suspended_mask,
        )
        artifacts = self._write_worker_inputs(staging, variables)

        metric_keys_by_variable = {
            selection.logical_name: definition.key
            for selection in selections
            if (definition := self._registry.get(selection.logical_name)) is not None
        }
        data_report = DataValidator(self._registry).validate(
            variables,
            expected_start=plan.warmup_start or request.start_date,
            expected_end=request.end_date,
            metric_keys=metric_keys_by_variable,
        )
        formula_report = FormulaValidator().validate(spec)
        _raise_on_errors("data", data_report.findings)
        _raise_on_errors("formula", formula_report.findings)

        manifest = ManifestBuilder().build(spec, plan, selections, artifacts)
        (run_root / "manifest.json").write_text(manifest.canonical_json(), encoding="utf-8")
        signed = sign(manifest, key=signing_secret)
        store = JobStore(run_root / "jobs")
        combined_input_hash = hashlib.sha256(
            "".join(sorted(artifact.sha256 for artifact in artifacts)).encode("ascii")
        ).hexdigest()
        job_id = store.enqueue(
            session_id=f"real-wind-{case_id}",
            session_version=1,
            manifest_sha256=manifest.sha256,
            input_sha256=combined_input_hash,
            signed_payload=signed.payload,
            signature=signed.signature,
        )
        job_inputs = run_root / "inputs" / job_id
        job_inputs.mkdir(parents=True, exist_ok=False)
        for selection in selections:
            shutil.copy2(
                staging / f"{selection.logical_name}.parquet",
                job_inputs / f"{selection.logical_name}.parquet",
            )

        worker = Worker(
            store,
            signing_key=signing_secret,
            artifact_root=run_root / "artifacts",
            input_root=run_root / "inputs",
            app_env=self._settings.app_env,
        )
        outcome = worker.run_once()
        if outcome.status != "completed" or outcome.runtime is None:
            detail = outcome.error.code if outcome.error is not None else outcome.status
            raise RealWindExecutionError(f"worker did not complete: {detail}")

        result_path = run_root / "artifacts" / job_id / "result.parquet"
        factor = pd.read_parquet(result_path)
        metric_keys = [
            definition.key
            for selection in selections
            if (definition := self._registry.get(selection.logical_name)) is not None
        ]
        result_report = ResultValidator(self._registry).validate(
            factor,
            metric_keys=metric_keys,
            # A transformed/composite factor is no longer in any source
            # metric's unit. Source bounds were checked above, before execution.
            apply_metric_bounds=False,
        )
        _raise_on_errors("result", result_report.findings)
        formula_after = FormulaValidator().validate(
            spec, pipeline_trace=outcome.runtime.pipeline_trace
        )
        _raise_on_errors("formula_after_execution", formula_after.findings)

        reports = {
            "data": data_report,
            "formula": formula_after,
            "result": result_report,
        }
        (run_root / "validation.json").write_text(
            json.dumps(
                {name: report.model_dump(mode="json") for name, report in reports.items()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return RealWindRunResult(
            run_dir=str(run_root.resolve()),
            job_id=job_id,
            manifest_sha256=manifest.sha256,
            result_rows=outcome.runtime.rows,
            result_columns=outcome.runtime.columns,
            result_non_null_rate=outcome.runtime.non_null_rate,
            input_rows={name: len(frame) for name, frame in variables.items()},
            input_securities={name: len(frame.columns) for name, frame in variables.items()},
            worker_environment_keys=outcome.environment_keys,
            validation_counts={
                name: _severity_counts(report.findings) for name, report in reports.items()
            },
            metric_review_status={
                key: definition.review_status.value
                for key in metric_keys
                if (definition := self._registry.get(key)) is not None
            },
        )

    def _run_root(self, case_id: str) -> Path:
        safe_case = "".join(ch for ch in case_id if ch.isalnum() or ch in {"-", "_"})
        if not safe_case:
            raise RealWindExecutionError("case id has no safe filesystem characters")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return Path(self._settings.artifact_root) / "real-wind" / safe_case / stamp

    @staticmethod
    def _membership(plan: ExecutionPlan) -> dict[Any, list[str]]:
        step = next(
            (item for item in plan.steps if item.tool == "wind.index_components"),
            None,
        )
        if step is None:
            raise RealWindExecutionError(
                "real execution currently requires a historical index universe"
            )
        result = adapter.index_components(**step.arguments)
        if not isinstance(result, dict):
            raise RealWindExecutionError("index membership did not return a dated mapping")
        return result

    @staticmethod
    def _filter_masks(
        plan: ExecutionPlan, codes: list[str]
    ) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
        st_mask: pd.DataFrame | None = None
        suspended_mask: pd.DataFrame | None = None
        for step in plan.steps:
            if step.tool not in {"wind.is_st_stock", "wind.is_suspended"}:
                continue
            arguments = dict(step.arguments)
            arguments["order_book_ids"] = codes
            if step.tool == "wind.is_st_stock":
                st_mask = adapter.is_st_stock(**arguments)
            else:
                suspended_mask = adapter.is_suspended(**arguments)
        return st_mask, suspended_mask

    @staticmethod
    def _variables(
        plan: ExecutionPlan,
        selections: list[FieldSelection],
        codes: list[str],
        universe_mask: pd.DataFrame,
        st_mask: pd.DataFrame | None,
        suspended_mask: pd.DataFrame | None,
    ) -> tuple[dict[str, pd.DataFrame], list[pd.DataFrame]]:
        variables: dict[str, pd.DataFrame] = {}
        raw_frames: list[pd.DataFrame] = []
        for selection in selections:
            step = next(
                (
                    item
                    for item in plan.steps
                    if selection.logical_name in item.inputs
                    and item.tool in {"wind.get_price", "wind.execute_generic_query_plan"}
                ),
                None,
            )
            if step is None:
                raise RealWindExecutionError(f"no retrieval step for {selection.logical_name!r}")
            arguments = dict(step.arguments)
            arguments["order_book_ids"] = codes
            if step.tool == "wind.get_price":
                raw = adapter.get_price(**arguments)
                requested_fields = arguments.get("fields") or []
                if len(requested_fields) != 1:
                    raise RealWindExecutionError(
                        f"price plan for {selection.logical_name!r} must request one field"
                    )
                wide = _wide_price(raw, str(requested_fields[0]))
            else:
                raw = adapter.execute_generic_query_plan(arguments)
                shape = arguments["query_shape"]
                if shape in {"point_range", "cross_section_asof"}:
                    wide = _wide_point(raw, selection.field)
                elif shape == "report_period":
                    wide = _wide_report_period(
                        raw,
                        selection.field,
                        pd.DatetimeIndex(universe_mask.index),
                        int(arguments.get("as_of_offset_days", 1)),
                    )
                else:
                    raise RealWindExecutionError(
                        f"query shape {shape!r} cannot produce a factor matrix"
                    )
            raw_frames.append(_raw_audit_frame(raw, selection.logical_name))
            variables[selection.logical_name] = _apply_universe_filters(
                wide, universe_mask, st_mask, suspended_mask
            )
        return variables, raw_frames

    @staticmethod
    def _write_audit_parquets(
        run_root: Path,
        raw_frames: list[pd.DataFrame],
        variables: dict[str, pd.DataFrame],
        universe_mask: pd.DataFrame,
        st_mask: pd.DataFrame | None,
        suspended_mask: pd.DataFrame | None,
    ) -> None:
        raw = pd.concat(raw_frames, ignore_index=True, sort=False)
        raw.to_parquet(run_root / "raw_input.parquet", index=False)
        aligned_parts: list[pd.DataFrame] = []
        for name, frame in variables.items():
            part = (
                frame.rename_axis(index="date", columns="code")
                .reset_index()
                .melt(id_vars="date", var_name="code", value_name="value")
            )
            part.insert(0, "variable", name)
            aligned_parts.append(part)
        pd.concat(aligned_parts, ignore_index=True).to_parquet(
            run_root / "aligned_input.parquet", index=False
        )
        universe_mask.to_parquet(run_root / "universe_membership.parquet")
        for name, mask in (
            ("st_exclusions", st_mask),
            ("suspended_exclusions", suspended_mask),
        ):
            if mask is not None:
                mask.reindex(
                    index=universe_mask.index,
                    columns=universe_mask.columns,
                ).fillna(False).astype(bool).to_parquet(run_root / f"{name}.parquet")

    @staticmethod
    def _write_worker_inputs(
        staging: Path, variables: dict[str, pd.DataFrame]
    ) -> list[InputArtifact]:
        artifacts: list[InputArtifact] = []
        for name, frame in variables.items():
            path = staging / f"{name}.parquet"
            frame.to_parquet(path)
            artifacts.append(
                InputArtifact(uri=path.resolve().as_uri(), sha256=_sha256(path), rows=len(frame))
            )
        return artifacts


def _membership_mask(membership: dict[Any, list[str]]) -> pd.DataFrame:
    dates = pd.DatetimeIndex(sorted(pd.Timestamp(value) for value in membership), name="date")
    codes = sorted({code for values in membership.values() for code in values})
    mask = pd.DataFrame(False, index=dates, columns=codes, dtype=bool)
    normalized = {pd.Timestamp(key): value for key, value in membership.items()}
    for date in dates:
        active = [code for code in normalized.get(date, []) if code in mask.columns]
        mask.loc[date, active] = True
    return mask


def _wide_price(raw: pd.DataFrame, field: str) -> pd.DataFrame:
    if field not in raw.columns:
        raise RealWindExecutionError(f"price response omitted {field!r}")
    if not isinstance(raw.index, pd.MultiIndex):
        raise RealWindExecutionError("price response has no (security, date) index")
    return raw[field].unstack("order_book_id").sort_index()


def _wide_point(raw: pd.DataFrame, field: str) -> pd.DataFrame:
    required = {"order_book_id", "observation_date", field}
    missing = required - set(raw.columns)
    if missing:
        raise RealWindExecutionError(f"point response omitted columns: {sorted(missing)}")
    clean = raw.dropna(subset=["order_book_id", "observation_date"]).copy()
    clean = clean.sort_values("observation_date").drop_duplicates(
        ["observation_date", "order_book_id"], keep="last"
    )
    return clean.pivot(index="observation_date", columns="order_book_id", values=field).sort_index()


def _wide_report_period(
    raw: pd.DataFrame,
    field: str,
    dates: pd.DatetimeIndex,
    offset_days: int,
) -> pd.DataFrame:
    required = {"order_book_id", "report_period", "announcement_date", field}
    missing = required - set(raw.columns)
    if missing:
        raise RealWindExecutionError(f"report response omitted columns: {sorted(missing)}")
    clean = raw.dropna(subset=["order_book_id", "report_period", "announcement_date"]).copy()
    clean["available_date"] = pd.to_datetime(clean["announcement_date"]) + pd.to_timedelta(
        offset_days, unit="D"
    )
    output: dict[str, pd.Series] = {}
    for code, records in clean.groupby("order_book_id"):
        ordered = records.sort_values(["available_date", "announcement_date", "report_period"])
        by_period: dict[pd.Timestamp, tuple[pd.Timestamp, float]] = {}
        timeline_dates: list[pd.Timestamp] = []
        timeline_values: list[float] = []
        for available_date, events in ordered.groupby("available_date", sort=True):
            for event in events.itertuples(index=False):
                report_period = pd.Timestamp(str(event.report_period))
                announcement_date = pd.Timestamp(str(event.announcement_date))
                value = pd.to_numeric(getattr(event, field), errors="coerce")
                previous = by_period.get(report_period)
                if previous is None or announcement_date >= previous[0]:
                    by_period[report_period] = (announcement_date, float(value))
            # A late revision to an old report must not replace a newer report
            # that was already public. Select the highest report period known as
            # of this availability date; revisions only compete within a period.
            latest_period = max(by_period)
            timeline_dates.append(pd.Timestamp(str(available_date)))
            timeline_values.append(by_period[latest_period][1])
        series = pd.Series(
            timeline_values,
            index=pd.DatetimeIndex(timeline_dates),
        )
        output[str(code)] = series.reindex(dates, method="ffill")
    return pd.DataFrame(output, index=dates)


def _apply_universe_filters(
    wide: pd.DataFrame,
    universe: pd.DataFrame,
    st_mask: pd.DataFrame | None,
    suspended_mask: pd.DataFrame | None,
) -> pd.DataFrame:
    aligned = wide.reindex(index=universe.index, columns=universe.columns)
    aligned = aligned.where(universe)
    for exclusion in (st_mask, suspended_mask):
        if exclusion is not None:
            aligned = aligned.mask(
                exclusion.reindex(index=aligned.index, columns=aligned.columns).fillna(False)
            )
    return aligned.astype(float)


def _raw_audit_frame(raw: pd.DataFrame, variable: str) -> pd.DataFrame:
    frame = raw.reset_index() if not isinstance(raw.index, pd.RangeIndex) else raw.copy()
    frame.insert(0, "variable", variable)
    return frame


def _raise_on_errors(layer: str, findings: list[Any]) -> None:
    errors = [item.code for item in findings if item.severity is ValidationSeverity.ERROR]
    if errors:
        raise RealWindExecutionError(f"{layer} validation failed: {', '.join(errors)}")


def _severity_counts(findings: list[Any]) -> dict[str, int]:
    counts = {severity.value: 0 for severity in ValidationSeverity}
    for finding in findings:
        counts[finding.severity.value] += 1
    return counts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "RealWindCaseRunner",
    "RealWindExecutionError",
    "RealWindRunResult",
    "apply_confirmed_announcement_requirements",
    "complete_registered_selections",
]
