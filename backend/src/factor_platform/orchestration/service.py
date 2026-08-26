"""The P0 workflow: one place that sequences parse, confirm, plan, build and run.

Every public method has the same skeleton, and the skeleton is the point:

    check state and version → emit a "started" event → perform **one** external
    operation → validate its result → emit "succeeded" or "failed"

The started event is committed *before* the external call. If the call then
crashes, the session still records that execution was attempted rather than
silently rewinding to the previous step — which is the difference between a user
seeing "this run died" and a user seeing a state that quietly lies about what
happened.

**Never hold a SQLite transaction across an LLM, Wind or worker call.** The write
lock is database-wide, so a thirty-second model call inside a transaction blocks
every other session on the host. Hence the commit before and the commit after,
with an unlocked window in between.

Refusals happen before any external call, not after: a disputed metric or an
unconfirmed field stops the workflow while it is still free to stop.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from factor_platform.db.repository import SessionRepository
from factor_platform.domain.errors import (
    RealExecutionUnavailableError,
    ReportArtifactNotFoundError,
    ReportFormulaUnconfirmedError,
    SessionNotCompletedError,
)
from factor_platform.domain.models import (
    DataRequirement,
    ErrorCategory,
    ExecutionResult,
    ExecutionStatus,
    FactorSpec,
    FieldCandidateBinding,
    FieldSelection,
    FieldTimeRole,
    ReportEvidence,
    ResearchRequest,
    SessionSnapshot,
    StructuredError,
    ValidationReport,
)
from factor_platform.execution.real_wind import (
    RealWindCaseRunner,
    RealWindExecutionError,
    apply_confirmed_announcement_requirements,
    complete_registered_selections,
)
from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.factor.parser import FactorParser
from factor_platform.factor.renderer import render_canonical_formula
from factor_platform.library.service import FactorLibrary, LibraryEntry
from factor_platform.llm.base import LLMProvider
from factor_platform.orchestration.states import EventType, SessionState
from factor_platform.reports.extractor import ExtractedFactor, FormulaExtractionStatus
from factor_platform.wind.field_search import FieldSearch
from factor_platform.wind.planner import WindPlanner
from factor_platform.wind.schema_verify import SchemaVerifier


class WorkflowService:
    """Sequences the P0 loop over the event-sourced session store."""

    def __init__(
        self,
        repository: SessionRepository,
        provider: LLMProvider,
        planner: WindPlanner,
        *,
        registry: MetricRegistry | None = None,
        clarifier: ClarificationEngine | None = None,
        field_search: FieldSearch | None = None,
        schema_verifier: SchemaVerifier | None = None,
        real_wind_runner: RealWindCaseRunner | None = None,
        library: FactorLibrary | None = None,
    ) -> None:
        self._repository = repository
        self._parser = FactorParser(provider)
        self._planner = planner
        self._registry = registry or MetricRegistry.load()
        self._clarifier = clarifier or ClarificationEngine(self._registry)
        aliases_path = Path(__file__).resolve().parents[3] / "data" / "wind_aliases.yaml"
        self._field_search = field_search or FieldSearch.from_aliases_path(aliases_path)
        self._schema_verifier = schema_verifier
        self._real_wind_runner = real_wind_runner
        self._library = library

    # ------------------------------------------------------------------ session

    async def create_session(self, session_id: str) -> SessionSnapshot:
        await self._repository.create_session(session_id)
        snapshot = await self._repository.get_snapshot(session_id)
        assert snapshot is not None
        return snapshot

    async def submit_message(
        self,
        session_id: str,
        request: ResearchRequest,
        expected_version: int,
        *,
        source_evidence: Sequence[ReportEvidence] | None = None,
    ) -> SessionSnapshot:
        """Parse a research idea into a spec, then audit it for ambiguity.

        The parse-started event lands before the model call so a crash mid-call
        leaves a session that says so.
        """
        version = await self._repository.append_event(
            session_id, EventType.PARSE_STARTED, {"request": _json(request)}, expected_version
        )

        # --- unlocked window: the only external call in this method ---
        spec = await self._parser.parse(request)
        if source_evidence:
            spec = spec.model_copy(update={"source_evidence": list(source_evidence)})

        questions = self._clarifier.questions(spec, request.research_idea)
        blocking = [q for q in questions if q.blocking]
        if blocking:
            await self._repository.append_event(
                session_id,
                EventType.CLARIFICATION_REQUESTED,
                {
                    "factor_spec": _json(spec),
                    "clarifications": [_json(q) for q in questions],
                },
                version,
            )
        else:
            await self._repository.append_event(
                session_id,
                EventType.FORMULA_PROPOSED,
                {"factor_spec": _json(spec)},
                version,
            )
        return await self._snapshot(session_id)

    async def enter_from_report(
        self,
        session_id: str,
        artifact_id: str,
        request: ResearchRequest,
        manual_formula: str | None,
        expected_version: int,
        *,
        extraction_path: Path,
    ) -> SessionSnapshot:
        """Seed a session from a persisted extraction; never from a client blob."""
        extraction = _load_extraction(extraction_path, artifact_id)
        formula = extraction.formula_extraction
        extracted = (
            formula.status == FormulaExtractionStatus.EXTRACTED and formula.formula_ast is not None
        )
        typed = (manual_formula or "").strip()
        if not extracted and not typed:
            raise ReportFormulaUnconfirmedError(
                "low-confidence extraction cannot enter the workflow without a typed formula"
            )

        await self.create_session(session_id)
        request = request.model_copy(update={"report_artifact_id": artifact_id})
        evidence = _evidence_from_extraction(extraction)

        if extracted:
            spec = _spec_from_extraction(extraction, request)
            version = await self._repository.append_event(
                session_id,
                EventType.PARSE_STARTED,
                {"request": _json(request)},
                expected_version,
            )
            questions = self._clarifier.questions(spec, request.research_idea)
            blocking = [q for q in questions if q.blocking]
            if blocking:
                await self._repository.append_event(
                    session_id,
                    EventType.CLARIFICATION_REQUESTED,
                    {
                        "factor_spec": _json(spec),
                        "clarifications": [_json(q) for q in questions],
                    },
                    version,
                )
            else:
                await self._repository.append_event(
                    session_id,
                    EventType.FORMULA_PROPOSED,
                    {"factor_spec": _json(spec)},
                    version,
                )
            return await self._snapshot(session_id)

        request = request.model_copy(update={"research_idea": typed})
        return await self.submit_message(
            session_id, request, expected_version, source_evidence=evidence
        )

    async def resolve_clarification(
        self,
        session_id: str,
        answers: dict[str, str],
        expected_version: int,
    ) -> SessionSnapshot:
        snapshot = await self._snapshot(session_id)
        if snapshot.factor_spec is None:
            raise ValueError("clarification draft is missing its factor spec")
        spec = self._clarifier.apply_answers(snapshot.factor_spec, answers)
        await self._repository.append_event(
            session_id,
            EventType.CLARIFICATION_RESOLVED,
            {"factor_spec": _json(spec), "clarifications": []},
            expected_version,
        )
        snapshot = await self._snapshot(session_id)
        await self._repository.append_event(
            session_id,
            EventType.FORMULA_PROPOSED,
            {"factor_spec": _json(spec)},
            snapshot.version,
        )
        return await self._snapshot(session_id)

    async def confirm_formula(
        self, session_id: str, spec: FactorSpec, expected_version: int
    ) -> SessionSnapshot:
        confirmed = _canonical_spec(spec)
        await self._repository.append_event(
            session_id,
            EventType.FORMULA_CONFIRMED,
            {"factor_spec": _json(confirmed)},
            expected_version,
        )
        return await self._snapshot(session_id)

    # ------------------------------------------------------------------ fields

    async def search_fields(
        self, session_id: str, candidates: Sequence[Any], expected_version: int
    ) -> SessionSnapshot:
        await self._repository.append_event(
            session_id,
            EventType.FIELD_CANDIDATES_FOUND,
            {"field_candidates": [_json(c) for c in candidates]},
            expected_version,
        )
        return await self._snapshot(session_id)

    async def discover_fields(self, session_id: str, expected_version: int) -> SessionSnapshot:
        """Find bounded, local candidates and verify their live schema when available."""
        snapshot = await self._snapshot(session_id)
        if snapshot.factor_spec is None:
            raise ValueError("cannot discover fields before a formula is confirmed")

        candidates: list[FieldCandidateBinding] = []
        for requirement in snapshot.factor_spec.variables:
            definition = self._registry.get(requirement.logical_name)
            if definition is not None:
                candidates.append(
                    FieldCandidateBinding(
                        logical_name=requirement.logical_name,
                        table=definition.wind_table,
                        field=definition.wind_field,
                        meaning_zh=definition.display_zh,
                        unit=definition.unit,
                        time_role=(
                            FieldTimeRole(definition.time_role) if definition.time_role else None
                        ),
                        metadata_source="metric_registry",
                        source_tier="registry",
                        evidence=f"registry:{definition.key}",
                    )
                )
            for candidate in self._field_search.search(requirement, limit=5):
                candidates.append(
                    FieldCandidateBinding(
                        logical_name=requirement.logical_name,
                        **candidate.model_dump(mode="python"),
                    )
                )

        deduplicated: list[FieldCandidateBinding] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in candidates:
            key = (candidate.logical_name, candidate.table, candidate.field)
            if key in seen:
                continue
            seen.add(key)
            if self._schema_verifier is not None:
                verdict = await self._schema_verifier.verify(
                    candidate, expected_time_role=candidate.time_role
                )
                candidate = candidate.model_copy(update={"schema_status": verdict.status.value})
            else:
                candidate = candidate.model_copy(update={"schema_status": "not_verified"})
            deduplicated.append(candidate)

        await self._repository.append_event(
            session_id,
            EventType.FIELD_CANDIDATES_FOUND,
            {"field_candidates": [_json(candidate) for candidate in deduplicated]},
            expected_version,
        )
        return await self._snapshot(session_id)

    async def confirm_fields(
        self,
        session_id: str,
        selections: Sequence[FieldSelection],
        expected_version: int,
    ) -> SessionSnapshot:
        """Accept confirmed bindings, refusing disputed metrics first.

        The registry check runs before the event is appended, so a disputed
        mapping never becomes part of the session's history.
        """
        for selection in selections:
            if self._registry.get(selection.logical_name) is not None:
                self._registry.enforce(selection.logical_name)
            if self._schema_verifier is not None:
                verdict = await self._schema_verifier.verify(
                    FieldCandidateBinding(
                        logical_name=selection.logical_name,
                        table=selection.table,
                        field=selection.field,
                        time_role=selection.time_role,
                    ),
                    expected_time_role=selection.time_role,
                )
                if verdict.is_blocking:
                    from factor_platform.wind.planner import PlanningError

                    raise PlanningError(f"field verification failed: {verdict.status.value}")

        completed = complete_registered_selections(list(selections), self._registry)

        await self._repository.append_event(
            session_id,
            EventType.FIELDS_CONFIRMED,
            {"field_selections": [_json(s) for s in completed]},
            expected_version,
        )
        return await self._snapshot(session_id)

    # ------------------------------------------------------------------ build

    async def build_manifest(
        self,
        session_id: str,
        request: ResearchRequest,
        expected_version: int,
    ) -> SessionSnapshot:
        """Plan retrieval, then record the plan and its build hash."""
        snapshot = await self._snapshot(session_id)
        if snapshot.factor_spec is None:
            raise ValueError("cannot build a manifest before a formula is confirmed")

        selections = complete_registered_selections(snapshot.field_selections, self._registry)
        confirmed_spec = apply_confirmed_announcement_requirements(snapshot.factor_spec, selections)
        plan = self._planner.plan(confirmed_spec, selections, request)

        await self._repository.append_event(
            session_id,
            EventType.CODE_GENERATED,
            {"plan": _json(plan)},
            expected_version,
        )
        return await self._snapshot(session_id)

    # ---------------------------------------------------------------- execution

    async def execute_real_wind(self, session_id: str, expected_version: int) -> SessionSnapshot:
        """Run the confirmed plan with backend-only Wind access and an isolated worker."""
        if self._real_wind_runner is None:
            raise RealExecutionUnavailableError(
                "real Wind execution is not configured in this environment"
            )
        snapshot = await self._snapshot(session_id)
        if snapshot.request is None or snapshot.factor_spec is None or snapshot.plan is None:
            raise ValueError("real execution requires request, confirmed spec, and plan")

        version = await self._repository.append_event(
            session_id, EventType.EXECUTION_STARTED, {}, expected_version
        )
        selections = complete_registered_selections(snapshot.field_selections, self._registry)
        spec = apply_confirmed_announcement_requirements(
            _canonical_spec(snapshot.factor_spec), selections
        )
        try:
            run = await asyncio.to_thread(
                self._real_wind_runner.run,
                case_id=f"session-{session_id}",
                spec=spec,
                request=snapshot.request,
                selections=selections,
                plan=snapshot.plan,
            )
            validation = json.loads(
                (Path(run.run_dir) / "validation.json").read_text(encoding="utf-8")
            )
            artifact_uri = str(
                (Path(run.run_dir) / "artifacts" / run.job_id / "result.parquet").resolve()
            )
            review_status = dict(run.metric_review_status)
            for selection in selections:
                if self._registry.get(selection.logical_name) is None:
                    review_status[selection.logical_name.upper()] = "unreviewed"
            result = ExecutionResult(
                status=ExecutionStatus.COMPLETED,
                artifact_uri=artifact_uri,
                data_validation=ValidationReport.model_validate(validation["data"]),
                formula_validation=ValidationReport.model_validate(validation["formula"]),
                result_validation=ValidationReport.model_validate(validation["result"]),
                log_summary="真实 Wind 取数、隔离 Worker 与三层校验已完成",
                resource_stats={
                    "rows": run.result_rows,
                    "columns": run.result_columns,
                    "non_null_rate": run.result_non_null_rate,
                    "metric_review_status": review_status,
                    "source": "real_wind",
                },
            )
        except Exception as exc:  # persist a scrubbed failure instead of hiding it
            message = (
                str(exc)
                if isinstance(exc, RealWindExecutionError)
                else f"real execution failed ({type(exc).__name__}); details redacted"
            )
            error = StructuredError(
                category=ErrorCategory.INFRASTRUCTURE,
                code="real_wind_execution_failed",
                message=message,
            )
            failed = ExecutionResult(
                status=ExecutionStatus.FAILED,
                log_summary=message,
                errors=[error],
            )
            await self._repository.append_event(
                session_id,
                EventType.EXECUTION_FAILED,
                {"execution_result": _json(failed), "last_error": _json(error)},
                version,
            )
            return await self._snapshot(session_id)

        validating_version = await self._repository.append_event(
            session_id,
            EventType.EXECUTION_SUCCEEDED,
            {"execution_result": _json(result), "artifact_uri": artifact_uri},
            version,
        )
        await self._repository.append_event(
            session_id,
            EventType.VALIDATION_PASSED,
            {"execution_result": _json(result), "artifact_uri": artifact_uri},
            validating_version,
        )
        return await self._snapshot(session_id)

    # ------------------------------------------------------------------ revisions

    async def revise_formula(
        self, session_id: str, spec: FactorSpec, expected_version: int
    ) -> SessionSnapshot:
        confirmed = _canonical_spec(spec)
        return await self._revise(
            session_id,
            EventType.FORMULA_REVISED,
            {"factor_spec": _json(confirmed)},
            expected_version,
        )

    async def revise_fields(
        self,
        session_id: str,
        selections: Sequence[FieldSelection],
        expected_version: int,
    ) -> SessionSnapshot:
        return await self._revise(
            session_id,
            EventType.FIELDS_REVISED,
            {"field_selections": [_json(s) for s in selections]},
            expected_version,
        )

    async def revise_request(
        self, session_id: str, request: ResearchRequest, expected_version: int
    ) -> SessionSnapshot:
        return await self._revise(
            session_id, EventType.REQUEST_REVISED, {"request": _json(request)}, expected_version
        )

    async def revise_preprocessing(
        self, session_id: str, spec: FactorSpec, expected_version: int
    ) -> SessionSnapshot:
        return await self._revise(
            session_id,
            EventType.PREPROCESSING_REVISED,
            {"factor_spec": _json(spec)},
            expected_version,
        )

    async def revise_time_convention(
        self, session_id: str, spec: FactorSpec, expected_version: int
    ) -> SessionSnapshot:
        return await self._revise(
            session_id,
            EventType.TIME_CONVENTION_REVISED,
            {"factor_spec": _json(spec)},
            expected_version,
        )

    async def cancel_execution(self, session_id: str, expected_version: int) -> SessionSnapshot:
        return await self._revise(session_id, EventType.EXECUTION_CANCELLED, {}, expected_version)

    async def rerun(self, session_id: str, expected_version: int) -> SessionSnapshot:
        return await self._revise(session_id, EventType.RERUN_REQUESTED, {}, expected_version)

    async def clone_session(self, source_session_id: str, new_session_id: str) -> SessionSnapshot:
        """Seed a new session from another's definition; artifacts do not carry over."""
        source = await self._snapshot(source_session_id)
        await self._repository.create_session(new_session_id)
        await self._repository.append_event(
            new_session_id,
            EventType.SESSION_CLONED,
            {
                "request": _json(source.request) if source.request else None,
                "factor_spec": _json(source.factor_spec) if source.factor_spec else None,
                "cloned_from": {
                    "session_id": source_session_id,
                    "version": source.version,
                },
            },
            0,
        )
        return await self._snapshot(new_session_id)

    # ------------------------------------------------------------------ library

    async def publish_to_library(
        self, session_id: str, factor_id: str | None = None
    ) -> LibraryEntry:
        """Copy a completed session's result into the immutable library.

        Refuses rather than inventing a manifest hash, and does not catch the
        library's disputed-metric gate.
        """
        if self._library is None:
            raise RuntimeError("factor library is not configured")

        snapshot = await self._snapshot(session_id)
        result = snapshot.execution_result
        artifact_uri = snapshot.artifact_uri or (result.artifact_uri if result else None)
        artifact = Path(artifact_uri) if artifact_uri else None
        if (
            snapshot.state != SessionState.COMPLETED
            or result is None
            or result.status != ExecutionStatus.COMPLETED
            or snapshot.factor_spec is None
            or not snapshot.code_sha256
            or artifact is None
            or not artifact.is_file()
        ):
            raise SessionNotCompletedError(
                f"session {session_id} cannot be published until execution has completed"
            )

        review_status = result.resource_stats.get("metric_review_status") or {}
        metric_keys = [str(key) for key in review_status] if isinstance(review_status, dict) else []
        resolved_id = factor_id or _factor_id_slug(snapshot.factor_spec.factor_name)
        return self._library.publish(
            factor_id=resolved_id,
            session_id=session_id,
            spec=snapshot.factor_spec,
            manifest_sha256=snapshot.code_sha256,
            result_artifact=artifact,
            program_source=snapshot.generated_code or "",
            metric_keys=metric_keys,
        )

    # ------------------------------------------------------------------ internals

    async def _revise(
        self,
        session_id: str,
        event: EventType,
        payload: dict[str, Any],
        expected_version: int,
    ) -> SessionSnapshot:
        """All revisions share one path so cascade invalidation cannot be bypassed."""
        await self._repository.append_event(session_id, event, payload, expected_version)
        return await self._snapshot(session_id)

    async def _snapshot(self, session_id: str) -> SessionSnapshot:
        snapshot = await self._repository.get_snapshot(session_id)
        if snapshot is None:
            raise KeyError(f"unknown session: {session_id}")
        return snapshot


def _json(model: Any) -> Any:
    """Dump to JSON-safe primitives, since payloads round-trip through SQLite."""
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def _canonical_spec(spec: FactorSpec) -> FactorSpec:
    return spec.model_copy(update={"canonical_formula": render_canonical_formula(spec.formula_ast)})


def _factor_id_slug(factor_name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", factor_name.lower()).strip("_")
    return slug or "factor"


def _load_extraction(extraction_path: Path, artifact_id: str) -> ExtractedFactor:
    if not extraction_path.is_file():
        raise ReportArtifactNotFoundError(
            f"upload id is unknown or its extraction record is missing: {artifact_id}"
        )
    return ExtractedFactor.model_validate_json(extraction_path.read_text(encoding="utf-8"))


def _evidence_from_extraction(extraction: ExtractedFactor) -> list[ReportEvidence]:
    return [
        ReportEvidence(page_number=excerpt.page_number, quote=excerpt.text)
        for excerpt in extraction.evidence
    ]


def _spec_from_extraction(extraction: ExtractedFactor, request: ResearchRequest) -> FactorSpec:
    ast = extraction.formula_extraction.formula_ast
    assert ast is not None
    return FactorSpec(
        factor_name=extraction.factor_name or "extracted_factor",
        hypothesis=extraction.hypothesis,
        asset_type=request.asset_type,
        universe=request.universe,
        frequency=request.frequency,
        direction=extraction.direction,
        formula_ast=ast,
        canonical_formula=render_canonical_formula(ast),
        variables=[DataRequirement.model_validate(item) for item in extraction.variables],
        data_rules=request.data_rules,
        preprocessing=request.preprocessing,
        time_convention=request.time_convention,
        source_evidence=_evidence_from_extraction(extraction),
    )


__all__ = ["WorkflowService"]
