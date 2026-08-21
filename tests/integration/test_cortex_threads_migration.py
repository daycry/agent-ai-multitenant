"""Córtex F1 — migración 0092 (cortex_conversations + cortex_turns).

Ejercita la migración de las primeras tablas tenant-less del córtex (ADR 0074,
F1): aplica ``alembic upgrade head``, comprueba que ambas tablas + el índice
parcial + el CHECK de ``role`` existen, que un INSERT con ``role='agent'`` viola
el CHECK, y que ``alembic downgrade 0091_system_owner_f0`` las elimina
(reversible)."""

from __future__ import annotations

import asyncio

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

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
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
async def test_cortex_tables_and_check_and_reversible(
    configured_app, migrations_pg_dsn: str, alembic_config
) -> None:
    """Upgrade -> tablas+índice+CHECK presentes y el CHECK rechaza role='agent';
    downgrade -> tablas eliminadas (reversible)."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # --- upgrade head ya aplicado por configured_app ---
        assert await _table_exists(conn, "cortex_conversations")
        assert await _table_exists(conn, "cortex_turns")
        assert await _index_exists(conn, "ix_cortex_conversations_owner")
        assert await _index_exists(conn, "ix_cortex_turns_conversation")

        # Sembrar un owner + tenant para poder insertar filas válidas.
        from uuid import uuid4

        owner_id = uuid4()
        tenant_id = uuid4()
        conv_id = uuid4()
        await conn.execute(
            "TRUNCATE cortex_turns, cortex_conversations, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Tenant",
            "cortex-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            owner_id,
            "owner@cortex.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id) VALUES ($1, $2, $3)",
            conv_id,
            owner_id,
            tenant_id,
        )

        # Un role 'user' / 'cortex' es válido; 'agent' viola el CHECK.
        await conn.execute(
            "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content)"
            " VALUES ($1, $2, $3, 'user', 'hola')",
            uuid4(),
            conv_id,
            owner_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content)"
                " VALUES ($1, $2, $3, 'agent', 'nope')",
                uuid4(),
                conv_id,
                owner_id,
            )
    finally:
        await conn.close()

    # --- downgrade: las tablas desaparecen ---
    # ``command.downgrade`` corre ``asyncio.run`` internamente (env.py), así que
    # debe ejecutarse fuera del event loop del test → en un hilo aparte.
    await asyncio.to_thread(command.downgrade, alembic_config, "0091_system_owner_f0")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert not await _table_exists(conn, "cortex_conversations")
        assert not await _table_exists(conn, "cortex_turns")
    finally:
        await conn.close()
