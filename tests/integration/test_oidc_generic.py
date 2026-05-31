"""Integration tests for the generic OIDC SSO flow (Plan 08 task_08_01).

No real IdP: a :class:`httpx.MockTransport` serves a complete fake
OpenID Provider — discovery (`.well-known/openid-configuration`), JWKS,
token endpoint, and userinfo — and signs the ID token with an in-test
RSA key. The api-server's OIDC flow runs end-to-end fully offline.

Coverage:

  * happy callback → JIT-creates the user + an active membership, mints
    a live Redis session + a JWT that `get_principal` accepts.
  * happy callback for an EXISTING user → looked up, not duplicated.
  * bad ``state`` → 400 (anti-CSRF tripwire).
  * bad ``nonce`` (ID token replay) → 400.
  * disabled config → login 404; missing config → login 404.
  * cross-tenant isolation (@pytest.mark.cross_tenant): tenant A's SSO
    config never resolves for tenant B's login URL.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the session fixture creates a throwaway DB and flushes Redis 15.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from alembic import command
from api_server.auth.sso.secrets import encrypt_client_secret
from httpx import ASGITransport, AsyncClient
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fake IdP constants
# ---------------------------------------------------------------------------
_ISSUER = "https://idp.example.test"
_CLIENT_ID = "acme-oidc-client"
_CLIENT_SECRET = "super-secret-oidc-value"
_AUTHZ = f"{_ISSUER}/authorize"
_TOKEN = f"{_ISSUER}/token"
_USERINFO = f"{_ISSUER}/userinfo"
_JWKS = f"{_ISSUER}/jwks"
_KID = "test-key-1"

# One RSA key pair for the whole module — the IdP signs with the private
# half, the api-server verifies against the public half from /jwks.
_SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": _KID}, private=True)


def _id_token(*, nonce: str, sub: str = "idp-subject-123", aud: str = _CLIENT_ID) -> str:
    header = {"alg": "RS256", "kid": _KID}
    claims = {
        "iss": _ISSUER,
        "aud": aud,
        "sub": sub,
        "nonce": nonce,
        "email": "Worker@Acme.test",  # mixed case -> flow lowercases it
        "name": "Worker Person",
    }
    return joserfc_jwt.encode(header, claims, _SIGNING_KEY)


class _FakeIdP:
    """Stateful mock OpenID Provider.

    ``last_nonce`` captures the nonce the api-server sent on the authorize
    redirect so the token endpoint can echo it back inside the ID token —
    mimicking a real IdP. ``force_nonce`` overrides it to simulate a
    replay / mismatch.
    """

    def __init__(self) -> None:
        self.last_nonce: str | None = None
        self.force_nonce: str | None = None
        self.userinfo_sub = "idp-subject-123"

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
            # The api-server never actually GETs the authorize endpoint
            # (it only builds the URL), but capture the nonce if it does.
            self.last_nonce = dict(request.url.params).get("nonce")
            return httpx.Response(302, headers={"location": "ignored"})
        if url == _JWKS:
            return httpx.Response(200, json={"keys": [_SIGNING_KEY.as_dict(private=False)]})
        if url == _TOKEN:
            nonce = self.force_nonce or self.last_nonce or "missing-nonce"
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-access-token",
                    "token_type": "Bearer",
                    "id_token": _id_token(nonce=nonce, sub=self.userinfo_sub),
                },
            )
        if url == _USERINFO:
            return httpx.Response(
                200,
                json={
                    "sub": self.userinfo_sub,
                    "email": "Worker@Acme.test",
                    "name": "Worker Person",
                },
            )
        return httpx.Response(404, json={"error": "not_found"})  # pragma: no cover


# ---------------------------------------------------------------------------
# DB seed helpers
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


async def _seed_oidc_config(
    dsn: str,
    *,
    tenant_id: UUID,
    enabled: bool = True,
) -> None:
    """Insert an OIDC config row with the client secret Fernet-encrypted."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings)
            VALUES ($1, $2, 'oidc', 'Acme OIDC', $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            """,
            uuid4(),
            tenant_id,
            enabled,
            _ISSUER,
            _CLIENT_ID,
            encrypt_client_secret(_CLIENT_SECRET),
            json.dumps(["openid", "email", "profile"]),
            json.dumps({}),
        )
    finally:
        await conn.close()


async def _count_users_with_email(dsn: str, email: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE sso_configurations, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture: real api-server with a mocked IdP injected into the flow
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
    # No Vault in tests — secrets are Fernet-encrypted at rest.
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

    # Point the OIDC HTTP client at the fake IdP transport.
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


async def _do_login(client: AsyncClient, tenant_id: UUID) -> str:
    """Hit the login endpoint and return the ``state`` query param the
    server put on the IdP redirect."""
    resp = await client.get(f"/auth/sso/{tenant_id}/oidc/login")
    assert resp.status_code == 307, resp.text
    location = resp.headers["location"]
    params = dict(httpx.URL(location).params)
    return params["state"]


async def _capture_login_nonce(client: AsyncClient, tenant_id: UUID, idp: _FakeIdP) -> str:
    resp = await client.get(f"/auth/sso/{tenant_id}/oidc/login")
    assert resp.status_code == 307, resp.text
    location = resp.headers["location"]
    params = dict(httpx.URL(location).params)
    # The server stored this nonce server-side keyed by state; mirror it
    # into the fake IdP so the token endpoint echoes the matching value.
    idp.last_nonce = params["nonce"]
    return params["state"]


# ---------------------------------------------------------------------------
# Happy path — JIT provisioning
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_callback_creates_user_session_and_jwt(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        state = await _capture_login_nonce(client, tenant, idp)

        resp = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        token = body["access_token"]
        assert token

        # JWT is accepted by the live session check → GET /me works.
        me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        me_body = me.json()
        assert me_body["email"] == "worker@acme.test"
        assert me_body["active_tenant_id"] == str(tenant)
        # JIT membership with role tenant_user.
        roles = [m["role"] for m in me_body["memberships"] if m["tenant_id"] == str(tenant)]
        assert roles == ["tenant_user"]

    # Exactly one user row was created.
    assert await _count_users_with_email(migrations_pg_dsn, "worker@acme.test") == 1


@pytest.mark.asyncio
async def test_second_login_reuses_existing_user(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        for _ in range(2):
            state = await _capture_login_nonce(client, tenant, idp)
            resp = await client.get(
                "/auth/sso/oidc/callback",
                params={"code": "fake-auth-code", "state": state},
            )
            assert resp.status_code == 200, resp.text

    # No duplicate user despite two logins.
    assert await _count_users_with_email(migrations_pg_dsn, "worker@acme.test") == 1


# ---------------------------------------------------------------------------
# state / nonce validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_callback_bad_state_is_400(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        # Never started a login — the state is unknown.
        resp = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": "totally-bogus-state"},
        )
    assert resp.status_code == 400, resp.text
    assert "state" in resp.text.lower()


@pytest.mark.asyncio
async def test_callback_state_is_single_use(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        state = await _capture_login_nonce(client, tenant, idp)
        first = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
        assert first.status_code == 200, first.text
        # Replaying the same state must fail — single-use.
        replay = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
    assert replay.status_code == 400, replay.text


@pytest.mark.asyncio
async def test_callback_bad_nonce_is_400(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        state = await _do_login(client, tenant)
        # Force the IdP to mint an ID token with the WRONG nonce — replay
        # of a token captured from a different session.
        idp.force_nonce = "attacker-controlled-nonce"
        resp = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
    assert resp.status_code == 400, resp.text
    assert "oidc authentication failed" in resp.text.lower()


# ---------------------------------------------------------------------------
# disabled / missing config
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_disabled_config_is_404(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant, enabled=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/auth/sso/{tenant}/oidc/login")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_login_missing_config_is_404(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")  # no SSO config

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/auth/sso/{tenant}/oidc/login")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_a_config_does_not_resolve_for_tenant_b(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """Tenant A has an enabled OIDC config; tenant B does not. Tenant B's
    login URL must NOT find A's config — RLS scopes the lookup by
    ``app.tenant_id``, so A's row is invisible to B."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant_a)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        # Tenant A resolves fine.
        a_resp = await client.get(f"/auth/sso/{tenant_a}/oidc/login")
        assert a_resp.status_code == 307, a_resp.text

        # Tenant B (no config of its own) gets 404 — it cannot borrow A's.
        b_resp = await client.get(f"/auth/sso/{tenant_b}/oidc/login")
    assert b_resp.status_code == 404, b_resp.text


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_callback_provisions_into_state_tenant_only(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """The callback provisions the user into the tenant captured in the
    login ``state`` (tenant A), never some other tenant — the membership
    lands on A and B stays empty."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant_a)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        state = await _capture_login_nonce(client, tenant_a, idp)
        resp = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
        assert resp.status_code == 200, resp.text

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        a_count = await conn.fetchval(
            "SELECT count(*) FROM user_org_memberships WHERE tenant_id = $1", tenant_a
        )
        b_count = await conn.fetchval(
            "SELECT count(*) FROM user_org_memberships WHERE tenant_id = $1", tenant_b
        )
    finally:
        await conn.close()
    assert a_count == 1
    assert b_count == 0
