"""Córtex F1 — endpoints ``/owner/cortex/*`` (Tareas 7 + 8).

Ejercita el router del córtex end-to-end sobre el app real (DB throwaway + Redis):

  * ``test_post_turn_persists_and_returns_answer`` (Tarea 7): el owner mintea un
    token, ``get_cortex_model`` se sobreescribe con un ``ScriptedAssistantModel``,
    ``POST /owner/cortex/turns {message}`` devuelve 200 con ``conversation_id`` +
    ``answer``; un segundo POST con ese ``conversation_id`` añade turno, y
    ``GET /owner/cortex/turns`` devuelve los 4 turnos en orden.
  * ``test_non_owner_gets_403`` (Tarea 8): un ``tenant_admin`` con
    ``is_system_owner=false`` (incluso forjando el claim ``own`` en el token) recibe
    403 en ``POST /owner/cortex/turns`` y ``GET /owner/cortex/conversations`` (el
    gate es DB-authoritative).
  * ``test_list_conversations_owner_scoped`` (Tarea 8): el owner crea 2 hilos; el
    listado los devuelve más-reciente-primero con ``last_turn_preview``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
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


async def _seed_owner(dsn: str, *, is_owner: bool = True) -> dict[str, UUID]:
    """One user + tenant + active tenant_admin membership.

    ``is_owner`` flags the DB row as the System Owner (the gate is
    DB-authoritative; the JWT ``own`` claim is only a hint)."""
    owner_id = uuid4()
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
            "Cortex Endpoint Tenant",
            "cortex-endpoint-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner) VALUES ($1, $2, $3, $4)",
            owner_id,
            "owner@endpoint.test",
            "h",
            is_owner,
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


async def _mint(user_id: UUID, tenant_id: UUID, *, owner_claim: bool = False) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=owner_claim
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _scripted_answer(answer: str):
    """A scripted model that calls NO tools and just answers."""
    from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel

    return ScriptedAssistantModel(turns=[ModelTurn(content=answer)])


# ===========================================================================
# Tarea 7 — POST persists the thread + returns the answer
# ===========================================================================
@pytest.mark.asyncio
async def test_post_turn_persists_and_returns_answer(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    from api_server.routers.cortex import get_cortex_model

    configured_app.dependency_overrides[get_cortex_model] = lambda: _scripted_answer(
        "Hola owner, soy tu córtex."
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        first = await client.post("/owner/cortex/turns", json={"message": "hola"}, headers=headers)
        assert first.status_code == 200, first.text
        body = first.json()
        conv_id = body["conversation_id"]
        assert conv_id
        assert body["answer"] == "Hola owner, soy tu córtex."

        # A second turn on the SAME thread.
        second = await client.post(
            "/owner/cortex/turns",
            json={"message": "¿me recuerdas?", "conversation_id": conv_id},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        assert second.json()["conversation_id"] == conv_id

        # The thread now holds 4 turns (user/cortex × 2) in chronological order.
        listed = await client.get(
            "/owner/cortex/turns",
            params={"conversation_id": conv_id},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        turns = listed.json()
        assert [t["role"] for t in turns] == ["user", "cortex", "user", "cortex"]
        assert [t["content"] for t in turns] == [
            "hola",
            "Hola owner, soy tu córtex.",
            "¿me recuerdas?",
            "Hola owner, soy tu córtex.",
        ]


# ===========================================================================
# Tarea 8 — non-owner gets 403 (DB-authoritative gate)
# ===========================================================================
@pytest.mark.asyncio
async def test_non_owner_gets_403(configured_app, migrations_pg_dsn: str) -> None:
    # tenant_admin but NOT system owner in the DB.
    seed = await _seed_owner(migrations_pg_dsn, is_owner=False)
    from api_server.routers.cortex import get_cortex_model

    configured_app.dependency_overrides[get_cortex_model] = lambda: _scripted_answer("nope")
    # Forge the `own` claim — the DB check must still reject it.
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        post = await client.post("/owner/cortex/turns", json={"message": "hola"}, headers=headers)
        assert post.status_code == 403, post.text

        convs = await client.get("/owner/cortex/conversations", headers=headers)
        assert convs.status_code == 403, convs.text


# ===========================================================================
# Tarea 8 — list conversations owner-scoped, most-recent first, with preview
# ===========================================================================
@pytest.mark.asyncio
async def test_list_conversations_owner_scoped(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    from api_server.routers.cortex import get_cortex_model

    configured_app.dependency_overrides[get_cortex_model] = lambda: _scripted_answer("ok")
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        first = await client.post(
            "/owner/cortex/turns", json={"message": "primer hilo"}, headers=headers
        )
        assert first.status_code == 200, first.text
        conv1 = first.json()["conversation_id"]

        second = await client.post(
            "/owner/cortex/turns", json={"message": "segundo hilo"}, headers=headers
        )
        assert second.status_code == 200, second.text
        conv2 = second.json()["conversation_id"]
        assert conv1 != conv2

        convs = await client.get("/owner/cortex/conversations", headers=headers)
        assert convs.status_code == 200, convs.text
        body = convs.json()
        ids = [c["id"] for c in body]
        # Most-recent first: the second thread leads.
        assert ids == [conv2, conv1]
        # Preview present (last turn of each thread).
        previews = {c["id"]: c["last_turn_preview"] for c in body}
        assert previews[conv1] is not None
        assert previews[conv2] is not None
