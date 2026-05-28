"""Tests for the tenant role helpers in `api_server.auth.deps`.

Plan 06.8 task_06_8_01. Three FastAPI dependencies are added:

  * `require_tenant_member`   any active membership in the JWT tenant.
  * `require_tenant_admin`    `tenant_admin` role in the JWT tenant.
  * `require_tenant_role(r)`  parametric factory.

System admins always pass each gate regardless of membership. The
matrix tested here is:

  caller × gate × expected_status

  caller ∈ {tenant_admin, tenant_user, system_admin, no-tid, no-membership}
  gate   ∈ {member, admin, role(TENANT_USER)}

The tests mount a tiny FastAPI app (NOT the full api-server) that wires
each helper to a probe endpoint and exercises them with real JWTs +
Redis sessions + DB membership rows.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test FastAPI app: mounts one probe per helper.
# ---------------------------------------------------------------------------
def _build_probe_app() -> FastAPI:
    """A throwaway app exposing one endpoint per helper.

    We don't mount on the real api-server because we want to assert on
    the helpers in isolation — without the noise of any other route's
    auth dependencies."""
    from api_server.auth.deps import (
        AuthPrincipal,
        require_tenant_admin,
        require_tenant_member,
        require_tenant_role,
    )
    from api_server.db.models import UserRole

    app = FastAPI()
    # B008: build the parametric guard up-front so it's a stable
    # module-level value rather than a call inside argument defaults.
    role_user_guard = require_tenant_role(UserRole.TENANT_USER)

    @app.get("/probe/member")
    async def probe_member(
        principal: AuthPrincipal = Depends(require_tenant_member),
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    @app.get("/probe/admin")
    async def probe_admin(
        principal: AuthPrincipal = Depends(require_tenant_admin),
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    @app.get("/probe/role-user")
    async def probe_role_user(
        principal: AuthPrincipal = Depends(role_user_guard),
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    return app


# ---------------------------------------------------------------------------
# DB seed: two tenants × roles, plus a "no membership" user.
# ---------------------------------------------------------------------------
async def _seed_db(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    admin_user = uuid4()
    plain_user = uuid4()
    stranger = uuid4()  # registered but with no membership

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            "Acme",
            "acme",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, 'admin@acme.test', 'argon2-placeholder'),"
            " ($2, 'user@acme.test',  'argon2-placeholder'),"
            " ($3, 'stranger@acme.test', 'argon2-placeholder')",
            admin_user,
            plain_user,
            stranger,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user')",
            uuid4(),
            tenant,
            admin_user,
            uuid4(),
            tenant,
            plain_user,
        )
    finally:
        await conn.close()

    return {
        "tenant": tenant,
        "admin_user": admin_user,
        "plain_user": plain_user,
        "stranger": stranger,
    }


async def _promote_to_system_admin(dsn: str, user_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET is_system_admin = true WHERE id = $1", user_id)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Fixture: spin up DB + redis + minted JWTs for each test.
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

    yield _build_probe_app()

    reset_engine_cache()
    reset_redis_cache()
    get_settings.cache_clear()


async def _mint(user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False) -> str:
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


async def _get(client: AsyncClient, path: str, token: str) -> int:
    resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    return resp.status_code


# ---------------------------------------------------------------------------
# tenant_admin passes every gate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_admin_passes_all_gates(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        assert await _get(client, "/probe/member", token) == 200
        assert await _get(client, "/probe/admin", token) == 200
        # tenant_admin doesn't match role(TENANT_USER) → 403
        assert await _get(client, "/probe/role-user", token) == 403


# ---------------------------------------------------------------------------
# tenant_user passes member + role(TENANT_USER), fails admin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_user_passes_member_and_role_user_only(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        assert await _get(client, "/probe/member", token) == 200
        assert await _get(client, "/probe/admin", token) == 403
        assert await _get(client, "/probe/role-user", token) == 200


# ---------------------------------------------------------------------------
# system_admin always passes (regardless of membership)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_system_admin_bypasses_all_gates(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    # Stranger has no membership in `tenant`, but promote to system admin.
    await _promote_to_system_admin(migrations_pg_dsn, seed["stranger"])
    # Mint a token with no tid — system admins don't need one to pass.
    token = await _mint(seed["stranger"], None, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        assert await _get(client, "/probe/member", token) == 200
        assert await _get(client, "/probe/admin", token) == 200
        assert await _get(client, "/probe/role-user", token) == 200


# ---------------------------------------------------------------------------
# no-membership user gets 403 on all gates
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stranger_with_tid_but_no_membership_is_403(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    # Stranger carries the tenant's id in their JWT but has no row in
    # user_org_memberships for that tenant.
    token = await _mint(seed["stranger"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        assert await _get(client, "/probe/member", token) == 403
        assert await _get(client, "/probe/admin", token) == 403
        assert await _get(client, "/probe/role-user", token) == 403


# ---------------------------------------------------------------------------
# no-tid (logged-in but pre-tenant-pick) is 403 unless system_admin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_tid_is_403_for_regular_user(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    # admin_user without a tid claim — fresh login, hasn't picked tenant.
    token = await _mint(seed["admin_user"], None)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        assert await _get(client, "/probe/member", token) == 403
        assert await _get(client, "/probe/admin", token) == 403
        assert await _get(client, "/probe/role-user", token) == 403


# ---------------------------------------------------------------------------
# Missing bearer → 401 from get_principal, not 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        for path in ("/probe/member", "/probe/admin", "/probe/role-user"):
            resp = await client.get(path)
            assert resp.status_code == 401, path
