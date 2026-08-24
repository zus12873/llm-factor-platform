import pytest
from pydantic import ValidationError

from factor_platform.settings import Settings


def test_wind_disabled_needs_no_credentials() -> None:
    settings = Settings(app_env="test", wind_enabled=False)
    assert settings.wind_enabled is False


def test_wind_enabled_requires_all_connection_fields() -> None:
    with pytest.raises(ValidationError, match="wind_host"):
        Settings(app_env="test", wind_enabled=True)


def test_worker_environment_excludes_secrets() -> None:
    settings = Settings(
        app_env="test",
        wind_enabled=True,
        wind_host="db.internal",
        wind_user="research",
        wind_password="unit-test-only",  # pragma: allowlist secret
        wind_database="wind",
    )
    assert "WIND_PASSWORD" not in settings.worker_environment()
    assert "KIMI_API_KEY" not in settings.worker_environment()


# --------------------------------------------------------------------------- boundary B4


def test_outbound_boundary_defaults_are_the_restrictive_ones() -> None:
    """A missing .env entry must never be the permissive reading."""
    settings = Settings(app_env="test")
    assert settings.local_only_mode is False
    assert settings.outbound_allow_report_excerpt is False


def test_report_excerpts_require_a_positive_char_limit() -> None:
    """An unbounded 'excerpt' is the full body under another name."""
    with pytest.raises(ValidationError, match="outbound_max_excerpt_chars"):
        Settings(
            app_env="test",
            outbound_allow_report_excerpt=True,
            outbound_max_excerpt_chars=0,
        )


def test_coding_plan_url_and_key_must_be_configured_together() -> None:
    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            app_env="test",
            kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
            kimi_model="test-model",
        )


def test_outbound_filter_is_built_from_settings() -> None:
    settings = Settings(
        app_env="test",
        outbound_allow_report_excerpt=True,
        outbound_max_excerpt_chars=500,
    )
    boundary = settings.outbound_filter()
    assert boundary.check({"report_excerpt": "x" * 400}) is None
    assert boundary.check({"report_excerpt": "x" * 600}) is not None


def test_provider_specific_models_fall_back_to_legacy_model() -> None:
    settings = Settings(
        app_env="test",
        kimi_coding_base_url="https://coding.example.com/v1",
        kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
        kimi_metered_api_key="unit-test-only",  # pragma: allowlist secret
        kimi_model="legacy-model",
    )
    assert settings.coding_model == "legacy-model"
    assert settings.metered_model == "legacy-model"


def test_provider_specific_models_do_not_require_legacy_model() -> None:
    settings = Settings(
        app_env="test",
        kimi_coding_base_url="https://coding.example.com/v1",
        kimi_coding_api_key="unit-test-only",  # pragma: allowlist secret
        kimi_coding_model="coding-model",
        kimi_metered_api_key="unit-test-only",  # pragma: allowlist secret
        kimi_metered_model="metered-model",
    )
    assert settings.coding_model == "coding-model"
    assert settings.metered_model == "metered-model"
