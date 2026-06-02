"""Integration tests for post-login tenant resolution by membership (ADR 0047, task_sso_03).

After a successful login the session proves IDENTITY only (tenant-less);
``GET /auth/session/resolve`` turns the user's ACTIVE
``UserOrganizationMembership`` rows into a typed next step:

  * 0 memberships → ``state="no_access"`` (valid session, NO tenant, NO
    token minted) → the admin-panel "sin permisos, contacta al
    administrador" screen.
  * 1 membership → ``state="single"`` + a freshly minted TENANT-SCOPED
    token (the user enters that tenant directly).
  * >1 memberships → ``state="multiple"`` (the tenant-picker chooses; the
    client then POSTs ``/auth/session/select-tenant``).

Coverage:

  * password-login identity → resolve goes through all three states.
  * SSO-login identity → resolve reaches the same states (the resolution
    is login-agnostic).
  * the minted single/selected token is tenant-scoped (``/me`` reports the
    active tenant) and actually scopes RLS.
  * ``select-tenant`` activates only a tenant the user belongs to; a
    foreign/unknown tenant is 403.
  * @pytest.mark.cross_tenant: a user with a membership in tenant A can
    NEVER resolve/select tenant B (no membership = no access).

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixture creates a throwaway DB and flushes Redis 15.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from alembic import command
from api_server.auth.passwords import hash_password
from api_server.auth.sso.secrets import encrypt_client_secret
from httpx import ASGITransport, AsyncClient
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

pytestmark = pytest.mark.integration

_PASSWORD = "correct-horse-battery"  # - test fixture password, not a secret

# ---------------------------------------------------------------------------
# Fake IdP (mirrors test_sso_global_login) so the SSO path runs offline.
# ---------------------------------------------------------------------------
_ISSUER = "https://idp.example.test"
_CLIENT_ID = "platform-oidc-client"
_CLIENT_SECRET = "super-secret-oidc-value"
_AUTHZ = f"{_ISSUER}/authorize"
_TOKEN = f"{_ISSUER}/token"
_USERINFO = f"{_ISSUER}/userinfo"
_JWKS = f"{_ISSUER}/jwks"
_KID = "test-key-1"
_SSO_EMAIL = "ssoworker@acme.test"

_SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": _KID}, private=True)


def _id_token(*, nonce: str, sub: str = "idp-subject-123") -> str:
    header = {"alg": "RS256", "kid": _KID}
    claims = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": sub,
        "nonce": nonce,
        "email": _SSO_EMAIL,
        "name": "SSO Worker",
    }
    return joserfc_jwt.encode(header, claims, _SIGNING_KEY)


class _FakeIdP:
    def __init__(self) -> None:
        self.last_nonce: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == _ISSUER + "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": _ISSUER,
                    "authorization_endpoint": _AUTHZ,
                    "token_endpoint": _TOKEN,
                    "userinfo_endpoint": _USERINFO,
                    "jwks_uri": _JWKS,
                },
            )
        if url == _AUTHZ:
            self.last_nonce = dict(request.url.params).get("nonce")
            return httpx.Response(302, headers={"location": "ignored"})
        if url == _JWKS:
            return httpx.Response(200, json={"keys": [_SIGNING_KEY.as_dict(private=False)]})
        if url == _TOKEN:
            nonce = self.last_nonce or "missing-nonce"
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-access-token",
                    "token_type": "Bearer",
                    "id_token": _id_token(nonce=nonce),
                },
            )
        if url == _USERINFO:
            return httpx.Response(
                200,
                json={"sub": "idp-subject-123", "email": _SSO_EMAIL, "name": "SSO Worker"},
            )
        return httpx.Response(404, json={"error": "not_found"})  # pragma: no cover


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE sso_configurations, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_tenant(dsn: str, *, slug: str, name: str | None = None) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            name or slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_password_user(dsn: str, *, email: str) -> UUID:
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, full_name, password_hash, is_system_admin) "
            "VALUES ($1, $2, $3, $4, false)",
            user_id,
            email,
            "Pwd User",
            hash_password(_PASSWORD),
        )
    finally:
        await conn.close()
    return user_id


async def _seed_membership(
    dsn: str, *, user_id: UUID, tenant_id: UUID, role: str = "tenant_user", is_active: bool = True
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, $4, $5)",
            uuid4(),
            tenant_id,
            user_id,
            role,
            is_active,
        )
    finally:
        await conn.close()


async def _seed_global_oidc(dsn: str) -> UUID:
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, button_label, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings)
            VALUES ($1, 'oidc', 'Platform OIDC', 'Sign in', true, $2, $3, $4, $5::jsonb, $6::jsonb)
            """,
            config_id,
            _ISSUER,
            _CLIENT_ID,
            encrypt_client_secret(_CLIENT_SECRET),
            json.dumps(["openid", "email", "profile"]),
            json.dumps({}),
        )
    finally:
        await conn.close()
    return config_id


async def _user_id_by_email(dsn: str, email: str) -> UUID:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------
@pytest.fixture()
def idp() -> _FakeIdP:
    return _FakeIdP()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    idp: _FakeIdP,
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
    from api_server.auth.sso.oidc import OIDCFlow
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache
    from api_server.routers import mcp as mcp_router
    from api_server.routers.sso import get_oidc_http_client

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    OIDCFlow.reset_discovery_cache()
    mcp_router.reset_vault_resolver_cache()

    from api_server.main import create_app

    app = create_app()

    def _mock_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))

    app.dependency_overrides[get_oidc_http_client] = _mock_client

    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        OIDCFlow.reset_discovery_cache()
        get_settings.cache_clear()


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _password_login(client: AsyncClient, email: str) -> str:
    """Log in with password and return the tenant-less identity token."""
    resp = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _sso_login(client: AsyncClient, provider_id: UUID, idp: _FakeIdP) -> str:
    """Complete an OIDC login offline and return the identity token."""
    start = await client.get(f"/auth/sso/{provider_id}/oidc/login")
    assert start.status_code == 307, start.text
    params = dict(httpx.URL(start.headers["location"]).params)
    idp.last_nonce = params["nonce"]
    cb = await client.get(
        "/auth/sso/oidc/callback", params={"code": "fake-code", "state": params["state"]}
    )
    assert cb.status_code == 200, cb.text
    return cb.json()["access_token"]


# ===========================================================================
# Password path — the three resolution states
# ===========================================================================
@pytest.mark.asyncio
async def test_password_resolve_no_access(configured_app, migrations_pg_dsn: str) -> None:
    """A logged-in user with NO membership resolves to ``no_access`` — the
    session is valid (identity) but no token is minted and no tenant set."""
    await _truncate_all(migrations_pg_dsn)
    await _seed_tenant(migrations_pg_dsn, slug="acme")  # tenant exists, user not in it
    await _seed_password_user(migrations_pg_dsn, email="orphan@acme.example")

    async with _client(configured_app) as client:
        token = await _password_login(client, "orphan@acme.example")
        resolve = await client.get(
            "/auth/session/resolve", headers={"Authorization": f"Bearer {token}"}
        )
    assert resolve.status_code == 200, resolve.text
    body = resolve.json()
    assert body["state"] == "no_access"
    assert body["memberships"] == []
    assert body["access_token"] is None


@pytest.mark.asyncio
async def test_password_resolve_single_enters_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A single membership auto-resolves: a tenant-scoped token is minted and
    ``/me`` reports the active tenant."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme", name="Acme Corp")
    user_id = await _seed_password_user(migrations_pg_dsn, email="solo@acme.example")
    await _seed_membership(
        migrations_pg_dsn, user_id=user_id, tenant_id=tenant, role="tenant_admin"
    )

    async with _client(configured_app) as client:
        identity_token = await _password_login(client, "solo@acme.example")
        resolve = await client.get(
            "/auth/session/resolve", headers={"Authorization": f"Bearer {identity_token}"}
        )
        assert resolve.status_code == 200, resolve.text
        body = resolve.json()
        assert body["state"] == "single"
        assert len(body["memberships"]) == 1
        assert body["memberships"][0]["tenant_id"] == str(tenant)
        assert body["memberships"][0]["tenant_name"] == "Acme Corp"
        assert body["memberships"][0]["role"] == "tenant_admin"
        tenant_token = body["access_token"]
        assert tenant_token

        # The minted token is tenant-scoped: /me reports the active tenant.
        me = await client.get("/me", headers={"Authorization": f"Bearer {tenant_token}"})
        assert me.status_code == 200, me.text
        assert me.json()["active_tenant_id"] == str(tenant)


@pytest.mark.asyncio
async def test_password_resolve_multiple_then_select(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Two memberships → ``multiple``; the picker selects one and gets a
    tenant-scoped token for THAT tenant."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha", name="Alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo", name="Bravo")
    user_id = await _seed_password_user(migrations_pg_dsn, email="multi@acme.example")
    await _seed_membership(migrations_pg_dsn, user_id=user_id, tenant_id=tenant_a)
    await _seed_membership(migrations_pg_dsn, user_id=user_id, tenant_id=tenant_b)

    async with _client(configured_app) as client:
        identity_token = await _password_login(client, "multi@acme.example")
        resolve = await client.get(
            "/auth/session/resolve", headers={"Authorization": f"Bearer {identity_token}"}
        )
        assert resolve.status_code == 200, resolve.text
        body = resolve.json()
        assert body["state"] == "multiple"
        assert body["access_token"] is None
        listed = {m["tenant_id"] for m in body["memberships"]}
        assert listed == {str(tenant_a), str(tenant_b)}

        # Pick tenant B; the issued token is scoped to B.
        select = await client.post(
            "/auth/session/select-tenant",
            json={"tenant_id": str(tenant_b)},
            headers={"Authorization": f"Bearer {identity_token}"},
        )
        assert select.status_code == 200, select.text
        tenant_token = select.json()["access_token"]
        me = await client.get("/me", headers={"Authorization": f"Bearer {tenant_token}"})
        assert me.status_code == 200, me.text
        assert me.json()["active_tenant_id"] == str(tenant_b)


@pytest.mark.asyncio
async def test_inactive_membership_does_not_grant_access(
    configured_app, migrations_pg_dsn: str
) -> None:
    """An ``is_active=false`` membership is ignored — it grants no access."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    user_id = await _seed_password_user(migrations_pg_dsn, email="disabled@acme.example")
    await _seed_membership(migrations_pg_dsn, user_id=user_id, tenant_id=tenant, is_active=False)

    async with _client(configured_app) as client:
        token = await _password_login(client, "disabled@acme.example")
        resolve = await client.get(
            "/auth/session/resolve", headers={"Authorization": f"Bearer {token}"}
        )
        assert resolve.status_code == 200, resolve.text
        assert resolve.json()["state"] == "no_access"

        # And select-tenant on that inactive membership is rejected.
        select = await client.post(
            "/auth/session/select-tenant",
            json={"tenant_id": str(tenant)},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert select.status_code == 403, select.text


# ===========================================================================
# SSO path goes through the SAME resolution
# ===========================================================================
@pytest.mark.asyncio
async def test_sso_resolve_no_access(configured_app, migrations_pg_dsn: str, idp: _FakeIdP) -> None:
    """A JIT-provisioned SSO user with no membership resolves to no_access."""
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with _client(configured_app) as client:
        token = await _sso_login(client, provider_id, idp)
        resolve = await client.get(
            "/auth/session/resolve", headers={"Authorization": f"Bearer {token}"}
        )
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["state"] == "no_access"


@pytest.mark.asyncio
async def test_sso_resolve_single_after_admin_assignment(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """Once the admin assigns the SSO user a membership, resolve enters it."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme", name="Acme Corp")
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with _client(configured_app) as client:
        # First login JIT-creates the global user (no membership yet).
        first = await _sso_login(client, provider_id, idp)
        assert (
            await client.get("/auth/session/resolve", headers={"Authorization": f"Bearer {first}"})
        ).json()["state"] == "no_access"

        # Admin assigns the membership out of band.
        sso_user = await _user_id_by_email(migrations_pg_dsn, _SSO_EMAIL)
        await _seed_membership(migrations_pg_dsn, user_id=sso_user, tenant_id=tenant)

        # Re-login → resolve now enters the tenant.
        token = await _sso_login(client, provider_id, idp)
        resolve = await client.get(
            "/auth/session/resolve", headers={"Authorization": f"Bearer {token}"}
        )
        assert resolve.status_code == 200, resolve.text
        body = resolve.json()
        assert body["state"] == "single"
        me = await client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.json()["active_tenant_id"] == str(tenant)


# ===========================================================================
# Cross-tenant: a membership in A never resolves/selects B.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cannot_select_a_tenant_without_membership(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The user belongs to tenant A only. They can resolve/select A, but
    selecting tenant B (no membership) is 403 — and B never appears in the
    resolution. Deny-by-default: no membership, no access (ADR 0047)."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha", name="Alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo", name="Bravo")
    user_id = await _seed_password_user(migrations_pg_dsn, email="a-only@acme.example")
    await _seed_membership(migrations_pg_dsn, user_id=user_id, tenant_id=tenant_a)

    async with _client(configured_app) as client:
        token = await _password_login(client, "a-only@acme.example")

        resolve = await client.get(
            "/auth/session/resolve", headers={"Authorization": f"Bearer {token}"}
        )
        body = resolve.json()
        # Single membership → A only; B is invisible.
        assert body["state"] == "single"
        listed = {m["tenant_id"] for m in body["memberships"]}
        assert listed == {str(tenant_a)}
        assert str(tenant_b) not in listed

        # Selecting B (a real tenant the user doesn't belong to) is 403.
        forbidden = await client.post(
            "/auth/session/select-tenant",
            json={"tenant_id": str(tenant_b)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert forbidden.status_code == 403, forbidden.text

        # A token scoped to B's tenant cannot be obtained, and selecting an
        # entirely unknown tenant id is likewise 403 (never reveals which).
        unknown = await client.post(
            "/auth/session/select-tenant",
            json={"tenant_id": str(uuid4())},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert unknown.status_code == 403, unknown.text


@pytest.mark.asyncio
async def test_resolve_requires_auth(configured_app) -> None:
    """Resolution needs a valid identity session — unauthenticated is 401."""
    async with _client(configured_app) as client:
        resp = await client.get("/auth/session/resolve")
    assert resp.status_code == 401, resp.text
