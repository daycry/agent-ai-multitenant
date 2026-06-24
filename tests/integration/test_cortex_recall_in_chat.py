"""Córtex F1 — cableado fin-a-fin: recall híbrido en el hot-path del turno (Tarea 10).

Siembra una memoria del córtex del owner y comprueba que, en un ``POST
/owner/cortex/turns``, el recall híbrido la trae y ``augment_cortex_prompt`` la
inyecta en el ``system_prompt`` que recibe el modelo — sin que el modelo tenga que
llamar ninguna tool. Reutiliza el patrón de captura del ``decide`` del test del
asistente (``_CapturingModel``)."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_owner(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, cortex_turns, cortex_conversations,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Recall Chat Tenant",
            "cortex-recall-chat-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, true)",
            owner_id,
            "owner@recallchat.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "tenant_id": tenant_id}


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=True)


class _CapturingModel:
    """Records the system prompt it was handed, then answers (no tools)."""

    def __init__(self) -> None:
        self.system_prompt: str | None = None

    async def decide(self, state):
        from api_server.assistant.graph import ModelTurn

        self.system_prompt = state.system_prompt
        return ModelTurn(content="entendido")


@pytest.mark.asyncio
async def test_cortex_recall_surfaces_memory_in_system_prompt(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    tenant_id = seed["tenant_id"]

    # --- Seed a córtex memory of the owner (cortex=true) ---
    import api_server.db.session as session_mod
    from api_server.config import get_settings
    from api_server.cortex.memory import cortex_remember
    from api_server.routers.cortex import get_cortex_model

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    async with session_mod.get_admin_sessionmaker()() as session:
        await cortex_remember(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            content="Al owner le interesa la arquitectura hexagonal",
        )
        await session.commit()

    captured = _CapturingModel()
    configured_app.dependency_overrides[get_cortex_model] = lambda: captured
    token = await _mint(owner_id, tenant_id)
    headers = {"Authorization": f"Bearer {token}"}

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/owner/cortex/turns",
            json={"message": "cuéntame sobre arquitectura hexagonal"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    # The hybrid recall ran in the hot-path and the memory was folded into the prompt.
    assert captured.system_prompt is not None
    assert "Lo que sé de ti" in captured.system_prompt
    assert "Al owner le interesa la arquitectura hexagonal" in captured.system_prompt
