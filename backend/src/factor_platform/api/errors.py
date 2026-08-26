"""Domain exceptions mapped to stable HTTP codes and error identifiers.

The identifiers matter more than the status codes. A frontend that branches on a
message string breaks the first time someone improves the wording; one that
branches on ``stale_session_version`` keeps working. So every domain error gets a
code that is part of the API contract, and the human message is free to change.

409 for a stale version is deliberate rather than 400: the request was
well-formed and would have been valid a moment earlier. The client needs to
re-read and retry, not fix its payload — and those are different recoveries.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from factor_platform.analysis.metrics import AnalysisError
from factor_platform.domain.errors import (
    ConcurrentUpdateError,
    DisputedMetricError,
    IllegalTransitionError,
    LibraryEntryNotFoundError,
    LLMResponseError,
    RealExecutionUnavailableError,
    ReportArtifactNotFoundError,
    ReportFormulaUnconfirmedError,
    SessionNotCompletedError,
)
from factor_platform.execution.manifest import (
    ManifestSchemaError,
    ManifestVerificationError,
)
from factor_platform.library.service import ImmutableArtifactError, PublishRefusedError
from factor_platform.llm.data_boundary import LocalOnlyModeError, OutboundBlockedError
from factor_platform.llm.router import NoHealthyProviderError
from factor_platform.reports.pdf import ReportLimitError
from factor_platform.wind.planner import PlanningError

#: exception type -> (status, stable error code)
ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    ConcurrentUpdateError: (409, "stale_session_version"),
    IllegalTransitionError: (409, "illegal_transition"),
    DisputedMetricError: (422, "disputed_metric"),
    PlanningError: (422, "planning_failed"),
    ManifestSchemaError: (422, "manifest_schema_invalid"),
    ManifestVerificationError: (400, "manifest_verification_failed"),
    OutboundBlockedError: (422, "outbound_blocked"),
    LocalOnlyModeError: (503, "local_only_mode"),
    NoHealthyProviderError: (503, "llm_provider_unavailable"),
    LLMResponseError: (502, "llm_response_invalid"),
    RealExecutionUnavailableError: (503, "real_execution_unavailable"),
    # 413 rather than 400: the request is well-formed, it is simply too large.
    ReportLimitError: (413, "report_limit_exceeded"),
    ReportFormulaUnconfirmedError: (422, "report_formula_unconfirmed"),
    ReportArtifactNotFoundError: (404, "report_artifact_not_found"),
    AnalysisError: (422, "factor_not_analyzable"),
    PublishRefusedError: (422, "publish_refused"),
    ImmutableArtifactError: (409, "immutable_artifact"),
    SessionNotCompletedError: (409, "session_not_completed"),
    LibraryEntryNotFoundError: (404, "library_entry_not_found"),
}


def error_body(code: str, message: str, **extra: object) -> dict[str, object]:
    return {"error": {"code": code, "message": message, **extra}}


async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate a domain error into its contracted status and code."""
    for exception_type, (status, code) in ERROR_MAP.items():
        if isinstance(exc, exception_type):
            return JSONResponse(status_code=status, content=error_body(code, str(exc)))
    # Unmapped errors are a bug in this table, not in the caller's request.
    return JSONResponse(
        status_code=500,
        content=error_body("internal_error", "unexpected server error"),
    )


__all__ = ["ERROR_MAP", "domain_exception_handler", "error_body"]
