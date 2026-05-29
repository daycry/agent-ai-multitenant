"""Error-handling + observability hardening (Plan 06.14 task_06_14_10).

Regression coverage for three audit findings:

  - error-obs-logging-1 — request context never bound to logs. A
    `RequestContextMiddleware` now mints / propagates an X-Request-ID
    per request and binds it (+ user_id/tenant_id from the JWT) so every
    downstream log line is correlatable. We assert the echoed response
    header; the contextvar binding is covered by the dedicated unit test
    in tests/unit/test_request_context_middleware.py.
  - error-obs-logging-5 — no global exception handler. An unhandled
    error now returns a generic 500 body without leaking the exception
    or stack to the client.
  - error-obs-logging-3 — IntegrityError on duplicate KB name leaked
    `exc.orig` (SQLAlchemy internals) to the client. The endpoint now
    pre-checks the duplicate and returns a clean 409 whose body never
    contains the driver error.

Drives the FastAPI app against a real Postgres (RLS-enforced app_user
role) + Redis (test DB 15), mirroring test_kb_endpoints.py.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: two tenants, each with a tenant_admin user.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a-eh",
            tenant_b,
            "Tenant B",
            "tenant-b-eh",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-eh",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@eh.test",
            "argon2-placeholder",
            user_b,
            "bob@eh.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant_a,
            user_a,
            uuid4(),
            tenant_b,
            user_b,
        )
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
    }


# ---------------------------------------------------------------------------
# App fixture
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

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()

    # A test-only route that always blows up with a non-HTTP exception so
    # the global handler (error-obs-logging-5) can be exercised without
    # adding a boom endpoint to production code.
    async def _boom() -> None:
        raise RuntimeError("super secret internal detail 0xCAFEBABE")

    app.add_api_route("/_test/boom", _boom, methods=["GET"])

    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# error-obs-logging-3 — duplicate KB name returns a clean 409
# ===========================================================================
@pytest.mark.asyncio
async def test_duplicate_kb_name_returns_clean_409(configured_app, migrations_pg_dsn: str) -> None:
    """Second KB with the same name in the tenant -> 409 whose body does
    NOT leak the SQLAlchemy driver error (exc.orig / constraint name)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await client.post("/knowledge-bases", json={"name": "Dup KB"}, headers=headers)
        assert first.status_code == 201, first.text

        second = await client.post("/knowledge-bases", json={"name": "Dup KB"}, headers=headers)
        assert second.status_code == 409, second.text

        body = second.json()
        assert body["detail"] == "kb name already exists in tenant"
        # The pre-Plan-06.14 bug interpolated `exc.orig` into the detail.
        # Assert none of those SQLAlchemy / Postgres internals leak.
        leaked = body["detail"].lower()
        for needle in (
            "ix_knowledge_bases_tenant_name",  # index name
            "duplicate key",  # Postgres error text
            "uniqueviolation",  # asyncpg / SQLAlchemy class
            "detail:",  # asyncpg detail block
            "psycopg",
            "asyncpg",
        ):
            assert needle not in leaked


@pytest.mark.asyncio
async def test_kb_name_reusable_after_soft_delete(configured_app, migrations_pg_dsn: str) -> None:
    """The unique index is partial on `deleted_at IS NULL`, so the
    pre-check must not block reusing a name freed by a soft-delete."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/knowledge-bases", json={"name": "Recyclable"}, headers=headers
        )
        assert created.status_code == 201
        kb_id = created.json()["id"]

        deleted = await client.delete(f"/knowledge-bases/{kb_id}", headers=headers)
        assert deleted.status_code == 204

        # Same name is now free again.
        recreate = await client.post(
            "/knowledge-bases", json={"name": "Recyclable"}, headers=headers
        )
        assert recreate.status_code == 201, recreate.text
        assert recreate.json()["id"] != kb_id


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_same_kb_name_allowed_across_tenants(configured_app, migrations_pg_dsn: str) -> None:
    """The (tenant_id, name) uniqueness is per-tenant: the pre-check must
    only look at the caller's own tenant, so tenant B can create a KB
    with the same name tenant A already uses (and never sees A's row)."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        a = await client.post(
            "/knowledge-bases",
            json={"name": "Shared Name"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert a.status_code == 201, a.text

        # B uses the same name -> allowed (different tenant).
        b = await client.post(
            "/knowledge-bases",
            json={"name": "Shared Name"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b.status_code == 201, b.text
        assert b.json()["id"] != a.json()["id"]

        # B's listing only contains B's KB, never A's (RLS).
        b_list = await client.get(
            "/knowledge-bases", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert b_list.status_code == 200
        assert [r["id"] for r in b_list.json()] == [b.json()["id"]]


# ===========================================================================
# error-obs-logging-5 — global exception handler returns a generic 500
# ===========================================================================
@pytest.mark.asyncio
async def test_unhandled_error_returns_generic_500(configured_app) -> None:
    """A route that raises a bare RuntimeError -> 500 with a generic body;
    the exception message / stack never reaches the client."""
    async with AsyncClient(
        transport=ASGITransport(app=configured_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get("/_test/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal server error"}
    # The secret detail raised inside the route must not leak.
    assert "0xCAFEBABE" not in resp.text
    assert "RuntimeError" not in resp.text
    assert "super secret" not in resp.text.lower()


@pytest.mark.asyncio
async def test_unhandled_error_echoes_request_id(configured_app) -> None:
    """Even on the 500 path the response carries X-Request-ID so the
    operator can correlate the client report with the server log line."""
    async with AsyncClient(
        transport=ASGITransport(app=configured_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.get("/_test/boom")

    assert resp.status_code == 500
    assert resp.headers.get("x-request-id")


# ===========================================================================
# error-obs-logging-1 — request id propagation on the happy path
# ===========================================================================
@pytest.mark.asyncio
async def test_request_id_minted_when_absent(configured_app) -> None:
    """A request with no inbound X-Request-ID gets one minted + echoed."""
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    minted = resp.headers.get("x-request-id")
    assert minted
    # A minted id is a UUID.
    UUID(minted)


@pytest.mark.asyncio
async def test_inbound_request_id_is_propagated(configured_app) -> None:
    """An inbound X-Request-ID is honoured (echoed back unchanged) so a
    correlation id set by a reverse proxy survives end to end."""
    incoming = "trace-from-the-edge-123"
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/healthz", headers={"X-Request-ID": incoming})

    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == incoming
