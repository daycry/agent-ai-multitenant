"""Integration tests for per-token sliding-window rate limiting (task_13_04).

The public REST API ``/api/v1`` (Phase B) is rate-limited PER TOKEN: each
``X-API-Token`` carries its own ``rate_limit`` (default 100 req/min,
configurable per token row) and the limiter counts hits in a Redis
sliding window keyed by the token id (mirrors the login rate-limit
sliding-window approach). This suite exercises
:func:`api_server.auth.api_token_auth.enforce_api_token_rate_limit`:

  * requests UNDER the budget pass and carry the standard
    ``X-RateLimit-Limit / -Remaining / -Reset`` headers;
  * the (limit+1)th request inside the window -> HTTP 429 with a
    ``Retry-After`` header (and the rate-limit headers);
  * the window SLIDES: once the early hits age out, requests succeed again;
  * each token has its OWN budget — saturating token A does not throttle
    token B (same tenant);
  * cross-tenant (@pytest.mark.cross_tenant): a tenant-B token keeps its
    full budget even when a tenant-A token is rate-limited.

The window + default budget are config/named constants; the tests set a
SHORT window (``API_SERVER_API_TOKEN_RATE_LIMIT_WINDOW_SECONDS``) and a
small per-token budget so the sliding behaviour is observable without long
waits.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.api_tokens import generate_api_token
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

# Short window + small budget keep the sliding-window behaviour observable
# in a test without long sleeps. The window is set on the app via env so it
# flows through the real Settings, not a magic number patched in code.
_TEST_WINDOW_SECONDS = 2
_TEST_BUDGET = 3


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
    rate_limit: int = _TEST_BUDGET,
) -> tuple[UUID, str]:
    """Seed an ``api_tokens`` row and return ``(token_id, clear_token)``.

    Only the SHA-256 digest is persisted (the clear token is returned to
    the test) — exactly as the admin mint endpoint does.
    """
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
            None,
            rate_limit,
            json.dumps([]),
            None,
        )
    finally:
        await conn.close()
    return token_id, minted.token


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
# App fixture: real api-server + a probe router behind the rate-limit gate
# ---------------------------------------------------------------------------
def _mount_probe_router(app: object) -> None:
    """Mount a ``/_probe/rate-limited`` endpoint behind the rate-limit gate.

    Phase B has no v1 surface yet, so this stands in for one: it depends on
    ``enforce_api_token_rate_limit`` (which both authenticates the token and
    enforces its budget, attaching the ``X-RateLimit-*`` headers to the
    response).
    """
    from api_server.auth.api_token_auth import (
        ApiTokenPrincipal,
        enforce_api_token_rate_limit,
    )
    from fastapi import APIRouter, Depends

    probe = APIRouter()

    @probe.get("/_probe/rate-limited")
    async def _probe(
        principal: ApiTokenPrincipal = Depends(enforce_api_token_rate_limit),
    ) -> dict[str, object]:
        return {"tenant_id": str(principal.tenant_id), "token_id": str(principal.token_id)}

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
    # Short window so the sliding behaviour is observable without long waits.
    monkeypatch.setenv("API_SERVER_API_TOKEN_RATE_LIMIT_WINDOW_SECONDS", str(_TEST_WINDOW_SECONDS))
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


def _hdr(token: str) -> dict[str, str]:
    return {"X-API-Token": token}


# ---------------------------------------------------------------------------
# Under the budget: requests pass and carry the rate-limit headers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_requests_under_limit_pass_with_headers(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id, token = await _seed_token(migrations_pg_dsn, tenant_id=tenant, name="t")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        for i in range(_TEST_BUDGET):
            resp = await client.get("/_probe/rate-limited", headers=_hdr(token))
            assert resp.status_code == 200, resp.text
            assert resp.headers["X-RateLimit-Limit"] == str(_TEST_BUDGET)
            # Remaining decrements: budget-1, budget-2, ... down to 0.
            assert resp.headers["X-RateLimit-Remaining"] == str(_TEST_BUDGET - 1 - i)
            assert int(resp.headers["X-RateLimit-Reset"]) > 0


# ---------------------------------------------------------------------------
# The (limit+1)th within the window -> 429 with Retry-After
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_over_limit_returns_429_with_retry_after(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id, token = await _seed_token(migrations_pg_dsn, tenant_id=tenant, name="t")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Spend the whole budget.
        for _ in range(_TEST_BUDGET):
            ok = await client.get("/_probe/rate-limited", headers=_hdr(token))
            assert ok.status_code == 200, ok.text
        # The next one is over budget.
        blocked = await client.get("/_probe/rate-limited", headers=_hdr(token))
        assert blocked.status_code == 429, blocked.text
        assert blocked.headers["X-RateLimit-Limit"] == str(_TEST_BUDGET)
        assert blocked.headers["X-RateLimit-Remaining"] == "0"
        retry_after = int(blocked.headers["Retry-After"])
        assert 1 <= retry_after <= _TEST_WINDOW_SECONDS


# ---------------------------------------------------------------------------
# The window slides: after it passes, requests succeed again
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_window_slides_requests_succeed_again(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id, token = await _seed_token(migrations_pg_dsn, tenant_id=tenant, name="t")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Saturate, then confirm the next is blocked.
        for _ in range(_TEST_BUDGET):
            await client.get("/_probe/rate-limited", headers=_hdr(token))
        blocked = await client.get("/_probe/rate-limited", headers=_hdr(token))
        assert blocked.status_code == 429, blocked.text

        # Let the whole window slide past the early hits (+ a small buffer).
        await asyncio.sleep(_TEST_WINDOW_SECONDS + 1)

        recovered = await client.get("/_probe/rate-limited", headers=_hdr(token))
        assert recovered.status_code == 200, recovered.text


# ---------------------------------------------------------------------------
# Each token has its OWN budget — saturating one does not throttle another
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_each_token_has_its_own_budget(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _id_a, token_a = await _seed_token(migrations_pg_dsn, tenant_id=tenant, name="a")
    _id_b, token_b = await _seed_token(migrations_pg_dsn, tenant_id=tenant, name="b")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Saturate token A and push it over its budget.
        for _ in range(_TEST_BUDGET):
            await client.get("/_probe/rate-limited", headers=_hdr(token_a))
        blocked_a = await client.get("/_probe/rate-limited", headers=_hdr(token_a))
        assert blocked_a.status_code == 429, blocked_a.text

        # Token B (same tenant) still has its full budget.
        ok_b = await client.get("/_probe/rate-limited", headers=_hdr(token_b))
        assert ok_b.status_code == 200, ok_b.text
        assert ok_b.headers["X-RateLimit-Remaining"] == str(_TEST_BUDGET - 1)


# ---------------------------------------------------------------------------
# Cross-tenant: a tenant-A token being throttled never spends tenant-B's
# budget (each token id keys its own window; ids are unique across tenants).
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_rate_limit_is_isolated_per_tenant(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    _id_a, token_a = await _seed_token(migrations_pg_dsn, tenant_id=tenant_a, name="alpha-token")
    _id_b, token_b = await _seed_token(migrations_pg_dsn, tenant_id=tenant_b, name="bravo-token")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Saturate + over-spend tenant A's token.
        for _ in range(_TEST_BUDGET):
            await client.get("/_probe/rate-limited", headers=_hdr(token_a))
        blocked_a = await client.get("/_probe/rate-limited", headers=_hdr(token_a))
        assert blocked_a.status_code == 429, blocked_a.text

        # Tenant B's token is untouched — full budget, and it authenticates
        # strictly as tenant B.
        ok_b = await client.get("/_probe/rate-limited", headers=_hdr(token_b))
        assert ok_b.status_code == 200, ok_b.text
        assert ok_b.json()["tenant_id"] == str(tenant_b)
        assert ok_b.headers["X-RateLimit-Remaining"] == str(_TEST_BUDGET - 1)
