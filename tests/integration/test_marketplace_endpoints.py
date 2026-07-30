"""Integration tests for the /marketplace endpoints (Plan 09 task_09_03).

Drives the REST surface end-to-end against the real Postgres + RLS:

  - browse the catalog (global + own private; another tenant's private
    listing is hidden) and fetch detail,
  - install a listing -> the installation row persists AND an
    ``marketplace_audit_entries`` row is written,
  - uninstall -> the install is marked revoked + soft-deleted and a
    second audit row is written,
  - list_installed returns only the caller-tenant's installs,
  - pagination bounds (``limit``/``offset`` ge/le) are enforced (422),
  - RBAC: a plain ``tenant_user`` cannot install/uninstall (403),
  - signature is never echoed (only ``is_signed``),
  - cross-tenant (``@pytest.mark.cross_tenant``): tenant B cannot
    install another tenant's PRIVATE listing, cannot uninstall tenant
    A's installation, and never sees tenant A's installs.

Fixture pattern mirrors ``test_agents_endpoints.py``: seed via the
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: two tenants, an admin + a plain member in tenant A, a project in A,
# one tenant-agnostic source, and three listings (global + private A + B).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "project_a": uuid4(),
        "source": uuid4(),
        "global_listing": uuid4(),
        "private_a": uuid4(),
        "private_b": uuid4(),
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
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-mkte",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-mkte",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-mkte",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@mkte.test",
            "h",
            ids["member_a"],
            "member-a@mkte.test",
            "h",
            ids["admin_b"],
            "admin-b@mkte.test",
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
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            ids["project_a"],
            ids["tenant_a"],
            "Project A",
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type)"
            " VALUES ($1, 'official-catalog', 'official')",
            ids["source"],
        )
        # Global catalog listing (signed) + one private listing per tenant.
        # Manifest materializable (remediación 2026-07-17: install MATERIALIZA
        # de verdad y valida el manifest — un tool listing sin
        # implementation_type es 422; los skill listings materializan por
        # name/description sin manifest).
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level,"
            "  requested_permissions, signature, manifest)"
            " VALUES"
            " ($1, $2, NULL, 'tool', 'public-tool', '1.0.0', 'verified',"
            '  \'[{"type": "allowed_domains", "value": ["api.x.com"]}]\'::jsonb,'
            "  'sig-secret-do-not-leak',"
            '  \'{"implementation_type": "http_endpoint",'
            '     "implementation_ref": "https://api.x.com/tool/{q}",'
            '     "input_schema": {"type": "object",'
            '       "properties": {"q": {"type": "string"}}}}\'::jsonb),'
            " ($3, $2, $4, 'skill', 'priv-a-skill', '0.1.0', 'community', '[]'::jsonb, NULL,"
            "  '{}'::jsonb),"
            " ($5, $2, $6, 'skill', 'priv-b-skill', '0.1.0', 'experimental', '[]'::jsonb, NULL,"
            "  '{}'::jsonb)",
            ids["global_listing"],
            ids["source"],
            ids["private_a"],
            ids["tenant_a"],
            ids["private_b"],
            ids["tenant_b"],
        )
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_agents_endpoints.configured_app)
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


# ===========================================================================
# Auth gate
# ===========================================================================
@pytest.mark.asyncio
async def test_unauthenticated_browse_is_401(configured_app) -> None:
    async with _client(configured_app) as client:
        resp = await client.get("/marketplace/listings")
    assert resp.status_code == 401


# ===========================================================================
# Browse + detail
# ===========================================================================
@pytest.mark.asyncio
async def test_browse_lists_global_and_own_private_only(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get("/marketplace/listings", headers=headers)
    assert resp.status_code == 200, resp.text
    names = {row["name"] for row in resp.json()}
    # Global catalog + tenant A's own private listing; never tenant B's.
    assert names == {"public-tool", "priv-a-skill"}
    assert "priv-b-skill" not in names


@pytest.mark.asyncio
async def test_browse_filter_by_kind_and_trust_level(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        tools = await client.get("/marketplace/listings?kind=tool", headers=headers)
        verified = await client.get("/marketplace/listings?trust_level=verified", headers=headers)
        bad = await client.get("/marketplace/listings?kind=not_a_kind", headers=headers)

    assert tools.status_code == 200
    assert {r["name"] for r in tools.json()} == {"public-tool"}
    assert verified.status_code == 200
    assert {r["name"] for r in verified.json()} == {"public-tool"}
    # Unknown enum value -> 422.
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_detail_never_echoes_signature(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get(
            f"/marketplace/listings/{seeded['global_listing']}", headers=headers
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "public-tool"
    assert body["is_signed"] is True
    # The raw signature must never cross the wire.
    assert "signature" not in body
    assert "sig-secret-do-not-leak" not in resp.text


@pytest.mark.asyncio
async def test_detail_404_on_unknown(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get(f"/marketplace/listings/{uuid4()}", headers=headers)
    assert resp.status_code == 404


# ===========================================================================
# Install persists + audits
# ===========================================================================
@pytest.mark.asyncio
async def test_install_persists_and_audits(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={
                "listing_id": str(seeded["global_listing"]),
                "project_id": str(seeded["project_a"]),
                "granted_permissions": [{"type": "allowed_domains", "value": ["api.x.com"]}],
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "enabled"
        assert body["version"] == "1.0.0"
        assert UUID(body["tenant_id"]) == seeded["tenant_a"]
        assert UUID(body["installed_by"]) == seeded["admin_a"]
        assert body["granted_permissions"] == [{"type": "allowed_domains", "value": ["api.x.com"]}]

        # The install shows up in list_installed.
        listed = await client.get("/marketplace/installations", headers=headers)
        assert listed.status_code == 200
        assert {UUID(r["id"]) for r in listed.json()} == {UUID(body["id"])}

    # An install audit row was written for the tenant.
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], "install") == 1


@pytest.mark.asyncio
async def test_install_unknown_listing_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(uuid4())},
            headers=headers,
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_live_install_conflicts(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    body = {"listing_id": str(seeded["global_listing"])}

    async with _client(configured_app) as client:
        first = await client.post("/marketplace/installations", json=body, headers=headers)
        assert first.status_code == 201, first.text
        second = await client.post("/marketplace/installations", json=body, headers=headers)
    assert second.status_code == 409


# ===========================================================================
# Uninstall revokes + audits
# ===========================================================================
@pytest.mark.asyncio
async def test_uninstall_revokes_and_audits(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["global_listing"])},
            headers=headers,
        )
        assert install.status_code == 201
        install_id = install.json()["id"]

        delete = await client.delete(f"/marketplace/installations/{install_id}", headers=headers)
        assert delete.status_code == 204

        # The revoked install is hidden from the default list...
        listed = await client.get("/marketplace/installations", headers=headers)
        assert listed.json() == []
        # ...but visible (status=revoked) with include_revoked=true.
        with_revoked = await client.get(
            "/marketplace/installations?include_revoked=true", headers=headers
        )
        rows = with_revoked.json()
        assert len(rows) == 1
        assert rows[0]["status"] == "revoked"
        assert rows[0]["revoked_at"] is not None
        assert UUID(rows[0]["revoked_by"]) == seeded["admin_a"]

        # A second delete on the now-revoked row 404s.
        again = await client.delete(f"/marketplace/installations/{install_id}", headers=headers)
        assert again.status_code == 404

    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], "uninstall") == 1


@pytest.mark.asyncio
async def test_uninstall_unknown_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.delete(f"/marketplace/installations/{uuid4()}", headers=headers)
    assert resp.status_code == 404


# ===========================================================================
# Pagination bounds
# ===========================================================================
@pytest.mark.asyncio
async def test_pagination_bounds_enforced(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        too_big = await client.get("/marketplace/listings?limit=99999", headers=headers)
        zero = await client.get("/marketplace/listings?limit=0", headers=headers)
        neg_offset = await client.get("/marketplace/listings?offset=-1", headers=headers)
        ok = await client.get("/marketplace/listings?limit=1&offset=0", headers=headers)

    assert too_big.status_code == 422
    assert zero.status_code == 422
    assert neg_offset.status_code == 422
    assert ok.status_code == 200
    assert len(ok.json()) == 1


# ===========================================================================
# RBAC — a plain member cannot install / uninstall
# ===========================================================================
@pytest.mark.asyncio
async def test_plain_member_cannot_install(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {member_token}"}

    async with _client(configured_app) as client:
        # A plain member CAN browse...
        browse = await client.get("/marketplace/listings", headers=headers)
        assert browse.status_code == 200
        # ...but CANNOT install.
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["global_listing"])},
            headers=headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_plain_member_cannot_uninstall(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])

    async with _client(configured_app) as client:
        install = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["global_listing"])},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert install.status_code == 201
        install_id = install.json()["id"]

        resp = await client.delete(
            f"/marketplace/installations/{install_id}",
            headers={"Authorization": f"Bearer {member_token}"},
        )
    assert resp.status_code == 403


# ===========================================================================
# Cross-tenant isolation — the multi-tenancy guarantee.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_install_tenant_a_private_listing(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A's PRIVATE listing is invisible to tenant B; trying to
    install it by id is a clean 404 (no cross-tenant leak)."""
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])
    headers = {"Authorization": f"Bearer {token_b}"}

    async with _client(configured_app) as client:
        # B cannot even see A's private listing in browse.
        browse = await client.get("/marketplace/listings", headers=headers)
        assert "priv-a-skill" not in {r["name"] for r in browse.json()}
        # ...and cannot install it by id.
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["private_a"])},
            headers=headers,
        )
    assert resp.status_code == 404


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_uninstall_or_see_tenant_a_install(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A installs a global listing. Tenant B must not see it in
    list_installed and must get 404 trying to revoke it."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        install = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["global_listing"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert install.status_code == 201
        install_id = install.json()["id"]

        # B's list_installed never includes A's install.
        listed_b = await client.get(
            "/marketplace/installations",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert listed_b.status_code == 200
        assert listed_b.json() == []

        # B cannot revoke A's install -> 404 (RLS hides the row).
        revoke_b = await client.delete(
            f"/marketplace/installations/{install_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert revoke_b.status_code == 404

    # A's install audit exists for A; none leaked to B.
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], "install") == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_b"], "uninstall") == 0
