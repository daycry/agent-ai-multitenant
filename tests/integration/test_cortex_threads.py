"""Córtex F1 — capa de persistencia del hilo owner-scoped (BYPASSRLS).

Las tablas del córtex son tenant-less sobre BYPASSRLS: el aislamiento es por un
filtro ``owner_user_id`` explícito en TODO SQL (no hay RLS). Este test ejercita
el round-trip create→append→list y comprueba que un ``owner_user_id`` ajeno al
hilo NUNCA escribe (lanza y no muta el conteo de turnos)."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

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


async def _seed_owner(dsn: str) -> dict[str, UUID]:
    """One owner + tenant + active membership; plus a second 'other' user."""
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
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
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            owner_id,
            "owner@cortex.test",
            "h",
            other_id,
            "other@cortex.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            owner_id,
            "tenant_admin",
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


def _admin_sessionmaker(admin_database_url: str):
    import api_server.db.session as session_mod
    from api_server.config import get_settings

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    return session_mod.get_admin_sessionmaker()


@pytest.mark.asyncio
async def test_append_and_list_turns_owner_scoped(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex import threads

    seed = await _seed_owner(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]

    sessionmaker = _admin_sessionmaker(admin_database_url)

    async with sessionmaker() as session:
        tenant_id = await threads.resolve_cortex_tenant_id(session, owner_id)
        assert tenant_id == seed["tenant_id"]

        conv = await threads.create_conversation(
            session, owner_user_id=owner_id, tenant_id=tenant_id, model_id="claude-sonnet-4-5"
        )
        await session.commit()

        await threads.append_turn(
            session,
            conversation_id=conv.id,
            owner_user_id=owner_id,
            role="user",
            content="hola córtex",
        )
        await threads.append_turn(
            session,
            conversation_id=conv.id,
            owner_user_id=owner_id,
            role="cortex",
            content="hola owner",
            model_id="claude-sonnet-4-5",
            rounds=1,
            reasoning_effort="high",
        )
        await session.commit()

        turns = await threads.list_turns(session, conversation_id=conv.id, owner_user_id=owner_id)
        assert [t.role for t in turns] == ["user", "cortex"]
        assert [t.content for t in turns] == ["hola córtex", "hola owner"]

        convs = await threads.list_conversations(session, owner_user_id=owner_id)
        assert [c.id for c in convs] == [conv.id]

        history = await threads.recent_history_for_prompt(
            session, conversation_id=conv.id, owner_user_id=owner_id
        )
        assert history == [
            {"role": "user", "content": "hola córtex"},
            {"role": "cortex", "content": "hola owner"},
        ]

        # --- cross-owner: un owner ajeno NO escribe ni lee el hilo ---
        # append_turn verifica la pertenencia con un SELECT explícito ANTES de
        # escribir: lanza sin tocar la BD, así que NO hay nada que rollback-ear.
        with pytest.raises(PermissionError):
            await threads.append_turn(
                session,
                conversation_id=conv.id,
                owner_user_id=other_id,
                role="user",
                content="intruso",
            )

        # El conteo de turnos no cambió.
        still = await threads.list_turns(session, conversation_id=conv.id, owner_user_id=owner_id)
        assert len(still) == 2

        # list_turns con el owner ajeno tampoco ve nada.
        with pytest.raises(PermissionError):
            await threads.list_turns(session, conversation_id=conv.id, owner_user_id=other_id)


@pytest.mark.asyncio
async def test_resolve_cortex_tenant_id_raises_without_membership(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex import threads
    from api_server.cortex.threads import CortexNoTenantError

    # Seed an owner with NO membership.
    owner_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_turns, cortex_conversations, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            owner_id,
            "lonely@cortex.test",
            "h",
        )
    finally:
        await conn.close()

    sessionmaker = _admin_sessionmaker(admin_database_url)
    async with sessionmaker() as session:
        with pytest.raises(CortexNoTenantError):
            await threads.resolve_cortex_tenant_id(session, owner_id)
