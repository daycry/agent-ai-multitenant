"""Alembic environment.

Async-aware: uses an async SQLAlchemy engine so we can keep a single
asyncpg driver across the codebase. Connection details come from the
DATABASE_URL env var (so the same migrations run unchanged in dev, CI
and prod).

Required env vars:
  DATABASE_URL    e.g. postgresql+asyncpg://migrations_user:pwd@host:5432/agentic_platform

Optional env vars:
  ALEMBIC_LOG_LEVEL    INFO|WARNING|DEBUG (default: INFO)
"""

from __future__ import annotations

import asyncio
import logging
import os
from logging.config import fileConfig

from alembic import context

# Import models so Base.metadata is fully populated.
from api_server.db import models as _models  # noqa: F401
from api_server.db.base import Base
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

#: Cross-process advisory lock so two concurrent ``alembic upgrade`` runs (app
#: replicas, the install one-shot + a manual run, …) cannot apply migrations at
#: the same time (Plan prod-01 task_12 / deploy-6). A transaction-scoped lock
#: (``pg_advisory_xact_lock``) auto-releases at COMMIT; the loser blocks, then
#: sees an up-to-date DB and no-ops. Arbitrary fixed key for the "agentic
#: migrations" namespace.
_MIGRATION_LOCK_KEY = 0x4147454E54  # "AGENT"

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logging.getLogger("alembic").setLevel(os.environ.get("ALEMBIC_LOG_LEVEL", "INFO").upper())

# Override sqlalchemy.url from the environment (the .ini ships empty).
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    raise RuntimeError(
        "DATABASE_URL must be set when running Alembic. Example:\n"
        "  DATABASE_URL=postgresql+asyncpg://migrations_user:pwd@localhost:5432/agentic_platform"
    )
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection.

    Used by `alembic upgrade head --sql` to generate a deployable
    migration script. RLS DDL is statically known so this works.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        # Serialize concurrent upgrades within this same transaction; released
        # automatically on COMMIT (Plan prod-01 task_12).
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        context.run_migrations()


async def run_async_migrations() -> None:
    """Connect with an async engine, then hand off to the sync runner
    via SQLAlchemy's run_sync."""
    section = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
