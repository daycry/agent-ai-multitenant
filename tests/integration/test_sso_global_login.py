"""Integration tests for the GLOBAL SSO login flow (task_sso_02, ADR 0047).

Auth providers are platform-global now: login is keyed by the global
provider id (NOT a tenant), the OIDC callback / SAML ACS are global, the
single-use ``state`` / ``RelayState`` carries the PROVIDER that started
the flow, and the issued session proves IDENTITY only — a global user
WITHOUT an active tenant (``active_tenant_id`` is ``None``; tenant access
is resolved by membership in task_sso_03).

No real IdP: a :class:`httpx.MockTransport` serves a complete fake
OpenID Provider — discovery, JWKS, token, userinfo — and signs the ID
token with an in-test RSA key, so the whole OIDC flow runs offline.

Coverage:

  * ``GET /auth/sso/providers`` (PUBLIC) lists the enabled global
    providers with id / kind / display_name / button_label / login_url
    and exposes NO secret field (no client_secret / SP key / Vault ref).
  * login start by provider (``GET /auth/sso/{provider_id}/oidc/login``)
    works → 307 to the IdP with a ``state``.
  * the OIDC callback resolves the provider from the state, mints an
    IDENTITY session (tenant-less) + JIT-creates the global user, and the
    JWT is accepted by ``/me`` with ``active_tenant_id == None`` and NO
    membership (access is by admin assignment — ADR 0047).
  * bad / replayed ``state`` → 400 (anti-CSRF + single-use).
  * the OLD per-tenant routes ``/auth/sso/{tenant_id}/oidc|saml/login`` +
    the old per-tenant ACS are GONE (404).
  * the SAML login route is keyed by provider (404 for an unknown id;
    501 when the native xmlsec stack is absent on this node).
  * @pytest.mark.cross_tenant: the identity session carries no tenant, so
    the user reaches NO tenant's data without a membership.

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
from api_server.auth.sso.saml import saml_available
from api_server.auth.sso.secrets import encrypt_client_secret
from httpx import ASGITransport, AsyncClient
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fake IdP constants
# ---------------------------------------------------------------------------
_ISSUER = "https://idp.example.test"
_CLIENT_ID = "platform-oidc-client"
_CLIENT_SECRET = "super-secret-oidc-value"
_AUTHZ = f"{_ISSUER}/authorize"
_TOKEN = f"{_ISSUER}/token"
_USERINFO = f"{_ISSUER}/userinfo"
_JWKS = f"{_ISSUER}/jwks"
_KID = "test-key-1"

# SAML seed constants (used only to assert the SAML provider is listed +
# addressed by id; the native ACS round-trip needs xmlsec, guarded below).
_SAML_ENTITY = "https://idp.example.test/saml/metadata"
_SAML_SSO = "https://idp.example.test/saml/sso"
_SAML_CERT = "-----BEGIN CERTIFICATE-----\nMIIDfake\n-----END CERTIFICATE-----"

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
    """Stateful mock OpenID Provider (same shape as test_oidc_generic)."""

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
# DB seed helpers — note: the table is GLOBAL (no tenant_id column).
# ---------------------------------------------------------------------------
async def _seed_global_oidc(
    dsn: str,
    *,
    enabled: bool = True,
    display_name: str = "Platform OIDC",
    button_label: str | None = "Sign in with Acme",
) -> UUID:
    """Insert the global OIDC config row (secret Fernet-encrypted). Return its id."""
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, button_label, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings)
            VALUES ($1, 'oidc', $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
            """,
            config_id,
            display_name,
            button_label,
            enabled,
            _ISSUER,
            _CLIENT_ID,
            encrypt_client_secret(_CLIENT_SECRET),
            json.dumps(["openid", "email", "profile"]),
            json.dumps({}),
        )
    finally:
        await conn.close()
    return config_id


async def _seed_global_saml(
    dsn: str, *, enabled: bool = True, button_label: str | None = "Corporate SAML"
) -> UUID:
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, button_label, enabled,
                 idp_entity_id, idp_sso_url, idp_x509_cert, attribute_mappings)
            VALUES ($1, 'saml', 'Platform SAML', $2, $3, $4, $5, $6, $7::jsonb)
            """,
            config_id,
            button_label,
            enabled,
            _SAML_ENTITY,
            _SAML_SSO,
            _SAML_CERT,
            json.dumps({}),
        )
    finally:
        await conn.close()
    return config_id


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


async def _capture_login_state(client: AsyncClient, provider_id: UUID, idp: _FakeIdP) -> str:
    """Start the OIDC login by provider; mirror the nonce into the fake IdP
    and return the ``state`` the server put on the IdP redirect."""
    resp = await client.get(f"/auth/sso/{provider_id}/oidc/login")
    assert resp.status_code == 307, resp.text
    params = dict(httpx.URL(resp.headers["location"]).params)
    idp.last_nonce = params["nonce"]
    return params["state"]


# ---------------------------------------------------------------------------
# PUBLIC providers list — no secrets
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_public_providers_lists_enabled_without_secrets(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    oidc_id = await _seed_global_oidc(migrations_pg_dsn, button_label="Sign in with Acme")
    saml_id = await _seed_global_saml(migrations_pg_dsn, button_label="Corporate SAML")

    async with _client(configured_app) as client:
        # PUBLIC: no Authorization header.
        resp = await client.get("/auth/sso/providers")
    assert resp.status_code == 200, resp.text
    providers = resp.json()

    # Both ENABLED providers are listed (one row per provider/kind — the
    # global uq_sso_config_provider constraint allows at most one each).
    kinds = sorted(p["kind"] for p in providers)
    assert kinds == ["oidc", "saml"]

    by_id = {p["id"]: p for p in providers}
    assert str(oidc_id) in by_id
    assert str(saml_id) in by_id

    oidc = by_id[str(oidc_id)]
    assert oidc["kind"] == "oidc"
    assert oidc["display_name"] == "Platform OIDC"
    assert oidc["button_label"] == "Sign in with Acme"
    assert oidc["login_url"] == f"/auth/sso/{oidc_id}/oidc/login"

    saml = by_id[str(saml_id)]
    assert saml["login_url"] == f"/auth/sso/{saml_id}/saml/login"

    # CRITICAL: no secret-bearing field leaks through the public endpoint.
    serialized = json.dumps(providers).lower()
    for forbidden in (
        "secret",
        "client_secret",
        "private_key",
        "sp_private_key",
        "vault",
        "encrypted",
        _CLIENT_SECRET.lower(),
    ):
        assert forbidden not in serialized, f"public providers leaked {forbidden!r}"
    allowed_keys = {"id", "kind", "display_name", "button_label", "login_url"}
    for p in providers:
        assert set(p.keys()) == allowed_keys


@pytest.mark.asyncio
async def test_public_providers_excludes_disabled(configured_app, migrations_pg_dsn: str) -> None:
    """A disabled provider is filtered out; an enabled one of another kind
    still shows. With NO enabled provider the list is empty."""
    await _truncate_all(migrations_pg_dsn)
    # Disabled OIDC + enabled SAML → only SAML is listed.
    await _seed_global_oidc(migrations_pg_dsn, enabled=False)
    saml_id = await _seed_global_saml(migrations_pg_dsn, enabled=True)
    async with _client(configured_app) as client:
        resp = await client.get("/auth/sso/providers")
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert [p["id"] for p in listed] == [str(saml_id)]

    # Disable the SAML one too → nothing enabled → empty list.
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_oidc(migrations_pg_dsn, enabled=False)
    async with _client(configured_app) as client:
        empty = await client.get("/auth/sso/providers")
    assert empty.status_code == 200, empty.text
    assert empty.json() == []


# ---------------------------------------------------------------------------
# Login start by provider + callback resolves provider from state
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_oidc_login_by_provider_redirects_to_idp(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with _client(configured_app) as client:
        resp = await client.get(f"/auth/sso/{provider_id}/oidc/login")
    assert resp.status_code == 307, resp.text
    location = resp.headers["location"]
    assert location.startswith(_AUTHZ)
    params = dict(httpx.URL(location).params)
    assert params["state"]
    assert params["nonce"]


@pytest.mark.asyncio
async def test_oidc_login_unknown_provider_is_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_oidc(migrations_pg_dsn)
    async with _client(configured_app) as client:
        # A random (non-existent) provider id.
        resp = await client.get(f"/auth/sso/{uuid4()}/oidc/login")
        # A non-UUID segment also resolves to a uniform 404.
        bad = await client.get("/auth/sso/not-a-uuid/oidc/login")
    assert resp.status_code == 404, resp.text
    assert bad.status_code == 404, bad.text


@pytest.mark.asyncio
async def test_callback_resolves_provider_and_mints_identity_session(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """The callback recovers the provider from the state, mints a tenant-less
    IDENTITY session, and JIT-creates the global user — with NO membership
    (access is by admin assignment, ADR 0047)."""
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with _client(configured_app) as client:
        state = await _capture_login_state(client, provider_id, idp)
        resp = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_type"] == "bearer"
        token = body["access_token"]
        assert token

        # The JWT is accepted; the session proves identity WITHOUT a tenant.
        me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        me_body = me.json()
        assert me_body["email"] == "worker@acme.test"
        assert me_body["active_tenant_id"] is None
        # ADR 0047: SSO login no longer auto-provisions a membership.
        assert me_body["memberships"] == []

    assert await _count_users_with_email(migrations_pg_dsn, "worker@acme.test") == 1


@pytest.mark.asyncio
async def test_callback_bad_state_is_400(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_oidc(migrations_pg_dsn)
    async with _client(configured_app) as client:
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
    provider_id = await _seed_global_oidc(migrations_pg_dsn)
    async with _client(configured_app) as client:
        state = await _capture_login_state(client, provider_id, idp)
        first = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
        assert first.status_code == 200, first.text
        replay = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
    assert replay.status_code == 400, replay.text


# ---------------------------------------------------------------------------
# SAML login is keyed by provider; old per-tenant routes are gone.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_saml_login_unknown_provider_is_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_saml(migrations_pg_dsn)
    async with _client(configured_app) as client:
        resp = await client.get(f"/auth/sso/{uuid4()}/saml/login")
    # 404 (unknown provider) when xmlsec is present, 501 when it is not —
    # either way the OLD per-tenant route shape is not what answers.
    assert resp.status_code in (404, 501), resp.text


@pytest.mark.asyncio
async def test_saml_login_by_provider(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_saml(migrations_pg_dsn)
    async with _client(configured_app) as client:
        resp = await client.get(f"/auth/sso/{provider_id}/saml/login")
    if not saml_available():
        assert resp.status_code == 501, resp.text
        return
    # SP-initiated: 302 redirect to the IdP SSO URL with the AuthnRequest.
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith(_SAML_SSO)


@pytest.mark.asyncio
async def test_global_saml_acs_route_exists(configured_app, migrations_pg_dsn: str) -> None:
    """The GLOBAL ACS exists at /auth/sso/saml/acs (no tenant in the path).

    A POST with garbage gets a 4xx/501 from the flow — never a 404 — which
    proves the route is registered globally."""
    await _truncate_all(migrations_pg_dsn)
    await _seed_global_saml(migrations_pg_dsn)
    async with _client(configured_app) as client:
        resp = await client.post(
            "/auth/sso/saml/acs",
            data={"SAMLResponse": "not-a-real-response"},
        )
    assert resp.status_code != 404, resp.text
    assert resp.status_code in (400, 501), resp.text


# ---------------------------------------------------------------------------
# The OLD per-tenant routes are RETIRED (no redirect) -> 404.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_old_per_tenant_login_routes_are_gone(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_global_oidc(migrations_pg_dsn)
    await _seed_global_saml(migrations_pg_dsn)

    async with _client(configured_app) as client:
        oidc_old = await client.get(f"/auth/sso/{tenant}/oidc/login")
        saml_old = await client.get(f"/auth/sso/{tenant}/saml/login")
        acs_old = await client.post(
            f"/auth/sso/{tenant}/saml/acs",
            data={"SAMLResponse": "x"},
        )

    # A tenant UUID is just an unknown provider id now → 404 for login; the
    # per-tenant ACS path no longer exists → 404.
    assert oidc_old.status_code == 404, oidc_old.text
    assert saml_old.status_code in (404, 501), saml_old.text
    assert acs_old.status_code == 404, acs_old.text


# ---------------------------------------------------------------------------
# Cross-tenant: the identity session reaches NO tenant without a membership.
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_identity_session_has_no_tenant_access(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """A fresh SSO login yields an identity session with no active tenant
    and no membership, so the user cannot reach any tenant's data even
    though tenants exist — access is granted only by an admin-assigned
    membership (ADR 0047; resolution is task_sso_03)."""
    await _truncate_all(migrations_pg_dsn)
    # Two tenants exist, but the user is assigned to neither.
    await _seed_tenant(migrations_pg_dsn, slug="alpha")
    await _seed_tenant(migrations_pg_dsn, slug="bravo")
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with _client(configured_app) as client:
        state = await _capture_login_state(client, provider_id, idp)
        resp = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["access_token"]

        me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        me_body = me.json()
        # No active tenant, no membership → the "no access" condition that
        # task_sso_03 turns into the "contact your admin" screen.
        assert me_body["active_tenant_id"] is None
        assert me_body["memberships"] == []
        assert me_body["is_system_admin"] is False

        # RLS-scoped memberships view also sees nothing for this user.
        mine = await client.get("/me/memberships", headers={"Authorization": f"Bearer {token}"})
        assert mine.status_code == 200, mine.text
        assert mine.json() == []
