"""Integration tests for cross-tenant sharing (Plan 09 task_09_17).

Cross-tenant sharing is the one place the marketplace deliberately crosses
tenant boundaries — so it is an EXPLICIT, opt-in, System-Admin-audited GRANT,
never an implicit RLS bypass. A tenant publishes a PRIVATE listing (Phase E
task_09_16) and may OPT IN to share it with a single target tenant; the target
can then see/install the listing ONLY through that grant. These tests drive
the REST surface end-to-end against the real Postgres + RLS:

  - ``POST /marketplace/shares`` makes the OWNER's private listing
    visible + installable to the TARGET tenant only,
  - a NON-target tenant still cannot see it (``@pytest.mark.cross_tenant``),
  - ``DELETE /marketplace/shares/{id}`` removes the visibility (the
    ``marketplace_listings_shared_read`` RLS policy no longer matches),
  - every share / revoke writes a ``share`` audit entry,
  - the System Admin (``GET /admin/marketplace/shares``, BYPASSRLS) enumerates
    ALL shares for audit,
  - default = nothing shared (no implicit visibility),
  - RBAC: a plain ``tenant_user`` cannot share (403); a tenant cannot share
    another tenant's listing (404) nor revoke another tenant's grant (404).

Tenant boundaries are the FEATURE here: a shared resource is visible to the
target tenant ONLY through the explicit, audited grant. Fixture wiring mirrors
``test_private_marketplace.py``.
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


# A well-formed SKILL.md (Phase C skill_format) — a tenant's internal skill.
_SKILL_MD = """\
---
name: shared-reporter
description: Generates a shareable status report.
version: 1.0.0
dependencies:
  - jinja2
permissions:
  allowed_paths: [/workspace/reports]
  network_policy: none
examples:
  - title: Weekly report
    prompt: "Generate this week's status report"
---

# Shared Reporter

A tenant-internal skill that compiles a shareable status report.
"""


async def _seed(dsn: str) -> dict[str, UUID]:
    """Three tenants (A owner, B target, C bystander) + admins/members."""
    ids = {
        "tenant_a": uuid4(),  # owner
        "tenant_b": uuid4(),  # target
        "tenant_c": uuid4(),  # non-target bystander
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "admin_c": uuid4(),
        "sysadmin": uuid4(),
        "source": uuid4(),
        "global_listing": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_shares,"
            " marketplace_installations, marketplace_listings, marketplace_sources,"
            " projects, agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-share",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-share",
            ids["tenant_c"],
            "Tenant C",
            "tenant-c-share",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, $3, false), ($4, $5, $6, false), ($7, $8, $9, false),"
            " ($10, $11, $12, false), ($13, $14, $15, true)",
            ids["admin_a"],
            "admin-a@share.test",
            "h",
            ids["member_a"],
            "member-a@share.test",
            "h",
            ids["admin_b"],
            "admin-b@share.test",
            "h",
            ids["admin_c"],
            "admin-c@share.test",
            "h",
            ids["sysadmin"],
            "sysadmin@share.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user'),"
            " ($7, $8, $9, 'tenant_admin'),"
            " ($10, $11, $12, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
            uuid4(),
            ids["tenant_c"],
            ids["admin_c"],
        )
        # A global (NULL-tenant) catalog listing every tenant can browse.
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type)"
            " VALUES ($1, 'official-catalog', 'official')",
            ids["source"],
        )
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level,"
            "  requested_permissions, signature)"
            " VALUES ($1, $2, NULL, 'tool', 'public-tool', '1.0.0', 'verified',"
            " '[]'::jsonb, 'sig')",
            ids["global_listing"],
            ids["source"],
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


async def _mint_token(
    user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False
) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _publish_private_skill(client: AsyncClient, token: str) -> str:
    """Owner publishes a private skill; returns its listing id."""
    resp = await client.post(
        "/marketplace/private/listings",
        json={"kind": "skill", "manifest": _SKILL_MD},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _audit_share_count(dsn: str, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM marketplace_audit_entries"
            " WHERE tenant_id = $1 AND action = 'share'"
            "   AND detail->>'event' LIKE 'cross_tenant_share%'",
            tenant_id,
        )
        return int(row["n"])
    finally:
        await conn.close()


# ===========================================================================
# Default: nothing shared — the target sees only global, not A's private.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_default_no_sharing(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        listing_id = await _publish_private_skill(client, token_a)

        # With NO share, tenant B sees only the global catalog, never A's
        # private listing.
        browse_b = await client.get(
            "/marketplace/listings", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert browse_b.status_code == 200
        b_ids = {r["id"] for r in browse_b.json()}
        assert str(seeded["global_listing"]) in b_ids
        assert listing_id not in b_ids

        # And cannot fetch it by id (404 — RLS hides it).
        detail_b = await client.get(
            f"/marketplace/listings/{listing_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert detail_b.status_code == 404


# ===========================================================================
# A share makes the listing visible + installable to the TARGET only.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_share_makes_listing_visible_and_installable_to_target_only(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A shares its private skill with tenant B (the target).

    The target B can then see + install it; the bystander C still cannot see
    it. The owner A keeps full visibility. This is the core opt-in grant: the
    target sees the listing ONLY because of the explicit share row.
    """
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])
    token_c = await _mint_token(seeded["admin_c"], seeded["tenant_c"])

    async with _client(configured_app) as client:
        listing_id = await _publish_private_skill(client, token_a)

        # Owner A opts in to share with target B.
        share = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_id, "target_tenant_id": str(seeded["tenant_b"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert share.status_code == 201, share.text
        share_body = share.json()
        assert share_body["owner_tenant_id"] == str(seeded["tenant_a"])
        assert share_body["target_tenant_id"] == str(seeded["tenant_b"])
        assert share_body["revoked_at"] is None

        # Target B now sees the shared listing (via the grant) + the global.
        browse_b = await client.get(
            "/marketplace/listings", headers={"Authorization": f"Bearer {token_b}"}
        )
        b_ids = {r["id"] for r in browse_b.json()}
        assert listing_id in b_ids
        assert str(seeded["global_listing"]) in b_ids

        # Target B can fetch it by id (200) and INSTALL it (the install row is
        # stamped with B's own tenant_id).
        detail_b = await client.get(
            f"/marketplace/listings/{listing_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert detail_b.status_code == 200
        install_b = await client.post(
            "/marketplace/installations",
            json={"listing_id": listing_id},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert install_b.status_code == 201, install_b.text
        assert install_b.json()["tenant_id"] == str(seeded["tenant_b"])

        # Bystander C still cannot see it (not a target).
        browse_c = await client.get(
            "/marketplace/listings", headers={"Authorization": f"Bearer {token_c}"}
        )
        c_ids = {r["id"] for r in browse_c.json()}
        assert listing_id not in c_ids
        detail_c = await client.get(
            f"/marketplace/listings/{listing_id}",
            headers={"Authorization": f"Bearer {token_c}"},
        )
        assert detail_c.status_code == 404


# ===========================================================================
# Revoke removes the visibility.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_revoke_removes_target_visibility(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        listing_id = await _publish_private_skill(client, token_a)
        share = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_id, "target_tenant_id": str(seeded["tenant_b"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        share_id = share.json()["id"]

        # Visible before revoke.
        detail_b = await client.get(
            f"/marketplace/listings/{listing_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert detail_b.status_code == 200

        # Owner A revokes the grant.
        revoke = await client.delete(
            f"/marketplace/shares/{share_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert revoke.status_code == 204

        # Target B can no longer see the listing — visibility removed.
        after = await client.get(
            f"/marketplace/listings/{listing_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert after.status_code == 404
        browse_b = await client.get(
            "/marketplace/listings", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert listing_id not in {r["id"] for r in browse_b.json()}

        # The owner's own live-share list is now empty; the grant is gone.
        owner_shares = await client.get(
            "/marketplace/shares", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert owner_shares.json() == []

        # Re-sharing after revoke is allowed (the live-share slot freed up).
        reshare = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_id, "target_tenant_id": str(seeded["tenant_b"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert reshare.status_code == 201


# ===========================================================================
# Every share / revoke is audited.
# ===========================================================================
@pytest.mark.asyncio
async def test_share_and_revoke_are_audited(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])

    async with _client(configured_app) as client:
        listing_id = await _publish_private_skill(client, token_a)
        share = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_id, "target_tenant_id": str(seeded["tenant_b"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        share_id = share.json()["id"]
        # One audit row for the share.
        assert await _audit_share_count(migrations_pg_dsn, seeded["tenant_a"]) == 1

        await client.delete(
            f"/marketplace/shares/{share_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        # A second audit row for the revoke.
        assert await _audit_share_count(migrations_pg_dsn, seeded["tenant_a"]) == 2


# ===========================================================================
# System Admin enumerates ALL shares for audit (BYPASSRLS).
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_system_admin_enumerates_all_shares(configured_app, migrations_pg_dsn: str) -> None:
    """Two owners each create a share with different targets; the System Admin
    sees BOTH (cross-tenant), while each owner sees only its own."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])
    sysadmin_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    async with _client(configured_app) as client:
        # A shares with B.
        listing_a = await _publish_private_skill(client, token_a)
        share_a = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_a, "target_tenant_id": str(seeded["tenant_b"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert share_a.status_code == 201
        share_a_id = share_a.json()["id"]

        # B shares (its own private listing) with C.
        listing_b = await _publish_private_skill(client, token_b)
        share_b = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_b, "target_tenant_id": str(seeded["tenant_c"])},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert share_b.status_code == 201
        share_b_id = share_b.json()["id"]

        # Owner A sees only its own grant.
        owner_a = await client.get(
            "/marketplace/shares", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert {s["id"] for s in owner_a.json()} == {share_a_id}

        # System Admin sees BOTH grants across tenants.
        admin_resp = await client.get(
            "/admin/marketplace/shares",
            headers={"Authorization": f"Bearer {sysadmin_token}"},
        )
        assert admin_resp.status_code == 200, admin_resp.text
        admin_ids = {s["id"] for s in admin_resp.json()}
        assert {share_a_id, share_b_id} <= admin_ids

        # A non-admin cannot reach the audit surface (403).
        forbidden = await client.get(
            "/admin/marketplace/shares",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert forbidden.status_code == 403


# ===========================================================================
# RBAC + ownership boundaries.
# ===========================================================================
@pytest.mark.asyncio
async def test_plain_member_cannot_share(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])

    async with _client(configured_app) as client:
        listing_id = await _publish_private_skill(client, token_a)
        resp = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_id, "target_tenant_id": str(seeded["tenant_b"])},
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 403


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cannot_share_or_revoke_across_ownership(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A tenant cannot share another tenant's listing (404) nor revoke another
    tenant's grant (404). It also cannot share with its own tenant (422)."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        listing_a = await _publish_private_skill(client, token_a)

        # Tenant B tries to share tenant A's private listing -> 404 (RLS hides
        # A's listing from B, so it cannot name it as its own to share).
        resp = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_a, "target_tenant_id": str(seeded["tenant_c"])},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404

        # Sharing with your own tenant is a no-op -> 422.
        self_share = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_a, "target_tenant_id": str(seeded["tenant_a"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert self_share.status_code == 422

        # A creates a real grant; B cannot revoke A's grant -> 404.
        share = await client.post(
            "/marketplace/shares",
            json={"listing_id": listing_a, "target_tenant_id": str(seeded["tenant_b"])},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        share_id = share.json()["id"]
        cross_revoke = await client.delete(
            f"/marketplace/shares/{share_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert cross_revoke.status_code == 404
