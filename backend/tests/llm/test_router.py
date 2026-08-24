import pytest
from pydantic import BaseModel

from factor_platform.llm.base import ChatMessage, FakeLLMProvider
from factor_platform.llm.router import NoHealthyProviderError, ProviderRouter


class Answer(BaseModel):
    value: int


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


async def test_empty_router_is_stably_unavailable() -> None:
    router = ProviderRouter([])
    assert (await router.health_check()).healthy is False
    with pytest.raises(NoHealthyProviderError):
        await router.active_provider()


async def test_structured_call_uses_the_selected_provider_once(
    coding: FakeLLMProvider, metered: FakeLLMProvider, router: ProviderRouter
) -> None:
    coding.healthy = False
    metered.healthy = True
    metered.enqueue_content('{"value": 7}')
    answer = await router.structured_chat(
        [ChatMessage(role="user", content="minimal test")], Answer
    )
    assert answer == Answer(value=7)
