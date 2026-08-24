"""Build the configured LLM provider graph without exposing credentials."""

from __future__ import annotations

from factor_platform.llm.base import LLMProvider
from factor_platform.llm.openai_compatible import OpenAICompatibleProvider
from factor_platform.llm.router import ProviderRouter
from factor_platform.llm.usage import LLMUsageSink
from factor_platform.settings import Settings


def build_llm_provider(
    settings: Settings, *, usage_sink: LLMUsageSink | None = None
) -> LLMProvider:
    """Prefer Coding Plan and retain the metered API as a health-checked fallback."""
    providers: list[LLMProvider] = []
    if settings.kimi_coding_base_url and settings.kimi_coding_api_key:
        assert settings.coding_model is not None
        providers.append(
            OpenAICompatibleProvider(
                name="kimi-coding-plan",
                base_url=settings.kimi_coding_base_url,
                api_key=settings.kimi_coding_api_key,
                model=settings.coding_model,
                reasoning_effort=settings.kimi_coding_reasoning_effort,
                # Kimi Coding Plan's K3 endpoint only accepts its fixed default
                # temperature. Omitting the field preserves that contract while
                # keeping deterministic temperature=0 for metered providers.
                temperature=None,
                usage_sink=usage_sink,
            )
        )
    if settings.kimi_metered_api_key:
        assert settings.metered_model is not None
        providers.append(
            OpenAICompatibleProvider(
                name="kimi-metered",
                base_url=settings.kimi_metered_base_url,
                api_key=settings.kimi_metered_api_key,
                model=settings.metered_model,
                reasoning_effort=settings.kimi_metered_reasoning_effort,
                usage_sink=usage_sink,
            )
        )

    # An empty router is deliberately unavailable.  The application factory must
    # never turn missing credentials (or local-only mode) into a successful fake
    # model call; FakeLLMProvider is reserved for explicit test/fixture injection.
    return ProviderRouter(providers, local_only_mode=settings.local_only_mode)


__all__ = ["build_llm_provider"]
