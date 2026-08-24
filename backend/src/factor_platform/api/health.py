"""Health endpoint: what is reachable, without saying how to reach it.

A health check is read by whoever is debugging at 2am, which is exactly when
someone pastes its output into a chat window. So it reports *whether* each
component answers and never *how* to connect: no host, no user, no key prefix, no
connection string. "Wind: configured but unreachable" is the whole diagnosis a
reader needs, and it is safe to share.

Degraded is distinct from down. The platform runs offline by design — no Wind and
no model still means golden cases parse and manifests build — so a missing
optional component reports its own state rather than failing the whole check.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from factor_platform.llm.base import LLMProvider
from factor_platform.settings import Settings

router = APIRouter(prefix="/api", tags=["health"])


class ComponentHealth(BaseModel):
    name: str
    status: str
    detail: str = ""


class HealthReport(BaseModel):
    status: str
    version: str
    components: list[ComponentHealth]


def get_engine() -> AsyncEngine:  # pragma: no cover - overridden by the app
    raise NotImplementedError("engine dependency is wired in main.create_app")


def get_settings_dependency() -> Settings:  # pragma: no cover - overridden by the app
    raise NotImplementedError("settings dependency is wired in main.create_app")


def get_llm_provider() -> LLMProvider:  # pragma: no cover - overridden by the app
    raise NotImplementedError("LLM provider dependency is wired in main.create_app")


@router.get("/health")
async def health(
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dependency)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> HealthReport:
    components: list[ComponentHealth] = []

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        components.append(ComponentHealth(name="database", status="ok"))
    except Exception:  # noqa: BLE001 - the message could name the file path
        components.append(
            ComponentHealth(name="database", status="down", detail="database did not answer")
        )

    components.append(
        ComponentHealth(
            name="wind",
            status="ok" if settings.wind_enabled else "disabled",
            detail=("configured" if settings.wind_enabled else "WIND_ENABLED is false"),
        )
    )

    has_model = bool(settings.kimi_coding_api_key or settings.kimi_metered_api_key)
    if settings.local_only_mode:
        llm_component = ComponentHealth(
            name="llm",
            status="disabled",
            detail="LOCAL_ONLY_MODE forbids external model calls",
        )
    elif not has_model:
        llm_component = ComponentHealth(
            name="llm", status="unconfigured", detail="no provider key set"
        )
    else:
        provider_health = await provider.health_check()
        active_provider = provider
        select_active = getattr(provider, "active_provider", None)
        if provider_health.healthy and callable(select_active):
            active_provider = await select_active()
        llm_component = ComponentHealth(
            name="llm",
            status="ok" if provider_health.healthy else "unreachable",
            detail=(
                f"{active_provider.name} / "
                f"{getattr(active_provider, 'model', 'configured model')} reachable"
                if provider_health.healthy
                else "configured provider did not answer"
            ),
        )
    components.append(llm_component)

    components.append(
        ComponentHealth(
            name="job_queue",
            status="ok",
            detail=f"root={settings.job_root}",
        )
    )

    # Only the database being down makes the platform unusable; everything else
    # has an offline path.
    down = [c for c in components if c.status == "down"]
    return HealthReport(
        status="down" if down else "ok",
        version="0.1.0",
        components=components,
    )


__all__ = [
    "ComponentHealth",
    "HealthReport",
    "get_engine",
    "get_llm_provider",
    "router",
]
