"""Integration tests for the private tenant marketplace (Plan 09 task_09_16).

A tenant publishes its OWN internal skills/tools as PRIVATE
``marketplace_listings`` (``tenant_id`` = caller tenant; the hybrid model +
RLS of Phase A isolate them). Drives the REST surface end-to-end against the
real Postgres + RLS:

  - ``POST /marketplace/private/listings`` validates the submitted manifest
    via the Phase C parsers and creates a PRIVATE listing stamped with the
    caller's tenant_id (a SKILL.md skill and a YAML tool manifest),
  - browse (``GET /marketplace/listings``) returns the caller tenant's OWN
    private listings PLUS the global catalog, but NEVER another tenant's
    private listing (``@pytest.mark.cross_tenant``),
  - RBAC: a plain ``tenant_user`` cannot publish (403),
  - a malformed manifest is rejected (422) and NO row is written,
  - update + unpublish re-validate / soft-delete the OWN listing only.

Tenant boundaries are the FEATURE here: a private listing is RLS-isolated to
its owner tenant, and a tenant can only ever publish into its own scope (the
RLS WITH CHECK on ``marketplace_listings``). Fixture wiring mirrors
``test_consent.py``.
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
name: internal-reporter
description: Generates the weekly internal status report.
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

# Internal Reporter

A tenant-internal skill that compiles a weekly status report.
"""

# A well-formed YAML tool manifest (Phase C tool_format) — internal tool.
_TOOL_YAML = """\
name: internal-deployer
version: 2.1.0
description: Deploys the internal service to staging.
kind: tool
entrypoint: deployer.main:run
implementation:
  runtime: python
  module: deployer.main
  reference: git+https://git.internal.test/tools/deployer@v2.1.0
permissions:
  allowed_domains: [staging.internal.test]
  network_policy: restricted
input_schema:
  type: object
  properties:
    target: {type: string}
  required: [target]
"""

# Malformed: missing the required ``version`` field -> the parser rejects it.
_BAD_SKILL_MD = """\
---
name: broken-skill
description: This skill has no version field.
---

# Broken
"""


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "source": uuid4(),
        "global_listing": uuid4(),
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
            "tenant-a-priv",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-priv",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@priv.test",
            "h",
            ids["member_a"],
            "member-a@priv.test",
            "h",
            ids["admin_b"],
            "admin-b@priv.test",
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
        # A global (NULL-tenant) catalog listing both tenants can browse.
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


async def _count_listings(dsn: str, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM marketplace_listings"
            " WHERE tenant_id = $1 AND deleted_at IS NULL",
            tenant_id,
        )
        return int(row["n"])
    finally:
        await conn.close()


# ===========================================================================
# Publish a private SKILL + TOOL
# ===========================================================================
@pytest.mark.asyncio
async def test_publish_skill_creates_private_listing(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/private/listings",
            json={"kind": "skill", "manifest": _SKILL_MD, "author": "Team A"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Stamped PRIVATE (tenant_id = caller), parsed from the manifest.
        assert body["tenant_id"] == str(seeded["tenant_a"])
        assert body["kind"] == "skill"
        assert body["name"] == "internal-reporter"
        assert body["version"] == "1.0.0"
        assert body["trust_level"] == "community"
        # Permissions came through the Phase C parser as descriptors.
        ptypes = {p["type"] for p in body["requested_permissions"]}
        assert ptypes == {"allowed_paths", "network_policy"}
        # The signature is never echoed.
        assert "signature" not in body
        assert body["is_signed"] is False

    assert await _count_listings(migrations_pg_dsn, seeded["tenant_a"]) == 1


@pytest.mark.asyncio
async def test_publish_tool_creates_private_listing(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/private/listings",
            json={"kind": "tool", "manifest": _TOOL_YAML},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["tenant_id"] == str(seeded["tenant_a"])
        assert body["kind"] == "tool"
        assert body["name"] == "internal-deployer"
        assert body["version"] == "2.1.0"
        # The manifest's machine-readable metadata persisted verbatim.
        assert body["manifest"]["entrypoint"] == "deployer.main:run"
        assert body["manifest"]["implementation"]["runtime"] == "python"


# ===========================================================================
# Browse shows own-private + global but NOT another tenant's private
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_browse_shows_own_private_and_global_not_other_tenants(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A publishes a private skill; tenant B publishes a private tool.

    Each tenant's browse must return its OWN private listing + the global
    catalog listing, and must NEVER see the other tenant's private listing
    (RLS isolation — the core multi-tenancy guarantee of this phase).
    """
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        pub_a = await client.post(
            "/marketplace/private/listings",
            json={"kind": "skill", "manifest": _SKILL_MD},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert pub_a.status_code == 201, pub_a.text
        a_listing_id = pub_a.json()["id"]

        pub_b = await client.post(
            "/marketplace/private/listings",
            json={"kind": "tool", "manifest": _TOOL_YAML},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert pub_b.status_code == 201, pub_b.text
        b_listing_id = pub_b.json()["id"]

        # Tenant A browses: sees own private + global, NOT tenant B's private.
        browse_a = await client.get(
            "/marketplace/listings", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert browse_a.status_code == 200
        a_ids = {r["id"] for r in browse_a.json()}
        assert a_listing_id in a_ids
        assert str(seeded["global_listing"]) in a_ids
        assert b_listing_id not in a_ids

        # Tenant B browses: sees own private + global, NOT tenant A's private.
        browse_b = await client.get(
            "/marketplace/listings", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert browse_b.status_code == 200
        b_ids = {r["id"] for r in browse_b.json()}
        assert b_listing_id in b_ids
        assert str(seeded["global_listing"]) in b_ids
        assert a_listing_id not in b_ids

        # Tenant B cannot fetch tenant A's private listing by id (404).
        detail_b = await client.get(
            f"/marketplace/listings/{a_listing_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert detail_b.status_code == 404

        # Tenant B cannot update/unpublish tenant A's private listing (404).
        upd_b = await client.put(
            f"/marketplace/private/listings/{a_listing_id}",
            json={"manifest": _SKILL_MD},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert upd_b.status_code == 404
        del_b = await client.delete(
            f"/marketplace/private/listings/{a_listing_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert del_b.status_code == 404

    # Tenant A's private listing is untouched.
    assert await _count_listings(migrations_pg_dsn, seeded["tenant_a"]) == 1


# ===========================================================================
# RBAC — a plain member cannot publish
# ===========================================================================
@pytest.mark.asyncio
async def test_plain_member_cannot_publish(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/private/listings",
            json={"kind": "skill", "manifest": _SKILL_MD},
            headers={"Authorization": f"Bearer {member_token}"},
        )
    assert resp.status_code == 403
    assert await _count_listings(migrations_pg_dsn, seeded["tenant_a"]) == 0


# ===========================================================================
# Bad manifest rejected (422) and NO row written
# ===========================================================================
@pytest.mark.asyncio
async def test_bad_manifest_rejected(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Missing required ``version`` field -> 422.
        resp = await client.post(
            "/marketplace/private/listings",
            json={"kind": "skill", "manifest": _BAD_SKILL_MD},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

        # A tool YAML submitted under kind=skill is also rejected (the SKILL.md
        # parser cannot parse a bare YAML mapping as frontmatter+body).
        resp2 = await client.post(
            "/marketplace/private/listings",
            json={"kind": "skill", "manifest": _TOOL_YAML},
            headers=headers,
        )
        assert resp2.status_code == 422

    assert await _count_listings(migrations_pg_dsn, seeded["tenant_a"]) == 0


# ===========================================================================
# Update + unpublish the OWN listing
# ===========================================================================
@pytest.mark.asyncio
async def test_update_and_unpublish_own_listing(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    updated_skill = _SKILL_MD.replace("version: 1.0.0", "version: 1.1.0")

    async with _client(configured_app) as client:
        pub = await client.post(
            "/marketplace/private/listings",
            json={"kind": "skill", "manifest": _SKILL_MD},
            headers=headers,
        )
        listing_id = pub.json()["id"]

        upd = await client.put(
            f"/marketplace/private/listings/{listing_id}",
            json={"manifest": updated_skill, "author": "Team A v2"},
            headers=headers,
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["version"] == "1.1.0"
        assert upd.json()["author"] == "Team A v2"

        # A manifest whose kind disagrees with the listing's kind is a 422.
        bad_kind = await client.put(
            f"/marketplace/private/listings/{listing_id}",
            json={"manifest": _TOOL_YAML},
            headers=headers,
        )
        assert bad_kind.status_code == 422

        # Unpublish soft-deletes it; it drops out of browse.
        delete = await client.delete(f"/marketplace/private/listings/{listing_id}", headers=headers)
        assert delete.status_code == 204

        browse = await client.get("/marketplace/listings", headers=headers)
        ids = {r["id"] for r in browse.json()}
        assert listing_id not in ids

    assert await _count_listings(migrations_pg_dsn, seeded["tenant_a"]) == 0
