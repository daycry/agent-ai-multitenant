"""Integration tests for the platform-global OIDC config CRUD (ADR 0047).

After the SSO global re-architecture (ADR 0047, supersedes the per-tenant
part of ADR 0031) the ``sso_configurations`` table is **platform-global**:
no ``tenant_id`` column, no RLS, identity by ``provider``/kind (one
``oidc`` row for the whole platform). The config CRUD backing the System
Admin "SSO configuration" UI is therefore **system_admin only** and runs
on the BYPASSRLS admin session. Coverage:

  * RBAC: unauthenticated -> 401; a non-system-admin (even a tenant_admin)
    -> 403 on read AND write; system_admin manages everything.
  * create -> 201, the row is persisted with the secret ENCRYPTED at rest
    (never plaintext), and the response NEVER echoes the secret.
  * a second create -> 409 (global uniqueness per provider).
  * edit without a secret keeps the stored one; edit with a new secret
    re-encrypts; toggling ``enabled`` flips the flag.
  * delete soft-deletes (the row disappears from the list).
  * templates + callback-url helper endpoints return sensible data
    (system_admin gated).

The per-tenant-isolation tests of the old model are gone (there is no
``tenant_id`` to isolate by); the security boundary they protected is now
the system_admin-only gate, asserted directly above.

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
# DB seed helpers — the table is GLOBAL now (no tenant_id column).
# ---------------------------------------------------------------------------
async def _seed_user(dsn: str, *, slug: str, is_system_admin: bool) -> UUID:
    """Insert a bare global user row. Returns its id."""
    user = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES ($1, $2, $3, $4)",
            user,
            f"{slug}@example.test",
            "argon2-placeholder",
            is_system_admin,
        )
    finally:
        await conn.close()
    return user


async def _seed_tenant_admin(dsn: str, *, slug: str) -> tuple[UUID, UUID]:
    """Insert a tenant + a tenant_admin user (NOT a system admin).

    Used to prove that a tenant_admin — the most privileged tenant role —
    is still forbidden from the platform-global SSO config (ADR 0047:
    config is system_admin only).
    """
    tenant = uuid4()
    user = await _seed_user(dsn, slug=slug, is_system_admin=False)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active)"
            " VALUES ($1, $2, $3, 'tenant_admin', true)",
            uuid4(),
            tenant,
            user,
        )
    finally:
        await conn.close()
    return tenant, user


async def _seed_global_oidc(dsn: str, *, enabled: bool = True) -> UUID:
    """Insert the single global OIDC config (no tenant_id). Returns its id."""
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


async def _mint_token(
    user_id: UUID, *, tenant_id: UUID | None = None, is_system_admin: bool = False
) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_admin=is_system_admin
    )


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
# RBAC — system_admin only (ADR 0047)
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
async def test_non_system_admin_cannot_create(configured_app, migrations_pg_dsn: str) -> None:
    """A tenant_admin (the top tenant role) is still forbidden — the
    global SSO config is system_admin only (ADR 0047)."""
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_admin(migrations_pg_dsn, slug="acme")
    token = await _mint_token(user, tenant_id=tenant, is_system_admin=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/config", json=_valid_payload(), headers=_auth(token))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_non_system_admin_cannot_list(configured_app, migrations_pg_dsn: str) -> None:
    """Reads are system_admin only too — a tenant_admin gets 403, not the
    config (ADR 0047: the platform-global config is not a tenant surface)."""
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_admin(migrations_pg_dsn, slug="acme")
    await _seed_global_oidc(migrations_pg_dsn)
    token = await _mint_token(user, tenant_id=tenant, is_system_admin=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/config", headers=_auth(token))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_system_admin_can_list(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    await _seed_global_oidc(migrations_pg_dsn)
    token = await _mint_token(admin, is_system_admin=True)

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
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

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
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

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
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

    payload = _valid_payload(client_secret_ref="vault:secret/data/oidc/acme")
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/auth/sso/config", json=payload, headers=_auth(token))
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_second_create_succeeds_with_display_name(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Multi-provider (0115): una SEGUNDA config OIDC con display_name -> 201
    (Google Y Microsoft a la vez); ambas visibles en la lista de config."""
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    await _seed_global_oidc(migrations_pg_dsn)
    token = await _mint_token(admin, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/sso/config",
            json=_valid_payload(display_name="Microsoft Entra"),
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
        listing = await client.get("/auth/sso/config", headers=_auth(token))
    assert listing.status_code == 200
    assert len(listing.json()) == 2


@pytest.mark.asyncio
async def test_second_create_without_display_name_is_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Multi-provider: la 2a config sin display_name -> 422 (botones del login
    indistinguibles)."""
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    await _seed_global_oidc(migrations_pg_dsn)
    token = await _mint_token(admin, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/sso/config",
            json=_valid_payload(display_name=""),
            headers=_auth(token),
        )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_edit_without_secret_keeps_stored_secret(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    config_id = await _seed_global_oidc(migrations_pg_dsn, enabled=False)
    token = await _mint_token(admin, is_system_admin=True)

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
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    config_id = await _seed_global_oidc(migrations_pg_dsn)
    token = await _mint_token(admin, is_system_admin=True)

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
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

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
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    config_id = await _seed_global_oidc(migrations_pg_dsn)
    token = await _mint_token(admin, is_system_admin=True)

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
async def test_non_system_admin_cannot_delete(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_admin(migrations_pg_dsn, slug="acme")
    config_id = await _seed_global_oidc(migrations_pg_dsn)
    token = await _mint_token(user, tenant_id=tenant, is_system_admin=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.delete(f"/auth/sso/config/{config_id}", headers=_auth(token))
    assert resp.status_code == 403, resp.text

    # The config is untouched.
    row = await _fetch_config_row(migrations_pg_dsn, config_id)
    assert row is not None
    assert row["deleted_at"] is None


# ---------------------------------------------------------------------------
# Templates + callback URL helpers (system_admin gated)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_templates_endpoint_lists_idps(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

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
async def test_templates_endpoint_forbidden_for_non_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_admin(migrations_pg_dsn, slug="acme")
    token = await _mint_token(user, tenant_id=tenant, is_system_admin=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/oidc/templates", headers=_auth(token))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_callback_url_endpoint(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/auth/sso/oidc/callback-url", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["callback_url"] == "http://testserver/auth/sso/oidc/callback"


# ---------------------------------------------------------------------------
# Public application base URL — GET/PUT (ADR 0047, operator-configurable)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_public_base_url_defaults_to_env_then_override_wins(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Fresh: GET reports the env bootstrap (is_override=False). After a PUT,
    the override WINS (is_override=True) and the derived callback URL reflects
    it — proving the value is operator-configurable, not env-locked."""
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Unset → env bootstrap (the fixture sets it to http://testserver).
        got = await client.get("/auth/sso/public-base-url", headers=_auth(token))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["base_url"] == "http://testserver"
        assert body["is_override"] is False
        assert body["env_default"] == "http://testserver"

        # Set the override (a trailing slash is normalised away).
        put = await client.put(
            "/auth/sso/public-base-url",
            json={"base_url": "https://agentic-orchestrator.com/"},
            headers=_auth(token),
        )
        assert put.status_code == 200, put.text
        assert put.json()["base_url"] == "https://agentic-orchestrator.com"
        assert put.json()["is_override"] is True

        # Override wins on the next read…
        got2 = await client.get("/auth/sso/public-base-url", headers=_auth(token))
        assert got2.json()["base_url"] == "https://agentic-orchestrator.com"
        assert got2.json()["is_override"] is True

        # …and the callback URL is the override + the well-known path.
        cb = await client.get("/auth/sso/oidc/callback-url", headers=_auth(token))
        assert cb.json()["callback_url"] == (
            "https://agentic-orchestrator.com/auth/sso/oidc/callback"
        )


@pytest.mark.asyncio
async def test_api_path_prefix_inserts_between_origin_and_sso_paths(
    configured_app, migrations_pg_dsn: str
) -> None:
    """ADR 0069 (opción C): el prefijo de API se inserta entre el origen y los
    paths SSO. Default "" = sin prefijo (callback actual); tras fijar `/api`,
    callback y SP metadata lo llevan → funciona bajo el reverse proxy single-origin."""
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        # Default: sin prefijo (retro-compatible).
        got = await client.get("/auth/sso/api-path-prefix", headers=_auth(token))
        assert got.status_code == 200, got.text
        assert got.json()["prefix"] == "" and got.json()["is_override"] is False
        cb0 = await client.get("/auth/sso/oidc/callback-url", headers=_auth(token))
        assert cb0.json()["callback_url"] == "http://testserver/auth/sso/oidc/callback"

        # Fijar /api (un trailing slash se normaliza).
        put = await client.put(
            "/auth/sso/api-path-prefix", json={"prefix": "/api/"}, headers=_auth(token)
        )
        assert put.status_code == 200, put.text
        assert put.json()["prefix"] == "/api" and put.json()["is_override"] is True

        # El callback OIDC y el SP metadata SAML ahora llevan el prefijo.
        cb = await client.get("/auth/sso/oidc/callback-url", headers=_auth(token))
        assert cb.json()["callback_url"] == "http://testserver/api/auth/sso/oidc/callback"
        meta = await client.get("/auth/sso/saml/sp-metadata", headers=_auth(token))
        assert meta.json()["sp_entity_id"] == "http://testserver/api/auth/sso/saml/metadata"
        assert meta.json()["acs_url"] == "http://testserver/api/auth/sso/saml/acs"

        # Un prefijo inválido (sin barra inicial) es 422.
        bad = await client.put(
            "/auth/sso/api-path-prefix", json={"prefix": "api"}, headers=_auth(token)
        )
        assert bad.status_code == 422, bad.text


@pytest.mark.asyncio
async def test_public_base_url_rejects_invalid(configured_app, migrations_pg_dsn: str) -> None:
    """A non-bare URL (carries a path) is a 422 and is never persisted."""
    await _truncate_all(migrations_pg_dsn)
    admin = await _seed_user(migrations_pg_dsn, slug="root", is_system_admin=True)
    token = await _mint_token(admin, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        bad = await client.put(
            "/auth/sso/public-base-url",
            json={"base_url": "https://example.com/auth/callback"},
            headers=_auth(token),
        )
        assert bad.status_code == 422, bad.text
        # Still the env default — nothing was stored.
        got = await client.get("/auth/sso/public-base-url", headers=_auth(token))
        assert got.json()["is_override"] is False


@pytest.mark.asyncio
async def test_public_base_url_non_system_admin_forbidden(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A tenant_admin cannot read OR write the public base URL (system_admin only)."""
    await _truncate_all(migrations_pg_dsn)
    tenant, user = await _seed_tenant_admin(migrations_pg_dsn, slug="acme")
    token = await _mint_token(user, tenant_id=tenant, is_system_admin=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        assert (
            await client.get("/auth/sso/public-base-url", headers=_auth(token))
        ).status_code == 403
        assert (
            await client.put(
                "/auth/sso/public-base-url",
                json={"base_url": "https://evil.example.com"},
                headers=_auth(token),
            )
        ).status_code == 403
