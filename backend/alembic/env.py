"""Alembic migration environment.

Migrations run against a *synchronous* SQLAlchemy engine. The runtime database URL
uses the async ``aiosqlite`` driver; here we strip it so the same on-disk SQLite
database is migrated synchronously. ``render_as_batch=True`` keeps ALTER operations
working on SQLite.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine.url import make_url

# Ensure the editable package is importable even outside `uv run`.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from factor_platform.db.base import Base  # noqa: E402
from factor_platform.db import models  # noqa: E402,F401  (register tables on metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_DEFAULT_URL = "sqlite:///./data/runtime/factor_platform.db"


def _resolve_url() -> str:
    url = os.environ.get("DATABASE_URL") or _DEFAULT_URL
    # Migrations are synchronous; drop the async driver suffix.
    return url.replace("+aiosqlite", "")


def _ensure_db_dir(url: str) -> None:
    database = make_url(url).database
    if database and database != ":memory:":
        directory = os.path.dirname(database)
        if directory:
            os.makedirs(directory, exist_ok=True)


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolve_url()
    _ensure_db_dir(url)
    configuration = config.get_section(config.config_ini_section, {}) or {}
    configuration["sqlalchemy.url"] = url
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
