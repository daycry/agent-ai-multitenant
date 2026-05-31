"""Integration tests for granular per-permission consent (Plan 09 task_09_07).

Drives the consent surface end-to-end against the real Postgres + RLS:

  - a community/experimental listing (per-permission consent required, per
    the trust policy of task_09_04) installs DISABLED with no granted
    permissions; a verified listing installs ENABLED (minimal friction),
  - GET .../permissions surfaces every requested permission tagged
    GRANTED / DENIED / PENDING + the consent_required / all_granted flags,
  - granting EVERY requested permission persists them and flips the install
    to ENABLED + writes a ``consent`` audit row,
  - a PARTIAL deny keeps the install DISABLED, persists the denial, and
    writes both a ``consent`` and a ``consent_denied`` audit row,
  - a decision on a permission the listing never requested is a 422,
  - RBAC: a plain ``tenant_user`` cannot POST consent (403),
  - cross-tenant (``@pytest.mark.cross_tenant``): tenant B cannot read or
    decide consent on tenant A's installation (404).

Fixture wiring mirrors ``test_marketplace_endpoints.py``: seed via the
BYPASSRLS migrations role, mint JWTs binding each user to a tenant, then
drive the API via AsyncClient.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "source": uuid4(),
        "community_listing": uuid4(),
        "verified_listing": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources,"
            " projects, agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-consent",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-consent",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@consent.test",
            "h",
            ids["member_a"],
            "member-a@consent.test",
            "h",
            ids["admin_b"],
            "admin-b@consent.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user'),"
            " ($7, $8, $9, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type)"
            " VALUES ($1, 'official-catalog', 'official')",
            ids["source"],
        )
        # Global community listing (consent required) requesting two perms,
        # plus a global verified listing (no consent) requesting one.
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level,"
            "  requested_permissions, signature)"
            " VALUES"
            " ($1, $2, NULL, 'tool', 'community-tool', '1.0.0', 'community',"
            '  \'[{"type": "allowed_domains", "value": ["api.x.com"]},'
            '     {"type": "network_policy", "value": "restricted"}]\'::jsonb, NULL),'
            " ($3, $2, NULL, 'tool', 'verified-tool', '1.0.0', 'verified',"
            '  \'[{"type": "allowed_domains", "value": ["api.y.com"]}]\'::jsonb,'
            "  'sig')",
            ids["community_listing"],
            ids["source"],
            ids["verified_listing"],
        )
    finally:
        await conn.close()
    return ids


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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _count_audit(dsn: str, tenant_id: UUID, action: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM marketplace_audit_entries"
            " WHERE tenant_id = $1 AND action = $2",
            tenant_id,
            action,
        )
        return int(row["n"])
    finally:
        await conn.close()


async def _install(client: AsyncClient, listing_id: UUID, headers: dict[str, str]) -> dict:
    resp = await client.post(
        "/marketplace/installations",
        json={"listing_id": str(listing_id)},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# Install-time consent gate
# ===========================================================================
@pytest.mark.asyncio
async def test_community_install_lands_disabled_pending_consent(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        body = await _install(client, seeded["community_listing"], headers)
        # Consent required -> disabled, nothing granted yet.
        assert body["status"] == "disabled"
        assert body["granted_permissions"] == []
        assert body["denied_permissions"] == []

        perms = await client.get(
            f"/marketplace/installations/{body['id']}/permissions", headers=headers
        )
        assert perms.status_code == 200, perms.text
        pb = perms.json()
        assert pb["consent_required"] is True
        assert pb["all_granted"] is False
        # Both requested permissions surface as PENDING.
        states = {p["type"]: p["state"] for p in pb["permissions"]}
        assert states == {"allowed_domains": "pending", "network_policy": "pending"}


@pytest.mark.asyncio
async def test_verified_install_is_enabled_without_consent(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        body = await _install(client, seeded["verified_listing"], headers)
        assert body["status"] == "enabled"

        perms = await client.get(
            f"/marketplace/installations/{body['id']}/permissions", headers=headers
        )
        pb = perms.json()
        # No per-permission consent required (minimal friction); the install
        # is enabled regardless of the per-permission state. ``all_granted``
        # is only the enable-gate when ``consent_required`` is True.
        assert pb["consent_required"] is False
        assert pb["status"] == "enabled"


# ===========================================================================
# Per-permission grant persists + enables
# ===========================================================================
@pytest.mark.asyncio
async def test_grant_all_permissions_enables_and_audits(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await _install(client, seeded["community_listing"], headers)
        install_id = install["id"]

        resp = await client.post(
            f"/marketplace/installations/{install_id}/consent",
            json={
                "decisions": [
                    {"type": "allowed_domains", "decision": "grant"},
                    {"type": "network_policy", "decision": "grant"},
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        pb = resp.json()
        assert pb["status"] == "enabled"
        assert pb["all_granted"] is True
        assert {p["state"] for p in pb["permissions"]} == {"granted"}

        # The granted descriptors persist on the installation row.
        listed = await client.get(
            "/marketplace/installations?include_revoked=true", headers=headers
        )
        row = next(r for r in listed.json() if r["id"] == install_id)
        assert row["status"] == "enabled"
        granted_types = {p["type"] for p in row["granted_permissions"]}
        assert granted_types == {"allowed_domains", "network_policy"}

    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], "consent") == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], "consent_denied") == 0


# ===========================================================================
# Partial deny keeps the install disabled + audits consent_denied
# ===========================================================================
@pytest.mark.asyncio
async def test_partial_deny_keeps_disabled_and_audits(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await _install(client, seeded["community_listing"], headers)
        install_id = install["id"]

        resp = await client.post(
            f"/marketplace/installations/{install_id}/consent",
            json={
                "decisions": [
                    {"type": "allowed_domains", "decision": "grant"},
                    {"type": "network_policy", "decision": "deny"},
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        pb = resp.json()
        # One denied required permission -> install stays disabled.
        assert pb["status"] == "disabled"
        assert pb["all_granted"] is False
        states = {p["type"]: p["state"] for p in pb["permissions"]}
        assert states == {"allowed_domains": "granted", "network_policy": "denied"}

    # Both a consent AND a consent_denied audit row were written.
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], "consent") == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], "consent_denied") == 1


@pytest.mark.asyncio
async def test_decision_on_unrequested_permission_is_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await _install(client, seeded["community_listing"], headers)
        resp = await client.post(
            f"/marketplace/installations/{install['id']}/consent",
            json={"decisions": [{"type": "allowed_paths", "decision": "grant"}]},
            headers=headers,
        )
    # allowed_paths is a valid permission key but NOT requested by this
    # listing -> the consent logic rejects it (422).
    assert resp.status_code == 422


# ===========================================================================
# RBAC — a plain member cannot decide consent
# ===========================================================================
@pytest.mark.asyncio
async def test_plain_member_cannot_decide_consent(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])

    async with _client(configured_app) as client:
        install = await _install(
            client, seeded["community_listing"], {"Authorization": f"Bearer {admin_token}"}
        )
        # A plain member CAN read the permission surface...
        read = await client.get(
            f"/marketplace/installations/{install['id']}/permissions",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert read.status_code == 200
        # ...but CANNOT decide consent.
        resp = await client.post(
            f"/marketplace/installations/{install['id']}/consent",
            json={"decisions": [{"type": "allowed_domains", "decision": "grant"}]},
            headers={"Authorization": f"Bearer {member_token}"},
        )
    assert resp.status_code == 403


# ===========================================================================
# Cross-tenant isolation
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_read_or_decide_tenant_a_consent(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A installs a community listing. Tenant B must not be able to
    read its permission surface nor decide its consent (404 — RLS hides the
    install row, no cross-tenant write)."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        install = await _install(
            client, seeded["community_listing"], {"Authorization": f"Bearer {token_a}"}
        )
        install_id = install["id"]

        read_b = await client.get(
            f"/marketplace/installations/{install_id}/permissions",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert read_b.status_code == 404

        decide_b = await client.post(
            f"/marketplace/installations/{install_id}/consent",
            json={"decisions": [{"type": "allowed_domains", "decision": "grant"}]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert decide_b.status_code == 404

    # No consent audit leaked into tenant B.
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_b"], "consent") == 0
