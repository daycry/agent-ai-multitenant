"""System-Admin privileges are re-verified against the DB (prod-09 task_prod09_04).

Finding authz-4: ``require_system_admin`` trusted the ``sys`` JWT claim, which is
stamped at LOGIN and lives for the 24 h session TTL. So ``UPDATE users SET
is_system_admin = false`` — the off-boarding move — left the degraded user with a
full cross-tenant, BYPASSRLS admin session for up to a day. Worse, there was no
way to end it: ``SessionStore.revoke_user_sessions`` walks a per-``(user,
tenant)`` index, and a System Admin's session is TENANT-LESS, so it is not in any
index at all.

The fix is a per-request re-read of ``users.is_system_admin`` (one indexed PK
lookup on a global table — the same read ``/auth/me`` already does). These tests
exercise it end-to-end through the real app and the real Postgres, because the
whole point is the DIVERGENCE between the token and the database: a unit test
with a hand-made principal cannot show it.

This is also the automated half of the human check "quitar is_system_admin a un
usuario en BD -> su siguiente request admin devuelve 403 sin esperar 24 h".
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

# An /admin route that needs nothing but the gate + a global settings read, so a
# failure here is unambiguously about authorisation.
_ADMIN_ROUTE = "/admin/backup/schedule"


@pytest.fixture()
def configured_app(
    alembic_config: Any,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_ENVIRONMENT", "dev")

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


async def _seed(dsn: str) -> dict[str, UUID]:
    """One System Admin + one plain tenant user in a tenant."""
    ids = {"tenant": uuid4(), "sysadmin": uuid4(), "member": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE platform_settings, audit_log,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ids["tenant"],
            "Tenant R",
            "tenant-revalidation",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, 'h', true), ($3, $4, 'h', false)",
            ids["sysadmin"],
            "sys@revalidation.test",
            ids["member"],
            "member@revalidation.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["member"],
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
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_admin=is_system_admin
    )


async def _session_is_live(sid: str) -> bool:
    from api_server.auth.deps import get_redis

    return await get_redis().get(f"session:{sid}") is not None


async def _set_flag(dsn: str, user_id: UUID, value: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET is_system_admin = $2 WHERE id = $1", user_id, value)
    finally:
        await conn.close()


async def _soft_delete(dsn: str, user_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET deleted_at = now() WHERE id = $1", user_id)
    finally:
        await conn.close()


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# The finding
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revoking_the_flag_in_db_403s_the_next_admin_request(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """SAME token, SAME live session: 200 before the UPDATE, 403 after.

    Nothing about the credential changes between the two calls — which is what
    proves the decision is made from the DB and not from the claim. Before this
    task the second call was another 200, for up to 24 h.
    """
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        before = await client.get(_ADMIN_ROUTE, headers=headers)
        assert before.status_code == 200, before.text

        await _set_flag(migrations_pg_dsn, seeded["sysadmin"], False)

        after = await client.get(_ADMIN_ROUTE, headers=headers)

    assert after.status_code == 403, after.text
    assert "revoked" in after.json()["detail"]


@pytest.mark.asyncio
async def test_the_session_itself_is_still_alive(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """The 403 above comes from the DB re-read, NOT from a revoked session.

    Worth pinning explicitly: the plan's other candidate fix was "revoke the
    user's sessions when the flag changes", which cannot work here — the
    per-user session index only holds TENANT-scoped sessions and an admin's
    session is tenant-less. This test documents that the session survives and
    the gate stops the request anyway.
    """
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    from api_server.auth.jwt import decode_jwt

    sid = decode_jwt(token)["sid"]

    await _set_flag(migrations_pg_dsn, seeded["sysadmin"], False)
    async with _client(configured_app) as client:
        resp = await client.get(_ADMIN_ROUTE, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403
    assert await _session_is_live(sid), "the session was revoked; this test would prove nothing"


@pytest.mark.asyncio
async def test_a_forged_sys_claim_is_worthless(configured_app: Any, migrations_pg_dsn: str) -> None:
    """A token that CLAIMS ``sys`` for a user who never had the flag is refused.

    This is the same defect from the other side: a leaked signing key, a stale
    mint path or a bug that sets the claim too generously used to be enough to
    become a cross-tenant admin. Now the claim buys nothing the DB does not
    confirm.
    """
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member"], None, is_system_admin=True)

    async with _client(configured_app) as client:
        resp = await client.get(_ADMIN_ROUTE, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403, resp.text
    assert "revoked" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_soft_deleted_admin_is_refused(configured_app: Any, migrations_pg_dsn: str) -> None:
    """Soft-deleting the admin row also ends admin access immediately — the
    re-read filters on ``deleted_at IS NULL``, so "delete the account" is not a
    weaker off-boarding than "clear the flag"."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    await _soft_delete(migrations_pg_dsn, seeded["sysadmin"])
    async with _client(configured_app) as client:
        resp = await client.get(_ADMIN_ROUTE, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_a_plain_member_is_still_refused_by_the_claim_check(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """The cheap claim check still runs FIRST, with its original message — a
    regular tenant user must not be able to make the admin surface hit the DB
    (that would be a free amplification primitive on an unauthenticated-ish
    path)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member"], seeded["tenant"])

    async with _client(configured_app) as client:
        resp = await client.get(_ADMIN_ROUTE, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "system admin role required"


@pytest.mark.asyncio
async def test_restoring_the_flag_restores_access(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Symmetry check: the re-read is a live read, not a one-way latch. Granting
    the flag back works on the next request without re-login — and it keeps the
    other tests honest (they would also pass if the gate simply always 403'd)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    await _set_flag(migrations_pg_dsn, seeded["sysadmin"], False)
    async with _client(configured_app) as client:
        denied = await client.get(_ADMIN_ROUTE, headers=headers)
        assert denied.status_code == 403, denied.text

        await _set_flag(migrations_pg_dsn, seeded["sysadmin"], True)
        allowed = await client.get(_ADMIN_ROUTE, headers=headers)

    assert allowed.status_code == 200, allowed.text
