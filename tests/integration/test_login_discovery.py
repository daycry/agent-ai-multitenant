"""Integration tests for login discovery (Plan 08 task_08_12).

The PUBLIC ``GET /auth/discover?email=<addr>`` endpoint maps an email's
DOMAIN to the tenant whose enabled SSO config claims it, so the login UI
can route the user straight to their IdP. No IdP round-trip is involved —
discovery only reads the configured ``email_domains`` — so this test
seeds configs directly and never mocks an OpenID Provider.

Coverage:

  * a configured + enabled SSO domain -> ``method: sso`` with the
    provider, tenant id, and the relative login URL to start the flow
    (OIDC and SAML both exercised).
  * case-insensitive domain matching.
  * a DISABLED config's domain -> the generic local-login response.
  * an UNKNOWN / unclaimed domain -> the generic local-login response.
  * NO enumeration: the response is identical whether or not a user with
    that email exists (a seeded user changes nothing).
  * a malformed email -> the same generic local-login response (no error).
  * cross-tenant isolation (@pytest.mark.cross_tenant): tenant A's claimed
    domain resolves to A's tenant id + login URL, never B's.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the session fixtures create a throwaway DB and flush Redis 15.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


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
    email_domains: list[str],
    enabled: bool = True,
) -> None:
    """Insert an OIDC config row claiming ``email_domains`` (no secret needed)."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled, issuer,
                 client_id, scopes, claim_mappings, email_domains)
            VALUES ($1, $2, 'oidc', 'Acme OIDC', $3, $4, $5,
                    $6::jsonb, $7::jsonb, $8::jsonb)
            """,
            uuid4(),
            tenant_id,
            enabled,
            "https://idp.example.test",
            "acme-oidc-client",
            json.dumps(["openid", "email", "profile"]),
            json.dumps({}),
            json.dumps(email_domains),
        )
    finally:
        await conn.close()


async def _seed_saml_config(
    dsn: str,
    *,
    tenant_id: UUID,
    email_domains: list[str],
    enabled: bool = True,
) -> None:
    """Insert a SAML config row claiming ``email_domains``."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled,
                 idp_entity_id, idp_sso_url, idp_x509_cert,
                 attribute_mappings, email_domains)
            VALUES ($1, $2, 'saml', 'Acme SAML', $3, $4, $5, $6,
                    $7::jsonb, $8::jsonb)
            """,
            uuid4(),
            tenant_id,
            enabled,
            "https://idp.example.test/saml/metadata",
            "https://idp.example.test/saml/sso",
            "-----BEGIN CERTIFICATE-----\nMIIDfake\n-----END CERTIFICATE-----",
            json.dumps({}),
            json.dumps(email_domains),
        )
    finally:
        await conn.close()


async def _seed_user(dsn: str, *, email: str) -> None:
    """Insert a bare local user row (to prove discovery ignores users)."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) "
            "VALUES ($1, $2, $3, false)",
            uuid4(),
            email.lower(),
            "!irrelevant-hash!",
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
# App fixture: real api-server (no IdP mock needed for discovery)
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


# ---------------------------------------------------------------------------
# SSO domain -> provider + login URL
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_enabled_oidc_domain_returns_provider_and_login_url(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant, email_domains=["acme.test"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/auth/discover", params={"email": "worker@acme.test"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == "sso"
    assert body["provider"] == "oidc"
    assert body["tenant_id"] == str(tenant)
    assert body["login_url"] == f"/auth/sso/{tenant}/oidc/login"


@pytest.mark.asyncio
async def test_enabled_saml_domain_returns_saml_login_url(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant, email_domains=["saml.test"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/auth/discover", params={"email": "worker@saml.test"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == "sso"
    assert body["provider"] == "saml"
    assert body["tenant_id"] == str(tenant)
    assert body["login_url"] == f"/auth/sso/{tenant}/saml/login"


@pytest.mark.asyncio
async def test_domain_match_is_case_insensitive(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant, email_domains=["acme.test"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        # Mixed-case domain in the query still matches the lower-case stored value.
        resp = await client.get("/auth/discover", params={"email": "Worker@ACME.Test"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == "sso"
    assert body["tenant_id"] == str(tenant)


# ---------------------------------------------------------------------------
# Fallback to generic local login — no enumeration
# ---------------------------------------------------------------------------
def _assert_generic_local(body: dict) -> None:
    assert body == {
        "method": "password",
        "provider": None,
        "tenant_id": None,
        "login_url": None,
    }


@pytest.mark.asyncio
async def test_disabled_config_domain_returns_generic_local(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(
        migrations_pg_dsn, tenant_id=tenant, email_domains=["acme.test"], enabled=False
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/auth/discover", params={"email": "worker@acme.test"})

    assert resp.status_code == 200, resp.text
    _assert_generic_local(resp.json())


@pytest.mark.asyncio
async def test_unknown_domain_returns_generic_local(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant, email_domains=["acme.test"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/auth/discover", params={"email": "someone@gmail.com"})

    assert resp.status_code == 200, resp.text
    _assert_generic_local(resp.json())


@pytest.mark.asyncio
async def test_no_enumeration_identical_shape_whether_user_exists(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The discovery answer for an unclaimed domain is byte-for-byte the
    same whether or not a user with that email is registered — proving the
    endpoint never queries the users table for existence."""
    await _truncate_all(migrations_pg_dsn)
    # No SSO config at all; one of the two emails has a real user row.
    await _seed_user(migrations_pg_dsn, email="real@unconfigured.test")

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        existing = await client.get("/auth/discover", params={"email": "real@unconfigured.test"})
        missing = await client.get("/auth/discover", params={"email": "ghost@unconfigured.test"})

    assert existing.status_code == 200, existing.text
    assert missing.status_code == 200, missing.text
    # Identical response regardless of account existence.
    assert existing.json() == missing.json()
    _assert_generic_local(existing.json())


@pytest.mark.asyncio
async def test_malformed_email_returns_generic_local(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/auth/discover", params={"email": "not-an-email"})

    assert resp.status_code == 200, resp.text
    _assert_generic_local(resp.json())


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_domain_resolves_to_owning_tenant_only(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A claims a domain; tenant B claims a different one. Each
    domain resolves to its OWNING tenant's id + login URL, never the
    other's — discovery scans cross-tenant on the admin role but answers
    only with the matching config."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant_a, email_domains=["alpha.test"])
    await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant_b, email_domains=["bravo.test"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://testserver",
    ) as client:
        a_resp = await client.get("/auth/discover", params={"email": "u@alpha.test"})
        b_resp = await client.get("/auth/discover", params={"email": "u@bravo.test"})

    a_body = a_resp.json()
    b_body = b_resp.json()
    assert a_body["tenant_id"] == str(tenant_a)
    assert a_body["provider"] == "oidc"
    assert a_body["login_url"] == f"/auth/sso/{tenant_a}/oidc/login"
    assert b_body["tenant_id"] == str(tenant_b)
    assert b_body["provider"] == "saml"
    assert b_body["login_url"] == f"/auth/sso/{tenant_b}/saml/login"
    # A's domain never leaks B's tenant and vice versa.
    assert a_body["tenant_id"] != b_body["tenant_id"]
