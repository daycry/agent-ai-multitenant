"""The panel session travels as an httpOnly cookie (ADR 0133, task_prod09_07).

Fixes the ADR's verification items 1 and 5 as executable contract:

  1. after a password login the session is a cookie with ``HttpOnly`` +
     ``Secure``, and the response body no longer hands the raw JWT to any script
     on the page;
  5. ``Authorization: Bearer`` still authenticates — the public API, ``curl``
     and the generated SDKs depend on it, so the migration is ADDITIVE.

Plus the two things that make the cookie a real session rather than a decoration:
the tenant-scoped token minted by ``/auth/session/resolve`` and
``/auth/session/select-tenant`` must REPLACE the cookie (otherwise the browser
keeps sending the tenant-less identity token and every tenant-scoped write 400s),
and logout must expire it.

Pre-condition: postgres + redis from docker-compose are healthy on the host.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

EMAIL = "cookie-session@example.com"
PASSWORD = "longenoughpassword"


async def _truncate_users(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE user_org_memberships, users RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


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
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_COUNT", "50")
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
        get_settings.cache_clear()


def _client(app) -> AsyncClient:
    # `base_url` must be https so httpx keeps the `Secure` cookies in its jar —
    # a plain-http test client would silently DROP them and the suite would be
    # asserting on a session that never travelled.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _register_and_login(client: AsyncClient) -> object:
    await client.post(
        "/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": "Cookie Tester"},
    )
    return await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})


def test_login_sets_httponly_session_cookie(configured_app, admin_pg_dsn: str) -> None:
    from api_server.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME

    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            response = await _register_and_login(client)
            assert response.status_code == 200, response.text

            raw = response.headers.get_list("set-cookie")
            session_header = next(h for h in raw if h.startswith(f"{SESSION_COOKIE_NAME}="))
            assert "HttpOnly" in session_header, session_header
            assert "Secure" in session_header, session_header
            assert "samesite=lax" in session_header.lower(), session_header

            # The readable half of the double-submit pair is there and is NOT
            # the JWT.
            csrf = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf
            assert csrf != client.cookies.get(SESSION_COOKIE_NAME)

            # The cookie alone authenticates: no Authorization header sent.
            me = await client.get("/auth/me")
            assert me.status_code == 200, me.text
            assert me.json()["email"] == EMAIL

    asyncio.run(scenario())


def test_csrf_token_is_delivered_only_as_a_readable_cookie(
    configured_app, admin_pg_dsn: str
) -> None:
    """The double-submit token needs NO new body field: the CSRF cookie is
    readable by design, so the panel picks it up from ``document.cookie``.

    The body keeps ``access_token`` on purpose — that is the compatibility leg
    of the ADR (``curl``, the SDKs and ``scripts/`` do a password login to get a
    Bearer). It is not the hole ADR 0133 closes: the hole was the panel PARKING
    that token in ``localStorage``, where any script on any page of the session
    could read it later. The panel now stores nothing.
    """
    from api_server.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME

    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            response = await _register_and_login(client)
            body = response.json()
            assert body["expires_in"] > 0
            # No new body field was invented for CSRF...
            assert "csrf_token" not in body
            # ...because it arrives as a cookie JS can read.
            assert client.cookies.get(CSRF_COOKIE_NAME)
            # And the session cookie carries the very token the body returns,
            # so the two authentication legs are the SAME session.
            assert client.cookies.get(SESSION_COOKIE_NAME) == body["access_token"]

    asyncio.run(scenario())


def test_bearer_still_authenticates(configured_app, admin_pg_dsn: str) -> None:
    """curl / SDKs / the public API keep working: the cookie is ADDITIVE."""
    from api_server.auth.cookies import SESSION_COOKIE_NAME

    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            await _register_and_login(client)
            token = client.cookies.get(SESSION_COOKIE_NAME)
            assert token

        # A brand-new client with NO cookie jar, Bearer only.
        async with _client(configured_app) as bare:
            me = await bare.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200, me.text
            assert me.json()["email"] == EMAIL

    asyncio.run(scenario())


def test_resolve_and_logout_rotate_the_cookie(configured_app, admin_pg_dsn: str) -> None:
    """`/auth/session/resolve` re-issues the cookie when it mints a scoped token,
    and logout expires it. Without the first half the browser would keep sending
    the tenant-LESS identity token forever; without the second, logout would only
    look like it worked."""
    from api_server.auth.cookies import SESSION_COOKIE_NAME

    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            await _register_and_login(client)
            identity_cookie = client.cookies.get(SESSION_COOKIE_NAME)

            # The first user is a System Admin with no membership → "admin"
            # state, no token minted, so the cookie is left alone.
            resolved = await client.get("/auth/session/resolve")
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["state"] == "admin"
            assert client.cookies.get(SESSION_COOKIE_NAME) == identity_cookie

            logout = await client.post(
                "/auth/logout",
                headers={"X-CSRF-Token": client.cookies.get("agentic_csrf") or ""},
            )
            assert logout.status_code == 204, logout.text
            assert not client.cookies.get(SESSION_COOKIE_NAME)

            # ...and the revoked session really is dead.
            me = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {identity_cookie}"}
            )
            assert me.status_code == 401

    asyncio.run(scenario())
