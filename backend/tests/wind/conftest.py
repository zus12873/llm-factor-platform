"""Shared fixtures for the Wind adapter/connection test suite."""

from __future__ import annotations

import pytest

from factor_platform.settings import Settings


@pytest.fixture
def settings() -> Settings:
    """Wind-enabled Settings with clearly-fake credentials for unit tests.

    ``app_env="test"`` avoids the session-cookie-secret requirement; the plain
    string password is coerced to SecretStr by pydantic.
    """
    return Settings(
        app_env="test",
        wind_enabled=True,
        wind_host="db.internal",
        wind_user="u",
        wind_password="unit-test-only",  # clearly fake, marked
        wind_database="wind",
    )
