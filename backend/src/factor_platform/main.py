"""FastAPI application factory.

Dependencies are wired here rather than imported at module scope so a test can
build an app against an in-memory database without a ``.env``, and so the
composition root is one readable function instead of a set of module globals
whose order of import matters.

Every domain exception routes through one handler. Handling them per-endpoint
would guarantee that the next endpoint forgets one, and an unmapped domain error
surfaces as a 500 — which reads as "the server is broken" when the truth is "your
request conflicted with someone else's".
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from factor_platform.api import analysis, events, health, reports, sessions
from factor_platform.api.errors import ERROR_MAP, domain_exception_handler
from factor_platform.db.repository import SessionRepository
from factor_platform.execution.real_wind import RealWindCaseRunner
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.llm.base import LLMProvider
from factor_platform.llm.factory import build_llm_provider
from factor_platform.orchestration.service import WorkflowService
from factor_platform.reports.extractor import ReportExtractor
from factor_platform.settings import Settings, get_settings
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog
from factor_platform.wind.planner import WindPlanner
from factor_platform.wind.query_executor import WindQueryExecutor
from factor_platform.wind.schema_verify import SchemaVerifier


def create_app(
    *,
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    provider: LLMProvider | None = None,
) -> FastAPI:
    """Build the application, injecting anything a test needs to replace."""
    resolved_settings = settings or get_settings()
    resolved_engine = engine or create_async_engine(resolved_settings.database_url)
    # Missing credentials and LOCAL_ONLY_MODE both produce an unavailable router;
    # the application must never silently keep using FakeLLMProvider.
    resolved_provider = provider or build_llm_provider(resolved_settings)

    registry = MetricRegistry.load()
    schema_verifier = (
        SchemaVerifier(
            WindQueryExecutor(resolved_settings),
            database=resolved_settings.wind_database or "",
        )
        if resolved_settings.wind_enabled
        else None
    )
    real_wind_runner = (
        RealWindCaseRunner(resolved_settings, registry=registry)
        if resolved_settings.wind_enabled
        else None
    )
    workflow = WorkflowService(
        SessionRepository(resolved_engine),
        resolved_provider,
        WindPlanner(CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES), registry),
        registry=registry,
        schema_verifier=schema_verifier,
        real_wind_runner=real_wind_runner,
    )

    app = FastAPI(
        title="Factor Platform",
        version="0.1.0",
        description="自然语言到可执行、可复现、可审计因子的研究平台",
    )

    extractor = ReportExtractor(
        resolved_provider,
        local_only_mode=resolved_settings.local_only_mode,
        outbound_filter=resolved_settings.outbound_filter(),
    )
    upload_root = Path(resolved_settings.artifact_root) / "uploads"

    app.dependency_overrides[sessions.get_workflow] = lambda: workflow
    app.dependency_overrides[reports.get_extractor] = lambda: extractor
    app.dependency_overrides[reports.get_upload_root] = lambda: upload_root
    app.dependency_overrides[events.get_engine] = lambda: resolved_engine
    app.dependency_overrides[health.get_engine] = lambda: resolved_engine
    app.dependency_overrides[health.get_settings_dependency] = lambda: resolved_settings
    app.dependency_overrides[health.get_llm_provider] = lambda: resolved_provider

    for exception_type in ERROR_MAP:
        app.add_exception_handler(exception_type, domain_exception_handler)

    app.include_router(sessions.router)
    app.include_router(events.router)
    app.include_router(reports.router)
    app.include_router(analysis.router)
    app.include_router(health.router)
    return app


app = create_app  # uvicorn factory target; see `--factory`

__all__ = ["create_app"]
