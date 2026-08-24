from factor_platform.api.errors import ERROR_MAP
from factor_platform.llm.router import NoHealthyProviderError


def test_no_healthy_provider_has_stable_service_unavailable_mapping() -> None:
    assert ERROR_MAP[NoHealthyProviderError] == (503, "llm_provider_unavailable")
