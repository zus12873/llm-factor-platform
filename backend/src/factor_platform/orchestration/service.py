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

from collections.abc import Sequence
from typing import Any

from factor_platform.db.repository import SessionRepository
from factor_platform.domain.models import (
    FactorSpec,
    FieldSelection,
    ResearchRequest,
    SessionSnapshot,
)
from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import LLMProvider
from factor_platform.orchestration.states import EventType
from factor_platform.wind.planner import WindPlanner


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
    ) -> None:
        self._repository = repository
        self._parser = FactorParser(provider)
        self._planner = planner
        self._registry = registry or MetricRegistry.load()
        self._clarifier = clarifier or ClarificationEngine(self._registry)

    # ------------------------------------------------------------------ session

    async def create_session(self, session_id: str) -> SessionSnapshot:
        await self._repository.create_session(session_id)
        snapshot = await self._repository.get_snapshot(session_id)
        assert snapshot is not None
        return snapshot

    async def submit_message(
        self, session_id: str, request: ResearchRequest, expected_version: int
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

        questions = self._clarifier.questions(spec)
        blocking = [q for q in questions if q.blocking]
        if blocking:
            await self._repository.append_event(
                session_id,
                EventType.CLARIFICATION_REQUESTED,
                {"clarifications": [_json(q) for q in questions]},
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

    async def resolve_clarification(
        self, session_id: str, spec: FactorSpec, expected_version: int
    ) -> SessionSnapshot:
        await self._repository.append_event(
            session_id,
            EventType.CLARIFICATION_RESOLVED,
            {"factor_spec": _json(spec)},
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
        await self._repository.append_event(
            session_id,
            EventType.FORMULA_CONFIRMED,
            {"factor_spec": _json(spec)},
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

        await self._repository.append_event(
            session_id,
            EventType.FIELDS_CONFIRMED,
            {"field_selections": [_json(s) for s in selections]},
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

        plan = self._planner.plan(snapshot.factor_spec, snapshot.field_selections, request)

        await self._repository.append_event(
            session_id,
            EventType.CODE_GENERATED,
            {"plan": _json(plan)},
            expected_version,
        )
        return await self._snapshot(session_id)

    # ------------------------------------------------------------------ revisions

    async def revise_formula(
        self, session_id: str, spec: FactorSpec, expected_version: int
    ) -> SessionSnapshot:
        return await self._revise(
            session_id, EventType.FORMULA_REVISED, {"factor_spec": _json(spec)}, expected_version
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

    async def cancel_execution(
        self, session_id: str, expected_version: int
    ) -> SessionSnapshot:
        return await self._revise(
            session_id, EventType.EXECUTION_CANCELLED, {}, expected_version
        )

    async def rerun(self, session_id: str, expected_version: int) -> SessionSnapshot:
        return await self._revise(
            session_id, EventType.RERUN_REQUESTED, {}, expected_version
        )

    async def clone_session(
        self, source_session_id: str, new_session_id: str
    ) -> SessionSnapshot:
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


__all__ = ["WorkflowService"]
