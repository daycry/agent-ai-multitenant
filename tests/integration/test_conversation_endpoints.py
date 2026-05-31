"""Integration tests for /conversations endpoints + /ws/conversation/{id}
(Plan 03 task_03_03).

Covers:
  - REST CRUD against the real DB through the FastAPI app.
  - Cross-tenant isolation enforced by RLS.
  - PUT that flips current_mode posts a system message and a
    mode-changed event.
  - WebSocket tails the per-conversation Redis stream live.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    project_a = uuid4()
    project_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE messages, conversations, projects, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a-conv",
            tenant_b,
            "Tenant B",
            "tenant-b-conv",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-conv",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@conv.test",
            "argon2-placeholder",
            user_b,
            "bob@conv.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
            uuid4(),
            tenant_b,
            user_b,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            project_a,
            tenant_a,
            "Project A",
            project_b,
            tenant_b,
            "Project B",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "project_a": project_a,
        "project_b": project_b,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
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


@pytest.fixture()
def ws_client(configured_app, test_redis_url: str) -> Iterator[TestClient]:
    """A separate TestClient for WebSocket tests. The dep override routes
    the WS endpoints to the test Redis DB."""
    from api_server.auth.deps import get_redis
    from redis.asyncio import Redis

    configured_app.dependency_overrides[get_redis] = lambda: Redis.from_url(
        test_redis_url, decode_responses=True
    )
    try:
        yield TestClient(configured_app)
    finally:
        configured_app.dependency_overrides.clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# Tests — REST
# ===========================================================================
@pytest.mark.asyncio
async def test_conversations_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{uuid4()}/conversations")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_conversation_crud_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_a']}/conversations",
            json={"title": "Inventory API planning"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["title"] == "Inventory API planning"
        # default mode is 'planning'
        assert body["current_mode"] == "planning"
        assert body["custom_mode_name"] is None
        assert body["project_id"] == str(seeded["project_a"])
        conv_id = body["id"]

        # List for the project
        listed = await client.get(f"/projects/{seeded['project_a']}/conversations", headers=headers)
        assert listed.status_code == 200
        assert {c["id"] for c in listed.json()} == {conv_id}

        # Update title
        upd = await client.put(
            f"/conversations/{conv_id}",
            json={"title": "Inventory API - planning v2"},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["title"] == "Inventory API - planning v2"

        # Soft-delete
        dele = await client.delete(f"/conversations/{conv_id}", headers=headers)
        assert dele.status_code == 204
        gone = await client.get(f"/conversations/{conv_id}", headers=headers)
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_conversation_cross_tenant_isolation(configured_app, migrations_pg_dsn: str) -> None:
    """B cannot see A's conversation, nor list A's project's conversations."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_a']}/conversations",
            json={"title": "secret"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert create.status_code == 201
        conv_id = create.json()["id"]

        # B asking for A's conv -> 404 (RLS hides it; we don't leak existence)
        b_get = await client.get(
            f"/conversations/{conv_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_get.status_code == 404

        # B listing A's project -> 404 (A's project is invisible to B)
        b_list = await client.get(
            f"/projects/{seeded['project_a']}/conversations",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_list.status_code == 404


@pytest.mark.asyncio
async def test_post_message_persists_with_active_mode(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_a']}/conversations",
            json={"title": "Chat"},
            headers=headers,
        )
        conv_id = create.json()["id"]

        post = await client.post(
            f"/conversations/{conv_id}/messages",
            json={
                "author_kind": "user",
                "content": "Hola equipo, ¿cómo arrancamos?",
                "attachments": [{"kind": "file", "ref": "minio://x/y.pdf"}],
            },
            headers=headers,
        )
        assert post.status_code == 201, post.text
        msg = post.json()
        assert msg["author_kind"] == "user"
        assert msg["author_user_id"] == str(seeded["user_a"])
        assert msg["mode"] == "planning"  # taken from conversation.current_mode
        assert msg["attachments"][0]["kind"] == "file"

        listed = await client.get(f"/conversations/{conv_id}/messages", headers=headers)
        assert listed.status_code == 200
        body = listed.json()
        assert len(body) == 1
        assert body[0]["content"] == "Hola equipo, ¿cómo arrancamos?"


@pytest.mark.asyncio
async def test_mode_change_emits_system_message(configured_app, migrations_pg_dsn: str) -> None:
    """A PUT that flips current_mode posts a system banner message."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_a']}/conversations",
            json={"title": "Chat"},
            headers=headers,
        )
        conv_id = create.json()["id"]

        upd = await client.put(
            f"/conversations/{conv_id}",
            json={"current_mode": "discussion"},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["current_mode"] == "discussion"

        listed = await client.get(f"/conversations/{conv_id}/messages", headers=headers)
        body = listed.json()
        # Exactly one system message exists.
        system_msgs = [m for m in body if m["author_kind"] == "system"]
        assert len(system_msgs) == 1
        assert "planning" in system_msgs[0]["content"]
        assert "discussion" in system_msgs[0]["content"]
        assert system_msgs[0]["mode"] == "discussion"


@pytest.mark.asyncio
async def test_message_author_kind_invariant_returns_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_a']}/conversations",
            json={"title": "Chat"},
            headers=headers,
        )
        conv_id = create.json()["id"]

        # author_kind=agent without author_agent_id -> 422 from Pydantic
        bad = await client.post(
            f"/conversations/{conv_id}/messages",
            json={"author_kind": "agent", "content": "hello"},
            headers=headers,
        )
        assert bad.status_code == 422


# ===========================================================================
# Tests — WebSocket
# ===========================================================================
def test_ws_conversation_streams_new_messages(
    ws_client: TestClient, configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    """A message published onto the per-conversation Redis stream lands
    on a connected WebSocket — for a member of the conversation's tenant.

    We pre-seed the stream with a raw Redis publish (instead of POSTing
    through the REST endpoint) because Starlette's sync TestClient and
    async httpx in the same test cause a portal/event-loop race on
    Windows. The publish path used by the REST endpoint is exercised
    end-to-end in the REST tests above; here we focus on the WS pump +
    tenant authorization (Plan 06.14 task_06_14_01): the socket now
    resolves the conversation under RLS, so it must exist in the
    caller's tenant and be backed by a live session.
    """
    from api_server.events import EVENT_MESSAGE_CREATED, publish_conversation_event
    from redis.asyncio import Redis

    seeded = asyncio.run(_seed(migrations_pg_dsn))
    conv_id = uuid4()

    async def _prep() -> str:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "INSERT INTO conversations (id, tenant_id, project_id, title)"
                " VALUES ($1, $2, $3, $4)",
                conv_id,
                seeded["tenant_a"],
                seeded["project_a"],
                "Chat",
            )
        finally:
            await conn.close()
        token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
        redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
        try:
            await redis.delete(f"conv:{conv_id}")
            await publish_conversation_event(
                redis,
                str(conv_id),
                event_type=EVENT_MESSAGE_CREATED,
                payload={
                    "message_id": str(uuid4()),
                    "author_kind": "user",
                    "content": "live hello",
                    "mode": "planning",
                    "attachments": [],
                    "is_summary": False,
                },
            )
        finally:
            await redis.aclose()
        return token

    token = asyncio.run(_prep())
    with ws_client.websocket_connect(f"/ws/conversation/{conv_id}?token={token}") as ws:
        event = ws.receive_json()

    assert event["type"] == "message.created"
    assert event["payload"]["content"] == "live hello"
    assert event["payload"]["author_kind"] == "user"


def test_ws_conversation_rejects_invalid_token(ws_client: TestClient) -> None:
    conv_id = str(uuid4())
    with (
        ws_client.websocket_connect(f"/ws/conversation/{conv_id}?token=not-a-jwt") as ws,
        pytest.raises(WebSocketDisconnect) as exc_info,
    ):
        ws.receive_json()
    assert exc_info.value.code == 1008
