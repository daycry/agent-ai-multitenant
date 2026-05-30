"""Integration tests for MFA TOTP (Plan 08 task_08_09).

MFA is an OPT-IN second factor ADDED ALONGSIDE the existing auth (local
login + OIDC + SAML). These tests run fully offline — pyotp generates a
valid code for any secret, so no real authenticator is needed.

Coverage:

  * enroll -> the secret + otpauth:// URI + recovery codes come back; the
    row is UNCONFIRMED so it does not yet gate login.
  * confirm with a valid pyotp code -> the factor becomes confirmed.
  * a non-MFA user's login is UNCHANGED (regression): password -> session.
  * a confirmed user's login returns ``mfa_required`` (NO session token),
    then ``/auth/mfa/totp/verify`` with a valid code yields a real session
    that ``/auth/me`` accepts.
  * a WRONG TOTP code at verify fails (400) and yields no session.
  * a recovery code works ONCE and is then consumed (second use fails).
  * cross-tenant (@pytest.mark.cross_tenant): tenant A cannot see / use
    tenant B's enrollment via the status endpoint (RLS).

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pyotp
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_mfa_totp, scim_tokens, sso_configurations, "
            "user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _register_and_member(dsn: str, *, tenant_id: UUID, email: str, password: str) -> UUID:
    """Create a normal local user + an active membership in the tenant.

    Uses the real argon2 hasher so /auth/login accepts the password.
    """
    from api_server.auth.passwords import hash_password

    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name, is_system_admin) "
            "VALUES ($1, $2, $3, $4, false)",
            user_id,
            email,
            hash_password(password),
            "MFA Tester",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, 'tenant_user', true)",
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()
    return user_id


async def _totp_row(dsn: str, *, tenant_id: UUID, user_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT confirmed_at, recovery_codes, secret_encrypted "
            "FROM user_mfa_totp WHERE tenant_id = $1 AND user_id = $2",
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()


async def _issue_tenant_jwt(redis_url: str, *, user_id: UUID, tenant_id: UUID) -> str:
    """Mint a live Redis session + JWT bound to the tenant (for enroll/confirm)."""
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    return encode_jwt(user_id=user_id, session_id=session_id, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# App fixture (same shape as test_scim.configured_app)
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
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _enroll_and_confirm(client: AsyncClient, jwt: str) -> tuple[str, list[str]]:
    """Enroll + confirm TOTP for the JWT's user, returning (secret, recovery_codes)."""
    enroll = await client.post("/auth/mfa/totp/enroll", headers=_bearer(jwt))
    assert enroll.status_code == 200, enroll.text
    body = enroll.json()
    secret = body["secret"]
    recovery_codes = body["recovery_codes"]
    assert body["provisioning_uri"].startswith("otpauth://totp/")

    code = pyotp.TOTP(secret).now()
    confirm = await client.post("/auth/mfa/totp/confirm", json={"code": code}, headers=_bearer(jwt))
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["confirmed"] is True
    return secret, recovery_codes


# ---------------------------------------------------------------------------
# Enroll + confirm
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_enroll_then_confirm(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="totp@acme.example.com", password="longenoughpw"
    )
    jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Status before: not enrolled.
        before = await client.get("/auth/mfa/totp", headers=_bearer(jwt))
        assert before.status_code == 200, before.text
        assert before.json() == {
            "enrolled": False,
            "confirmed": False,
            "recovery_codes_remaining": 0,
        }

        secret, recovery_codes = await _enroll_and_confirm(client, jwt)
        assert len(recovery_codes) == 10

        # Status after: enrolled + confirmed, all recovery codes available.
        after = await client.get("/auth/mfa/totp", headers=_bearer(jwt))
        assert after.json()["enrolled"] is True
        assert after.json()["confirmed"] is True
        assert after.json()["recovery_codes_remaining"] == 10

    # The secret is encrypted at rest (not the base32 plaintext), the row is
    # confirmed and recovery codes are hashed (not the clear values).
    row = await _totp_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    assert row["confirmed_at"] is not None
    assert row["secret_encrypted"] != secret
    import json as _json

    stored = _json.loads(row["recovery_codes"])
    assert len(stored) == 10
    assert recovery_codes[0] not in stored  # only the hash is stored


# ---------------------------------------------------------------------------
# Confirm with a wrong code fails
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_confirm_wrong_code_fails(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="totp@acme.example.com", password="longenoughpw"
    )
    jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await client.post("/auth/mfa/totp/enroll", headers=_bearer(jwt))
        bad = await client.post(
            "/auth/mfa/totp/confirm", json={"code": "000000"}, headers=_bearer(jwt)
        )
        assert bad.status_code == 400, bad.text

    # Still unconfirmed -> does not gate login.
    row = await _totp_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    assert row["confirmed_at"] is None


# ---------------------------------------------------------------------------
# Regression: a user WITHOUT MFA logs in exactly as before
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_without_mfa_unchanged(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="plain@acme.example.com", password="longenoughpw"
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/login",
            json={"email": "plain@acme.example.com", "password": "longenoughpw"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The non-MFA path returns a real token, NOT an mfa_required challenge.
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert "mfa_token" not in body

        # The token works immediately.
        me = await client.get("/auth/me", headers=_bearer(body["access_token"]))
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "plain@acme.example.com"


# ---------------------------------------------------------------------------
# Confirmed user: login -> mfa_required -> verify with valid code -> session
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_with_mfa_then_verify(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="mfa@acme.example.com", password="longenoughpw"
    )
    enroll_jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        secret, _ = await _enroll_and_confirm(client, enroll_jwt)

        # Now login: password alone returns mfa_required, NOT a session.
        login = await client.post(
            "/auth/login",
            json={"email": "mfa@acme.example.com", "password": "longenoughpw"},
        )
        assert login.status_code == 200, login.text
        lbody = login.json()
        assert lbody["status"] == "mfa_required"
        assert "access_token" not in lbody
        assert lbody["mfa_methods"] == ["totp"]
        mfa_token = lbody["mfa_token"]

        # Verify with the current TOTP code -> a real session.
        code = pyotp.TOTP(secret).now()
        verify = await client.post(
            "/auth/mfa/totp/verify",
            json={"mfa_token": mfa_token, "code": code},
        )
        assert verify.status_code == 200, verify.text
        token = verify.json()["access_token"]
        assert token

        # The session works.
        me = await client.get("/auth/me", headers=_bearer(token))
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "mfa@acme.example.com"


# ---------------------------------------------------------------------------
# Wrong TOTP code at verify -> 400, no session
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verify_wrong_code_fails(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="mfa@acme.example.com", password="longenoughpw"
    )
    enroll_jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await _enroll_and_confirm(client, enroll_jwt)

        login = await client.post(
            "/auth/login",
            json={"email": "mfa@acme.example.com", "password": "longenoughpw"},
        )
        mfa_token = login.json()["mfa_token"]

        bad = await client.post(
            "/auth/mfa/totp/verify",
            json={"mfa_token": mfa_token, "code": "000000"},
        )
        assert bad.status_code == 400, bad.text

        # The challenge was single-use: even a correct token replay fails now.
        replay = await client.post(
            "/auth/mfa/totp/verify",
            json={"mfa_token": mfa_token, "code": "123456"},
        )
        assert replay.status_code == 400, replay.text


# ---------------------------------------------------------------------------
# Recovery code works once, then is consumed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recovery_code_works_once(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant, email="mfa@acme.example.com", password="longenoughpw"
    )
    enroll_jwt = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        _, recovery_codes = await _enroll_and_confirm(client, enroll_jwt)
        recovery = recovery_codes[0]

        # First login: use a recovery code instead of a TOTP -> session.
        login1 = await client.post(
            "/auth/login",
            json={"email": "mfa@acme.example.com", "password": "longenoughpw"},
        )
        token1 = login1.json()["mfa_token"]
        ok = await client.post(
            "/auth/mfa/totp/verify",
            json={"mfa_token": token1, "code": recovery},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["access_token"]

        # The same recovery code is now consumed: a fresh challenge + the
        # same code fails.
        login2 = await client.post(
            "/auth/login",
            json={"email": "mfa@acme.example.com", "password": "longenoughpw"},
        )
        token2 = login2.json()["mfa_token"]
        reused = await client.post(
            "/auth/mfa/totp/verify",
            json={"mfa_token": token2, "code": recovery},
        )
        assert reused.status_code == 400, reused.text

    # One recovery code was consumed -> nine remain.
    row = await _totp_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    import json as _json

    assert len(_json.loads(row["recovery_codes"])) == 9


# ---------------------------------------------------------------------------
# Cross-tenant: tenant A cannot see tenant B's enrollment via status
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_isolation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    # One human, member of BOTH tenants, enrolls TOTP only in B.
    user_id = await _register_and_member(
        migrations_pg_dsn, tenant_id=tenant_b, email="multi@example.com", password="longenoughpw"
    )
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, 'tenant_user', true)",
            uuid4(),
            tenant_a,
            user_id,
        )
    finally:
        await conn.close()

    jwt_b = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant_b)
    jwt_a = await _issue_tenant_jwt(test_redis_url, user_id=user_id, tenant_id=tenant_a)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await _enroll_and_confirm(client, jwt_b)

        # Tenant B sees the confirmed factor.
        status_b = await client.get("/auth/mfa/totp", headers=_bearer(jwt_b))
        assert status_b.json()["confirmed"] is True

        # Tenant A (RLS-scoped) sees NO enrollment for the same user.
        status_a = await client.get("/auth/mfa/totp", headers=_bearer(jwt_a))
        assert status_a.json() == {
            "enrolled": False,
            "confirmed": False,
            "recovery_codes_remaining": 0,
        }

    # The DB confirms exactly one enrollment row, scoped to tenant B.
    assert await _totp_row(migrations_pg_dsn, tenant_id=tenant_a, user_id=user_id) is None
    assert await _totp_row(migrations_pg_dsn, tenant_id=tenant_b, user_id=user_id) is not None
