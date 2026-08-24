from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from factor_platform.llm.data_boundary import OutboundFilter


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./data/runtime/factor_platform.db"
    artifact_root: str = "./data/artifacts"
    job_root: str = "./data/runtime/jobs"
    wind_enabled: bool = False
    wind_host: str | None = None
    wind_port: int = 3306
    wind_user: str | None = None
    wind_password: SecretStr | None = None
    wind_database: str | None = None
    kimi_coding_base_url: str | None = None
    kimi_coding_api_key: SecretStr | None = None
    kimi_coding_model: str | None = None
    kimi_coding_reasoning_effort: str | None = None
    kimi_metered_base_url: str = "https://api.moonshot.cn/v1"
    kimi_metered_api_key: SecretStr | None = None
    kimi_metered_model: str | None = None
    kimi_metered_reasoning_effort: str | None = None
    kimi_model: str | None = None
    session_cookie_secret: SecretStr | None = None

    # --- Trust boundary B4: internal data -> external model services ---
    # Both defaults are the restrictive reading, so a missing .env entry can only
    # ever fail closed.
    local_only_mode: bool = False
    outbound_allow_report_excerpt: bool = False
    outbound_max_excerpt_chars: int = 2000

    @model_validator(mode="after")
    def validate_required_settings(self) -> "Settings":
        if self.app_env != "test" and self.session_cookie_secret is None:
            raise ValueError("session_cookie_secret is required outside tests")
        if self.outbound_allow_report_excerpt and self.outbound_max_excerpt_chars < 1:
            raise ValueError(
                "outbound_max_excerpt_chars must be positive when report excerpts "
                "are allowed; an unbounded excerpt is the full body"
            )
        if self.kimi_coding_api_key and not self.coding_model:
            raise ValueError(
                "kimi_coding_model or kimi_model is required when the Coding Plan "
                "provider is configured"
            )
        if self.kimi_metered_api_key and not self.metered_model:
            raise ValueError(
                "kimi_metered_model or kimi_model is required when the metered "
                "provider is configured"
            )
        if bool(self.kimi_coding_base_url) != bool(self.kimi_coding_api_key):
            raise ValueError(
                "kimi_coding_base_url and kimi_coding_api_key must be configured together"
            )
        if self.wind_enabled:
            required = (
                self.wind_host,
                self.wind_user,
                self.wind_password,
                self.wind_database,
            )
            if any(value is None for value in required):
                raise ValueError(
                    "wind_host, wind_user, wind_password and wind_database are required"
                )
        return self

    @property
    def coding_model(self) -> str | None:
        """Resolve the Coding Plan model without breaking the legacy setting."""
        return self.kimi_coding_model or self.kimi_model

    @property
    def metered_model(self) -> str | None:
        """Resolve the metered model without breaking the legacy setting."""
        return self.kimi_metered_model or self.kimi_model

    def worker_environment(self) -> dict[str, str]:
        return {"PYTHONUNBUFFERED": "1", "APP_ENV": self.app_env}

    def outbound_filter(self) -> OutboundFilter:
        """Build the B4 filter configured by this environment."""
        return OutboundFilter(
            allow_report_excerpt=self.outbound_allow_report_excerpt,
            max_excerpt_chars=self.outbound_max_excerpt_chars,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
