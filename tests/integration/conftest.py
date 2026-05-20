"""Shared fixtures for integration tests.

The integration suite talks to a real PostgreSQL — the one in
docker/docker-compose.yml. Each test session creates a throwaway
database (`agentic_platform_test`) so tests cannot pollute the dev
database; the DB is dropped on teardown.

Env overrides (defaults match docker/.env.example):
  TEST_PG_HOST                default: localhost
  TEST_PG_PORT                default: 5432
  TEST_PG_ADMIN_USER          default: postgres
  TEST_PG_ADMIN_PASSWORD      default: changeme-dev-only
  TEST_PG_MIGRATIONS_USER     default: migrations_user
  TEST_PG_MIGRATIONS_PASSWORD default: changeme-migrations-dev-only
  TEST_PG_DB_NAME             default: agentic_platform_test
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
# Default 15432 matches docker/docker-compose.dev.yml — avoids clashing
# with any local postgres on the host. Override TEST_PG_PORT for CI.
PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))
PG_ADMIN_USER = os.environ.get("TEST_PG_ADMIN_USER", "postgres")
PG_ADMIN_PASSWORD = os.environ.get("TEST_PG_ADMIN_PASSWORD", "changeme-dev-only")
PG_MIG_USER = os.environ.get("TEST_PG_MIGRATIONS_USER", "migrations_user")
PG_MIG_PASSWORD = os.environ.get("TEST_PG_MIGRATIONS_PASSWORD", "changeme-migrations-dev-only")
PG_TEST_DB = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")


def _admin_dsn(db: str = "postgres") -> str:
    return f"postgresql://{PG_ADMIN_USER}:{PG_ADMIN_PASSWORD}@{PG_HOST}:{PG_PORT}/{db}"


async def _drop_create_db() -> None:
    conn = await asyncpg.connect(_admin_dsn(db="postgres"))
    try:
        # Disconnect anyone still on the test DB (idempotent).
        await conn.execute(
            f"""
            SELECT pg_terminate_backend(pid)
              FROM pg_stat_activity
             WHERE datname = '{PG_TEST_DB}' AND pid <> pg_backend_pid()
            """
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{PG_TEST_DB}"')
        await conn.execute(f'CREATE DATABASE "{PG_TEST_DB}" OWNER "{PG_MIG_USER}"')
    finally:
        await conn.close()

    # Enable the extensions the production init scripts add (pgvector,
    # pg_trgm, pgcrypto, uuid-ossp) so migrations referencing them work.
    target = await asyncpg.connect(_admin_dsn(db=PG_TEST_DB))
    try:
        for ext in ("vector", "pg_trgm", "pgcrypto", "uuid-ossp"):
            await target.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
    finally:
        await target.close()


async def _drop_db() -> None:
    conn = await asyncpg.connect(_admin_dsn(db="postgres"))
    try:
        await conn.execute(
            f"""
            SELECT pg_terminate_backend(pid)
              FROM pg_stat_activity
             WHERE datname = '{PG_TEST_DB}' AND pid <> pg_backend_pid()
            """
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{PG_TEST_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """Session-scoped: create the test DB, yield its URL, drop on teardown."""
    asyncio.run(_drop_create_db())
    url = (
        f"postgresql+asyncpg://{PG_MIG_USER}:{PG_MIG_PASSWORD}" f"@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"
    )
    try:
        yield url
    finally:
        asyncio.run(_drop_db())


@pytest.fixture()
def alembic_config(test_database_url: str) -> Iterator[object]:
    """Alembic Config wired to the test DB. Use `alembic.command.*` with it."""
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    api_server_dir = repo_root / "apps" / "api-server"

    cfg = Config(str(api_server_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_server_dir / "migrations"))
    os.environ["DATABASE_URL"] = test_database_url
    try:
        yield cfg
    finally:
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture()
def admin_pg_dsn() -> str:
    """Sync-style DSN for ad-hoc inspection queries by admin (BYPASSRLS)."""
    return _admin_dsn(db=PG_TEST_DB)
