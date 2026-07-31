"""Double-submit CSRF on cookie-authenticated mutations (ADR 0133).

The honest half of the ADR: moving the session to a cookie CREATES a CSRF
surface that the Bearer scheme was immune to by construction, because the
browser attaches cookies to cross-site requests automatically. This file is the
ADR's verification item 2 — «una mutación con la cookie pero SIN la cabecera CSRF
→ 403» — plus the three neighbouring cases that decide whether the guard is real
or theatre:

  * a WRONG token must fail too (not just a missing one);
  * a SAFE method must NOT need the header (or every page load breaks);
  * a BEARER mutation must NOT need it (curl and the SDKs never had a CSRF
    cookie to echo, and Bearer cannot be forged cross-site anyway).

Pre-condition: postgres + redis from docker-compose are healthy on the host.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

EMAIL = "csrf-tester@example.com"
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
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _login(client: AsyncClient) -> None:
    await client.post(
        "/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": "CSRF Tester"},
    )
    response = await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text


def test_cookie_mutation_without_csrf_header_is_403(configured_app, admin_pg_dsn: str) -> None:
    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            await _login(client)
            # Cookie present (httpx jar), no X-CSRF-Token: this is precisely the
            # shape of a cross-site forged POST.
            response = await client.post("/auth/logout")
            assert response.status_code == 403, response.text
            assert "csrf" in response.text.lower()

    asyncio.run(scenario())


def test_cookie_mutation_with_wrong_csrf_header_is_403(configured_app, admin_pg_dsn: str) -> None:
    """A guard that only checks PRESENCE is not a guard."""
    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            await _login(client)
            response = await client.post(
                "/auth/logout", headers={"X-CSRF-Token": "not-the-real-token"}
            )
            assert response.status_code == 403, response.text

    asyncio.run(scenario())


def test_cookie_mutation_with_matching_csrf_header_passes(
    configured_app, admin_pg_dsn: str
) -> None:
    from api_server.auth.cookies import CSRF_COOKIE_NAME

    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            await _login(client)
            csrf = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf
            response = await client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
            assert response.status_code == 204, response.text

    asyncio.run(scenario())


def test_cookie_read_does_not_require_csrf(configured_app, admin_pg_dsn: str) -> None:
    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            await _login(client)
            response = await client.get("/auth/me")
            assert response.status_code == 200, response.text

    asyncio.run(scenario())


def test_bearer_mutation_does_not_require_csrf(configured_app, admin_pg_dsn: str) -> None:
    """The API clients never had a CSRF cookie and never will; requiring the
    header for Bearer would break every SDK for a threat Bearer does not have."""
    from api_server.auth.cookies import SESSION_COOKIE_NAME

    asyncio.run(_truncate_users(admin_pg_dsn))

    async def scenario() -> None:
        async with _client(configured_app) as client:
            await _login(client)
            token = client.cookies.get(SESSION_COOKIE_NAME)

        async with _client(configured_app) as bare:
            response = await bare.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 204, response.text

    asyncio.run(scenario())
