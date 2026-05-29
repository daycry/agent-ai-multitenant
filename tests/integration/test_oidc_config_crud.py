"""Integration tests for the per-tenant OIDC config CRUD (Plan 08 task_08_03).

These back the Tenant-Admin SSO-config UI. No IdP is involved — the CRUD
is pure DB + RBAC + RLS. Coverage:

  * RBAC: unauthenticated -> 401; a `tenant_user` write -> 403; reads
    allowed for any tenant member; writes require `tenant_admin`.
  * create -> 201, the row is persisted with the secret ENCRYPTED at rest
    (never plaintext), and the response NEVER echoes the secret.
  * a second create -> 409 (one OIDC config per tenant).
  * edit without a secret keeps the stored one; edit with a new secret
    re-encrypts; toggling `enabled` flips the flag.
  * delete soft-deletes (the row disappears from the list).
  * templates + callback-url helper endpoints return sensible data.
  * cross-tenant isolation (@pytest.mark.cross_tenant): tenant A's config
    id never resolves for a tenant-B session (RLS) — read/edit/delete 404.

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

_PLAINTEXT_SECRET = "super-secret-oidc-value"
_NEW_SECRET = "rotated-oidc-value"
_ISSUER = "https://idp.example.test"
_CLIENT_ID = "acme-oidc-client"


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


async def _seed_oidc_config(dsn: str, *, tenant_id: UUID, enabled: bool = True) -> UUID:
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, tenant_id, provider, display_name, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings)
            VALUES ($1, $2, 'oidc', 'Acme OIDC', $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            """,
            config_id,
            tenant_id,
            enabled,
            _ISSUER,
            _CLIENT_ID,
            encrypt_client_secret(_PLAINTEXT_SECRET),
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
        "display_name": "Acme OIDC",
        "enabled": True,
        "issuer": _ISSUER,
        "client_id": _CLIENT_ID,
        "client_secret": _PLAINTEXT_SECRET,
        "scopes": ["openid", "email", "profile"],
        "claim_mappings": {"email": "email", "full_name": "name"},
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
        resp = await client.get("/auth/sso/config")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_tenant_user_cannot_create(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/config", json=_valid_payload(), headers=_auth(token))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_tenant_user_can_list(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/config", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# Create / read — secret never echoed, persisted encrypted
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_persists_encrypted_and_never_echoes_secret(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/config", json=_valid_payload(), headers=_auth(token))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        config_id = UUID(body["id"])

        # The secret is NOT in the response — only the indicator + source.
        assert "client_secret" not in body
        assert "client_secret_encrypted" not in body
        assert "client_secret_ref" not in body
        assert body["has_client_secret"] is True
        assert body["client_secret_source"] == "encrypted"
        assert body["enabled"] is True
        # No plaintext anywhere in the serialized response.
        assert _PLAINTEXT_SECRET not in resp.text

    # In the DB the secret is Fernet ciphertext (decryptable back to the
    # plaintext), never stored in clear, and no Vault ref is set.
    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert row["client_secret_ref"] is None
    assert row["client_secret_encrypted"] is not None
    assert row["client_secret_encrypted"] != _PLAINTEXT_SECRET
    assert decrypt_client_secret(row["client_secret_encrypted"]) == _PLAINTEXT_SECRET


@pytest.mark.asyncio
async def test_create_requires_a_secret(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    payload = _valid_payload()
    del payload["client_secret"]
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/config", json=payload, headers=_auth(token))
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_rejects_both_secret_forms(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    payload = _valid_payload(client_secret_ref="vault:secret/data/oidc/acme")
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/config", json=payload, headers=_auth(token))
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_second_create_is_409(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/config", json=_valid_payload(), headers=_auth(token))
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_edit_without_secret_keeps_stored_secret(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    config_id = await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant, enabled=False)
    token = await _mint_token(user, tenant)

    payload = _valid_payload(enabled=True)
    del payload["client_secret"]  # no secret in the edit
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.put(f"/auth/sso/config/{config_id}", json=payload, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True

    # Stored secret is unchanged (still decrypts to the original).
    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert decrypt_client_secret(row["client_secret_encrypted"]) == _PLAINTEXT_SECRET


@pytest.mark.asyncio
async def test_edit_with_new_secret_reencrypts(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    config_id = await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.put(
            f"/auth/sso/config/{config_id}",
            json=_valid_payload(client_secret=_NEW_SECRET),
            headers=_auth(token),
        )
    assert resp.status_code == 200, resp.text
    assert _NEW_SECRET not in resp.text

    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert decrypt_client_secret(row["client_secret_encrypted"]) == _NEW_SECRET


@pytest.mark.asyncio
async def test_edit_missing_config_is_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.put(
            f"/auth/sso/config/{uuid4()}", json=_valid_payload(), headers=_auth(token)
        )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_soft_deletes(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_admin")
    config_id = await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.delete(f"/auth/sso/config/{config_id}", headers=_auth(token))
        assert resp.status_code == 204, resp.text
        # Gone from the list.
        listing = await client.get("/auth/sso/config", headers=_auth(token))
        assert listing.json() == []

    # Row still exists in the DB but with deleted_at set.
    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert row["deleted_at"] is not None


@pytest.mark.asyncio
async def test_tenant_user_cannot_delete(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    config_id = await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant)
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.delete(f"/auth/sso/config/{config_id}", headers=_auth(token))
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Templates + callback URL helpers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_templates_endpoint_lists_idps(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/oidc/templates", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    templates = resp.json()
    ids = {t["template_id"] for t in templates}
    assert {"azure_ad", "google_workspace", "okta", "auth0", "github", "gitlab"} <= ids
    azure = next(t for t in templates if t["template_id"] == "azure_ad")
    assert "tenant" in azure["required_params"]
    assert "openid" in azure["default_scopes"]


@pytest.mark.asyncio
async def test_callback_url_endpoint(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_with_user(migrations_pg_dsn, slug="acme", role="tenant_user")
    token = await _mint_token(user, tenant)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/oidc/callback-url", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["callback_url"] == "http://testserver/auth/sso/oidc/callback"


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_read_tenant_a_config(configured_app, migrations_pg_dsn: str) -> None:
    """Tenant A has an OIDC config. A tenant-B admin must not see it: the
    list returns empty and a direct edit/delete on A's id 404s — RLS
    scopes every query by ``app.tenant_id``."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a, _user_a = await _seed_tenant_with_user(
        migrations_pg_dsn, slug="alpha", role="tenant_admin"
    )
    tenant_b, user_b = await _seed_tenant_with_user(
        migrations_pg_dsn, slug="bravo", role="tenant_admin"
    )
    a_config_id = await _seed_oidc_config(migrations_pg_dsn, tenant_id=tenant_a)
    token_b = await _mint_token(user_b, tenant_b)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # B's list does NOT include A's config.
        listing = await client.get("/auth/sso/config", headers=_auth(token_b))
        assert listing.status_code == 200, listing.text
        assert listing.json() == []

        # B cannot edit A's config (RLS hides the row -> 404).
        edit = await client.put(
            f"/auth/sso/config/{a_config_id}", json=_valid_payload(), headers=_auth(token_b)
        )
        assert edit.status_code == 404, edit.text

        # B cannot delete A's config either.
        delete = await client.delete(f"/auth/sso/config/{a_config_id}", headers=_auth(token_b))
        assert delete.status_code == 404, delete.text

    # A's config is untouched.
    row = await _fetch_config_row(migrations_pg_dsn, a_config_id)
    assert row is not None
    assert row["deleted_at"] is None
