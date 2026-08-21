"""Integration tests for GLOBAL identity provisioning at SSO login (ADR 0047).

ADR 0047 (global auth + access-by-membership) re-scoped what an SSO login
does: a successful OIDC callback / SAML ACS provisions only the GLOBAL
user identity (``_provision_identity`` in ``routers/sso.py``) and creates
NO tenant membership and reads NO IdP groups — access to a tenant is
granted EXCLUSIVELY by the membership an admin assigns AFTER login. So the
old "JIT login also creates an active tenant_user membership in the
config's tenant" behaviour is GONE; the membership assertions of the
per-tenant model are removed, along with the per-tenant
"lands-in-config-tenant-only" test.

What survives — and stays meaningful — is the identity-provisioning
policy, reworked to the global login route (``GET
/auth/sso/{provider_id}/oidc/login`` + the shared callback):

  * first SSO login CREATES the global user with no usable local password
    (the ``!sso-no-local-login!`` sentinel hash) and ``is_sso_provisioned
    = true`` — and NO membership (the JWT session is tenant-less);
  * a second login REUSES the same user (no duplicate row);
  * an EXISTING user (matched by verified email) is LINKED, never
    duplicated — including a pre-existing LOCAL-password user, whose local
    password keeps working untouched while they also gain the SSO identity;
  * concurrent first-logins are idempotent — the user is never duplicated
    under a race;
  * an SSO-provisioned user is rejected by LOCAL login with a clean 401
    (the sentinel password hash never reaches the argon2 verifier).

No real IdP: a :class:`httpx.MockTransport` serves a complete fake
OpenID Provider so the whole flow runs offline.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
import json
import time
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

# ---------------------------------------------------------------------------
# Fake IdP constants — one user identity ("Worker"), reused across logins.
# ---------------------------------------------------------------------------
_ISSUER = "https://idp.example.test"
_CLIENT_ID = "acme-oidc-client"
_CLIENT_SECRET = "super-secret-oidc-value"
_AUTHZ = f"{_ISSUER}/authorize"
_TOKEN = f"{_ISSUER}/token"
_USERINFO = f"{_ISSUER}/userinfo"
_JWKS = f"{_ISSUER}/jwks"
_KID = "test-key-1"

# The IdP asserts a MIXED-CASE email; the flow + provisioning both
# lower-case it, so the stored/lookup email is the normalized lower-case
# form. We use a `.example.com` domain (not `.test`) because the
# LOCAL-login negative tests POST through `LoginRequest.email: EmailStr`,
# whose RFC validator rejects the reserved `.test` TLD — the SSO callback
# path itself accepts any IdP-asserted address.
_IDP_EMAIL = "Worker@Acme.example.com"
_NORMALIZED_EMAIL = "worker@acme.example.com"
_LOCAL_PASSWORD = "correct horse battery staple"  # - test-only, >= 8 chars

_SSO_PASSWORD_SENTINEL = "!sso-no-local-login!"

_SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": _KID}, private=True)


def _id_token(*, nonce: str, sub: str = "idp-subject-123", aud: str = _CLIENT_ID) -> str:
    # `exp`/`iat` de verdad: el IdP falso acuñaba tokens SIN caducidad, y el
    # verificador tampoco la miraba (gotcha joserfc-decode-no-valida-exp). Al
    # cerrar ese hueco en `auth/sso/oidc.py` estos dobles tenían que dejar de
    # emitir tokens inmortales: un fake que no puede caducar no ejercita el
    # camino real.
    now = int(time.time())
    header = {"alg": "RS256", "kid": _KID}
    claims = {
        "iss": _ISSUER,
        "aud": aud,
        "sub": sub,
        "nonce": nonce,
        "email": _IDP_EMAIL,
        "name": "Worker Person",
        "iat": now,
        "exp": now + 3600,
    }
    return joserfc_jwt.encode(header, claims, _SIGNING_KEY)


class _FakeIdP:
    """Stateful mock OpenID Provider (mirrors test_sso_global_login)."""

    def __init__(self) -> None:
        self.last_nonce: str | None = None
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
        if url == _AUTHZ:  # pragma: no cover - flow only builds the URL
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
                    "id_token": _id_token(nonce=nonce, sub=self.userinfo_sub),
                },
            )
        if url == _USERINFO:
            return httpx.Response(
                200,
                json={
                    "sub": self.userinfo_sub,
                    "email": _IDP_EMAIL,
                    "name": "Worker Person",
                },
            )
        return httpx.Response(404, json={"error": "not_found"})  # pragma: no cover


# ---------------------------------------------------------------------------
# DB seed + inspection helpers — the table is GLOBAL now (no tenant_id).
# ---------------------------------------------------------------------------
async def _seed_global_oidc(dsn: str, *, enabled: bool = True) -> UUID:
    """Insert the single global OIDC config. Returns its provider id."""
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings)
            VALUES ($1, 'oidc', 'Acme OIDC', $2, $3, $4, $5, $6::jsonb, $7::jsonb)
            """,
            config_id,
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


async def _seed_local_user(dsn: str, *, email: str, password: str) -> UUID:
    """Seed a pre-existing LOCAL-password user (is_sso_provisioned=false)."""
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO users (id, email, password_hash, full_name, is_system_admin)
            VALUES ($1, $2, $3, $4, false)
            """,
            user_id,
            email,
            hash_password(password),
            "Pre-existing Local User",
        )
    finally:
        await conn.close()
    return user_id


async def _count_users_with_email(dsn: str, email: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
    finally:
        await conn.close()


async def _user_row(dsn: str, email: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT id, password_hash, is_sso_provisioned FROM users WHERE email = $1",
            email,
        )
    finally:
        await conn.close()


async def _count_memberships(dsn: str, *, email: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            """
            SELECT count(*)
              FROM user_org_memberships m
              JOIN users u ON u.id = m.user_id
             WHERE u.email = $1
            """,
            email,
        )
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
# App fixture: real api-server with the mocked IdP injected into the flow
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
    # Generous rate limit so the local-login negative test never trips 429.
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_COUNT", "50")
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")
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


async def _login_state(client: AsyncClient, provider_id: UUID, idp: _FakeIdP) -> str:
    """Start a login by provider; return the ``state`` and mirror the nonce
    into the IdP so its token endpoint echoes the matching value."""
    resp = await client.get(f"/auth/sso/{provider_id}/oidc/login")
    assert resp.status_code == 307, resp.text
    params = dict(httpx.URL(resp.headers["location"]).params)
    idp.last_nonce = params["nonce"]
    return params["state"]


async def _sso_callback(client: AsyncClient, state: str) -> httpx.Response:
    # `follow_redirects=False` desde el ADR 0133: el callback ya no contesta el
    # `LoginResponse` JSON, sino 303 + `Set-Cookie` hacia el panel. Sin esto el
    # cliente seguiría el 303 hasta una URL de panel que aquí no existe, y el
    # estado que se afirmaría sería el del salto, no el del login.
    return await client.get(
        "/auth/sso/oidc/callback",
        params={"code": "fake-auth-code", "state": state},
        follow_redirects=False,
    )


def _session_cookie(client: AsyncClient) -> str:
    """La credencial que dejó el callback, ya no en el cuerpo sino en la cookie."""
    token = client.cookies.get("agentic_session")
    assert token, "el callback no dejó cookie de sesión"
    return token


async def _full_sso_login(client: AsyncClient, provider_id: UUID, idp: _FakeIdP) -> httpx.Response:
    state = await _login_state(client, provider_id, idp)
    return await _sso_callback(client, state)


# ---------------------------------------------------------------------------
# First login: creates the global user (sso-provisioned) with NO membership
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_first_login_creates_user_without_membership(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _full_sso_login(client, provider_id, idp)
        # 303 + Set-Cookie desde el ADR 0133; el contrato del salto en sí está
        # fijado en test_sso_callback_redirect.py.
        assert resp.status_code == 303, resp.text
        token = _session_cookie(client)

        me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["email"] == _NORMALIZED_EMAIL
        # Identity session: no active tenant, no membership (ADR 0047).
        assert body["active_tenant_id"] is None
        assert body["memberships"] == []

    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
    row = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
    assert row is not None
    # SSO-provisioned: flagged + no usable local password (sentinel hash).
    assert row["is_sso_provisioned"] is True
    assert row["password_hash"] == _SSO_PASSWORD_SENTINEL
    # No membership is auto-created (access is by admin assignment).
    assert await _count_memberships(migrations_pg_dsn, email=_NORMALIZED_EMAIL) == 0


# ---------------------------------------------------------------------------
# Second login: reuses the same user (no duplicate)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_second_login_reuses_user(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        first = await _full_sso_login(client, provider_id, idp)
        assert first.status_code == 303, first.text
        first_user = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
        assert first_user is not None

        second = await _full_sso_login(client, provider_id, idp)
        assert second.status_code == 303, second.text

    # Exactly one user despite two logins (same row reused).
    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
    second_user = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
    assert second_user is not None
    assert second_user["id"] == first_user["id"]


# ---------------------------------------------------------------------------
# Existing email links to the existing user (no duplicate) — and a
# pre-existing LOCAL user keeps their local password working.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_existing_email_links_no_duplicate(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)
    # Pre-existing LOCAL user with the SAME (normalized) email the IdP asserts.
    local_id = await _seed_local_user(
        migrations_pg_dsn, email=_NORMALIZED_EMAIL, password=_LOCAL_PASSWORD
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _full_sso_login(client, provider_id, idp)
        assert resp.status_code == 303, resp.text

    # No duplicate: the SSO login linked to the existing user row.
    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
    row = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
    assert row is not None
    assert row["id"] == local_id
    # The existing local user is NOT clobbered into an SSO-only identity:
    # its local password hash and the is_sso_provisioned flag stay intact,
    # so local login keeps working.
    assert row["is_sso_provisioned"] is False
    assert row["password_hash"] != _SSO_PASSWORD_SENTINEL

    # And the pre-existing local password still authenticates.
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        login = await client.post(
            "/auth/login",
            json={"email": _NORMALIZED_EMAIL, "password": _LOCAL_PASSWORD},
        )
    assert login.status_code == 200, login.text


# ---------------------------------------------------------------------------
# Local login is rejected for an SSO-provisioned user — clean 401, not 500.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sso_provisioned_user_cannot_login_locally(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # First SSO login materialises the SSO-only user.
        resp = await _full_sso_login(client, provider_id, idp)
        assert resp.status_code == 303, resp.text

        # Any local password attempt is rejected with the generic 401 —
        # the sentinel hash must NEVER reach the argon2 verifier (a 500).
        login = await client.post(
            "/auth/login",
            json={"email": _NORMALIZED_EMAIL, "password": "any-password-guess"},
        )
    assert login.status_code == 401, login.text
    assert "invalid email or password" in login.text.lower()


# ---------------------------------------------------------------------------
# Concurrency: simultaneous first-logins do not duplicate the user
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_first_logins_are_idempotent(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Two independent login states (distinct nonce/state) so each
        # callback runs a full, valid flow; both share the same identity
        # (same email) — they race to create the SAME user.
        state_1 = await _login_state(client, provider_id, idp)
        results = await asyncio.gather(
            _sso_callback(client, state_1),
            _full_sso_login(client, provider_id, idp),
            return_exceptions=True,
        )

    # At least one login succeeded; none raised an unhandled exception.
    statuses = [r.status_code for r in results if isinstance(r, httpx.Response)]
    assert any(s == 303 for s in statuses), results
    assert all(not isinstance(r, BaseException) for r in results), results

    # Idempotent: exactly one user row despite the race.
    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
