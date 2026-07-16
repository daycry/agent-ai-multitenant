"""AUD16-10 (auditoría 2026-07-16): inbox de PLATAFORMA para el System Admin.

Todos los envíos reales del sistema (infra_alert, cortex_message, credential_*)
son platform-scoped (``tenant_id IS NULL``) y el inbox de tenant los excluye por
diseño — ninguna notificación llegaba jamás a un ojo humano
(``notification_log_reads`` = 0 en toda la historia lo confirmaba). Este suite
fija el contrato del camino nuevo:

  * ``GET /notifications/platform/logs`` — solo System Admin (403 para un
    tenant admin), lista SOLO las filas ``tenant_id IS NULL`` con read-marker
    por usuario.
  * ``POST /notifications/platform/logs/{id}/read`` — marca leída una notif de
    plataforma (idempotente).
  * El inbox de tenant (``GET /notifications/logs``) SIGUE sin incluir filas de
    plataforma (sin cambios de contrato allí).

Mismo harness real (FastAPI app + Postgres con RLS + Redis de test) que
``test_notification_inbox.py``.
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
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed(dsn: str) -> dict[str, object]:
    """Un tenant con su admin + un System Admin global + logs de AMBOS mundos:
    2 filas platform-scoped (tenant NULL) y 1 fila del tenant."""
    ids: dict[str, object] = {
        "tenant_a": uuid4(),
        "tenant_admin": uuid4(),
        "system_admin": uuid4(),
        "channel_platform": uuid4(),
        "channel_a": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE notification_log_reads, notification_logs,"
            " notification_preferences, notification_channels, platform_settings,"
            " audit_log, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-platform-inbox",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, 'h', false), ($3, $4, 'h', true)",
            ids["tenant_admin"],
            "admin-a@pinbox.test",
            ids["system_admin"],
            "root@pinbox.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["tenant_admin"],
        )
        await conn.execute(
            "INSERT INTO notification_channels"
            " (id, scope, channel_type, tenant_id, name, enabled, config)"
            " VALUES ($1, 'platform', 'in_app', NULL, 'Platform inbox', true, '{}'),"
            "        ($2, 'tenant', 'in_app', $3, 'A inbox', true, '{}')",
            ids["channel_platform"],
            ids["channel_a"],
            ids["tenant_a"],
        )
        platform_logs: list[UUID] = []
        for event in ("infra_alert", "cortex_message"):
            lid = uuid4()
            platform_logs.append(lid)
            await conn.execute(
                "INSERT INTO notification_logs"
                " (id, channel_id, tenant_id, event_type, channel_type, status, target, attempt)"
                " VALUES ($1, $2, NULL, $3, 'in_app', 'sent', 'platform-inbox', 1)",
                lid,
                ids["channel_platform"],
                event,
            )
        ids["platform_logs"] = platform_logs
        tenant_log = uuid4()
        await conn.execute(
            "INSERT INTO notification_logs"
            " (id, channel_id, tenant_id, event_type, channel_type, status, target, attempt)"
            " VALUES ($1, $2, $3, 'task_blocked', 'in_app', 'sent', 'inbox-a', 1)",
            tenant_log,
            ids["channel_a"],
            ids["tenant_a"],
        )
        ids["tenant_log"] = tenant_log
    finally:
        await conn.close()
    return ids


async def _mint_token(
    user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False
) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# El System Admin ve las notifs de plataforma (y solo esas) en su inbox.
# ===========================================================================
@pytest.mark.asyncio
async def test_platform_inbox_lists_null_tenant_logs(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["system_admin"], None, is_system_admin=True)  # type: ignore[arg-type]

    async with _client(configured_app) as client:
        resp = await client.get(
            "/notifications/platform/logs",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    listed = {item["id"] for item in body["items"]}
    assert listed == {str(lid) for lid in seeded["platform_logs"]}  # type: ignore[union-attr]
    assert str(seeded["tenant_log"]) not in listed
    assert body["total"] == 2
    assert body["unread"] == 2


@pytest.mark.asyncio
async def test_platform_inbox_is_forbidden_for_tenant_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["tenant_admin"], seeded["tenant_a"])  # type: ignore[arg-type]

    async with _client(configured_app) as client:
        resp = await client.get(
            "/notifications/platform/logs",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_platform_log_mark_read_is_idempotent(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["system_admin"], None, is_system_admin=True)  # type: ignore[arg-type]
    log_id = seeded["platform_logs"][0]  # type: ignore[index]
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        first = await client.post(f"/notifications/platform/logs/{log_id}/read", headers=headers)
        second = await client.post(f"/notifications/platform/logs/{log_id}/read", headers=headers)
        inbox = await client.get("/notifications/platform/logs", headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert inbox.json()["unread"] == 1
    read_flags = {item["id"]: item["read"] for item in inbox.json()["items"]}
    assert read_flags[str(log_id)] is True


@pytest.mark.asyncio
async def test_tenant_inbox_still_excludes_platform_logs(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["tenant_admin"], seeded["tenant_a"])  # type: ignore[arg-type]

    async with _client(configured_app) as client:
        resp = await client.get(
            "/notifications/logs",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    listed = {item["id"] for item in resp.json()["items"]}
    assert listed == {str(seeded["tenant_log"])}
