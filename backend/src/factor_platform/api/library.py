"""Factor library endpoints: thin handlers over the immutable store.

Publish is a session action; listing and fetching a version do not need one.
Copy-not-reference and the disputed-metric gate live in the library and in
``WorkflowService.publish_to_library`` — not here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from factor_platform.api.sessions import get_workflow
from factor_platform.domain.errors import LibraryEntryNotFoundError
from factor_platform.library.service import FactorLibrary, LibraryEntry
from factor_platform.orchestration.service import WorkflowService, require_factor_id

router = APIRouter(prefix="/api/library", tags=["library"])


class PublishBody(BaseModel):
    session_id: str
    factor_id: str | None = None


def get_library() -> FactorLibrary:  # pragma: no cover - overridden by the app
    raise NotImplementedError("library dependency is wired in main.create_app")


Library = Annotated[FactorLibrary, Depends(get_library)]
Workflow = Annotated[WorkflowService, Depends(get_workflow)]


@router.get("")
async def list_factors(library: Library) -> list[LibraryEntry]:
    return library.list_factors()


@router.get("/{factor_id}/v/{version}")
async def get_version(factor_id: str, version: int, library: Library) -> LibraryEntry:
    require_factor_id(factor_id)
    try:
        return library.get_version(factor_id, version)
    except KeyError as exc:
        raise LibraryEntryNotFoundError(str(exc)) from exc


@router.post("", status_code=201)
async def publish(body: PublishBody, workflow: Workflow) -> LibraryEntry:
    return await workflow.publish_to_library(body.session_id, body.factor_id)


__all__ = ["get_library", "router"]
