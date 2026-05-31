"""Integration tests for SCIM 2.0 user provisioning (Plan 08 task_08_08).

SCIM is ADDED ALONGSIDE the interactive auth (local login + OIDC + SAML),
as a machine-to-machine provisioning channel an IdP drives with a
per-tenant bearer token (``scim_tokens`` table) — never a JWT. These
tests exercise the full ``/scim/v2/Users`` surface offline (no real IdP);
the SCIM token is seeded directly as its SHA-256 digest.

Coverage:

  * POST create -> user + active membership appear; GET by id returns it.
  * GET list (no filter) and GET list with ``filter=userName eq`` work.
  * PUT replace updates the mapped attributes.
  * PATCH ``active=false`` deprovisions: the membership goes inactive AND
    the user's live session in the tenant is revoked (access cut now).
  * DELETE deprovisions the same way (soft-delete membership + revoke).
  * bad / missing token -> 401 (SCIM-shaped error).
  * cross-tenant (@pytest.mark.cross_tenant): a token for tenant A cannot
    read or mutate tenant B's users; SCIM provisioning under A's token
    lands only in tenant A.
  * SCIM token CRUD (mint/list/revoke) via the tenant_admin UI; a revoked
    token authenticates nothing.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.scim.tokens import generate_scim_token, hash_scim_token, token_prefix
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_SCIM_CONTENT_TYPE = "application/scim+json"
_SENTINEL_HASH = "!sso-no-local-login!"


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


async def _seed_scim_token(dsn: str, *, tenant_id: UUID, token: str) -> UUID:
    """Insert a SCIM token row (only its digest, never the clear value)."""
    token_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO scim_tokens (id, tenant_id, token_hash, token_prefix, description)
            VALUES ($1, $2, $3, $4, $5)
            """,
            token_id,
            tenant_id,
            hash_scim_token(token),
            token_prefix(token),
            "test token",
        )
    finally:
        await conn.close()
    return token_id


async def _seed_user_with_session(
    dsn: str, redis_url: str, *, tenant_id: UUID, email: str
) -> tuple[UUID, str]:
    """Seed a user + active membership + a LIVE Redis session, returning
    ``(user_id, jwt)`` so a test can prove the session is revoked on
    deprovisioning."""
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO users (id, email, password_hash, full_name, is_system_admin,
                               is_sso_provisioned)
            VALUES ($1, $2, $3, $4, false, true)
            """,
            user_id,
            email,
            _SENTINEL_HASH,
            "Seeded SCIM User",
        )
        await conn.execute(
            """
            INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active)
            VALUES ($1, $2, $3, 'tenant_user', true)
            """,
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        store = SessionStore(redis)
        await store.create(session_id, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    finally:
        await redis.aclose()
    jwt = encode_jwt(user_id=user_id, session_id=session_id, tenant_id=tenant_id)
    return user_id, jwt


async def _membership_row(dsn: str, *, tenant_id: UUID, user_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            """
            SELECT is_active, external_id, deleted_at
              FROM user_org_memberships
             WHERE tenant_id = $1 AND user_id = $2
            """,
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()


async def _count_memberships(dsn: str, *, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM user_org_memberships WHERE tenant_id = $1 AND deleted_at IS NULL",
            tenant_id,
        )
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE scim_tokens, sso_configurations, user_org_memberships, "
            "organizations, users RESTART IDENTITY CASCADE"
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


def _scim_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": _SCIM_CONTENT_TYPE}


def _user_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "worker@acme.example.com",
        "externalId": "idp-ext-123",
        "displayName": "Worker Person",
        "emails": [{"value": "worker@acme.example.com", "primary": True, "type": "work"}],
        "active": True,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Create -> user appears; GET by id returns it
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_user_then_get(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant, token=token)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/scim/v2/Users", json=_user_body(), headers=_scim_headers(token))
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["userName"] == "worker@acme.example.com"
        assert created["externalId"] == "idp-ext-123"
        assert created["active"] is True
        assert created["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:User"]
        assert created["meta"]["resourceType"] == "User"
        user_id = created["id"]

        # GET by id returns the same resource.
        got = await client.get(f"/scim/v2/Users/{user_id}", headers=_scim_headers(token))
        assert got.status_code == 200, got.text
        assert got.json()["id"] == user_id
        assert got.json()["userName"] == "worker@acme.example.com"

    # The membership landed in the tenant, active, with the IdP external id.
    row = await _membership_row(migrations_pg_dsn, tenant_id=tenant, user_id=UUID(user_id))
    assert row is not None
    assert row["is_active"] is True
    assert row["external_id"] == "idp-ext-123"


# ---------------------------------------------------------------------------
# Duplicate create in same tenant -> 409 uniqueness
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_duplicate_conflicts(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant, token=token)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        first = await client.post("/scim/v2/Users", json=_user_body(), headers=_scim_headers(token))
        assert first.status_code == 201, first.text
        second = await client.post(
            "/scim/v2/Users", json=_user_body(), headers=_scim_headers(token)
        )
        assert second.status_code == 409, second.text
        assert second.json()["scimType"] == "uniqueness"

    assert await _count_memberships(migrations_pg_dsn, tenant_id=tenant) == 1


# ---------------------------------------------------------------------------
# List + filter by userName
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_and_filter_by_username(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant, token=token)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        await client.post(
            "/scim/v2/Users",
            json=_user_body(
                userName="alice@acme.example.com",
                externalId="a",
                emails=[{"value": "alice@acme.example.com", "primary": True}],
            ),
            headers=_scim_headers(token),
        )
        await client.post(
            "/scim/v2/Users",
            json=_user_body(
                userName="bob@acme.example.com",
                externalId="b",
                emails=[{"value": "bob@acme.example.com", "primary": True}],
            ),
            headers=_scim_headers(token),
        )

        # No filter -> both.
        all_resp = await client.get("/scim/v2/Users", headers=_scim_headers(token))
        assert all_resp.status_code == 200, all_resp.text
        body = all_resp.json()
        assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
        assert body["totalResults"] == 2
        assert {r["userName"] for r in body["Resources"]} == {
            "alice@acme.example.com",
            "bob@acme.example.com",
        }

        # Filter userName eq -> exactly one.
        filt = await client.get(
            "/scim/v2/Users",
            params={"filter": 'userName eq "alice@acme.example.com"'},
            headers=_scim_headers(token),
        )
        assert filt.status_code == 200, filt.text
        fbody = filt.json()
        assert fbody["totalResults"] == 1
        assert fbody["Resources"][0]["userName"] == "alice@acme.example.com"

        # Filter for a non-existent user -> empty.
        none = await client.get(
            "/scim/v2/Users",
            params={"filter": 'userName eq "nobody@acme.example.com"'},
            headers=_scim_headers(token),
        )
        assert none.status_code == 200, none.text
        assert none.json()["totalResults"] == 0


# ---------------------------------------------------------------------------
# PUT replace updates mapped attributes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_replace_updates_attributes(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant, token=token)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        created = await client.post(
            "/scim/v2/Users", json=_user_body(), headers=_scim_headers(token)
        )
        user_id = created.json()["id"]

        replaced = await client.put(
            f"/scim/v2/Users/{user_id}",
            json=_user_body(displayName="Renamed Worker", externalId="idp-ext-999"),
            headers=_scim_headers(token),
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["displayName"] == "Renamed Worker"
        assert replaced.json()["externalId"] == "idp-ext-999"

    row = await _membership_row(migrations_pg_dsn, tenant_id=tenant, user_id=UUID(user_id))
    assert row is not None
    assert row["external_id"] == "idp-ext-999"


# ---------------------------------------------------------------------------
# PATCH active=false -> access revoked (membership inactive + session gone)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_patch_active_false_revokes_access(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant, token=token)
    # Seed a user with a LIVE session so we can prove it is revoked.
    user_id, jwt = await _seed_user_with_session(
        migrations_pg_dsn, test_redis_url, tenant_id=tenant, email="seeded@acme.example.com"
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # The seeded session is live: /auth/me succeeds.
        me_before = await client.get("/auth/me", headers={"Authorization": f"Bearer {jwt}"})
        assert me_before.status_code == 200, me_before.text

        patch = await client.patch(
            f"/scim/v2/Users/{user_id}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "active", "value": False}],
            },
            headers=_scim_headers(token),
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["active"] is False

        # Access revoked: the session is gone -> 401.
        me_after = await client.get("/auth/me", headers={"Authorization": f"Bearer {jwt}"})
        assert me_after.status_code == 401, me_after.text

    row = await _membership_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    assert row["is_active"] is False


# ---------------------------------------------------------------------------
# DELETE deprovisions (soft-delete membership + revoke session)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_deprovisions(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant, token=token)
    user_id, jwt = await _seed_user_with_session(
        migrations_pg_dsn, test_redis_url, tenant_id=tenant, email="seeded@acme.example.com"
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        deleted = await client.delete(f"/scim/v2/Users/{user_id}", headers=_scim_headers(token))
        assert deleted.status_code == 204, deleted.text

        # Session revoked -> 401.
        me_after = await client.get("/auth/me", headers={"Authorization": f"Bearer {jwt}"})
        assert me_after.status_code == 401, me_after.text

        # GET now 404s (membership soft-deleted).
        got = await client.get(f"/scim/v2/Users/{user_id}", headers=_scim_headers(token))
        assert got.status_code == 404, got.text

    row = await _membership_row(migrations_pg_dsn, tenant_id=tenant, user_id=user_id)
    assert row is not None
    assert row["is_active"] is False
    assert row["deleted_at"] is not None


# ---------------------------------------------------------------------------
# Bad / missing token -> 401
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_and_bad_token_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    token = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant, token=token)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # No Authorization header.
        no_auth = await client.get("/scim/v2/Users")
        assert no_auth.status_code == 401, no_auth.text
        assert no_auth.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]

        # Unknown token.
        bad = await client.get(
            "/scim/v2/Users", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert bad.status_code == 401, bad.text

        # Malformed scheme.
        malformed = await client.get("/scim/v2/Users", headers={"Authorization": token})
        assert malformed.status_code == 401, malformed.text

        # Create with bad token -> 401, no row created.
        create_bad = await client.post(
            "/scim/v2/Users",
            json=_user_body(),
            headers={"Authorization": "Bearer wrong"},
        )
        assert create_bad.status_code == 401, create_bad.text

    assert await _count_memberships(migrations_pg_dsn, tenant_id=tenant) == 0


# ---------------------------------------------------------------------------
# Cross-tenant: a token for tenant A cannot touch tenant B
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_token_a_cannot_touch_tenant_b(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    token_a = generate_scim_token()
    token_b = generate_scim_token()
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant_a, token=token_a)
    await _seed_scim_token(migrations_pg_dsn, tenant_id=tenant_b, token=token_b)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Create a user in tenant B with B's token.
        created_b = await client.post(
            "/scim/v2/Users", json=_user_body(), headers=_scim_headers(token_b)
        )
        assert created_b.status_code == 201, created_b.text
        b_user_id = created_b.json()["id"]

        # Token A's list must NOT see B's user.
        list_a = await client.get("/scim/v2/Users", headers=_scim_headers(token_a))
        assert list_a.status_code == 200, list_a.text
        assert list_a.json()["totalResults"] == 0

        # Token A cannot GET B's user by id (RLS scopes it to A -> 404).
        get_a = await client.get(f"/scim/v2/Users/{b_user_id}", headers=_scim_headers(token_a))
        assert get_a.status_code == 404, get_a.text

        # Token A cannot deprovision B's user (404, not a silent success).
        del_a = await client.delete(f"/scim/v2/Users/{b_user_id}", headers=_scim_headers(token_a))
        assert del_a.status_code == 404, del_a.text

    # B's user is untouched: still an active membership in tenant B only.
    assert await _count_memberships(migrations_pg_dsn, tenant_id=tenant_a) == 0
    assert await _count_memberships(migrations_pg_dsn, tenant_id=tenant_b) == 1
    b_row = await _membership_row(migrations_pg_dsn, tenant_id=tenant_b, user_id=UUID(b_user_id))
    assert b_row is not None
    assert b_row["is_active"] is True
    assert b_row["deleted_at"] is None


# ---------------------------------------------------------------------------
# Token CRUD via the tenant_admin UI + revoked token authenticates nothing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_token_mint_list_revoke(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")

    # Seed a tenant_admin user + a JWT session bound to the tenant.
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    admin_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) "
            "VALUES ($1, $2, $3, false)",
            admin_id,
            "admin@acme.example.com",
            "x",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, 'tenant_admin', true)",
            uuid4(),
            tenant,
            admin_id,
        )
    finally:
        await conn.close()

    from tests.integration.conftest import TEST_REDIS_URL

    session_id = uuid7()
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=admin_id, tenant_id=tenant, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    admin_jwt = encode_jwt(user_id=admin_id, session_id=session_id, tenant_id=tenant)
    auth = {"Authorization": f"Bearer {admin_jwt}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Mint a token: the clear value comes back exactly once.
        mint = await client.post(
            "/auth/sso/scim/tokens", json={"description": "Okta prod"}, headers=auth
        )
        assert mint.status_code == 201, mint.text
        clear_token = mint.json()["token"]
        token_id = mint.json()["id"]
        assert mint.json()["token_prefix"] == clear_token[:8]

        # List never returns the clear token.
        listing = await client.get("/auth/sso/scim/tokens", headers=auth)
        assert listing.status_code == 200, listing.text
        assert len(listing.json()) == 1
        assert "token" not in listing.json()[0]

        # The minted token authenticates a SCIM call.
        ok = await client.get("/scim/v2/Users", headers=_scim_headers(clear_token))
        assert ok.status_code == 200, ok.text

        # Revoke it -> SCIM calls now 401.
        revoke = await client.delete(f"/auth/sso/scim/tokens/{token_id}", headers=auth)
        assert revoke.status_code == 204, revoke.text
        after = await client.get("/scim/v2/Users", headers=_scim_headers(clear_token))
        assert after.status_code == 401, after.text
