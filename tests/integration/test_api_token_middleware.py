"""Integration tests for the X-API-Token auth middleware (Plan 13 task_13_03).

The public REST API ``/api/v1`` (Phase B) is authenticated by a per-tenant
``X-API-Token`` HEADER (Plan 13 Decisiones Clave: header, NEVER a query
param; the token grants access SCOPED to its own tenant only). This suite
exercises the resolving dependency in
:mod:`api_server.auth.api_token_auth`:

  * a valid token authenticates as its tenant and a tenant-scoped query
    under it sees ONLY that tenant's rows;
  * a missing / unknown / expired / revoked token -> 401;
  * a source IP outside the token's ``ip_allowlist`` -> 403;
  * the token -> tenant resolution is Redis-cached: a second call is served
    from the cache even after the row is revoked DIRECTLY in the DB (the
    cache TTL bounds the staleness; the admin revoke endpoint, which
    invalidates the cache, is covered separately);
  * cross-tenant (@pytest.mark.cross_tenant): a tenant-A token never
    resolves tenant B and a query under it never returns tenant-B rows.

There is no v1 surface yet (Phase B), so the test mounts a tiny probe
router on the app under test that depends on the middleware and lists the
``api_tokens`` rows visible under the resolved tenant's RLS scope — the
simplest tenant-owned table to prove the scoping end to end.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.api_tokens import generate_api_token
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
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


async def _seed_token(
    dsn: str,
    *,
    tenant_id: UUID,
    name: str,
    expires_at: datetime | None = None,
    revoked: bool = False,
    ip_allowlist: list[str] | None = None,
) -> tuple[UUID, str]:
    """Seed an ``api_tokens`` row and return ``(token_id, clear_token)``.

    The clear token is minted here (and returned to the test) but only its
    SHA-256 digest is persisted — exactly as the admin mint endpoint does.
    """
    import json

    minted = generate_api_token()
    token_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO api_tokens "
            "(id, tenant_id, token_hash, prefix, name, scopes, expires_at, "
            " rate_limit, ip_allowlist, revoked_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9::jsonb, $10)",
            token_id,
            tenant_id,
            minted.token_hash,
            minted.prefix,
            name,
            json.dumps(["read", "write"]),
            expires_at,
            100,
            json.dumps(ip_allowlist or []),
            datetime.now(tz=UTC) if revoked else None,
        )
    finally:
        await conn.close()
    return token_id, minted.token


async def _last_used_at(dsn: str, *, token_id: UUID) -> datetime | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT last_used_at FROM api_tokens WHERE id = $1", token_id)
    finally:
        await conn.close()


async def _force_revoke_in_db(dsn: str, *, token_id: UUID) -> None:
    """Revoke a token directly in the DB (NOT via the admin endpoint).

    Used to prove the cache serves a now-stale-but-still-cached resolution
    until its TTL elapses — the admin revoke path additionally invalidates
    the cache, which is its own concern.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE id = $1", token_id)
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
# App fixture: real api-server + a probe router behind the middleware
# ---------------------------------------------------------------------------
def _mount_probe_router(app: object) -> None:
    """Mount a tiny ``/_probe/api-token`` endpoint behind the middleware.

    Phase B has no v1 surface yet, so this stands in for one: it depends on
    ``get_api_token_principal`` (proving auth) and runs a query through
    ``get_api_token_session`` (proving the tenant-scoped RLS session). It
    lists the ``api_tokens`` rows visible under RLS — i.e. only the
    resolved tenant's tokens — to prove the scoping.
    """
    from api_server.auth.api_token_auth import (
        ApiTokenPrincipal,
        get_api_token_principal,
        get_api_token_session,
    )
    from api_server.db.models import ApiToken
    from fastapi import APIRouter, Depends
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    probe = APIRouter()

    @probe.get("/_probe/api-token")
    async def _probe(
        principal: ApiTokenPrincipal = Depends(get_api_token_principal),
        session: AsyncSession = Depends(get_api_token_session),
    ) -> dict[str, object]:
        result = await session.execute(select(ApiToken.name).order_by(ApiToken.name))
        return {
            "tenant_id": str(principal.tenant_id),
            "scopes": list(principal.scopes),
            "visible_token_names": list(result.scalars().all()),
        }

    app.include_router(probe)  # type: ignore[attr-defined]


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
    _mount_probe_router(app)
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _hdr(token: str, *, ip: str | None = None) -> dict[str, str]:
    headers = {"X-API-Token": token}
    if ip is not None:
        headers["X-Forwarded-For"] = ip
    return headers


# ---------------------------------------------------------------------------
# Valid token authenticates as its tenant + sees only that tenant
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_valid_token_authenticates_and_is_tenant_scoped(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id_a, token_a = await _seed_token(migrations_pg_dsn, tenant_id=tenant_a, name="a-token")
    # A second tenant with its own token — must stay invisible to A.
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="globex")
    await _seed_token(migrations_pg_dsn, tenant_id=tenant_b, name="b-token")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/_probe/api-token", headers=_hdr(token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tenant_id"] == str(tenant_a)
        assert body["scopes"] == ["read", "write"]
        # RLS scopes the query to tenant A: only A's token is visible.
        assert body["visible_token_names"] == ["a-token"]


# ---------------------------------------------------------------------------
# Missing / invalid / expired / revoked -> 401
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_token_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/_probe/api-token")
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_unknown_token_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/_probe/api-token", headers=_hdr("aapt_deadbeef_nope"))
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_expired_token_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id, token = await _seed_token(
        migrations_pg_dsn,
        tenant_id=tenant,
        name="expired",
        expires_at=datetime.now(tz=UTC) - timedelta(minutes=1),
    )
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/_probe/api-token", headers=_hdr(token))
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_revoked_token_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="revoked", revoked=True
    )
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/_probe/api-token", headers=_hdr(token))
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# IP not in allowlist -> 403; in allowlist -> 200
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ip_not_in_allowlist_403(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id, token = await _seed_token(
        migrations_pg_dsn,
        tenant_id=tenant,
        name="ip-locked",
        ip_allowlist=["10.0.0.0/24"],
    )
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # From an IP outside the allowlist -> 403 (valid token, wrong place).
        blocked = await client.get("/_probe/api-token", headers=_hdr(token, ip="192.168.1.5"))
        assert blocked.status_code == 403, blocked.text
        # From an IP inside the allowlist -> 200.
        allowed = await client.get("/_probe/api-token", headers=_hdr(token, ip="10.0.0.42"))
        assert allowed.status_code == 200, allowed.text


# ---------------------------------------------------------------------------
# Resolution is Redis-cached: a second call after a DIRECT-DB revoke still
# resolves from the cache (the TTL bounds the staleness; the admin revoke
# endpoint additionally invalidates the cache — that path is covered in the
# admin tests).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resolution_is_redis_cached(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token_id, token = await _seed_token(migrations_pg_dsn, tenant_id=tenant, name="cached")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # First call: cache miss -> DB resolution -> bumps last_used_at +
        # populates the cache.
        first = await client.get("/_probe/api-token", headers=_hdr(token))
        assert first.status_code == 200, first.text
        used_after_first = await _last_used_at(migrations_pg_dsn, token_id=token_id)
        assert used_after_first is not None

        # Revoke directly in the DB WITHOUT going through the admin endpoint
        # (so the cache is NOT invalidated).
        await _force_revoke_in_db(migrations_pg_dsn, token_id=token_id)

        # Second call: served from the cache despite the DB row now being
        # revoked — proving the resolution was cached (no DB hit).
        second = await client.get("/_probe/api-token", headers=_hdr(token))
        assert second.status_code == 200, second.text
        assert second.json()["tenant_id"] == str(tenant)

    # The cache short-circuited the DB, so last_used_at was NOT bumped again.
    used_after_second = await _last_used_at(migrations_pg_dsn, token_id=token_id)
    assert used_after_second == used_after_first


# ---------------------------------------------------------------------------
# Cross-tenant: a tenant-A token never resolves tenant B nor sees its rows
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_a_token_never_resolves_tenant_b(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    _id_a, token_a = await _seed_token(migrations_pg_dsn, tenant_id=tenant_a, name="alpha-token")
    await _seed_token(migrations_pg_dsn, tenant_id=tenant_b, name="bravo-token")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/_probe/api-token", headers=_hdr(token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Authenticates strictly as tenant A.
        assert body["tenant_id"] == str(tenant_a)
        assert body["tenant_id"] != str(tenant_b)
        # And under RLS sees ONLY tenant A's tokens — never tenant B's.
        assert body["visible_token_names"] == ["alpha-token"]
        assert "bravo-token" not in body["visible_token_names"]
