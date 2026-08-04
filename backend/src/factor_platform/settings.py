from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    kimi_metered_base_url: str = "https://api.moonshot.cn/v1"
    kimi_metered_api_key: SecretStr | None = None
    kimi_model: str | None = None
    session_cookie_secret: SecretStr | None = None

    @model_validator(mode="after")
    def validate_required_settings(self) -> "Settings":
        if self.app_env != "test" and self.session_cookie_secret is None:
            raise ValueError("session_cookie_secret is required outside tests")
        if (self.kimi_coding_api_key or self.kimi_metered_api_key) and not self.kimi_model:
            raise ValueError("kimi_model is required when a Kimi provider is configured")
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

    def worker_environment(self) -> dict[str, str]:
        return {"PYTHONUNBUFFERED": "1", "APP_ENV": self.app_env}


@lru_cache
def get_settings() -> Settings:
    return Settings()
