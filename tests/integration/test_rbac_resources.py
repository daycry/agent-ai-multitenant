"""Cross-role RBAC tests for tenant-scoped resource endpoints.

Plan 06.8 task_06_8_03 — sanity check that the gates introduced in
auth/deps.py are wired correctly on the most critical mutation endpoints
(the matrix `docs/04-reference/rbac.md` is the full contract).

For each endpoint, exercise with four callers:

  - `tenant_user`    — active membership, role=tenant_user
  - `tenant_admin`   — active membership, role=tenant_admin
  - `system_admin`   — `users.is_system_admin = true`, no membership
  - `stranger`       — registered user, NO membership in tenant

The matrix expectation per caller is hard-coded in `_EXPECTATIONS` —
when adding a new gated endpoint, extend it and the matrix together.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed: a tenant, three users in three roles, plus a stranger user.
# ---------------------------------------------------------------------------
async def _seed_db(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    admin_user = uuid4()
    plain_user = uuid4()
    stranger = uuid4()

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
# Fixture
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


# ---------------------------------------------------------------------------
# Endpoint matrix
# ---------------------------------------------------------------------------
# Each entry: (method, path, json-body or None, expected status when authorised)
#
# A pair of (caller, endpoint) is "authorised" when the matrix
# (rbac.md) says so. The expected_authorised codes are what the
# endpoint returns on a happy path (2xx). On rejection we always expect
# 403 — there are no 401s here (every caller is logged in).
#
# Body payloads are intentionally minimal — for the RBAC test we don't
# care if the create succeeds with full fields; a 4xx from validation
# would also "leak" the gate (the gate runs first), so 403 vs 4xx tells
# us the gate is in place.
_ADMIN_GATED: list[tuple[str, str, dict[str, Any]]] = [
    ("POST", "/projects", {"name": "p", "status": "draft"}),
    (
        "POST",
        "/agents",
        {
            "name": "A",
            "role": "backend_dev",
            "scope": "global_tenant_template",
            "model_provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
        },
    ),
    ("POST", "/teams", {"name": "T"}),
    ("POST", "/knowledge-bases", {"name": "kb", "is_public": False}),
    ("POST", "/skills", {"name": "s", "category": "general"}),
    ("POST", "/tools", {"name": "t", "implementation_type": "internal_function"}),
    ("PUT", "/tenant-settings/hourly-rate", {"hourly_rate": "75.00"}),
]

_MEMBER_GATED: list[tuple[str, str]] = [
    ("GET", "/projects"),
    ("GET", "/agents"),
    ("GET", "/teams"),
    ("GET", "/knowledge-bases"),
    ("GET", "/memories"),
    ("GET", "/tenant-settings/_registry"),
]


# ---------------------------------------------------------------------------
# Admin-gated endpoints — tenant_user gets 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _ADMIN_GATED)
async def test_admin_gated_rejects_tenant_user(
    configured_app, migrations_pg_dsn: str, method: str, path: str, body: dict[str, Any]
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Admin-gated endpoints — stranger (no membership) gets 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _ADMIN_GATED)
async def test_admin_gated_rejects_stranger(
    configured_app, migrations_pg_dsn: str, method: str, path: str, body: dict[str, Any]
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["stranger"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Member-gated GET endpoints — stranger gets 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), _MEMBER_GATED)
async def test_member_gated_rejects_stranger(
    configured_app, migrations_pg_dsn: str, method: str, path: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["stranger"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Member-gated GET endpoints — tenant_user passes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), _MEMBER_GATED)
async def test_member_gated_allows_tenant_user(
    configured_app, migrations_pg_dsn: str, method: str, path: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    # Should NOT be 403 — anything in 200 / 201 / 204 / 404 acceptable for a
    # smoke that only checks the gate (the body might be empty / missing).
    assert resp.status_code != 403, f"{method} {path}: {resp.status_code} {resp.text}"
    assert resp.status_code < 500, f"{method} {path}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Admin-gated endpoints — system_admin always passes (no membership needed)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_gated_allows_system_admin(configured_app, migrations_pg_dsn: str) -> None:
    """Smoke that system_admin bypasses the tenant_admin gate.

    We test one endpoint (POST /projects) — it's enough to validate
    the bypass path; the gates are uniform across the matrix.
    """
    seed = await _seed_db(migrations_pg_dsn)
    await _promote_to_system_admin(migrations_pg_dsn, seed["stranger"])
    token = await _mint(seed["stranger"], seed["tenant"], is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "byadmin", "status": "draft"},
        )
    # 403 = gate refused; any other status means the gate let us through
    # (the body might still fail validation — that's fine, the gate ran
    # first).
    assert resp.status_code != 403, f"{resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Admin-gated endpoints — tenant_admin passes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_gated_allows_tenant_admin(configured_app, migrations_pg_dsn: str) -> None:
    """Smoke that tenant_admin passes the gate (with a real create).

    Picking POST /projects — same reasoning as above.
    """
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "byadminmember", "status": "active"},
        )
    assert resp.status_code < 400, f"{resp.status_code} {resp.text}"
