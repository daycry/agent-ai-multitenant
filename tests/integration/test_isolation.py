"""Cross-tenant isolation tests.

End-to-end proof that:

  1. The FastAPI `get_tenant_session` dependency wires the JWT's
     `tid` claim into `SET LOCAL app.tenant_id` before any query runs.
  2. PostgreSQL RLS then refuses to surface rows belonging to other
     tenants — even when the requester knows their UUIDs and the
     application code does NOT add a WHERE filter.

We seed two tenants (A and B) plus two memberships each, then call
GET /me/memberships as a user from tenant A and confirm only tenant
A's rows come back. Symmetric check for tenant B.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers — seed two tenants via the BYPASSRLS migrations role.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    """Insert two tenants and their users + memberships. Returns the ids."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    membership_a = uuid4()
    membership_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        # Clean slate — earlier tests may have left rows behind.
        await conn.execute("TRUNCATE user_org_memberships, organizations RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE users RESTART IDENTITY CASCADE")

        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            tenant_b,
            "Tenant B",
            "tenant-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@a.test",
            "argon2-placeholder",
            user_b,
            "bob@b.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            membership_a,
            tenant_a,
            user_a,
            "tenant_admin",
            membership_b,
            tenant_b,
            user_b,
            "tenant_admin",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "membership_a": membership_a,
        "membership_b": membership_b,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Build a FastAPI app whose engine talks as the NOBYPASSRLS app_user.

    Steps:
      1. Run alembic upgrade head to ensure schema + RLS.
      2. Point api_server.config.Settings at the test DB URL via env.
      3. Clear the cached engine so the next get_engine() picks it up.
      4. Build a fresh FastAPI app instance.
      5. On teardown, dispose the engine.
    """
    command.upgrade(alembic_config, "head")

    # The default privileges configured in conftest._drop_create_db only
    # apply to tables created *afterwards*. Migrations may have run
    # already (e.g. by an earlier test), so retro-grant DML on the live
    # tables before the app starts querying them as app_user.
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    # Rebuild the settings cache so the new env vars take effect.
    from api_server.config import get_settings

    get_settings.cache_clear()

    from api_server.db.session import reset_engine_cache

    reset_engine_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        # NOTE: we deliberately do NOT call `engine.dispose()` here.
        # The engine's connections were created inside pytest-asyncio's
        # event loop; calling dispose() via `asyncio.run()` in teardown
        # would spawn a new loop and asyncpg's proactor transports crash
        # mid-close on Windows. Letting the engine GC at process exit is
        # safe for tests.
        reset_engine_cache()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_tenant_isolation(
    configured_app,
    migrations_pg_dsn: str,
) -> None:
    """User A's JWT must see only tenant A's memberships, never B's."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.auth.jwt import encode_jwt

    token_a = encode_jwt(user_id=seeded["user_a"], tenant_id=seeded["tenant_a"])
    token_b = encode_jwt(user_id=seeded["user_b"], tenant_id=seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp_a = await client.get(
            "/me/memberships",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp_b = await client.get(
            "/me/memberships",
            headers={"Authorization": f"Bearer {token_b}"},
        )

    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    body_a = resp_a.json()
    body_b = resp_b.json()

    # Tenant A sees exactly one membership — its own.
    assert len(body_a) == 1
    assert UUID(body_a[0]["id"]) == seeded["membership_a"]
    assert UUID(body_a[0]["tenant_id"]) == seeded["tenant_a"]

    # Tenant B sees exactly one membership — its own.
    assert len(body_b) == 1
    assert UUID(body_b[0]["id"]) == seeded["membership_b"]
    assert UUID(body_b[0]["tenant_id"]) == seeded["tenant_b"]

    # Neither tenant ever observes the other's id.
    assert all(
        UUID(row["tenant_id"]) != seeded["tenant_b"] for row in body_a
    ), "tenant A should NEVER see tenant B rows"
    assert all(
        UUID(row["tenant_id"]) != seeded["tenant_a"] for row in body_b
    ), "tenant B should NEVER see tenant A rows"


@pytest.mark.asyncio
async def test_missing_authorization_header_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/me/memberships")
    assert resp.status_code == 401
    assert "missing" in resp.text.lower() or "unauthorized" in resp.text.lower()


@pytest.mark.asyncio
async def test_malformed_bearer_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/me/memberships",
            headers={"Authorization": "NotBearer token"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_jwt_signature_is_401(configured_app) -> None:
    # Token signed with a different secret.
    from datetime import datetime, timedelta

    from jose import jwt as jose_jwt

    bad_token = jose_jwt.encode(
        {
            "sub": str(uuid4()),
            "tid": str(uuid4()),
            "iat": int(datetime.now(tz=UTC).timestamp()),
            "exp": int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp()),
        },
        "WRONG-SECRET",
        algorithm="HS256",
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/me/memberships",
            headers={"Authorization": f"Bearer {bad_token}"},
        )
    assert resp.status_code == 401
