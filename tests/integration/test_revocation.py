"""Integration tests for marketplace revocation + mandatory audit (Plan 09 task_09_08).

Drives revocation end-to-end against the real Postgres + RLS:

  - ``POST .../revoke`` flips the install to ``revoked`` (disabled for
    agents/projects, soft-deleted) and writes EXACTLY ONE ``revoke`` audit
    entry stamped with the acting principal,
  - a revoked install is no longer "live": the partial-unique live index
    (``uq_marketplace_installations_live``) frees the (tenant, listing,
    project) slot, so the same listing can be re-installed,
  - the marketplace audit trail is APPEND-ONLY at the database level: an
    UPDATE or DELETE issued by the NOBYPASSRLS app role touches ZERO rows
    (migration 0043 leaves only SELECT + INSERT policies under FORCE RLS),
  - RBAC: a plain ``tenant_user`` cannot revoke (403),
  - cross-tenant (``@pytest.mark.cross_tenant``): tenant B cannot revoke
    tenant A's installation (404 — RLS hides the row, no cross-tenant write
    and no audit leak).

Fixture wiring mirrors ``test_consent.py`` / ``test_marketplace_endpoints.py``:
seed via the BYPASSRLS migrations role, mint JWTs binding each user to a
tenant, then drive the API via AsyncClient. The append-only check connects
as the NOBYPASSRLS ``app_user`` (the role the FastAPI app uses in
production) so the RLS policy actually bites.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

# App role (NOBYPASSRLS) — matches tests/integration/conftest.py defaults.
_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))
_PG_APP_USER = os.environ.get("TEST_PG_APP_USER", "app_user")
_PG_APP_PASSWORD = os.environ.get("TEST_PG_APP_PASSWORD", "changeme-app-dev-only")
_PG_TEST_DB = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")


def _app_dsn() -> str:
    return f"postgresql://{_PG_APP_USER}:{_PG_APP_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_PG_TEST_DB}"


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "source": uuid4(),
        "listing": uuid4(),
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
            "tenant-a-revoke",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-revoke",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@revoke.test",
            "h",
            ids["member_a"],
            "member-a@revoke.test",
            "h",
            ids["admin_b"],
            "admin-b@revoke.test",
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
        # A global VERIFIED listing so install lands ENABLED (no consent
        # gate) — revocation logic is what's under test, not consent.
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, signature)"
            " VALUES ($1, $2, NULL, 'tool', 'verified-tool', '1.0.0', 'verified', 'sig')",
            ids["listing"],
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


async def _audit_rows(dsn: str, tenant_id: UUID, action: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            "SELECT id, actor, installation_id, listing_id FROM marketplace_audit_entries"
            " WHERE tenant_id = $1 AND action = $2",
            tenant_id,
            action,
        )
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
# Revoke flips status + writes EXACTLY ONE revoke audit row with the actor
# ===========================================================================
@pytest.mark.asyncio
async def test_revoke_flips_status_and_writes_single_audit(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await _install(client, seeded["listing"], headers)
        assert install["status"] == "enabled"
        install_id = install["id"]

        resp = await client.post(f"/marketplace/installations/{install_id}/revoke", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "revoked"
        assert body["revoked_at"] is not None
        assert UUID(body["revoked_by"]) == seeded["admin_a"]

        # No longer "live": absent from the default (non-revoked) list.
        live = await client.get("/marketplace/installations", headers=headers)
        assert all(r["id"] != install_id for r in live.json())
        # ...still visible (status=revoked) with include_revoked=true.
        with_revoked = await client.get(
            "/marketplace/installations?include_revoked=true", headers=headers
        )
        revoked_row = next(r for r in with_revoked.json() if r["id"] == install_id)
        assert revoked_row["status"] == "revoked"

    # EXACTLY ONE revoke audit row, stamped with the acting principal, no
    # uninstall row leaked (revoke is the distinct action).
    rows = await _audit_rows(migrations_pg_dsn, seeded["tenant_a"], "revoke")
    assert len(rows) == 1
    assert rows[0]["actor"] == f"user:{seeded['admin_a']}"
    assert UUID(str(rows[0]["installation_id"])) == UUID(install_id)
    uninstall_rows = await _audit_rows(migrations_pg_dsn, seeded["tenant_a"], "uninstall")
    assert len(uninstall_rows) == 0


@pytest.mark.asyncio
async def test_double_revoke_is_404(configured_app, migrations_pg_dsn: str) -> None:
    """A second revoke on an already-revoked install 404s (no double audit)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await _install(client, seeded["listing"], headers)
        first = await client.post(
            f"/marketplace/installations/{install['id']}/revoke", headers=headers
        )
        assert first.status_code == 200
        second = await client.post(
            f"/marketplace/installations/{install['id']}/revoke", headers=headers
        )
        assert second.status_code == 404

    rows = await _audit_rows(migrations_pg_dsn, seeded["tenant_a"], "revoke")
    assert len(rows) == 1


# ===========================================================================
# A revoked install frees the partial-unique live slot — re-install allowed
# ===========================================================================
@pytest.mark.asyncio
async def test_revoked_install_frees_live_slot_for_reinstall(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        first = await _install(client, seeded["listing"], headers)
        # While live, a duplicate install conflicts (409).
        dup = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["listing"])},
            headers=headers,
        )
        assert dup.status_code == 409

        # Revoke frees the live slot...
        revoke = await client.post(
            f"/marketplace/installations/{first['id']}/revoke", headers=headers
        )
        assert revoke.status_code == 200

        # ...so a fresh install of the same listing now succeeds with a new id.
        reinstall = await _install(client, seeded["listing"], headers)
        assert reinstall["id"] != first["id"]
        assert reinstall["status"] == "enabled"


# ===========================================================================
# Audit trail is append-only at the DB level (no UPDATE / DELETE)
# ===========================================================================
@pytest.mark.asyncio
async def test_audit_rows_are_immutable(configured_app, migrations_pg_dsn: str) -> None:
    """An UPDATE / DELETE on a marketplace audit row by the NOBYPASSRLS app
    role touches ZERO rows (migration 0043: SELECT + INSERT policies only
    under FORCE RLS). The row survives unchanged."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await _install(client, seeded["listing"], headers)
        revoke = await client.post(
            f"/marketplace/installations/{install['id']}/revoke", headers=headers
        )
        assert revoke.status_code == 200

    rows = await _audit_rows(migrations_pg_dsn, seeded["tenant_a"], "revoke")
    assert len(rows) == 1
    audit_id = UUID(str(rows[0]["id"]))

    # Connect as the NOBYPASSRLS app role with the tenant GUC bound, exactly
    # like the FastAPI app does at runtime, and try to mutate the audit row.
    conn = await asyncpg.connect(_app_dsn())
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(seeded["tenant_a"])
            )
            upd = await conn.execute(
                "UPDATE marketplace_audit_entries SET actor = 'tampered' WHERE id = $1",
                audit_id,
            )
            # asyncpg returns the command tag, e.g. "UPDATE 0".
            assert upd == "UPDATE 0", f"audit UPDATE was not blocked: {upd!r}"
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(seeded["tenant_a"])
            )
            dele = await conn.execute(
                "DELETE FROM marketplace_audit_entries WHERE id = $1", audit_id
            )
            assert dele == "DELETE 0", f"audit DELETE was not blocked: {dele!r}"
    finally:
        await conn.close()

    # The row is intact and unchanged.
    rows_after = await _audit_rows(migrations_pg_dsn, seeded["tenant_a"], "revoke")
    assert len(rows_after) == 1
    assert rows_after[0]["actor"] == f"user:{seeded['admin_a']}"


# ===========================================================================
# RBAC — a plain member cannot revoke
# ===========================================================================
@pytest.mark.asyncio
async def test_plain_member_cannot_revoke(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])

    async with _client(configured_app) as client:
        install = await _install(
            client, seeded["listing"], {"Authorization": f"Bearer {admin_token}"}
        )
        resp = await client.post(
            f"/marketplace/installations/{install['id']}/revoke",
            headers={"Authorization": f"Bearer {member_token}"},
        )
    assert resp.status_code == 403

    # The install was NOT revoked and no revoke audit row was written.
    rows = await _audit_rows(migrations_pg_dsn, seeded["tenant_a"], "revoke")
    assert len(rows) == 0


# ===========================================================================
# Cross-tenant isolation
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_revoke_tenant_a_install(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A installs; tenant B must not be able to revoke it (404 — RLS
    hides the row, no cross-tenant write, no audit leak into B)."""
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        install = await _install(client, seeded["listing"], {"Authorization": f"Bearer {token_a}"})
        install_id = install["id"]

        revoke_b = await client.post(
            f"/marketplace/installations/{install_id}/revoke",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert revoke_b.status_code == 404

        # A's install is still live / enabled.
        live_a = await client.get(
            "/marketplace/installations", headers={"Authorization": f"Bearer {token_a}"}
        )
        row = next(r for r in live_a.json() if r["id"] == install_id)
        assert row["status"] == "enabled"

    # No revoke audit leaked into tenant B.
    assert len(await _audit_rows(migrations_pg_dsn, seeded["tenant_b"], "revoke")) == 0
