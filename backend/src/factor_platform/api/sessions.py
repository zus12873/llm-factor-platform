"""Session endpoints: thin handlers over the workflow service.

Deliberately thin. Every decision — what blocks, what cascades, what may be
executed — lives in the domain, and duplicating any of it here would create a
second place to change when a rule moves. The handler's job is to parse a body,
call one method, and return a snapshot.

Every mutation carries ``expected_version``. Two browser tabs on one session are
the normal case, not an edge case, and without the version the second tab's
confirmation silently overwrites the first tab's revision.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from factor_platform.domain.models import (
    FactorSpec,
    FieldSelection,
    ResearchRequest,
    SessionSnapshot,
)
from factor_platform.orchestration.service import WorkflowService

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionBody(BaseModel):
    session_id: str


class MessageBody(BaseModel):
    expected_version: int
    request: ResearchRequest


class SpecBody(BaseModel):
    expected_version: int
    factor_spec: FactorSpec


class ClarificationBody(BaseModel):
    expected_version: int
    answers: dict[str, str] = Field(default_factory=dict)


class FieldsBody(BaseModel):
    expected_version: int
    field_selections: list[FieldSelection] = Field(default_factory=list)


class CandidatesBody(BaseModel):
    expected_version: int
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class BuildBody(BaseModel):
    expected_version: int
    request: ResearchRequest


class VersionBody(BaseModel):
    expected_version: int


class CloneBody(BaseModel):
    new_session_id: str


def get_workflow() -> WorkflowService:  # pragma: no cover - overridden by the app
    raise NotImplementedError("workflow dependency is wired in main.create_app")


Workflow = Annotated[WorkflowService, Depends(get_workflow)]


@router.post("", status_code=201)
async def create_session(body: CreateSessionBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.create_session(body.session_id)


@router.get("/{session_id}")
async def get_session(session_id: str, workflow: Workflow) -> SessionSnapshot:
    return await workflow._snapshot(session_id)


@router.post("/{session_id}/messages")
async def submit_message(session_id: str, body: MessageBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.submit_message(session_id, body.request, body.expected_version)


@router.post("/{session_id}/confirm-formula")
async def confirm_formula(session_id: str, body: SpecBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.confirm_formula(session_id, body.factor_spec, body.expected_version)


@router.post("/{session_id}/resolve-clarification")
async def resolve_clarification(
    session_id: str, body: ClarificationBody, workflow: Workflow
) -> SessionSnapshot:
    return await workflow.resolve_clarification(session_id, body.answers, body.expected_version)


@router.post("/{session_id}/field-candidates")
async def search_fields(
    session_id: str, body: CandidatesBody, workflow: Workflow
) -> SessionSnapshot:
    return await workflow.search_fields(session_id, body.candidates, body.expected_version)


@router.post("/{session_id}/discover-fields")
async def discover_fields(
    session_id: str, body: VersionBody, workflow: Workflow
) -> SessionSnapshot:
    return await workflow.discover_fields(session_id, body.expected_version)


@router.post("/{session_id}/confirm-fields")
async def confirm_fields(session_id: str, body: FieldsBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.confirm_fields(session_id, body.field_selections, body.expected_version)


@router.post("/{session_id}/manifest")
async def build_manifest(session_id: str, body: BuildBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.build_manifest(session_id, body.request, body.expected_version)


@router.post("/{session_id}/execute-real-wind")
async def execute_real_wind(
    session_id: str, body: VersionBody, workflow: Workflow
) -> SessionSnapshot:
    return await workflow.execute_real_wind(session_id, body.expected_version)


# --------------------------------------------------------------------------- revisions


@router.post("/{session_id}/revise-formula")
async def revise_formula(session_id: str, body: SpecBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.revise_formula(session_id, body.factor_spec, body.expected_version)


@router.post("/{session_id}/revise-fields")
async def revise_fields(session_id: str, body: FieldsBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.revise_fields(session_id, body.field_selections, body.expected_version)


@router.post("/{session_id}/revise-request")
async def revise_request(session_id: str, body: MessageBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.revise_request(session_id, body.request, body.expected_version)


@router.post("/{session_id}/cancel")
async def cancel_execution(
    session_id: str, body: VersionBody, workflow: Workflow
) -> SessionSnapshot:
    return await workflow.cancel_execution(session_id, body.expected_version)


@router.post("/{session_id}/clone")
async def clone_session(session_id: str, body: CloneBody, workflow: Workflow) -> SessionSnapshot:
    return await workflow.clone_session(session_id, body.new_session_id)


__all__ = ["get_workflow", "router"]
