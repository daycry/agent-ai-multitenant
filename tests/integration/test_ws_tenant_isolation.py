"""Cross-tenant isolation of the real-time WebSocket endpoints
(Plan 06.14 task_06_14_01).

Regression for the audit finding (ws-sse-realtime-1/2/3/5, auth-rbac-casbin-3):
the four `/ws/*` endpoints used to accept ANY valid JWT and stream a
resource identified only by its UUID, so a member of tenant A could tail
tenant B's executions, kanban, conversations and document-ingestion
streams by guessing an id. The fix authorises the resource under RLS and
validates the server-side session before streaming.

These tests drive the FastAPI app through Starlette's TestClient against
a real Postgres (RLS-enforced app_user role) + Redis (test DB 15):
  - a member of another tenant is rejected with close code 1008,
  - the owning tenant streams normally (no regression),
  - a revoked / missing / invalid session is rejected.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


# ---------------------------------------------------------------------------
# Seed: two tenants, each with a user; tenant A owns one of every streamable
# resource (execution, project, conversation, document).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "user_a": uuid4(),
        "user_b": uuid4(),
        "project_a": uuid4(),
        "project_b": uuid4(),
        "task_a": uuid4(),
        "execution_a": uuid4(),
        "conversation_a": uuid4(),
        "kb_a": uuid4(),
        "document_a": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE documents, knowledge_bases, executions, tasks, conversations,"
            " projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-ws",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-ws",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["user_a"],
            "alice@ws.test",
            "argon2-placeholder",
            ids["user_b"],
            "bob@ws.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,$4),($5,$6,$7,$8)",
            uuid4(),
            ids["tenant_a"],
            ids["user_a"],
            "tenant_admin",
            uuid4(),
            ids["tenant_b"],
            ids["user_b"],
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["project_a"],
            ids["tenant_a"],
            "Project A",
            ids["project_b"],
            ids["tenant_b"],
            "Project B",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1,$2,$3,$4)",
            ids["task_a"],
            ids["tenant_a"],
            ids["project_a"],
            "Task A",
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id) VALUES ($1,$2,$3)",
            ids["execution_a"],
            ids["tenant_a"],
            ids["task_a"],
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, project_id, title) VALUES ($1,$2,$3,$4)",
            ids["conversation_a"],
            ids["tenant_a"],
            ids["project_a"],
            "Conv A",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1,$2,$3)",
            ids["kb_a"],
            ids["tenant_a"],
            "KB A",
        )
        await conn.execute(
            "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
            " source_mime_type, source_storage_key) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            ids["document_a"],
            ids["tenant_a"],
            ids["kb_a"],
            "Doc A",
            "a.pdf",
            "application/pdf",
            "kb/a/a/a/a.pdf",
        )
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# Fixtures (mirror test_conversation_endpoints.py)
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
    from api_server.auth.deps import get_redis
    from redis.asyncio import Redis

    configured_app.dependency_overrides[get_redis] = lambda: Redis.from_url(
        test_redis_url, decode_responses=True
    )
    try:
        yield TestClient(configured_app)
    finally:
        configured_app.dependency_overrides.clear()


async def _mint_token(user_id: UUID, tenant_id: UUID | None) -> UUID:
    """Create a live Redis session and return its sid (the JWT is built
    from it). Returns the session id so a test can revoke it.

    Uses a fresh Redis client (not the cached ``get_redis()``): the
    singleton binds to the first event loop it runs in, and reusing it
    across ``asyncio.run()`` boundaries blows up the Windows proactor
    loop with "Event loop is closed".
    """
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis

    from tests.integration.conftest import TEST_REDIS_URL

    sid = uuid7()
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await SessionStore(redis).create(
            sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    return sid


async def _revoke_session(sid: UUID) -> None:
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis

    from tests.integration.conftest import TEST_REDIS_URL

    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await SessionStore(redis).revoke(sid)
    finally:
        await redis.aclose()


def _token(
    user_id: UUID, tenant_id: UUID | None, sid: UUID, *, is_system_admin: bool = False
) -> str:
    from api_server.auth.jwt import encode_jwt

    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_admin=is_system_admin
    )


def _mint(user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False) -> str:
    sid = asyncio.run(_mint_token(user_id, tenant_id))
    return _token(user_id, tenant_id, sid, is_system_admin=is_system_admin)


async def _publish(redis_url: str, stream: str, fields: dict[str, str]) -> None:
    from redis.asyncio import Redis

    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await redis.delete(stream)
        await redis.xadd(stream, fields)
    finally:
        await redis.aclose()


def _expect_1008(ws_client: TestClient, url: str) -> None:
    with (
        ws_client.websocket_connect(url) as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_json()
    assert exc.value.code == 1008


# ===========================================================================
# Cross-tenant denial — the core security property
# ===========================================================================
def test_ws_execution_rejects_cross_tenant(ws_client, migrations_pg_dsn: str) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token_b = _mint(ids["user_b"], ids["tenant_b"])
    _expect_1008(ws_client, f"/ws/executions/{ids['execution_a']}?token={token_b}")


def test_ws_kanban_rejects_cross_tenant(ws_client, migrations_pg_dsn: str) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token_b = _mint(ids["user_b"], ids["tenant_b"])
    _expect_1008(ws_client, f"/ws/kanban/{ids['project_a']}?token={token_b}")


def test_ws_conversation_rejects_cross_tenant(ws_client, migrations_pg_dsn: str) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token_b = _mint(ids["user_b"], ids["tenant_b"])
    _expect_1008(ws_client, f"/ws/conversation/{ids['conversation_a']}?token={token_b}")


def test_ws_document_rejects_cross_tenant(ws_client, migrations_pg_dsn: str) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token_b = _mint(ids["user_b"], ids["tenant_b"])
    _expect_1008(ws_client, f"/ws/documents/{ids['document_a']}?token={token_b}")


# ===========================================================================
# Owner happy path — no regression
# ===========================================================================
def test_ws_execution_allows_owner(ws_client, migrations_pg_dsn: str, test_redis_url: str) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token_a = _mint(ids["user_a"], ids["tenant_a"])
    asyncio.run(
        _publish(
            test_redis_url,
            f"exec:{ids['execution_a']}",
            {
                "type": "step.started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps({"n": 1}),
            },
        )
    )
    with ws_client.websocket_connect(f"/ws/executions/{ids['execution_a']}?token={token_a}") as ws:
        event = ws.receive_json()
    assert event["type"] == "step.started"
    assert event["payload"] == {"n": 1}


def test_ws_kanban_allows_owner_and_filters_other_tenant(
    ws_client, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token_a = _mint(ids["user_a"], ids["tenant_a"])

    async def _seed_stream() -> None:
        from redis.asyncio import Redis

        redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
        try:
            await redis.delete("events:tasks")
            # right project + right tenant -> delivered
            await redis.xadd(
                "events:tasks",
                {
                    "type": "task.status_changed",
                    "tenant_id": str(ids["tenant_a"]),
                    "project_id": str(ids["project_a"]),
                    "task_id": str(uuid4()),
                    "payload": json.dumps({"new_status": "in_progress"}),
                },
            )
            # SAME project id but a foreign tenant_id -> must be filtered out
            await redis.xadd(
                "events:tasks",
                {
                    "type": "task.status_changed",
                    "tenant_id": str(uuid4()),
                    "project_id": str(ids["project_a"]),
                    "task_id": str(uuid4()),
                    "payload": json.dumps({"new_status": "leaked"}),
                },
            )
            # right project + right tenant again -> delivered
            await redis.xadd(
                "events:tasks",
                {
                    "type": "task.status_changed",
                    "tenant_id": str(ids["tenant_a"]),
                    "project_id": str(ids["project_a"]),
                    "task_id": str(uuid4()),
                    "payload": json.dumps({"new_status": "done"}),
                },
            )
        finally:
            await redis.aclose()

    asyncio.run(_seed_stream())
    with ws_client.websocket_connect(f"/ws/kanban/{ids['project_a']}?token={token_a}") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    # The foreign-tenant event for the same project_id was dropped.
    assert first["payload"]["new_status"] == "in_progress"
    assert second["payload"]["new_status"] == "done"


def test_ws_conversation_allows_owner(
    ws_client, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token_a = _mint(ids["user_a"], ids["tenant_a"])
    asyncio.run(
        _publish(
            test_redis_url,
            f"conv:{ids['conversation_a']}",
            {
                "type": "message.created",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps({"content": "hola"}),
            },
        )
    )
    with ws_client.websocket_connect(
        f"/ws/conversation/{ids['conversation_a']}?token={token_a}"
    ) as ws:
        event = ws.receive_json()
    assert event["type"] == "message.created"
    assert event["payload"]["content"] == "hola"


# ===========================================================================
# Session / token gate
# ===========================================================================
def test_ws_rejects_revoked_session(ws_client, migrations_pg_dsn: str) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    sid = asyncio.run(_mint_token(ids["user_a"], ids["tenant_a"]))
    token = _token(ids["user_a"], ids["tenant_a"], sid)

    asyncio.run(_revoke_session(sid))
    # Session gone from Redis -> even a signature-valid JWT is rejected.
    _expect_1008(ws_client, f"/ws/executions/{ids['execution_a']}?token={token}")


def test_ws_rejects_missing_token(ws_client) -> None:
    _expect_1008(ws_client, f"/ws/executions/{uuid4()}")


def test_ws_rejects_invalid_token(ws_client) -> None:
    _expect_1008(ws_client, f"/ws/kanban/{uuid4()}?token=not-a-jwt")


# ===========================================================================
# System-admin acting-as-tenant override (?tenant_id=) — the WS mirror of the
# REST X-Tenant-Id header. Regression for "live WS silent under a cross-tenant
# admin view": the browser WebSocket API can't send headers, so a superadmin
# viewing a tenant that isn't their JWT `tid` had every stream rejected and the
# client reconnected forever. The query param lets an admin — and ONLY an
# admin — tail the streams of the tenant they are acting as.
# ===========================================================================
def test_ws_execution_allows_sysadmin_acting_as_tenant(
    ws_client, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    # Superadmin whose JWT home tenant is B, acting as tenant A via ?tenant_id=.
    token = _mint(ids["user_b"], ids["tenant_b"], is_system_admin=True)
    asyncio.run(
        _publish(
            test_redis_url,
            f"exec:{ids['execution_a']}",
            {
                "type": "step.started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps({"n": 1}),
            },
        )
    )
    url = f"/ws/executions/{ids['execution_a']}?token={token}&tenant_id={ids['tenant_a']}"
    with ws_client.websocket_connect(url) as ws:
        event = ws.receive_json()
    assert event["type"] == "step.started"
    assert event["payload"] == {"n": 1}


def test_ws_conversation_allows_sysadmin_acting_as_tenant(
    ws_client, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    token = _mint(ids["user_b"], ids["tenant_b"], is_system_admin=True)
    asyncio.run(
        _publish(
            test_redis_url,
            f"conv:{ids['conversation_a']}",
            {
                "type": "message.created",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": json.dumps({"content": "hola"}),
            },
        )
    )
    url = f"/ws/conversation/{ids['conversation_a']}?token={token}&tenant_id={ids['tenant_a']}"
    with ws_client.websocket_connect(url) as ws:
        event = ws.receive_json()
    assert event["type"] == "message.created"
    assert event["payload"]["content"] == "hola"


def test_ws_non_admin_query_tenant_does_not_bypass(ws_client, migrations_pg_dsn: str) -> None:
    ids = asyncio.run(_seed(migrations_pg_dsn))
    # A NON-admin from tenant B tries to reach tenant A's stream via the query
    # param — it must be ignored (still scoped to tenant B) and rejected.
    token = _mint(ids["user_b"], ids["tenant_b"])  # is_system_admin=False
    _expect_1008(
        ws_client,
        f"/ws/executions/{ids['execution_a']}?token={token}&tenant_id={ids['tenant_a']}",
    )
