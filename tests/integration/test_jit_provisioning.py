"""Integration tests for hardened JIT provisioning (Plan 08 task_08_07).

Phase A/B already create a user on first SSO login (the
``_jit_provision_user`` helper, shared by the OIDC callback and the SAML
ACS). This module exercises the *hardened* policy task_08_07 adds on top:

  * first SSO login creates the user + an ACTIVE membership with role
    ``tenant_user`` in the SSO config's tenant, and flags the user
    ``is_sso_provisioned = true`` (no usable local password);
  * a second login REUSES the same user (no duplicate row);
  * an EXISTING user (matched by verified email) is LINKED, never
    duplicated — including a pre-existing LOCAL-password user, whose
    local password keeps working untouched while they also gain SSO;
  * the user lands in the SSO config's tenant ONLY
    (``@pytest.mark.cross_tenant``);
  * concurrent first-logins are idempotent — neither the user nor the
    membership is duplicated under a race;
  * an SSO-provisioned user is rejected by LOCAL login with a clean 401
    (the sentinel password hash never reaches the argon2 verifier).

No real IdP: a :class:`httpx.MockTransport` serves a complete fake
OpenID Provider so the whole flow runs offline. The fake IdP and the
seed helpers mirror ``test_oidc_generic.py`` so the two suites stay
consistent.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB and flush Redis DB 15.
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

# The IdP asserts a MIXED-CASE email; the flow + JIT both lower-case it,
# so the stored/lookup email is the normalized lower-case form. We use a
# `.example.com` domain (not `.test`) because the LOCAL-login negative
# tests POST through `LoginRequest.email: EmailStr`, whose RFC validator
# rejects the reserved `.test` TLD — the SSO callback path itself accepts
# any IdP-asserted address.
_IDP_EMAIL = "Worker@Acme.example.com"
_NORMALIZED_EMAIL = "worker@acme.example.com"
_LOCAL_PASSWORD = "correct horse battery staple"  # - test-only, >= 8 chars

_SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": _KID}, private=True)


def _id_token(*, nonce: str, sub: str = "idp-subject-123", aud: str = _CLIENT_ID) -> str:
    header = {"alg": "RS256", "kid": _KID}
    claims = {
        "iss": _ISSUER,
        "aud": aud,
        "sub": sub,
        "nonce": nonce,
        "email": _IDP_EMAIL,
        "name": "Worker Person",
    }
    return joserfc_jwt.encode(header, claims, _SIGNING_KEY)


class _FakeIdP:
    """Stateful mock OpenID Provider (mirrors test_oidc_generic)."""

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


async def _seed_oidc_config(dsn: str, *, tenant_id: UUID, enabled: bool = True) -> None:
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


async def _membership_rows(dsn: str, *, tenant_id: UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(
            await conn.fetch(
                "SELECT user_id, role, is_active FROM user_org_memberships WHERE tenant_id = $1",
                tenant_id,
            )
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


async def _login_state(client: AsyncClient, tenant_id: UUID, idp: _FakeIdP) -> str:
    """Start a login; return the ``state`` and mirror the nonce into the IdP
    so its token endpoint echoes the matching value."""
    resp = await client.get(f"/auth/sso/{tenant_id}/oidc/login")
    assert resp.status_code == 307, resp.text
    params = dict(httpx.URL(resp.headers["location"]).params)
    idp.last_nonce = params["nonce"]
    return params["state"]


async def _sso_callback(client: AsyncClient, state: str) -> httpx.Response:
    return await client.get(
        "/auth/sso/oidc/callback",
        params={"code": "fake-auth-code", "state": state},
    )


async def _full_sso_login(client: AsyncClient, tenant_id: UUID, idp: _FakeIdP) -> httpx.Response:
    state = await _login_state(client, tenant_id, idp)
    return await _sso_callback(client, state)


# ---------------------------------------------------------------------------
# First login: creates user + membership(tenant_user) + sso-provisioned flag
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_first_login_creates_user_and_membership(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _full_sso_login(client, tenant, idp)
        assert resp.status_code == 200, resp.text
        token = resp.json()["access_token"]

        me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["email"] == _NORMALIZED_EMAIL
        assert body["active_tenant_id"] == str(tenant)
        roles = [m["role"] for m in body["memberships"] if m["tenant_id"] == str(tenant)]
        assert roles == ["tenant_user"]

    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
    row = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
    assert row is not None
    # SSO-provisioned: flagged + no usable local password (sentinel hash).
    assert row["is_sso_provisioned"] is True
    assert row["password_hash"] == "!sso-no-local-login!"

    memberships = await _membership_rows(migrations_pg_dsn, tenant_id=tenant)
    assert len(memberships) == 1
    assert memberships[0]["role"] == "tenant_user"
    assert memberships[0]["is_active"] is True


# ---------------------------------------------------------------------------
# Second login: reuses the same user + membership (no duplicate)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_second_login_reuses_user_and_membership(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        first = await _full_sso_login(client, tenant, idp)
        assert first.status_code == 200, first.text
        first_user = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
        assert first_user is not None

        second = await _full_sso_login(client, tenant, idp)
        assert second.status_code == 200, second.text

    # Exactly one user and one membership despite two logins.
    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
    second_user = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
    assert second_user is not None
    assert second_user["id"] == first_user["id"]  # same row reused
    assert len(await _membership_rows(migrations_pg_dsn, tenant_id=tenant)) == 1


# ---------------------------------------------------------------------------
# Existing email links to the existing user (no duplicate) — and a
# pre-existing LOCAL user keeps their local password working.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_existing_email_links_no_duplicate(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)
    # Pre-existing LOCAL user with the SAME (normalized) email the IdP asserts.
    local_id = await _seed_local_user(
        migrations_pg_dsn, email=_NORMALIZED_EMAIL, password=_LOCAL_PASSWORD
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _full_sso_login(client, tenant, idp)
        assert resp.status_code == 200, resp.text

    # No duplicate: the SSO login linked to the existing user row.
    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
    row = await _user_row(migrations_pg_dsn, _NORMALIZED_EMAIL)
    assert row is not None
    assert row["id"] == local_id
    # The existing local user is NOT clobbered into an SSO-only identity:
    # its local password hash and the is_sso_provisioned flag stay intact,
    # so local login keeps working AND it now also has an SSO membership.
    assert row["is_sso_provisioned"] is False
    assert row["password_hash"] != "!sso-no-local-login!"

    memberships = await _membership_rows(migrations_pg_dsn, tenant_id=tenant)
    assert len(memberships) == 1
    assert memberships[0]["user_id"] == local_id
    assert memberships[0]["role"] == "tenant_user"

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
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # First SSO login materialises the SSO-only user.
        resp = await _full_sso_login(client, tenant, idp)
        assert resp.status_code == 200, resp.text

        # Any local password attempt is rejected with the generic 401 —
        # the sentinel hash must NEVER reach the argon2 verifier (a 500).
        login = await client.post(
            "/auth/login",
            json={"email": _NORMALIZED_EMAIL, "password": "any-password-guess"},
        )
    assert login.status_code == 401, login.text
    assert "invalid email or password" in login.text.lower()


# ---------------------------------------------------------------------------
# Concurrency: simultaneous first-logins do not duplicate user/membership
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_first_logins_are_idempotent(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Two independent login states (distinct nonce/state) so each
        # callback runs a full, valid flow. Mirror the nonce per state by
        # capturing them sequentially, then firing the callbacks together.
        state_1 = await _login_state(client, tenant, idp)
        # The fake IdP echoes whatever last_nonce is set at /token time;
        # both states share the same identity (same email) which is the
        # point — they race to create the SAME user.
        results = await asyncio.gather(
            _sso_callback(client, state_1),
            _full_sso_login(client, tenant, idp),
            return_exceptions=True,
        )

    # At least one login succeeded; none raised an unhandled exception.
    statuses = [r.status_code for r in results if isinstance(r, httpx.Response)]
    assert any(s == 200 for s in statuses), results
    assert all(not isinstance(r, BaseException) for r in results), results

    # Idempotent: exactly one user row and exactly one membership.
    assert await _count_users_with_email(migrations_pg_dsn, _NORMALIZED_EMAIL) == 1
    assert len(await _membership_rows(migrations_pg_dsn, tenant_id=tenant)) == 1


# ---------------------------------------------------------------------------
# Cross-tenant: the user lands ONLY in the SSO config's tenant
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_user_lands_in_config_tenant_only(
    configured_app, migrations_pg_dsn: str, idp: _FakeIdP
) -> None:
    """Tenant A has the OIDC config; the JIT membership must land on A and
    tenant B must stay empty — the membership is written under
    ``app.tenant_id`` bound to the state's (config's) tenant."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant_a)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await _full_sso_login(client, tenant_a, idp)
        assert resp.status_code == 200, resp.text

    a_members = await _membership_rows(migrations_pg_dsn, tenant_id=tenant_a)
    b_members = await _membership_rows(migrations_pg_dsn, tenant_id=tenant_b)
    assert len(a_members) == 1
    assert a_members[0]["role"] == "tenant_user"
    assert len(b_members) == 0
