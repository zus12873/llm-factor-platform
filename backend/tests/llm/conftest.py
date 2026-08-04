from __future__ import annotations

import pytest

from factor_platform.llm.base import FakeLLMProvider
from factor_platform.llm.router import ProviderRouter
from factor_platform.llm.usage import LLMUsageSink


@pytest.fixture
def usage_sink() -> LLMUsageSink:
    return LLMUsageSink()


@pytest.fixture
def mock_provider(usage_sink: LLMUsageSink) -> FakeLLMProvider:
    return FakeLLMProvider(name="mock", usage_sink=usage_sink)


@pytest.fixture
def coding(usage_sink: LLMUsageSink) -> FakeLLMProvider:
    return FakeLLMProvider(name="coding", healthy=True, usage_sink=usage_sink)


@pytest.fixture
def metered(usage_sink: LLMUsageSink) -> FakeLLMProvider:
    return FakeLLMProvider(name="metered", healthy=True, usage_sink=usage_sink)


@pytest.fixture
def router(coding: FakeLLMProvider, metered: FakeLLMProvider) -> ProviderRouter:
    return ProviderRouter([coding, metered])
