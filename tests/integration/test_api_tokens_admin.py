"""Integration tests for Tenant-Admin public-API token CRUD (Plan 13 task_13_02).

``/auth/api-tokens`` — the Tenant Admin mints / lists / revokes the
per-tenant ``X-API-Token`` credentials that authenticate the public REST
API ``/api/v1`` (Plan 13 Decisiones Clave: the token grants access SCOPED
to its own tenant only). These endpoints are JWT-authenticated, gated on
the ``tenant_admin`` role and run on a tenant-scoped RLS session.

Coverage:

  * create returns the plaintext token EXACTLY ONCE and persists only its
    SHA-256 hash (never the clear value).
  * list shows prefix / name / scopes / expiry / last_used / revoked but
    NEVER the secret.
  * revoke marks the token revoked (soft-revoke).
  * RBAC: a non-admin (tenant_user) is denied (403).
  * cross-tenant (@pytest.mark.cross_tenant): a tenant cannot see or revoke
    another tenant's tokens.

Migration reversibility (down to 0040 / up) is proven by the dedicated
``test_migration_api_tokens_reversible`` test.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.api_tokens import hash_api_token
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed + inspection helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
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


async def _seed_user_with_jwt(
    dsn: str, redis_url: str, *, tenant_id: UUID, email: str, role: str
) -> tuple[UUID, str]:
    """Seed a user + active membership with ``role`` + a LIVE Redis session,
    returning ``(user_id, jwt)`` so the test can call the API as that user."""
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) "
            "VALUES ($1, $2, $3, false)",
            user_id,
            email,
            "x",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, $4, true)",
            uuid4(),
            tenant_id,
            user_id,
            role,
        )
    finally:
        await conn.close()

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    jwt = encode_jwt(user_id=user_id, session_id=session_id, tenant_id=tenant_id)
    return user_id, jwt


async def _token_row(dsn: str, *, token_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT tenant_id, token_hash, prefix, name, revoked_at FROM api_tokens WHERE id = $1",
            token_id,
        )
    finally:
        await conn.close()


async def _count_tokens(dsn: str, *, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM api_tokens WHERE tenant_id = $1",
            tenant_id,
        )
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE api_tokens, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture: real api-server wired to the test DB + Redis
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


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# ---------------------------------------------------------------------------
# Create returns the plaintext ONCE + persists only the hash
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_returns_plaintext_once_persists_only_hash(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/api-tokens",
            json={"name": "CI pipeline", "scopes": ["read", "write"]},
            headers=_auth(admin_jwt),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        clear_token = body["token"]
        token_id = UUID(body["id"])
        # The clear token is returned exactly once and starts with the prefix.
        assert clear_token
        assert clear_token.startswith(body["prefix"])
        assert body["name"] == "CI pipeline"
        assert body["scopes"] == ["read", "write"]
        assert body["rate_limit"] == 100  # platform default
        assert body["revoked_at"] is None

    # The DB stores only the SHA-256 hash, never the clear token.
    row = await _token_row(migrations_pg_dsn, token_id=token_id)
    assert row is not None
    assert row["token_hash"] == hash_api_token(clear_token)
    assert row["token_hash"] != clear_token
    assert clear_token not in row["token_hash"]
    assert row["tenant_id"] == tenant


# ---------------------------------------------------------------------------
# List never exposes the secret
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_never_exposes_secret(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        mint = await client.post(
            "/auth/api-tokens", json={"name": "Grafana export"}, headers=_auth(admin_jwt)
        )
        assert mint.status_code == 201, mint.text
        clear_token = mint.json()["token"]

        listing = await client.get("/auth/api-tokens", headers=_auth(admin_jwt))
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        assert len(rows) == 1
        entry = rows[0]
        # Metadata is present; the secret is not, in any form.
        assert entry["name"] == "Grafana export"
        assert entry["scopes"] == ["read"]
        assert "prefix" in entry
        assert "token" not in entry
        assert clear_token not in listing.text


# ---------------------------------------------------------------------------
# Revoke marks the token revoked
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revoke_marks_revoked(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        mint = await client.post(
            "/auth/api-tokens", json={"name": "to-revoke"}, headers=_auth(admin_jwt)
        )
        token_id = mint.json()["id"]

        revoke = await client.delete(f"/auth/api-tokens/{token_id}", headers=_auth(admin_jwt))
        assert revoke.status_code == 204, revoke.text

        # The listing now shows it revoked.
        listing = await client.get("/auth/api-tokens", headers=_auth(admin_jwt))
        assert listing.json()[0]["revoked_at"] is not None

        # Revoking again 404s (no live row matches).
        again = await client.delete(f"/auth/api-tokens/{token_id}", headers=_auth(admin_jwt))
        assert again.status_code == 404, again.text

    row = await _token_row(migrations_pg_dsn, token_id=UUID(token_id))
    assert row is not None
    assert row["revoked_at"] is not None


# ---------------------------------------------------------------------------
# RBAC: a non-admin (tenant_user) is denied (403)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_admin_denied_403(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _user_id, user_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="member@acme.example.com",
        role="tenant_user",
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Both create and list are gated on tenant_admin.
        create = await client.post(
            "/auth/api-tokens", json={"name": "nope"}, headers=_auth(user_jwt)
        )
        assert create.status_code == 403, create.text

        listing = await client.get("/auth/api-tokens", headers=_auth(user_jwt))
        assert listing.status_code == 403, listing.text

    # No token was created by the non-admin.
    assert await _count_tokens(migrations_pg_dsn, tenant_id=tenant) == 0


# ---------------------------------------------------------------------------
# Cross-tenant: a tenant cannot see or revoke another tenant's tokens
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_a_cannot_touch_tenant_b_tokens(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    _admin_a, jwt_a = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant_a,
        email="admin@alpha.example.com",
        role="tenant_admin",
    )
    _admin_b, jwt_b = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant_b,
        email="admin@bravo.example.com",
        role="tenant_admin",
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Tenant B mints a token.
        mint_b = await client.post(
            "/auth/api-tokens", json={"name": "b-token"}, headers=_auth(jwt_b)
        )
        assert mint_b.status_code == 201, mint_b.text
        b_token_id = mint_b.json()["id"]

        # Tenant A's listing must NOT see B's token (RLS scopes to A).
        list_a = await client.get("/auth/api-tokens", headers=_auth(jwt_a))
        assert list_a.status_code == 200, list_a.text
        assert list_a.json() == []

        # Tenant A cannot revoke B's token (404, not a silent success).
        revoke_a = await client.delete(f"/auth/api-tokens/{b_token_id}", headers=_auth(jwt_a))
        assert revoke_a.status_code == 404, revoke_a.text

    # B's token is untouched and still lives only in tenant B.
    assert await _count_tokens(migrations_pg_dsn, tenant_id=tenant_a) == 0
    assert await _count_tokens(migrations_pg_dsn, tenant_id=tenant_b) == 1
    row = await _token_row(migrations_pg_dsn, token_id=UUID(b_token_id))
    assert row is not None
    assert row["tenant_id"] == tenant_b
    assert row["revoked_at"] is None


# ---------------------------------------------------------------------------
# Migration reversibility — up -> down to 0040 -> up.
#
# NOTE: SYNC (no @pytest.mark.asyncio). ``alembic.command.*`` spins up its
# own event loop via ``asyncio.run`` inside env.py; calling it from inside a
# running loop raises. So we stay synchronous and drive the asyncpg probe
# through ``asyncio.run`` in this otherwise loop-free thread.
# ---------------------------------------------------------------------------
def _table_present(dsn: str) -> bool:
    async def _go() -> bool:
        conn = await asyncpg.connect(dsn)
        try:
            return bool(await conn.fetchval("SELECT to_regclass('public.api_tokens')"))
        finally:
            await conn.close()

    return asyncio.run(_go())


def test_migration_api_tokens_reversible(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    assert _table_present(migrations_pg_dsn) is True

    # Full downgrade past 0054 (down target per the plan).
    command.downgrade(alembic_config, "0040_sso_email_domains")
    assert _table_present(migrations_pg_dsn) is False

    # Re-apply: the table comes back cleanly (idempotent up/down/up).
    command.upgrade(alembic_config, "head")
    assert _table_present(migrations_pg_dsn) is True
