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
  TEST_REDIS_PASSWORD         default: se lee de docker/.env (REDIS_PASSWORD)
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

# Redis test DB — DB 15 por defecto, para no pisar la dev (DB 0). La resolución
# (contraseña incluida) vive en `_redis_url.py`, que es la ÚNICA fuente de verdad
# del arnés: escribir la URL a mano aquí o en un test es la trampa que documenta
# `gotchas/redis-con-contrasena-rompe-la-integracion.md`.
from ._redis_url import TEST_REDIS_URL

#: `127.0.0.1` y NO `localhost`, a propósito. En Windows el resolver devuelve
#: `::1` ANTES que `127.0.0.1`, y los puertos que publica Docker Desktop sólo
#: escuchan en IPv4: cada conexión paga ~2 s esperando el rechazo del intento
#: IPv6 antes de caer al fallback. No es un error —todo acaba conectando—, así
#: que no se ve: sólo se nota en que la suite tarda horas… y en que
#: `/readyz`, cuyo deadline por check es de 2 s, da 503 con las dos
#: dependencias VIVAS. Ver
#: `docs/03-guides/gotchas/localhost-ipv6-primero-cuesta-dos-segundos.md`.
PG_HOST = os.environ.get("TEST_PG_HOST", "127.0.0.1")
# Default 15432 matches docker/docker-compose.dev.yml — avoids clashing
# with any local postgres on the host. Override TEST_PG_PORT for CI.
PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))


PG_ADMIN_USER = os.environ.get("TEST_PG_ADMIN_USER", "postgres")
PG_ADMIN_PASSWORD = os.environ.get("TEST_PG_ADMIN_PASSWORD", "changeme-dev-only")
PG_MIG_USER = os.environ.get("TEST_PG_MIGRATIONS_USER", "migrations_user")
PG_MIG_PASSWORD = os.environ.get("TEST_PG_MIGRATIONS_PASSWORD", "changeme-migrations-dev-only")
PG_APP_USER = os.environ.get("TEST_PG_APP_USER", "app_user")
PG_APP_PASSWORD = os.environ.get("TEST_PG_APP_PASSWORD", "changeme-app-dev-only")
PG_TEST_DB = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")


def _admin_dsn(db: str = "postgres") -> str:
    return f"postgresql://{PG_ADMIN_USER}:{PG_ADMIN_PASSWORD}@{PG_HOST}:{PG_PORT}/{db}"


async def _drop_create_db() -> None:
    conn = await asyncpg.connect(_admin_dsn(db="postgres"))
    try:
        # Disconnect anyone still on the test DB (idempotent).
        await conn.execute(f"""
            SELECT pg_terminate_backend(pid)
              FROM pg_stat_activity
             WHERE datname = '{PG_TEST_DB}' AND pid <> pg_backend_pid()
            """)
        await conn.execute(f'DROP DATABASE IF EXISTS "{PG_TEST_DB}"')
        await conn.execute(f'CREATE DATABASE "{PG_TEST_DB}" OWNER "{PG_MIG_USER}"')
    finally:
        await conn.close()

    # Enable the extensions the production init scripts add (pgvector,
    # pg_trgm, pgcrypto, uuid-ossp), grant baseline schema USAGE to
    # app_user, and set the ALTER DEFAULT PRIVILEGES that production
    # also configures (so any later CREATE TABLE BY migrations_user
    # auto-grants DML to app_user — same behaviour as prod).
    target = await asyncpg.connect(_admin_dsn(db=PG_TEST_DB))
    try:
        for ext in ("vector", "pg_trgm", "pgcrypto", "uuid-ossp"):
            await target.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
        await target.execute(f'GRANT USAGE ON SCHEMA public TO "{PG_APP_USER}"')
        await target.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{PG_MIG_USER}" IN SCHEMA public '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{PG_APP_USER}"'
        )
        await target.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{PG_MIG_USER}" IN SCHEMA public '
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{PG_APP_USER}"'
        )
    finally:
        await target.close()


#: Tablas a las que una migración RETIRA el acceso de la aplicación a propósito.
#:
#: El retro-grant de abajo es un `ON ALL TABLES` sin excepciones, así que
#: **deshacía esos revokes** y dejaba el arnés MÁS PERMISIVO QUE PRODUCCIÓN. Lo
#: destapó `test_the_backfill_table_is_unreachable_from_the_app`, que pasaba en
#: aislamiento y fallaba en lote: cualquier test anterior que llamase aquí volvía
#: a conceder lo que la migración 0138 había quitado.
#:
#: Ese patrón —guarda que solo pasa sola— es el que acaba con la guarda borrada
#: por «flaky», y la guarda tenía razón.
#:
#: Añadir una entrada exige que exista la migración que la revoca. Hoy:
#:   · `approval_policy_backfill_0133` → migración 0138 (respaldo interno de la
#:     0133; la aplicación no lo consulta y no debe poder leerlo).
_APP_REVOKED_TABLES: tuple[str, ...] = ("approval_policy_backfill_0133",)


async def _grant_app_user_existing_tables() -> None:
    """Retro-grant DML on tables that already exist (the default privs
    above only apply to tables created *after* they are set). Idempotent.

    Reaplica después los revokes de :data:`_APP_REVOKED_TABLES`, para que el
    arnés reproduzca los permisos de producción y no unos más laxos.
    """
    conn = await asyncpg.connect(_admin_dsn(db=PG_TEST_DB))
    try:
        await conn.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE"
            f' ON ALL TABLES IN SCHEMA public TO "{PG_APP_USER}"'
        )
        await conn.execute(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{PG_APP_USER}"'
        )
        for table in _APP_REVOKED_TABLES:
            # `to_regclass` porque el retro-grant corre también sobre esquemas a
            # medio migrar, donde la tabla puede no existir todavía.
            await conn.execute(f"""
                DO $$
                BEGIN
                    IF to_regclass('public.{table}') IS NOT NULL THEN
                        EXECUTE 'REVOKE ALL ON TABLE public.{table} FROM "{PG_APP_USER}"';
                    END IF;
                END $$;
                """)
    finally:
        await conn.close()


async def _drop_db() -> None:
    conn = await asyncpg.connect(_admin_dsn(db="postgres"))
    try:
        await conn.execute(f"""
            SELECT pg_terminate_backend(pid)
              FROM pg_stat_activity
             WHERE datname = '{PG_TEST_DB}' AND pid <> pg_backend_pid()
            """)
        await conn.execute(f'DROP DATABASE IF EXISTS "{PG_TEST_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """Session-scoped: create the test DB, yield its URL, drop on teardown.

    DO NOT run this suite under pytest-xdist (``-n``). The whole integration
    suite shares this ONE session-scoped database, and some tests depend on
    execution order (e.g. ``test_migrations.py`` asserts on the shared schema
    after its upgrade->downgrade->upgrade round-trip). Parallel workers would
    race on both the shared DB and that ordering, producing flaky failures.
    Follow-up (Plan prod-02 task_12, finding tests-8): give each xdist worker
    its own throwaway database (worker-id-suffixed name) before enabling ``-n``.
    """
    asyncio.run(_drop_create_db())
    url = f"postgresql+asyncpg://{PG_MIG_USER}:{PG_MIG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"
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


@pytest.fixture()
def migrations_pg_dsn() -> str:
    """DSN as migrations_user — has BYPASSRLS, used to seed test data
    bypassing RLS policies."""
    return f"postgresql://{PG_MIG_USER}:{PG_MIG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


@pytest.fixture()
def app_database_url() -> str:
    """SQLAlchemy URL as app_user (NOBYPASSRLS). Use this for the
    FastAPI app under test so it goes through RLS like in production."""
    return f"postgresql+asyncpg://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


@pytest.fixture()
def admin_database_url() -> str:
    """SQLAlchemy URL as migrations_user (BYPASSRLS). Used by /admin/*
    endpoints so System Admin can read across tenants and write
    audit_log rows with tenant_id IS NULL."""
    return f"postgresql+asyncpg://{PG_MIG_USER}:{PG_MIG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


@pytest.fixture()
def test_redis_url() -> str:
    """Redis URL the FastAPI app under test should use."""
    return TEST_REDIS_URL


async def _flush_redis(url: str) -> None:
    """Wipe the test Redis DB. Idempotent."""
    from redis.asyncio import Redis

    client = Redis.from_url(url, decode_responses=True)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


@pytest.fixture()
def configured_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[object]:
    """A real api-server app, migrated DB + flushed Redis, wired for tests.

    Shared by the public-API integration suites (``test_api_v1_endpoints``,
    ``test_api_versioning``) so they exercise the SAME wired app (every v1
    router-level dependency included). Upgrades the throwaway DB to head,
    grants the app role on the freshly-created tables, flushes the Redis
    test DB, points the api-server config at all three via env, then builds
    the app via :func:`create_app`. Engine/Redis/settings caches are reset
    on both setup and teardown so each test gets a clean, correctly-wired
    process state.
    """
    from alembic import command

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()
