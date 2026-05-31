"""Integration tests for the 3-layer notification-config endpoints (task_10_15).

The config UI's backend surface, exercised through the real FastAPI app + the
real test Postgres so the RLS / RBAC boundary is the one under test:

  * **Platform layer** — ``GET /notifications/platform/channel-types`` is
    readable by any tenant member; ``PUT`` is System-Admin-only (a Tenant
    Admin is 403). The stored set narrows what a tenant may configure.

  * **Channels (tenant/user scope)** — a ``tenant_admin`` can create / list /
    update / delete channels; a plain ``tenant_user`` cannot mutate (403).
    The channel SECRET is NEVER echoed: the response only carries
    ``has_secret`` + ``secret_source``, and the clear value never lands in
    the DB row's ``config`` (it is Fernet-encrypted into ``secret_encrypted``).
    A channel whose transport is not platform-enabled is rejected (409).

  * **Preferences (tenant/user scope)** — upsert a routing rule (the
    human_10_02 "mute budget_alert on Slack, keep it on email" primitive),
    idempotent on its natural key; a ``tenant_user`` cannot mutate (403).

  * **Cross-tenant isolation** (``@pytest.mark.cross_tenant``) — tenant B
    cannot see, update, or delete tenant A's channel: RLS makes it a clean
    404 (no leak), and the listing never returns another tenant's rows.

The app runs through the RLS-bound ``app_user`` engine (NOBYPASSRLS) so the
tenant-isolation boundary is real. No LLM, no broker, no real network.
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


# ---------------------------------------------------------------------------
# App under test (RLS-bound app_user engine).
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


# ---------------------------------------------------------------------------
# Seeding: two tenants, an admin + a member in A, an admin in B + System Admin.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "sysadmin": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE notification_logs, notification_preferences,"
            " notification_channels, platform_settings, audit_log,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-nc",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-nc",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, 'h', false), ($3, $4, 'h', false),"
            " ($5, $6, 'h', false), ($7, $8, 'h', true)",
            ids["admin_a"],
            "admin-a@nc.test",
            ids["member_a"],
            "member-a@nc.test",
            ids["admin_b"],
            "admin-b@nc.test",
            ids["sysadmin"],
            "sys@nc.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user'),"
            " ($7, $8, $9, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
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


async def _channel_row(dsn: str, channel_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT config, secret_encrypted, secret_ref, owner_user_id, scope"
            " FROM notification_channels WHERE id = $1",
            channel_id,
        )
    finally:
        await conn.close()


# ===========================================================================
# Platform layer — System Admin sets enabled channel transports.
# ===========================================================================
@pytest.mark.asyncio
async def test_platform_channel_types_default_is_full_catalogue(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    async with _client(configured_app) as client:
        resp = await client.get(
            "/notifications/platform/channel-types",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Unset -> every transport in the closed catalogue is enabled.
    assert set(body["enabled"]) == set(body["available"])
    assert "telegram" in body["available"]


@pytest.mark.asyncio
async def test_platform_channel_types_set_requires_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    async with _client(configured_app) as client:
        # A Tenant Admin cannot set the platform list.
        forbidden = await client.put(
            "/notifications/platform/channel-types",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"enabled": ["telegram", "email"]},
        )
        assert forbidden.status_code == 403

        # The System Admin can.
        ok = await client.put(
            "/notifications/platform/channel-types",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"enabled": ["telegram", "email"]},
        )
        assert ok.status_code == 200, ok.text
        assert set(ok.json()["enabled"]) == {"telegram", "email"}


# ===========================================================================
# Channels — CRUD, secret-never-echoed, RBAC, platform gate.
# ===========================================================================
@pytest.mark.asyncio
async def test_create_channel_never_echoes_secret(configured_app, migrations_pg_dsn: str) -> None:
    """The clear secret is encrypted at rest and never returned nor stored in
    ``config`` — the response carries only has_secret + secret_source."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/notifications/channels",
            headers=headers,
            json={
                "scope": "tenant",
                "channel_type": "telegram",
                "name": "Ops bot",
                "config": {"chat_id": "12345"},
                "secret": "super-secret-bot-token",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # The secret never appears in the response, in any form.
    assert "secret" not in body
    assert "super-secret-bot-token" not in resp.text
    assert body["has_secret"] is True
    assert body["secret_source"] == "encrypted"

    # And it is encrypted at rest — never in clear, never in config.
    row = await _channel_row(migrations_pg_dsn, UUID(body["id"]))
    assert row is not None
    assert row["secret_encrypted"] is not None
    assert "super-secret-bot-token" not in row["secret_encrypted"]
    assert "super-secret-bot-token" not in str(row["config"])


@pytest.mark.asyncio
async def test_create_channel_rejects_secret_in_config(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Putting the clear secret in ``config`` is rejected (validation 422)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    async with _client(configured_app) as client:
        resp = await client.post(
            "/notifications/channels",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "channel_type": "telegram",
                "name": "Bad bot",
                "config": {"chat_id": "1", "token": "leaked-in-config"},
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_channel_requires_tenant_admin(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    async with _client(configured_app) as client:
        resp = await client.post(
            "/notifications/channels",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel_type": "email", "name": "x", "config": {}},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_channel_rejects_platform_disabled_type(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A transport not enabled platform-wide is rejected (409)."""
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    async with _client(configured_app) as client:
        # System Admin enables only telegram.
        await client.put(
            "/notifications/platform/channel-types",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"enabled": ["telegram"]},
        )
        # A tenant cannot configure email (not enabled).
        resp = await client.post(
            "/notifications/channels",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"channel_type": "email", "name": "Mail", "config": {}},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_channel_rotates_secret_and_keeps_when_omitted(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['admin_a'], seeded['tenant_a'])}"
    }

    async with _client(configured_app) as client:
        created = await client.post(
            "/notifications/channels",
            headers=headers,
            json={"channel_type": "slack", "name": "S", "secret": "first-secret"},
        )
        cid = created.json()["id"]
        row1 = await _channel_row(migrations_pg_dsn, UUID(cid))

        # Update name only -> secret kept.
        patched = await client.put(
            f"/notifications/channels/{cid}",
            headers=headers,
            json={"name": "Slack prod"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "Slack prod"
        assert patched.json()["has_secret"] is True
        row2 = await _channel_row(migrations_pg_dsn, UUID(cid))
        assert row2["secret_encrypted"] == row1["secret_encrypted"]

        # Rotate the secret -> ciphertext changes, still not echoed.
        rotated = await client.put(
            f"/notifications/channels/{cid}",
            headers=headers,
            json={"secret": "second-secret"},
        )
        assert rotated.status_code == 200
        assert "second-secret" not in rotated.text
        row3 = await _channel_row(migrations_pg_dsn, UUID(cid))
        assert row3["secret_encrypted"] != row1["secret_encrypted"]


@pytest.mark.asyncio
async def test_user_scoped_channel_is_owned_by_caller(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['admin_a'], seeded['tenant_a'])}"
    }
    async with _client(configured_app) as client:
        resp = await client.post(
            "/notifications/channels",
            headers=headers,
            json={"scope": "user", "channel_type": "telegram", "name": "My DM"},
        )
    assert resp.status_code == 201, resp.text
    row = await _channel_row(migrations_pg_dsn, UUID(resp.json()["id"]))
    assert row["scope"] == "user"
    assert row["owner_user_id"] == seeded["admin_a"]


@pytest.mark.asyncio
async def test_delete_channel_soft_deletes(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['admin_a'], seeded['tenant_a'])}"
    }
    async with _client(configured_app) as client:
        created = await client.post(
            "/notifications/channels",
            headers=headers,
            json={"channel_type": "discord", "name": "D"},
        )
        cid = created.json()["id"]
        deleted = await client.delete(f"/notifications/channels/{cid}", headers=headers)
        assert deleted.status_code == 204
        listing = await client.get("/notifications/channels", headers=headers)
    assert all(c["id"] != cid for c in listing.json())


# ===========================================================================
# Preferences — upsert idempotent, RBAC.
# ===========================================================================
@pytest.mark.asyncio
async def test_preference_upsert_is_idempotent(configured_app, migrations_pg_dsn: str) -> None:
    """The human_10_02 primitive: mute budget_alert on Slack. Upsert keyed on
    the natural key, so a second PUT updates rather than duplicates."""
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['admin_a'], seeded['tenant_a'])}"
    }
    async with _client(configured_app) as client:
        first = await client.put(
            "/notifications/preferences",
            headers=headers,
            json={
                "scope": "user",
                "event_type": "budget_alert",
                "channel_type": "slack",
                "enabled": False,
            },
        )
        assert first.status_code == 200, first.text
        assert first.json()["enabled"] is False

        # Flip it back on — same key, no duplicate.
        second = await client.put(
            "/notifications/preferences",
            headers=headers,
            json={
                "scope": "user",
                "event_type": "budget_alert",
                "channel_type": "slack",
                "enabled": True,
            },
        )
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["enabled"] is True

        listing = await client.get("/notifications/preferences", headers=headers)
    rules = [
        p
        for p in listing.json()
        if p["event_type"] == "budget_alert" and p["channel_type"] == "slack"
    ]
    assert len(rules) == 1


@pytest.mark.asyncio
async def test_preference_upsert_requires_tenant_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['member_a'], seeded['tenant_a'])}"
    }
    async with _client(configured_app) as client:
        resp = await client.put(
            "/notifications/preferences",
            headers=headers,
            json={"event_type": "task_blocked", "channel_type": "email", "enabled": True},
        )
    assert resp.status_code == 403


# ===========================================================================
# Cross-tenant isolation.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_channel_cross_tenant_is_isolated(configured_app, migrations_pg_dsn: str) -> None:
    """Tenant B cannot see, update, or delete tenant A's channel (RLS 404)."""
    seeded = await _seed(migrations_pg_dsn)
    a_headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['admin_a'], seeded['tenant_a'])}"
    }
    b_headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['admin_b'], seeded['tenant_b'])}"
    }
    async with _client(configured_app) as client:
        created = await client.post(
            "/notifications/channels",
            headers=a_headers,
            json={"channel_type": "telegram", "name": "A bot", "secret": "a-token"},
        )
        cid = created.json()["id"]

        # B's listing never shows A's channel.
        b_list = await client.get("/notifications/channels", headers=b_headers)
        assert all(c["id"] != cid for c in b_list.json())

        # B cannot update or delete A's channel — clean 404, no leak.
        b_update = await client.put(
            f"/notifications/channels/{cid}", headers=b_headers, json={"name": "hijack"}
        )
        assert b_update.status_code == 404
        b_delete = await client.delete(f"/notifications/channels/{cid}", headers=b_headers)
        assert b_delete.status_code == 404

    # A's channel row is untouched.
    row = await _channel_row(migrations_pg_dsn, UUID(cid))
    assert row is not None
