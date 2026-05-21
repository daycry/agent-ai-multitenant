"""End-to-end tests for the /auth/* router.

Covers:
  - POST /auth/register creates a user and returns 201 + UserResponse.
  - POST /auth/register with a duplicate email returns 409.
  - POST /auth/login with valid credentials returns 200 + JWT/expires_in
    and provisions a server-side session in Redis.
  - POST /auth/login with bad password returns 401 (and never leaks
    whether the email exists).
  - POST /auth/login with an unknown email returns 401.
  - GET  /auth/me returns the principal's User row.
  - POST /auth/logout returns 204 and the JWT no longer works.
  - 6th login attempt within the rate-limit window returns 429.

Pre-condition: postgres and redis from docker-compose are healthy on
the host (15432 + 6379). The test session creates a throwaway DB,
flushes Redis DB 15, and tears both down on exit.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def _truncate_users(dsn: str) -> None:
    """Wipe the users + memberships tables so first-user promotion
    behaves deterministically regardless of test ordering. The
    session-scoped test DB persists between tests, so per-test state
    has to be explicit."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE user_org_memberships, users RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Same shape as test_isolation.configured_app — duplicated here to
    keep each test module self-contained."""
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    # Force a tiny rate-limit window so test_rate_limit doesn't have
    # to wait 15 minutes in CI.
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_COUNT", "5")
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
# /auth/register
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_first_user_becomes_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Fresh install: the very first registered user is auto-promoted
    to system admin so the operator has a way in. Subsequent users
    default to non-admin (covered by the next test)."""
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/auth/register",
            json={
                "email": "alice@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Alice",
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["full_name"] == "Alice"
    assert body["is_system_admin"] is True
    assert body["is_active"] is True
    assert "id" in body
    # Password must never round-trip.
    assert "password" not in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_register_subsequent_user_is_not_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Second user (and onward) keeps the DB default
    `is_system_admin=false`. Promotion to admin afterwards is the
    job of /admin/users (system-admin gated)."""
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Seed a first user; this one becomes admin.
        first = await client.post(
            "/auth/register",
            json={"email": "operator@example.com", "password": "longenoughpw"},
        )
        assert first.status_code == 201
        assert first.json()["is_system_admin"] is True

        # Anyone after is non-admin until promoted.
        resp = await client.post(
            "/auth/register",
            json={"email": "alice@example.com", "password": "longenoughpw"},
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["is_system_admin"] is False


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        first = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "longenoughpw"},
        )
        assert first.status_code == 201

        second = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "anotherlongpw"},
        )

    assert second.status_code == 409
    assert "already registered" in second.text.lower()


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_returns_jwt_with_expires_in(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "bob@example.com", "password": "longenoughpw"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "bob@example.com", "password": "longenoughpw"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert isinstance(body["access_token"], str) and body["access_token"]


@pytest.mark.asyncio
async def test_login_wrong_password_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "carol@example.com", "password": "rightpassword"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "carol@example.com", "password": "wrongpassword"},
        )

    assert resp.status_code == 401
    # Generic message — no leak of "user exists".
    assert "invalid email or password" in resp.text.lower()


@pytest.mark.asyncio
async def test_login_unknown_email_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "anything"},
        )

    assert resp.status_code == 401
    assert "invalid email or password" in resp.text.lower()


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_returns_user_info(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        reg = await client.post(
            "/auth/register",
            json={
                "email": "dave@example.com",
                "password": "longenoughpw",
                "full_name": "Dave",
            },
        )
        user_id = reg.json()["id"]

        login = await client.post(
            "/auth/login",
            json={"email": "dave@example.com", "password": "longenoughpw"},
        )
        token = login.json()["access_token"]

        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert me.status_code == 200, me.text
    body = me.json()
    assert body["id"] == user_id
    assert body["email"] == "dave@example.com"
    assert body["full_name"] == "Dave"


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_logout_revokes_session_immediately(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "eve@example.com", "password": "longenoughpw"},
        )
        login = await client.post(
            "/auth/login",
            json={"email": "eve@example.com", "password": "longenoughpw"},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Sanity: the token works before logout.
        ok = await client.get("/auth/me", headers=headers)
        assert ok.status_code == 200

        logout = await client.post("/auth/logout", headers=headers)
        assert logout.status_code == 204

        # Same token, now revoked.
        after = await client.get("/auth/me", headers=headers)
    assert after.status_code == 401
    assert "revoked" in after.text.lower()


# ---------------------------------------------------------------------------
# Rate limiting — auto_00_10_b
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rate_limit(configured_app) -> None:
    """The 6th login attempt within the window returns 429.

    Limit is 5/window per IP and per email; we hit the IP limit first
    because all attempts come from the same fake client.
    """
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "frank@example.com", "password": "longenoughpw"},
        )

        # 5 attempts with the wrong password — each returns 401, none
        # should trigger 429 yet.
        for _ in range(5):
            r = await client.post(
                "/auth/login",
                json={"email": "frank@example.com", "password": "wrong"},
            )
            assert r.status_code == 401, r.text

        # 6th attempt within the same window — limit tripped.
        sixth = await client.post(
            "/auth/login",
            json={"email": "frank@example.com", "password": "wrong"},
        )

    assert sixth.status_code == 429, sixth.text
    assert "Retry-After" in sixth.headers
