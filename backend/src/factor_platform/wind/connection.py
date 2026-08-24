"""Wind MySQL connection factory.

This module owns three concerns that the original ``rq_wind_replica`` mixed
into a module-level ``MYSQL_CONFIG`` dict:

* **Config extraction** — read Wind connection fields from a :class:`Settings`
  instance (never from ``os.environ`` directly), unwrapping the password
  ``SecretStr`` only at ``connect()`` time.
* **Retry on transient errors** — MySQL error codes 2006/2013 typically mean
  the server went away mid-flight; one bounded retry is attempted before
  surfacing the error.
* **Connection creation** — the single place ``pymysql.connect`` is called.

Constructing the factory is cheap and side-effect free: it does NOT open a
socket. Only :meth:`WindConnectionFactory.connect` does, so importing this
module (or the adapter) requires no credentials and performs no I/O.

The password is never logged. Debug log records include only the error code
and attempt number.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pymysql  # type: ignore[import-untyped]

from factor_platform.secrets import reveal_secret
from factor_platform.settings import Settings

_LOG = logging.getLogger(__name__)

# MySQL error codes that typically indicate the server/connection went away
# and a retry may succeed: 2006 (CR_SERVER_GONE_ERR), 2013 (CR_SERVER_LOST).
# Shared with the adapter so query-level and connection-level retries agree.
TRANSIENT_MYSQL_ERROR_CODES: frozenset[int] = frozenset({2006, 2013})

_CONNECT_MAX_ATTEMPTS = 2
_CONNECT_RETRY_SLEEP_SEC = 0.25

_DEFAULT_CHARSET = "utf8mb4"
_DEFAULT_CONNECT_TIMEOUT_SEC = 10
_DEFAULT_READ_TIMEOUT_SEC = 60
_DEFAULT_WRITE_TIMEOUT_SEC = 60


class WindConnectionFactory:
    """Builds pymysql connections from :class:`Settings`.

    The factory reads Wind connection parameters at construction time but
    defers unwrapping the password ``SecretStr`` and opening the socket until
    :meth:`connect` is called. This keeps module import and factory
    construction credential-free and side-effect free, which is required so
    that importing ``factor_platform.wind.adapter`` never touches a database.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def connect(self) -> Any:
        """Open and return a pymysql connection, retrying transient errors.

        Raises ``RuntimeError`` if the Wind settings are incomplete; raises
        the underlying ``pymysql`` error if connection ultimately fails.
        """
        kwargs = self._connect_kwargs()
        last_error: Exception | None = None
        for attempt in range(_CONNECT_MAX_ATTEMPTS):
            try:
                return pymysql.connect(**kwargs)
            except (pymysql.err.InterfaceError, pymysql.err.OperationalError) as error:
                code = error.args[0] if error.args else None
                last_error = error
                if (
                    code not in TRANSIENT_MYSQL_ERROR_CODES
                    or attempt + 1 >= _CONNECT_MAX_ATTEMPTS
                ):
                    raise
                _LOG.debug(
                    "wind mysql connect attempt %d failed (code=%r); retrying",
                    attempt + 1,
                    code,
                )
                time.sleep(_CONNECT_RETRY_SLEEP_SEC)
        # Every loop iteration either returns or raises, so this is unreachable
        # in practice; the assert+raise exists to satisfy the type checker.
        assert last_error is not None  # pragma: no cover
        raise last_error  # pragma: no cover

    def _connect_kwargs(self) -> dict[str, Any]:
        """Build the pymysql.connect keyword arguments from Settings.

        The password ``SecretStr`` is unwrapped here, so it only leaves the
        SecretStr the moment a connection is actually opened.
        """
        settings = self._settings
        host = settings.wind_host
        user = settings.wind_user
        password_secret = settings.wind_password
        database = settings.wind_database
        if host is None or user is None or password_secret is None or database is None:
            raise RuntimeError(
                "Wind connection is incompletely configured: "
                "wind_host, wind_user, wind_password and wind_database are required."
            )
        return {
            "host": host,
            "port": settings.wind_port,
            "user": user,
            "password": reveal_secret(password_secret),
            "database": database,
            "charset": _DEFAULT_CHARSET,
            "connect_timeout": _DEFAULT_CONNECT_TIMEOUT_SEC,
            "read_timeout": _DEFAULT_READ_TIMEOUT_SEC,
            "write_timeout": _DEFAULT_WRITE_TIMEOUT_SEC,
            "cursorclass": pymysql.cursors.DictCursor,
        }
