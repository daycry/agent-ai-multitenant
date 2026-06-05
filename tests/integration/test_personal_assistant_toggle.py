"""Integration tests for the personal-assistant on/off toggle
(Plan 10 task assistant-enable-toggle).

A Tenant Admin enables/disables the assistant for THEIR tenant via
``GET/PUT /tenant-settings/personal-assistant``. The column behind it is
``Organization.personal_assistant_enabled`` (default false; migration 0047).

Binding constraints under test:

  * Tenant-Admin-only: a ``tenant_user`` / member gets 403 on both verbs.
  * The toggle gate that lives on ``/assistant/*`` is the SAME column, so
    flipping it here changes the real gating: after a Tenant Admin turns it
    ON, ``/assistant/identity`` stops 403'ing with "disabled".
  * Multi-tenant + RLS: tenant B's admin can neither read nor change tenant
    A's toggle, and A's PUT must NEVER affect B (``@pytest.mark.cross_tenant``).

The endpoint is gated ONLY by ``require_tenant_admin`` — never by
``require_assistant_access`` (which requires the toggle ON), or it would be
impossible to ever turn the assistant on (chicken-and-egg).
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: two tenants, both with the toggle OFF (the default).
#   Tenant A: an admin + a member.
#   Tenant B: an admin (the cross-tenant actor).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin_a = uuid4()
    member_a = uuid4()
    admin_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tenant_settings, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled) VALUES"
            " ($1, $2, $3, false), ($4, $5, $6, false), ($7, $8, $9, false)",
            tenant_a,
            "Tenant A",
            "tenant-a-pat",
            tenant_b,
            "Tenant B",
            "tenant-b-pat",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-pat",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            admin_a,
            "admin-a@pat.test",
            "argon2-placeholder",
            member_a,
            "member-a@pat.test",
            "argon2-placeholder",
            admin_b,
            "admin-b@pat.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12)",
            uuid4(),
            tenant_a,
            admin_a,
            "tenant_admin",
            uuid4(),
            tenant_a,
            member_a,
            "tenant_user",
            uuid4(),
            tenant_b,
            admin_b,
            "tenant_admin",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "member_a": member_a,
        "admin_b": admin_b,
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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# Tenant Admin can read + flip the toggle (default OFF)
# ===========================================================================
@pytest.mark.asyncio
async def test_get_returns_false_by_default(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/tenant-settings/personal-assistant", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_tenant_admin_can_enable_and_disable(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Turn ON.
        upd = await client.put(
            "/tenant-settings/personal-assistant",
            json={"enabled": True},
            headers=headers,
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["enabled"] is True

        # Persisted across a fresh GET.
        roundtrip = await client.get("/tenant-settings/personal-assistant", headers=headers)
        assert roundtrip.json()["enabled"] is True

        # Turn OFF again.
        off = await client.put(
            "/tenant-settings/personal-assistant",
            json={"enabled": False},
            headers=headers,
        )
        assert off.status_code == 200, off.text
        assert off.json()["enabled"] is False


@pytest.mark.asyncio
async def test_enabling_unlocks_the_assistant_gate(configured_app, migrations_pg_dsn: str) -> None:
    """The toggle this endpoint writes is the SAME column ``/assistant/*``
    reads. With it OFF, ``/assistant/identity`` 403s "disabled"; after the
    Tenant Admin turns it ON here, the assistant gate opens."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Toggle OFF (default) -> assistant denied with the "disabled" reason.
        denied = await client.get("/assistant/identity", headers=headers)
        assert denied.status_code == 403, denied.text
        assert "disabled" in denied.json()["detail"].lower()

        # Flip it ON via the toggle endpoint.
        upd = await client.put(
            "/tenant-settings/personal-assistant",
            json={"enabled": True},
            headers=headers,
        )
        assert upd.status_code == 200, upd.text

        # Now the assistant gate opens.
        ok = await client.get("/assistant/identity", headers=headers)
        assert ok.status_code == 200, ok.text


# ===========================================================================
# Member is denied (Tenant-Admin-only) on BOTH verbs
# ===========================================================================
@pytest.mark.asyncio
async def test_member_get_is_403(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/tenant-settings/personal-assistant", headers=headers)
    assert resp.status_code == 403, resp.text
    assert "tenant_admin" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_member_put_is_403(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/tenant-settings/personal-assistant",
            json={"enabled": True},
            headers=headers,
        )
    assert resp.status_code == 403, resp.text
    assert "tenant_admin" in resp.json()["detail"]


# ===========================================================================
# Tenant isolation (RLS): B cannot read/change A's toggle; A's PUT ≠ B
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_admin_cannot_affect_tenant_a(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant B's admin only ever sees/writes its OWN org row (RLS). It can
    NEVER read or flip tenant A's toggle, and A's flip is invisible to B."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # A turns its assistant ON.
        upd_a = await client.put(
            "/tenant-settings/personal-assistant",
            json={"enabled": True},
            headers=headers_a,
        )
        assert upd_a.status_code == 200, upd_a.text
        assert upd_a.json()["enabled"] is True

        # B reads its OWN toggle: still OFF — A's change did not leak.
        b_state = await client.get("/tenant-settings/personal-assistant", headers=headers_b)
        assert b_state.status_code == 200, b_state.text
        assert b_state.json()["enabled"] is False

        # B turns its OWN assistant ON.
        upd_b = await client.put(
            "/tenant-settings/personal-assistant",
            json={"enabled": True},
            headers=headers_b,
        )
        assert upd_b.status_code == 200, upd_b.text

        # B then turns its OWN assistant OFF: must NOT touch A.
        off_b = await client.put(
            "/tenant-settings/personal-assistant",
            json={"enabled": False},
            headers=headers_b,
        )
        assert off_b.status_code == 200, off_b.text
        assert off_b.json()["enabled"] is False

    # Assert at the data layer that each tenant kept its own value:
    # A = True, B = False — neither admin reached across the boundary.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        enabled_a = await conn.fetchval(
            "SELECT personal_assistant_enabled FROM organizations WHERE id = $1",
            seeded["tenant_a"],
        )
        enabled_b = await conn.fetchval(
            "SELECT personal_assistant_enabled FROM organizations WHERE id = $1",
            seeded["tenant_b"],
        )
    finally:
        await conn.close()
    assert enabled_a is True
    assert enabled_b is False
