"""The one place verification queries reach a real Wind connection.

The verifiers are written against a :class:`~factor_platform.wind.schema_verify.
QueryExecutor` protocol so their logic can be exercised offline against fakes.
This module supplies the production implementation, and it is the seam where the
async verification code meets the synchronous ``pymysql`` driver: each query runs
on a worker thread so a slow database cannot block the event loop.

It refuses to construct itself when Wind is not configured. The alternative —
building fine and failing at query time — turns a missing-credential problem into
what looks like a data problem, several layers away from the cause.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from factor_platform.settings import Settings
from factor_platform.wind.connection import WindConnectionFactory


class WindNotConfiguredError(RuntimeError):
    """Raised when a live query is attempted without Wind credentials."""


class WindQueryExecutor:
    """Runs bounded, parameterized reads against the Wind replica."""

    def __init__(self, settings: Settings) -> None:
        if not settings.wind_enabled:
            raise WindNotConfiguredError(
                "WIND_ENABLED is false; set it and the WIND_* credentials in .env "
                "to run live verification"
            )
        self._factory = WindConnectionFactory(settings)

    async def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_blocking, sql, dict(params))

    def _fetch_blocking(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        connection = self._factory.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        finally:
            connection.close()


__all__ = ["WindNotConfiguredError", "WindQueryExecutor"]
