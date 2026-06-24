"""Córtex F1 — 🔒 aislamiento cross-owner OBLIGATORIO (Tarea 9).

Las tablas del córtex son tenant-less sobre BYPASSRLS: NO hay RLS que proteja, así
que el ÚNICO mecanismo de aislamiento es el filtro ``owner_user_id`` explícito en
TODO SQL de :mod:`api_server.cortex.threads`. Este test es la condición de mérito de
seguridad de F1 (excepción consciente al Principio 1).

Se siembran dos owners A y B con hilos y turnos propios (insert directo, dos
``owner_user_id`` distintos en ``cortex_conversations``/``cortex_turns``) y se
verifica que ``list_turns``/``list_conversations``/``recent_history_for_prompt``/
``append_turn`` con el owner A NUNCA ven ni tocan filas del owner B, corriendo sobre
BYPASSRLS.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


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


async def _seed_two_owners(dsn: str) -> dict[str, UUID]:
    """Two distinct owners (A, B), each with one thread holding two turns.

    Inserted directly (BYPASSRLS) so the only thing that can keep A and B apart
    in :mod:`api_server.cortex.threads` is the explicit ``owner_user_id`` filter."""
    owner_a, owner_b = uuid4(), uuid4()
    tenant_id = uuid4()
    conv_a, conv_b = uuid7(), uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_turns, cortex_conversations, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cross Owner Tenant",
            "cross-owner-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            owner_a,
            "a@cross.test",
            "h",
            owner_b,
            "b@cross.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id, title)"
            " VALUES ($1, $2, $3, 'hilo A'), ($4, $5, $6, 'hilo B')",
            conv_a,
            owner_a,
            tenant_id,
            conv_b,
            owner_b,
            tenant_id,
        )
        # Two turns per owner.
        for conv, owner, prefix in ((conv_a, owner_a, "A"), (conv_b, owner_b, "B")):
            await conn.execute(
                "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content)"
                " VALUES ($1, $2, $3, 'user', $4), ($5, $6, $7, 'cortex', $8)",
                uuid7(),
                conv,
                owner,
                f"pregunta de {prefix}",
                uuid7(),
                conv,
                owner,
                f"respuesta a {prefix}",
            )
    finally:
        await conn.close()
    return {
        "owner_a": owner_a,
        "owner_b": owner_b,
        "tenant_id": tenant_id,
        "conv_a": conv_a,
        "conv_b": conv_b,
    }


def _admin_sessionmaker():
    import api_server.db.session as session_mod
    from api_server.config import get_settings

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    return session_mod.get_admin_sessionmaker()


@pytest.mark.asyncio
async def test_owner_a_never_sees_or_touches_owner_b(
    configured_app, migrations_pg_dsn: str
) -> None:
    from api_server.cortex import threads

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_a = seed["owner_a"]
    owner_b = seed["owner_b"]
    conv_a = seed["conv_a"]
    conv_b = seed["conv_b"]

    sessionmaker = _admin_sessionmaker()

    async with sessionmaker() as session:
        # --- list_conversations is owner-scoped: A sees ONLY A's thread ---
        convs_a = await threads.list_conversations(session, owner_user_id=owner_a)
        assert [c.id for c in convs_a] == [conv_a]
        assert all(c.owner_user_id == owner_a for c in convs_a)

        convs_b = await threads.list_conversations(session, owner_user_id=owner_b)
        assert [c.id for c in convs_b] == [conv_b]

        # --- list_turns: A reading A's thread works; A reading B's is rejected ---
        turns_a = await threads.list_turns(session, conversation_id=conv_a, owner_user_id=owner_a)
        assert [t.content for t in turns_a] == ["pregunta de A", "respuesta a A"]

        # A trying to read B's thread → PermissionError (the WHERE owner_user_id
        # filter on the ownership SELECT finds no row for A).
        with pytest.raises(PermissionError):
            await threads.list_turns(session, conversation_id=conv_b, owner_user_id=owner_a)

        # recent_history_for_prompt is owner-scoped too.
        with pytest.raises(PermissionError):
            await threads.recent_history_for_prompt(
                session, conversation_id=conv_b, owner_user_id=owner_a
            )

        # --- append_turn: A can NEVER write into B's thread ---
        with pytest.raises(PermissionError):
            await threads.append_turn(
                session,
                conversation_id=conv_b,
                owner_user_id=owner_a,
                role="user",
                content="intruso",
            )

    # B's thread is intact — A's failed write touched nothing.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count_b = await conn.fetchval(
            "SELECT count(*) FROM cortex_turns WHERE conversation_id = $1", conv_b
        )
        # Every turn row of B still belongs to B (no cross-owner leak).
        owners_b = await conn.fetch(
            "SELECT DISTINCT owner_user_id FROM cortex_turns WHERE conversation_id = $1",
            conv_b,
        )
    finally:
        await conn.close()
    assert count_b == 2
    assert [r["owner_user_id"] for r in owners_b] == [owner_b]
