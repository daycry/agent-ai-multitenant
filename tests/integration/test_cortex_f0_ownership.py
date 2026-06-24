"""Córtex F0 — System Owner foundation (ADR 0074).

Exercises the F0 cimiento end-to-end: first-user bootstrap as owner, the singleton
DB invariant, the `own` claim surfaced on /me, and the `require_system_owner` /
`require_admin_or_owner` gates verified against the DB."""

from __future__ import annotations

import asyncio
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


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


async def _truncate_users(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE users RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


async def _register(client: AsyncClient, email: str) -> dict:
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": "Sup3r-secret-pw!", "full_name": email.split("@")[0]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login_me(client: AsyncClient, email: str) -> dict:
    login = await client.post("/auth/login", json={"email": email, "password": "Sup3r-secret-pw!"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    return me.json()


@pytest.mark.asyncio
async def test_first_user_is_system_owner_second_is_not(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        alice = await _register(client, "alice@acme.io")
        bob = await _register(client, "bob@acme.io")
        # Bootstrap: the very first user is the owner (and admin); the rest are not.
        assert alice["is_system_owner"] is True
        assert alice["is_system_admin"] is True
        assert bob["is_system_owner"] is False

        # /me surfaces the flag (via the `own` claim path + DB row).
        assert (await _login_me(client, "alice@acme.io"))["is_system_owner"] is True
        assert (await _login_me(client, "bob@acme.io"))["is_system_owner"] is False


@pytest.mark.asyncio
async def test_system_owner_is_a_singleton(configured_app, migrations_pg_dsn: str) -> None:
    """The partial unique index forbids a second owner."""
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        await _register(client, "alice@acme.io")  # owner
        bob = await _register(client, "bob@acme.io")

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "UPDATE users SET is_system_owner = true WHERE id = $1", UUID(bob["id"])
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_require_system_owner_gate_checks_the_db(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The gate is DB-authoritative: owner passes, non-owner gets 403, even with a
    forged `is_system_owner=True` hint on the principal."""
    from api_server.auth.deps import AuthPrincipal, require_admin_or_owner, require_system_owner

    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        alice = await _register(client, "alice@acme.io")
        bob = await _register(client, "bob@acme.io")

    owner_principal = AuthPrincipal(
        user_id=UUID(alice["id"]), session_id=uuid7(), tenant_id=None, is_system_owner=True
    )
    # Non-owner with a FORGED hint — the DB check must still reject it.
    forged_principal = AuthPrincipal(
        user_id=UUID(bob["id"]), session_id=uuid7(), tenant_id=None, is_system_owner=True
    )

    assert await require_system_owner(owner_principal) is owner_principal
    assert await require_admin_or_owner(owner_principal) is owner_principal

    with pytest.raises(HTTPException) as exc:
        await require_system_owner(forged_principal)
    assert exc.value.status_code == 403
