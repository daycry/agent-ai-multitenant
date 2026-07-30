"""Integration tests for the platform-global SSO config (task_sso_01, ADR 0047).

After migration ``0076_sso_global`` the ``sso_configurations`` table is
**platform-global**: no ``tenant_id`` column, no RLS policy, identity by
``provider``/kind (one ``oidc`` + one ``saml`` row for the whole platform).
Coverage:

  * the schema is global: ``tenant_id`` is gone, RLS is OFF, there is no
    ``tenant_isolation`` policy, and — desde la migración 0115
    (multi-provider) — tampoco queda NINGUNA unique por kind.
  * a second ``oidc`` row is allowed (multi-provider; display_name
    disambiguates and the API layer enforces it from the 2nd config on).
  * ``button_label`` round-trips (write → read).
  * the loaders ``_load_enabled_oidc_config`` / ``_load_enabled_saml_config``
    return the GLOBAL enabled config (no tenant argument), reading on the
    BYPASSRLS admin engine.
  * a disabled / soft-deleted config is not returned by the loaders.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the session fixtures create a throwaway DB and flush Redis 15.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.sso.secrets import encrypt_client_secret

pytestmark = pytest.mark.integration

_ISSUER = "https://idp.example.test"
_CLIENT_ID = "platform-oidc-client"
_PLAINTEXT_SECRET = "super-secret-oidc-value"
_SAML_ENTITY = "https://idp.example.test/saml/metadata"
_SAML_SSO = "https://idp.example.test/saml/sso"
_SAML_CERT = "-----BEGIN CERTIFICATE-----\nMIIDfake\n-----END CERTIFICATE-----"


# ---------------------------------------------------------------------------
# DB seed helpers — note: NO tenant_id column anymore (global table).
# ---------------------------------------------------------------------------
async def _seed_global_oidc(
    dsn: str, *, enabled: bool = True, button_label: str | None = None, deleted: bool = False
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, button_label, enabled, issuer,
                 client_id, client_secret_encrypted, scopes, claim_mappings,
                 deleted_at)
            VALUES ($1, 'oidc', 'Platform OIDC', $2, $3, $4, $5, $6,
                    $7::jsonb, $8::jsonb, $9)
            """,
            uuid4(),
            button_label,
            enabled,
            _ISSUER,
            _CLIENT_ID,
            encrypt_client_secret(_PLAINTEXT_SECRET),
            json.dumps(["openid", "email", "profile"]),
            json.dumps({}),
            datetime.now(tz=UTC) if deleted else None,
        )
    finally:
        await conn.close()


async def _seed_global_saml(
    dsn: str, *, enabled: bool = True, button_label: str | None = None
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, button_label, enabled,
                 idp_entity_id, idp_sso_url, idp_x509_cert,
                 attribute_mappings)
            VALUES ($1, 'saml', 'Platform SAML', $2, $3, $4, $5, $6, $7::jsonb)
            """,
            uuid4(),
            button_label,
            enabled,
            _SAML_ENTITY,
            _SAML_SSO,
            _SAML_CERT,
            json.dumps({}),
        )
    finally:
        await conn.close()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE sso_configurations RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


async def _fetchval(dsn: str, sql: str, *args: object) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Settings fixture — wire the loaders' admin engine at the test DB.
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_settings(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    try:
        yield
    finally:
        reset_engine_cache()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Schema: the table is platform-global
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_schema_is_global_no_tenant_identity(configured_settings, admin_pg_dsn: str) -> None:
    # tenant_id column is gone.
    has_tenant_col = await _fetchval(
        admin_pg_dsn,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'sso_configurations' AND column_name = 'tenant_id'",
    )
    assert has_tenant_col == 0, "global table must not have a tenant_id column"

    # button_label column exists.
    has_button = await _fetchval(
        admin_pg_dsn,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'sso_configurations' AND column_name = 'button_label'",
    )
    assert has_button == 1

    # RLS is OFF and there is no tenant_isolation policy.
    rls_on = await _fetchval(
        admin_pg_dsn,
        "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n "
        "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
        "AND c.relname = 'sso_configurations'",
    )
    assert rls_on is False, "global table must have RLS disabled"
    policy_count = await _fetchval(
        admin_pg_dsn,
        "SELECT count(*) FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = 'sso_configurations'",
    )
    assert policy_count == 0, "global table must have no RLS policy"

    # Multi-provider (0115): NEITHER unique remains — several configs of the
    # same kind may coexist; the per-tenant one is long gone (0076).
    constraints = await _fetchval(
        admin_pg_dsn,
        "SELECT array_agg(conname) FROM pg_constraint "
        "WHERE conrelid = 'sso_configurations'::regclass AND contype = 'u'",
    )
    names = set(constraints or [])
    assert "uq_sso_config_provider" not in names
    assert "uq_sso_config_tenant_provider" not in names


@pytest.mark.asyncio
async def test_global_second_oidc_row_is_allowed(configured_settings, admin_pg_dsn: str) -> None:
    """Multi-provider (migración 0115): la unique singleton por kind se
    eliminó — pueden coexistir varias configs OIDC (el display_name las
    desambigua en el login; la capa API exige display_name a partir de la
    segunda). El candado antiguo pinneaba el UniqueViolationError."""
    await _truncate(admin_pg_dsn)
    await _seed_global_oidc(admin_pg_dsn)
    await _seed_global_oidc(admin_pg_dsn)
    rows = await _fetchval(
        admin_pg_dsn,
        "SELECT count(*) FROM sso_configurations WHERE provider = 'oidc'",
    )
    assert rows == 2


@pytest.mark.asyncio
async def test_button_label_round_trips(configured_settings, admin_pg_dsn: str) -> None:
    await _truncate(admin_pg_dsn)
    await _seed_global_oidc(admin_pg_dsn, button_label="Sign in with Acme")
    stored = await _fetchval(
        admin_pg_dsn,
        "SELECT button_label FROM sso_configurations WHERE provider = 'oidc'",
    )
    assert stored == "Sign in with Acme"


# ---------------------------------------------------------------------------
# Loaders return the GLOBAL config (no tenant argument)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_oidc_loader_returns_global_config(configured_settings, admin_pg_dsn: str) -> None:
    from api_server.routers.sso import _load_enabled_oidc_config

    await _truncate(admin_pg_dsn)
    await _seed_global_oidc(admin_pg_dsn, button_label="Acme")

    row = await _load_enabled_oidc_config()
    assert row is not None
    assert row.provider == "oidc"
    assert row.issuer == _ISSUER
    assert row.button_label == "Acme"


@pytest.mark.asyncio
async def test_saml_loader_returns_global_config(configured_settings, admin_pg_dsn: str) -> None:
    from api_server.routers.sso import _load_enabled_saml_config

    await _truncate(admin_pg_dsn)
    await _seed_global_saml(admin_pg_dsn)

    row = await _load_enabled_saml_config()
    assert row is not None
    assert row.provider == "saml"
    assert row.idp_entity_id == _SAML_ENTITY


@pytest.mark.asyncio
async def test_loader_ignores_disabled_and_deleted(configured_settings, admin_pg_dsn: str) -> None:
    from api_server.routers.sso import _load_enabled_oidc_config

    await _truncate(admin_pg_dsn)
    # Only a disabled config exists -> loader returns nothing.
    await _seed_global_oidc(admin_pg_dsn, enabled=False)
    assert await _load_enabled_oidc_config() is None

    await _truncate(admin_pg_dsn)
    # Only a soft-deleted config exists -> loader returns nothing.
    await _seed_global_oidc(admin_pg_dsn, deleted=True)
    assert await _load_enabled_oidc_config() is None
