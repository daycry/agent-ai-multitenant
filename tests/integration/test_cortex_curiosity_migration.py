"""Córtex F4 — migración 0095 (cortex_curiosity_pursuits).

Ejercita la tabla de auditoría/idempotencia de la curiosidad autónoma (ADR 0078):
``alembic upgrade head`` crea la tabla con los 2 índices y el CHECK de ``status``;
un INSERT con ``status='bogus'`` viola el CHECK; ``downgrade -1`` la elimina
(reversible). Patrón de ``test_cortex_threads_migration.py``.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    yield
    reset_engine_cache()
    reset_redis_cache()
    get_settings.cache_clear()


async def _table_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = $1)",
            name,
        )
    )


async def _index_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public'"
            " AND indexname = $1)",
            name,
        )
    )


@pytest.mark.asyncio
async def test_pursuits_table_indexes_check_and_reversible(
    configured_app, migrations_pg_dsn: str, alembic_config
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _table_exists(conn, "cortex_curiosity_pursuits")
        assert await _index_exists(conn, "ix_cortex_pursuits_owner_status")
        assert await _index_exists(conn, "ix_cortex_pursuits_owner_topic_created")

        owner_id = uuid4()
        await conn.execute(
            "TRUNCATE cortex_curiosity_pursuits, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, true)",
            owner_id,
            "owner@curiosity.test",
            "h",
        )

        # Un status válido entra; 'bogus' viola el CHECK.
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status)"
            " VALUES ($1, $2, 'arquitectura hexagonal', 'selected')",
            uuid4(),
            owner_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status)"
                " VALUES ($1, $2, 'x', 'bogus')",
                uuid4(),
                owner_id,
            )
    finally:
        await conn.close()

    # downgrade -1 → la tabla desaparece (reversible).
    await asyncio.to_thread(command.downgrade, alembic_config, "0094_cortex_identity")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert not await _table_exists(conn, "cortex_curiosity_pursuits")
    finally:
        await conn.close()
