"""Integration tests for System-Admin user-membership administration (ADR 0047, task_sso_04).

The System Admin manages tenant access EXCLUSIVELY through
``UserOrganizationMembership`` rows (no email-domain claiming, no
auto-create — ADR 0047). These endpoints, all under ``/admin``, are
``require_system_admin``-gated:

  * GET    /admin/users/{user_id}/memberships          — list a user's tenants+roles
  * POST   /admin/users/{user_id}/memberships          — assign tenant + role (201)
  * PATCH  /admin/users/{user_id}/memberships/{id}      — set role / activate-deactivate
  * DELETE /admin/users/{user_id}/memberships/{id}      — revoke (soft-delete)

Coverage:

  * assign → list reflects it; role change + deactivate persist; revoke
    removes it from the listing.
  * RBAC: a non-admin user gets 403 on every membership endpoint;
    unauthenticated gets 401.
  * After an admin assigns a membership, the target user's post-login
    resolution (``GET /auth/session/resolve``) actually reaches that
    tenant (was "no_access" before, "single" after).
  * @pytest.mark.cross_tenant: a deactivated/revoked membership grants no
    access; a user only ever resolves a tenant they have an ACTIVE
    membership in.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixture creates a throwaway DB and flushes Redis 15.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture — same shape as test_admin_rbac.configured_app.
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
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_COUNT", "100")
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")

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
# Helpers
# ---------------------------------------------------------------------------
async def _promote_to_system_admin(dsn: str, email: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET is_system_admin = true WHERE email = $1", email)
    finally:
        await conn.close()


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _register(client: AsyncClient, email: str, password: str = "longenoughpw") -> str:
    """Register a user and return their user id."""
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def _register_and_login_admin(
    client: AsyncClient,
    migrations_dsn: str,
    email: str | None = None,
    password: str = "longenoughpw",
) -> str:
    # Unique admin per call: integration tests share one session DB (the
    # fixture migrates, does not recreate), so a fixed "root@example.com"
    # collides with other suites that create it. uuid-suffix keeps isolation.
    if email is None:
        email = f"admin-{uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": password})
    await _promote_to_system_admin(migrations_dsn, email)
    return await _login(client, email, password)


async def _create_tenant(client: AsyncClient, token: str, *, name: str, slug: str) -> str:
    resp = await client.post(
        "/admin/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "slug": slug},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Happy path — assign / list / update / revoke persists
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assign_list_update_revoke_membership(configured_app, migrations_pg_dsn: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        admin_token = await _register_and_login_admin(client, migrations_pg_dsn)
        u = uuid4().hex[:8]
        user_id = await _register(client, f"worker-{u}@example.com")
        tenant_id = await _create_tenant(client, admin_token, name=f"Acme {u}", slug=f"acme-{u}")

        # Initially no memberships.
        listed = await client.get(f"/admin/users/{user_id}/memberships", headers=_auth(admin_token))
        assert listed.status_code == 200, listed.text
        assert listed.json() == []

        # Assign.
        assigned = await client.post(
            f"/admin/users/{user_id}/memberships",
            headers=_auth(admin_token),
            json={"tenant_id": tenant_id, "role": "tenant_user"},
        )
        assert assigned.status_code == 201, assigned.text
        body = assigned.json()
        membership_id = body["id"]
        assert body["tenant_id"] == tenant_id
        assert body["tenant_name"] == f"Acme {u}"
        assert body["tenant_slug"] == f"acme-{u}"
        assert body["role"] == "tenant_user"
        assert body["is_active"] is True
        UUID(membership_id)  # parses

        # List reflects it.
        listed = await client.get(f"/admin/users/{user_id}/memberships", headers=_auth(admin_token))
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["id"] == membership_id

        # Update role + deactivate.
        updated = await client.patch(
            f"/admin/users/{user_id}/memberships/{membership_id}",
            headers=_auth(admin_token),
            json={"role": "tenant_admin", "is_active": False},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["role"] == "tenant_admin"
        assert updated.json()["is_active"] is False

        # Re-activate via PATCH (is_active only).
        reactivated = await client.patch(
            f"/admin/users/{user_id}/memberships/{membership_id}",
            headers=_auth(admin_token),
            json={"is_active": True},
        )
        assert reactivated.status_code == 200, reactivated.text
        assert reactivated.json()["is_active"] is True

        # Revoke (soft-delete).
        revoked = await client.delete(
            f"/admin/users/{user_id}/memberships/{membership_id}",
            headers=_auth(admin_token),
        )
        assert revoked.status_code == 204, revoked.text

        listed = await client.get(f"/admin/users/{user_id}/memberships", headers=_auth(admin_token))
        assert listed.json() == []


@pytest.mark.asyncio
async def test_duplicate_assign_conflicts_then_reassign_revives(
    configured_app, migrations_pg_dsn: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        admin_token = await _register_and_login_admin(client, migrations_pg_dsn)
        u = uuid4().hex[:8]
        user_id = await _register(client, f"dup-{u}@example.com")
        tenant_id = await _create_tenant(client, admin_token, name=f"Beta {u}", slug=f"beta-{u}")

        first = await client.post(
            f"/admin/users/{user_id}/memberships",
            headers=_auth(admin_token),
            json={"tenant_id": tenant_id, "role": "tenant_user"},
        )
        assert first.status_code == 201

        # Second assign while active → 409.
        dup = await client.post(
            f"/admin/users/{user_id}/memberships",
            headers=_auth(admin_token),
            json={"tenant_id": tenant_id, "role": "tenant_admin"},
        )
        assert dup.status_code == 409, dup.text

        # Revoke, then re-assign → revives the row (no unique-constraint blow-up).
        membership_id = first.json()["id"]
        await client.delete(
            f"/admin/users/{user_id}/memberships/{membership_id}",
            headers=_auth(admin_token),
        )
        revived = await client.post(
            f"/admin/users/{user_id}/memberships",
            headers=_auth(admin_token),
            json={"tenant_id": tenant_id, "role": "tenant_admin"},
        )
        assert revived.status_code == 201, revived.text
        assert revived.json()["role"] == "tenant_admin"
        assert revived.json()["is_active"] is True


# ---------------------------------------------------------------------------
# RBAC — system_admin required
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_membership_endpoints_require_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        admin_token = await _register_and_login_admin(client, migrations_pg_dsn)
        u = uuid4().hex[:8]
        target_id = await _register(client, f"target-{u}@example.com")
        tenant_id = await _create_tenant(client, admin_token, name=f"Gamma {u}", slug=f"gamma-{u}")
        assigned = await client.post(
            f"/admin/users/{target_id}/memberships",
            headers=_auth(admin_token),
            json={"tenant_id": tenant_id, "role": "tenant_user"},
        )
        membership_id = assigned.json()["id"]

        # A regular (non-admin) user.
        await client.post(
            "/auth/register", json={"email": f"evil-{u}@example.com", "password": "longenoughpw"}
        )
        user_token = await _login(client, f"evil-{u}@example.com", "longenoughpw")
        h = _auth(user_token)

        assert (
            await client.get(f"/admin/users/{target_id}/memberships", headers=h)
        ).status_code == 403
        assert (
            await client.post(
                f"/admin/users/{target_id}/memberships",
                headers=h,
                json={"tenant_id": tenant_id, "role": "tenant_user"},
            )
        ).status_code == 403
        assert (
            await client.patch(
                f"/admin/users/{target_id}/memberships/{membership_id}",
                headers=h,
                json={"is_active": False},
            )
        ).status_code == 403
        assert (
            await client.delete(f"/admin/users/{target_id}/memberships/{membership_id}", headers=h)
        ).status_code == 403


@pytest.mark.asyncio
async def test_membership_endpoints_unauthenticated_is_401(
    configured_app, migrations_pg_dsn: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        admin_token = await _register_and_login_admin(client, migrations_pg_dsn)
        u = uuid4().hex[:8]
        target_id = await _register(client, f"anon-target-{u}@example.com")
        tenant_id = await _create_tenant(client, admin_token, name=f"Delta {u}", slug=f"delta-{u}")

        resp = await client.post(
            f"/admin/users/{target_id}/memberships",
            json={"tenant_id": tenant_id, "role": "tenant_user"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# End-to-end: a new membership lets the user reach that tenant
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assigned_membership_lets_user_reach_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        admin_token = await _register_and_login_admin(client, migrations_pg_dsn)
        u = uuid4().hex[:8]
        await client.post(
            "/auth/register", json={"email": f"newhire-{u}@example.com", "password": "longenoughpw"}
        )
        user_token = await _login(client, f"newhire-{u}@example.com", "longenoughpw")
        user_id = (await client.get("/auth/me", headers=_auth(user_token))).json()["id"]
        tenant_id = await _create_tenant(
            client, admin_token, name=f"Epsilon {u}", slug=f"epsilon-{u}"
        )

        # Before assignment: no access.
        before = await client.get("/auth/session/resolve", headers=_auth(user_token))
        assert before.status_code == 200, before.text
        assert before.json()["state"] == "no_access"

        # Admin assigns a membership.
        assigned = await client.post(
            f"/admin/users/{user_id}/memberships",
            headers=_auth(admin_token),
            json={"tenant_id": tenant_id, "role": "tenant_user"},
        )
        assert assigned.status_code == 201, assigned.text

        # After assignment: the user resolves directly into that tenant.
        after = await client.get("/auth/session/resolve", headers=_auth(user_token))
        assert after.status_code == 200, after.text
        body = after.json()
        assert body["state"] == "single"
        assert body["memberships"][0]["tenant_id"] == tenant_id
        # The single-state mint gives the user a tenant-scoped token.
        assert body["access_token"] is not None


# ---------------------------------------------------------------------------
# Cross-tenant: deactivated/revoked grants no access; no membership = none
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_revoked_or_inactive_membership_denies_tenant_access(
    configured_app, migrations_pg_dsn: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        admin_token = await _register_and_login_admin(client, migrations_pg_dsn)
        u = uuid4().hex[:8]
        await client.post(
            "/auth/register",
            json={"email": f"boundary-{u}@example.com", "password": "longenoughpw"},
        )
        user_token = await _login(client, f"boundary-{u}@example.com", "longenoughpw")
        user_id = (await client.get("/auth/me", headers=_auth(user_token))).json()["id"]

        tenant_a = await _create_tenant(
            client, admin_token, name=f"TenantA {u}", slug=f"tenant-a-{u}"
        )
        tenant_b = await _create_tenant(
            client, admin_token, name=f"TenantB {u}", slug=f"tenant-b-{u}"
        )

        # Membership in A only.
        assigned = await client.post(
            f"/admin/users/{user_id}/memberships",
            headers=_auth(admin_token),
            json={"tenant_id": tenant_a, "role": "tenant_user"},
        )
        membership_id = assigned.json()["id"]

        # The user can resolve A, never B.
        resolved = await client.get("/auth/session/resolve", headers=_auth(user_token))
        assert resolved.json()["state"] == "single"
        assert resolved.json()["memberships"][0]["tenant_id"] == tenant_a
        # Selecting B (no membership) must be denied.
        select_b = await client.post(
            "/auth/session/select-tenant",
            headers=_auth(user_token),
            json={"tenant_id": tenant_b},
        )
        assert select_b.status_code == 403, select_b.text

        # Deactivate the A membership → no access at all.
        await client.patch(
            f"/admin/users/{user_id}/memberships/{membership_id}",
            headers=_auth(admin_token),
            json={"is_active": False},
        )
        after_deactivate = await client.get("/auth/session/resolve", headers=_auth(user_token))
        assert after_deactivate.json()["state"] == "no_access"

        # Re-activate then revoke → still no access.
        await client.patch(
            f"/admin/users/{user_id}/memberships/{membership_id}",
            headers=_auth(admin_token),
            json={"is_active": True},
        )
        await client.delete(
            f"/admin/users/{user_id}/memberships/{membership_id}",
            headers=_auth(admin_token),
        )
        after_revoke = await client.get("/auth/session/resolve", headers=_auth(user_token))
        assert after_revoke.json()["state"] == "no_access"

        # The foreign tenant_b was always distinct and never reachable.
        assert tenant_a != tenant_b
