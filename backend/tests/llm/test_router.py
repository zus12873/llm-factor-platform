import pytest

from factor_platform.llm.base import FakeLLMProvider
from factor_platform.llm.router import NoHealthyProviderError, ProviderRouter


async def test_router_falls_back_when_coding_plan_is_unhealthy(
    coding: FakeLLMProvider, metered: FakeLLMProvider, router: ProviderRouter
) -> None:
    coding.healthy = False
    metered.healthy = True
    assert await router.active_provider() is metered


async def test_router_prefers_first_healthy_provider(
    coding: FakeLLMProvider, metered: FakeLLMProvider, router: ProviderRouter
) -> None:
    coding.healthy = True
    metered.healthy = True
    assert await router.active_provider() is coding


async def test_router_raises_when_all_unhealthy(router: ProviderRouter) -> None:
    for provider in router._providers:
        provider.healthy = False
    with pytest.raises(NoHealthyProviderError):
        await router.active_provider()
