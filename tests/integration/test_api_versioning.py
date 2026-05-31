"""Integration tests for v1 API versioning + usage tracking (task_13_07).

Plan 13 Decisiones Clave: the version lives in the PATH (``/api/v1``); the
``X-API-Version`` header is an OPTIONAL pin/observe signal layered on top.
This suite proves the :func:`enforce_api_version` router-level dependency
(composed WITH the Fase A ``X-API-Token`` auth, never replacing it):

  * a v1 request WITHOUT the header defaults to ``v1``, is tracked, and the
    response carries ``X-API-Version: v1``;
  * a request WITH ``X-API-Version: v1`` is accepted + tracked;
  * an unsupported version pin -> a clean 400;
  * the per-version Redis usage counter increments per successful request;
  * a tracking-backend hiccup (Redis ``INCR`` raising) does NOT fail the
    request — usage is best-effort observability;
  * tenant/token scoping is preserved (the Fase A auth still gates: no
    token -> 401), so versioning never weakens isolation.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15. The
``configured_app`` fixture is shared from :mod:`tests.integration.conftest`;
the DB seed helpers are reused from :mod:`tests.integration.test_api_v1_endpoints`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import redis.asyncio as aioredis
from api_server.routers.api_v1._versioning import (
    API_VERSION_HEADER,
    SERVED_VERSION,
    version_usage_key,
)
from httpx import ASGITransport, AsyncClient

from tests.integration.test_api_v1_endpoints import (
    _hdr,
    _seed_tenant,
    _seed_token,
    _truncate_all,
)

pytestmark = pytest.mark.integration

# ``configured_app`` (the real wired api-server) is provided by
# tests/integration/conftest.py and shared with the v1 endpoint suite.


def _today_key() -> str:
    return version_usage_key(SERVED_VERSION, day=datetime.now(tz=UTC).strftime("%Y%m%d"))


async def _usage_count(redis_url: str) -> int:
    """Read the served version's usage counter for today (0 if absent)."""
    client: aioredis.Redis = aioredis.Redis.from_url(redis_url, decode_responses=True)
    try:
        raw = await client.get(_today_key())
    finally:
        await client.aclose()
    return int(raw) if raw is not None else 0


# ===========================================================================
# No header -> default v1, tracked, response carries X-API-Version: v1
# ===========================================================================
@pytest.mark.asyncio
async def test_no_header_defaults_to_v1_and_is_tracked(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="reader", scopes=["read"]
    )

    before = await _usage_count(test_redis_url)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/v1/projects", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    # The served version is advertised back even though no header was sent.
    assert resp.headers[API_VERSION_HEADER] == SERVED_VERSION
    # ...and the request was tracked.
    assert await _usage_count(test_redis_url) == before + 1


# ===========================================================================
# Explicit X-API-Version: v1 -> accepted + tracked
# ===========================================================================
@pytest.mark.asyncio
async def test_matching_version_header_accepted_and_tracked(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="reader", scopes=["read"]
    )

    before = await _usage_count(test_redis_url)
    headers = {**_hdr(token), API_VERSION_HEADER: SERVED_VERSION}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/v1/projects", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers[API_VERSION_HEADER] == SERVED_VERSION
    assert await _usage_count(test_redis_url) == before + 1


# ===========================================================================
# Unsupported version pin -> clean 400 (and not tracked)
# ===========================================================================
@pytest.mark.asyncio
async def test_unsupported_version_header_400(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="reader", scopes=["read"]
    )

    before = await _usage_count(test_redis_url)
    headers = {**_hdr(token), API_VERSION_HEADER: "v2"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/v1/projects", headers=headers)
    assert resp.status_code == 400, resp.text
    # Clean message: names the served version + supported set, never echoes
    # the rejected value as if it were honoured.
    detail = resp.json()["detail"]
    assert API_VERSION_HEADER in detail
    assert SERVED_VERSION in detail
    # An unsupported pin is rejected before tracking, so the counter is flat.
    assert await _usage_count(test_redis_url) == before


# ===========================================================================
# The per-version usage counter increments per successful request
# ===========================================================================
@pytest.mark.asyncio
async def test_usage_counter_increments_per_request(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="reader", scopes=["read"]
    )

    before = await _usage_count(test_redis_url)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        for _ in range(3):
            resp = await client.get("/api/v1/projects", headers=_hdr(token))
            assert resp.status_code == 200, resp.text
    assert await _usage_count(test_redis_url) == before + 3


# ===========================================================================
# A tracking-backend hiccup does NOT fail the request
# ===========================================================================
class _BrokenIncrRedis:
    """Delegates everything to a real client EXCEPT INCR/EXPIRE, which raise.

    Lets the Fase A auth (GET/SET on the resolution cache, sliding-window
    rate limit) keep working while the version-tracking INCR fails, so the
    test exercises ONLY the tracking-failure swallow path.
    """

    def __init__(self, inner: aioredis.Redis) -> None:
        self._inner = inner

    async def incr(self, *_args: object, **_kwargs: object) -> int:
        raise ConnectionError("simulated tracking backend hiccup")

    async def expire(self, *_args: object, **_kwargs: object) -> bool:
        raise ConnectionError("simulated tracking backend hiccup")

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_tracking_failure_does_not_fail_request(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _tid, token = await _seed_token(
        migrations_pg_dsn, tenant_id=tenant, name="reader", scopes=["read"]
    )

    from api_server.auth.deps import get_redis

    real = aioredis.Redis.from_url(test_redis_url, decode_responses=True)

    def _broken_redis() -> object:
        return _BrokenIncrRedis(real)

    configured_app.dependency_overrides[get_redis] = _broken_redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=configured_app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/api/v1/projects", headers=_hdr(token))
        # INCR raised inside tracking, but the request still succeeds and the
        # served version is still advertised.
        assert resp.status_code == 200, resp.text
        assert resp.headers[API_VERSION_HEADER] == SERVED_VERSION
    finally:
        configured_app.dependency_overrides.pop(get_redis, None)
        await real.aclose()


# ===========================================================================
# Tenant/token scoping preserved: versioning never weakens the Fase A auth
# ===========================================================================
@pytest.mark.asyncio
async def test_versioning_preserves_auth_no_token_401(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # No X-API-Token: the Fase A auth still rejects, even with a valid
        # (default) version. Versioning composes WITH auth, never bypasses it.
        resp = await client.get("/api/v1/projects", headers={API_VERSION_HEADER: SERVED_VERSION})
    assert resp.status_code == 401, resp.text
