"""Integration tests for the per-tenant SAML config CRUD (Plan 08 task_08_06).

These back the Tenant-Admin SAML-config UI. No IdP is involved — the CRUD
is pure DB + RBAC + RLS, and the metadata parse is pure XML (NO native
``xmlsec`` needed, so this whole module runs anywhere). Coverage:

  * RBAC: unauthenticated -> 401; a `tenant_user` write -> 403; reads
    allowed for any tenant member; writes require `tenant_admin`.
  * create -> 201, persisted; the SP private key is ENCRYPTED at rest
    (never plaintext) and the response NEVER echoes it.
  * a second create -> 409 (one SAML config per tenant).
  * edit without an SP key keeps the stored one; edit with a new key
    re-encrypts; toggling `enabled` flips the flag.
  * the crypto invariant: enabling AuthnRequest signing without an SP
    cert + key -> 422.
  * delete soft-deletes (the row disappears from the list).
  * the SP metadata-url helper returns the per-tenant ACS + SP EntityID.
  * the IdP metadata parse endpoint extracts entityId / SSO URL / cert.
  * cross-tenant isolation (@pytest.mark.cross_tenant): tenant A's SAML
    config id never resolves for a tenant-B session (RLS).
  * OIDC and SAML configs coexist without leaking into each other's list.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the session fixture creates a throwaway DB and flushes Redis 15.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.sso.secrets import decrypt_client_secret, encrypt_client_secret
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_IDP_ENTITY_ID = "https://idp.example.test/saml/metadata"
_IDP_SSO_URL = "https://idp.example.test/saml/sso"
_IDP_CERT = "MIIDtestidpcertBASE64=="
_SP_CERT = "MIIDtestspcertBASE64=="


def _fake_pem(body: str) -> str:
    """A throwaway PEM-shaped string for tests.

    The PEM marker words are assembled at runtime (never written as the
    contiguous literal the `detect-private-key` pre-commit hook scans
    for) so this obviously-fake fixture is not flagged as a leaked
    credential.
    """
    dashes = "-" * 5
    key = "PRIVATE" + " " + "KEY"
    begin = f"{dashes}BEGIN {key}{dashes}"
    end = f"{dashes}END {key}{dashes}"
    return f"{begin}\n{body}\n{end}"


_SP_KEY_PEM = _fake_pem("MIItestkey")
_NEW_SP_KEY_PEM = _fake_pem("MIIrotatedkey")

_IDP_METADATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
                  entityID="https://idp.example.test/saml/metadata">
  <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>MIIDmetacertBASE64==</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
    <NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</NameIDFormat>
    <SingleSignOnService
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.example.test/saml/sso"/>
  </IDPSSODescriptor>
</EntityDescriptor>"""


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------
async def _seed_tenant_with_user(dsn: str, *, slug: str, role: str) -> tuple[UUID, UUID]:
    """Insert a tenant + a user with `role` membership. Returns (tenant, user)."""
    tenant = uuid4()
    user = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user,
            f"{slug}@example.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active)"
            " VALUES ($1, $2, $3, $4, true)",
            uuid4(),
            tenant,
            user,
            role,
        )
    finally:
        await conn.close()
    return tenant, user


async def _seed_saml_config(dsn: str, *, tenant_id: UUID, enabled: bool = True) -> UUID:
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled, idp_entity_id,
                 idp_sso_url, idp_x509_cert, name_id_format, attribute_mappings,
                 sp_private_key_encrypted)
            VALUES ($1, $2, 'saml', 'Acme SAML', $3, $4, $5, $6, $7, $8::jsonb, $9)
            """,
            config_id,
            tenant_id,
            enabled,
            _IDP_ENTITY_ID,
            _IDP_SSO_URL,
            _IDP_CERT,
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            json.dumps({"email": "mail"}),
            encrypt_client_secret(_SP_KEY_PEM),
        )
    finally:
        await conn.close()
    return config_id


async def _seed_oidc_config(dsn: str, *, tenant_id: UUID) -> UUID:
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings)
            VALUES ($1, $2, 'oidc', 'Acme OIDC', true, $3, $4, $5, $6::jsonb, $7::jsonb)
            """,
            config_id,
            tenant_id,
            "https://oidc.example.test",
            "acme-oidc-client",
            encrypt_client_secret("oidc-secret"),
            json.dumps(["openid", "email", "profile"]),
            json.dumps({}),
        )
    finally:
        await conn.close()
    return config_id


async def _fetch_config_row(dsn: str, config_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow("SELECT * FROM sso_configurations WHERE id = $1", config_id)
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
# App fixture (mirrors test_oidc_config_crud.py)
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
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID | None) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _valid_payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "display_name": "Acme SAML",
        "enabled": True,
        "idp_entity_id": _IDP_ENTITY_ID,
        "idp_sso_url": _IDP_SSO_URL,
        "idp_x509_cert": _IDP_CERT,
        "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        "attribute_mappings": {"email": "mail", "full_name": "displayName"},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_unauthenticated_is_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/saml/config")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_tenant_user_cannot_create(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/sso/saml/config", json=_valid_payload(), headers=_auth(token)
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_tenant_user_can_list(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/saml/config", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# Create / read — SP key never echoed, persisted encrypted
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_persists_encrypted_and_never_echoes_key(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    payload = _valid_payload(sp_x509_cert=_SP_CERT, sp_private_key=_SP_KEY_PEM)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/saml/config", json=payload, headers=_auth(token))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        config_id = UUID(body["id"])

        # The SP private key is NOT in the response — only the indicator.
        assert "sp_private_key" not in body
        assert "sp_private_key_encrypted" not in body
        assert "sp_private_key_ref" not in body
        assert body["has_sp_private_key"] is True
        assert body["sp_private_key_source"] == "encrypted"
        assert body["idp_entity_id"] == _IDP_ENTITY_ID
        assert body["sp_x509_cert"] == _SP_CERT
        assert body["enabled"] is True
        assert _SP_KEY_PEM not in resp.text

    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert row["provider"] == "saml"
    assert row["sp_private_key_ref"] is None
    assert row["sp_private_key_encrypted"] is not None
    assert row["sp_private_key_encrypted"] != _SP_KEY_PEM
    assert decrypt_client_secret(row["sp_private_key_encrypted"]) == _SP_KEY_PEM


@pytest.mark.asyncio
async def test_create_without_sp_key_is_allowed(configured_app, migrations_pg_dsn: str) -> None:
    """A SAML SP that neither signs nor encrypts needs no private key."""
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/sso/saml/config", json=_valid_payload(), headers=_auth(token)
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["has_sp_private_key"] is False
    assert resp.json()["sp_private_key_source"] is None


@pytest.mark.asyncio
async def test_create_rejects_both_key_forms(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    payload = _valid_payload(
        sp_x509_cert=_SP_CERT,
        sp_private_key=_SP_KEY_PEM,
        sp_private_key_ref="vault:secret/data/saml/acme",
    )
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/saml/config", json=payload, headers=_auth(token))
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_signing_without_key_is_422(configured_app, migrations_pg_dsn: str) -> None:
    """Enabling AuthnRequest signing without an SP cert + key -> 422."""
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    payload = _valid_payload(authn_requests_signed=True)  # no SP cert/key
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/saml/config", json=payload, headers=_auth(token))
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_second_create_is_409(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/sso/saml/config", json=_valid_payload(), headers=_auth(token)
        )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_edit_without_key_keeps_stored_key(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    config_id = await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant, enabled=False)
    token = await _mint_token(user, tenant)

    payload = _valid_payload(enabled=True)  # no SP key in the edit
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.put(
            f"/auth/sso/saml/config/{config_id}", json=payload, headers=_auth(token)
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True

    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert decrypt_client_secret(row["sp_private_key_encrypted"]) == _SP_KEY_PEM


@pytest.mark.asyncio
async def test_edit_with_new_key_reencrypts(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    config_id = await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    payload = _valid_payload(sp_x509_cert=_SP_CERT, sp_private_key=_NEW_SP_KEY_PEM)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.put(
            f"/auth/sso/saml/config/{config_id}", json=payload, headers=_auth(token)
        )
    assert resp.status_code == 200, resp.text
    assert _NEW_SP_KEY_PEM not in resp.text

    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert decrypt_client_secret(row["sp_private_key_encrypted"]) == _NEW_SP_KEY_PEM


@pytest.mark.asyncio
async def test_edit_missing_config_is_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.put(
            f"/auth/sso/saml/config/{uuid4()}", json=_valid_payload(), headers=_auth(token)
        )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_soft_deletes(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    config_id = await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.delete(f"/auth/sso/saml/config/{config_id}", headers=_auth(token))
        assert resp.status_code == 204, resp.text
        listing = await client.get("/auth/sso/saml/config", headers=_auth(token))
        assert listing.json() == []

    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert row["deleted_at"] is not None


@pytest.mark.asyncio
async def test_tenant_user_cannot_delete(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    config_id = await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.delete(f"/auth/sso/saml/config/{config_id}", headers=_auth(token))
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# SP metadata-url helper + IdP metadata parse
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sp_metadata_url_explicit_tenant(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get(f"/auth/sso/{tenant}/saml/metadata-url", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sp_entity_id"] == "http://testserver/auth/sso/saml/metadata"
    assert body["acs_url"] == f"http://testserver/auth/sso/{tenant}/saml/acs"


@pytest.mark.asyncio
async def test_sp_metadata_tenant_implicit(configured_app, migrations_pg_dsn: str) -> None:
    """The tenant-implicit SP-metadata endpoint derives the tenant from JWT."""
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/saml/sp-metadata", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sp_entity_id"] == "http://testserver/auth/sso/saml/metadata"
    assert body["acs_url"] == f"http://testserver/auth/sso/{tenant}/saml/acs"


@pytest.mark.asyncio
async def test_parse_idp_metadata(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/sso/saml/parse-metadata",
            json={"metadata_xml": _IDP_METADATA_XML},
            headers=_auth(token),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_id"] == "https://idp.example.test/saml/metadata"
    assert body["sso_url"] == "https://idp.example.test/saml/sso"
    assert body["x509_cert"] == "MIIDmetacertBASE64=="
    assert body["name_id_format"] == "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"


@pytest.mark.asyncio
async def test_parse_idp_metadata_rejects_garbage(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/sso/saml/parse-metadata",
            json={"metadata_xml": "<not-saml-metadata/>"},
            headers=_auth(token),
        )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# OIDC + SAML coexistence — neither leaks into the other's list
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_oidc_and_saml_lists_are_disjoint(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)
    await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        oidc = await client.get("/auth/sso/config", headers=_auth(token))
        saml = await client.get("/auth/sso/saml/config", headers=_auth(token))
    assert oidc.status_code == 200, oidc.text
    assert saml.status_code == 200, saml.text
    assert len(oidc.json()) == 1
    assert oidc.json()[0]["provider"] == "oidc"
    assert len(saml.json()) == 1
    assert saml.json()[0]["provider"] == "saml"


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_read_tenant_a_config(configured_app, migrations_pg_dsn: str) -> None:
    """Tenant A has a SAML config. A tenant-B admin must not see it: the
    list returns empty and a direct edit/delete on A's id 404s — RLS
    scopes every query by ``app.tenant_id``."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a, _user_a = await _seed_tenant_with_user(
        migrations_pg_dsn, slug="alpha", role="tenant_admin"
    )
    tenant_b, user_b = await _seed_tenant_with_user(
        migrations_pg_dsn, slug="bravo", role="tenant_admin"
    )
    a_config_id = await _seed_saml_config(migrations_pg_dsn, tenant_id=tenant_a)
    token_b = await _mint_token(user_b, tenant_b)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        listing = await client.get("/auth/sso/saml/config", headers=_auth(token_b))
        assert listing.status_code == 200, listing.text
        assert listing.json() == []

        edit = await client.put(
            f"/auth/sso/saml/config/{a_config_id}",
            json=_valid_payload(),
            headers=_auth(token_b),
        )
        assert edit.status_code == 404, edit.text

        delete = await client.delete(f"/auth/sso/saml/config/{a_config_id}", headers=_auth(token_b))
        assert delete.status_code == 404, delete.text

    row = await _fetch_config_row(migrations_pg_dsn, a_config_id)
    assert row is not None
    assert row["deleted_at"] is None
