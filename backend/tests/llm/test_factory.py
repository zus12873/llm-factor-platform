import pytest
from pydantic import BaseModel

from factor_platform.llm.data_boundary import LocalOnlyModeError
from factor_platform.llm.factory import build_llm_provider
from factor_platform.llm.router import NoHealthyProviderError, ProviderRouter
from factor_platform.settings import Settings


async def test_no_configured_key_is_unavailable_and_never_uses_fake() -> None:
    provider = build_llm_provider(Settings(app_env="test"))
    assert isinstance(provider, ProviderRouter)
    assert provider._providers == []
    with pytest.raises(NoHealthyProviderError):
        await provider.active_provider()


async def test_local_only_mode_is_unavailable_even_with_configured_key() -> None:
    provider = build_llm_provider(
        Settings(
            app_env="test",
            local_only_mode=True,
            kimi_coding_base_url="https://llm.example.com/v1",
            kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_model="test-model",
        )
    )
    assert isinstance(provider, ProviderRouter)
    with pytest.raises(LocalOnlyModeError):
        await provider.active_provider()


def test_configured_coding_plan_never_silently_uses_fake() -> None:
    provider = build_llm_provider(
        Settings(
            app_env="test",
            kimi_coding_base_url="https://llm.example.com/v1",
            kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_model="test-model",
        )
    )
    assert isinstance(provider, ProviderRouter)
    assert [item.name for item in provider._providers] == ["kimi-coding-plan"]


def test_coding_plan_precedes_the_metered_fallback() -> None:
    provider = build_llm_provider(
        Settings(
            app_env="test",
            kimi_coding_base_url="https://coding.example.com/v1",
            kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_metered_base_url="https://metered.example.com/v1",
            kimi_metered_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_model="test-model",
        )
    )
    assert isinstance(provider, ProviderRouter)
    assert [item.name for item in provider._providers] == [
        "kimi-coding-plan",
        "kimi-metered",
    ]


def test_provider_specific_models_and_reasoning_take_precedence() -> None:
    provider = build_llm_provider(
        Settings(
            app_env="test",
            kimi_coding_base_url="https://coding.example.com/v1",
            kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_coding_model="coding-model",
            kimi_coding_reasoning_effort="max",
            kimi_metered_base_url="https://metered.example.com/v1",
            kimi_metered_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_metered_model="metered-model",
            kimi_metered_reasoning_effort="high",
            kimi_model="legacy-model",
        )
    )
    assert isinstance(provider, ProviderRouter)
    coding, metered = provider._providers
    assert coding.model == "coding-model"
    assert coding.reasoning_effort == "max"
    assert metered.model == "metered-model"
    assert metered.reasoning_effort == "high"


def test_coding_plan_omits_temperature_but_metered_keeps_default() -> None:
    provider = build_llm_provider(
        Settings(
            app_env="test",
            kimi_coding_base_url="https://coding.example.com/v1",
            kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_coding_model="k3",
            kimi_metered_base_url="https://metered.example.com/v1",
            kimi_metered_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_metered_model="metered-model",
        )
    )
    assert isinstance(provider, ProviderRouter)
    coding, metered = provider._providers
    assert "temperature" not in coding._payload([], _HealthResponse)
    assert metered._payload([], _HealthResponse)["temperature"] == 0


def test_reasoning_effort_is_sent_only_when_configured() -> None:
    provider = build_llm_provider(
        Settings(
            app_env="test",
            kimi_coding_base_url="https://coding.example.com/v1",
            kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_coding_model="coding-model",
            kimi_coding_reasoning_effort="max",
        )
    )
    assert isinstance(provider, ProviderRouter)
    coding = provider._providers[0]
    payload = coding._payload([], _HealthResponse)
    assert payload["model"] == "coding-model"
    assert payload["reasoning_effort"] == "max"


class _HealthResponse(BaseModel):
    status: str
