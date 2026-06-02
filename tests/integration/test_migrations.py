"""Verify the phase-0 Alembic migration is reversible AND turns RLS on.

This suite intentionally avoids importing api_server.db.models so the
"down" path can recreate a clean schema without metadata leakage.

It relies on the docker-compose Postgres being healthy on localhost
(see tests/integration/conftest.py for connection knobs).
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers — small async wrappers we can call from sync test functions
# ---------------------------------------------------------------------------
async def _fetch_one(dsn: str, sql: str) -> tuple | None:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(sql)
        return tuple(row) if row else None
    finally:
        await conn.close()


async def _fetch_all(dsn: str, sql: str) -> list[tuple]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(sql)
        return [tuple(r) for r in rows]
    finally:
        await conn.close()


async def _exec(dsn: str, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _fetch_row_args(dsn: str, sql: str, *args: object) -> tuple | None:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(sql, *args)
        return tuple(row) if row else None
    finally:
        await conn.close()


def _tables(dsn: str) -> set[str]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
        )
    )
    return {r[0] for r in rows}


def _rls_enabled_tables(dsn: str) -> set[str]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relrowsecurity = true
            """,
        )
    )
    return {r[0] for r in rows}


def _policies(dsn: str) -> set[tuple[str, str]]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            """
            SELECT tablename, policyname
              FROM pg_policies
             WHERE schemaname = 'public'
            """,
        )
    )
    return {(r[0], r[1]) for r in rows}


def _indexes(dsn: str) -> set[str]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'",
        )
    )
    return {r[0] for r in rows}


EXPECTED_TABLES = {
    "organizations",
    "users",
    "user_org_memberships",
    "sessions",
    "audit_log",
    "alembic_version",  # alembic's own bookkeeping table
}

EXPECTED_RLS_TABLES = {
    "organizations",
    "user_org_memberships",
    "sessions",
    "audit_log",
}

EXPECTED_POLICIES = {
    ("organizations", "org_self_only"),
    ("user_org_memberships", "membership_tenant_isolation"),
    ("sessions", "session_owner_only"),
    ("audit_log", "audit_log_tenant_isolation"),
}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_upgrade_head_creates_all_tables(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")

    tables = _tables(admin_pg_dsn)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"upgrade head left these tables missing: {missing}"


def test_upgrade_head_enables_rls_where_expected(alembic_config, admin_pg_dsn: str) -> None:
    # The previous test already ran upgrade — alembic_version table records
    # state, so re-running command.upgrade is a no-op. We re-run to keep
    # this test isolated when invoked in any order.
    command.upgrade(alembic_config, "head")

    enabled = _rls_enabled_tables(admin_pg_dsn)
    missing = EXPECTED_RLS_TABLES - enabled
    assert not missing, f"RLS not enabled on: {missing}"

    # users (global) must NOT have RLS.
    assert "users" not in enabled, "users table must stay un-RLSed"


def test_upgrade_head_creates_expected_policies(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")

    policies = _policies(admin_pg_dsn)
    missing = EXPECTED_POLICIES - policies
    assert not missing, f"policies missing: {missing}"


def test_downgrade_base_drops_all_tables(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    tables = _tables(admin_pg_dsn)
    leaked = (EXPECTED_TABLES - {"alembic_version"}) & tables
    assert not leaked, f"downgrade base left these tables behind: {leaked}"


def test_upgrade_downgrade_upgrade_is_idempotent(alembic_config, admin_pg_dsn: str) -> None:
    """Round-trip: head → base → head leaves the DB in the same shape."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    tables = _tables(admin_pg_dsn)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"second upgrade left these tables missing: {missing}"

    enabled = _rls_enabled_tables(admin_pg_dsn)
    assert enabled >= EXPECTED_RLS_TABLES, "RLS state not restored after round-trip"


# ---------------------------------------------------------------------------
# task_06_14_15 — FK index cleanup migration (0031, db-models-migrations-3/4)
# ---------------------------------------------------------------------------
_FK_CLEANUP_INDEXES = {"ix_projects_team_id", "ix_review_sessions_plan_status"}


def test_fk_cleanup_indexes_created_at_head(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    indexes = _indexes(admin_pg_dsn)
    missing = _FK_CLEANUP_INDEXES - indexes
    assert not missing, f"head is missing the FK cleanup indexes: {missing}"
    # The pre-existing simple index on review_sessions.plan_id stays.
    assert "ix_review_sessions_plan_id" in indexes


def test_fk_cleanup_migration_is_reversible(alembic_config, admin_pg_dsn: str) -> None:
    """Downgrading *past* migration 0031 drops exactly the two new indexes —
    the migration is cleanly reversible.

    0031 is no longer the migration tip (dozens of migrations stack on top of
    it), so `downgrade -1` would only undo the current head. We therefore
    target the revision immediately *before* 0031 explicitly, which exercises
    undoing 0031 (along with the rest of the top chain) and re-applying it."""
    command.upgrade(alembic_config, "head")
    assert _indexes(admin_pg_dsn) >= _FK_CLEANUP_INDEXES

    command.downgrade(alembic_config, "0030_kb_categories_is_builtin")
    after = _indexes(admin_pg_dsn)
    leaked = _FK_CLEANUP_INDEXES & after
    assert not leaked, f"downgrading past 0031 left the new indexes behind: {leaked}"
    # The sibling indexes from earlier migrations survive the downgrade.
    assert "ix_projects_tenant_id" in after
    assert "ix_review_sessions_plan_id" in after

    # Re-upgrading restores them (idempotent round-trip).
    command.upgrade(alembic_config, "head")
    assert _indexes(admin_pg_dsn) >= _FK_CLEANUP_INDEXES


# ---------------------------------------------------------------------------
# task_06_14_16 — data preservation on a reversible round-trip (tests-quality-2)
#
# The structural round-trip above (head→base→head) cannot carry data: dropping
# to `base` deletes every table. So we instead round-trip the *last* migration,
# which is purely additive and reversible (0031 only creates/drops two FK
# indexes — see 20260529_0031_fk_indexes_cleanup.py). A row written at `head`
# must survive `downgrade -1` (index removed) then `upgrade head` (index back)
# with its column values byte-for-byte intact. This proves the reversible
# migration touches indexes, never the rows underneath them.
# ---------------------------------------------------------------------------
def test_reversible_migration_preserves_row_data(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")

    # A clean, isolated organizations row (BYPASSRLS via admin_pg_dsn). We pick
    # `organizations` because it predates 0031 and has no FK dependencies, so
    # the insert is unaffected by whatever the round-tripped migration changes.
    org_id = uuid4()
    org_name = "Round-Trip Org ✓"  # non-ASCII to catch any encoding drift
    org_slug = f"round-trip-{org_id.hex[:12]}"
    asyncio.run(
        _exec(
            admin_pg_dsn,
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            org_id,
            org_name,
            org_slug,
        )
    )

    before = asyncio.run(
        _fetch_row_args(
            admin_pg_dsn,
            "SELECT id, name, slug FROM organizations WHERE id = $1",
            org_id,
        )
    )
    assert before == (org_id, org_name, org_slug), "seed row not written as expected"

    # Round-trip the reversible top migration around the live row.
    command.downgrade(alembic_config, "-1")
    command.upgrade(alembic_config, "head")

    after = asyncio.run(
        _fetch_row_args(
            admin_pg_dsn,
            "SELECT id, name, slug FROM organizations WHERE id = $1",
            org_id,
        )
    )
    assert after is not None, "row vanished across the downgrade/upgrade round-trip"
    assert after == before, f"row mutated across round-trip: {before!r} -> {after!r}"

    # The schema is also writable again after the round-trip (the round-tripped
    # index didn't leave the table in a half-migrated state).
    second_id = uuid4()
    asyncio.run(
        _exec(
            admin_pg_dsn,
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            second_id,
            "Post-Round-Trip Org",
            f"post-{second_id.hex[:12]}",
        )
    )
    written = asyncio.run(
        _fetch_one(admin_pg_dsn, f"SELECT count(*) FROM organizations WHERE id = '{second_id}'")
    )
    assert written == (1,), "schema not writable after the round-trip"


# ---------------------------------------------------------------------------
# task_sso_01 — sso_configurations platform-global (migration 0076, ADR 0047)
#
# The table loses its per-tenant scoping: no tenant_id, no RLS, identity by
# provider/kind. The migration must be reversible (up/down/up) and the
# per-tenant rows must be consolidated into one global row per provider
# (most-recently-updated wins) without losing the surviving row's data.
# ---------------------------------------------------------------------------
_SSO_BEFORE_0076 = "0075_memory_source_human_ws"


def _column_exists(dsn: str, table: str, column: str) -> bool:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND column_name = '{column}'",
        )
    )
    return len(rows) == 1


def _unique_constraints(dsn: str, table: str) -> set[str]:
    rows = asyncio.run(
        _fetch_all(
            dsn,
            f"SELECT conname FROM pg_constraint "
            f"WHERE conrelid = '{table}'::regclass AND contype = 'u'",
        )
    )
    return {r[0] for r in rows}


def test_sso_global_migration_drops_tenant_scoping(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")

    # No tenant_id, has button_label.
    assert not _column_exists(admin_pg_dsn, "sso_configurations", "tenant_id")
    assert _column_exists(admin_pg_dsn, "sso_configurations", "button_label")

    # RLS off, no tenant_isolation policy.
    assert "sso_configurations" not in _rls_enabled_tables(admin_pg_dsn)
    assert ("sso_configurations", "tenant_isolation") not in _policies(admin_pg_dsn)

    # Global unique by provider; per-tenant unique gone.
    uniques = _unique_constraints(admin_pg_dsn, "sso_configurations")
    assert "uq_sso_config_provider" in uniques
    assert "uq_sso_config_tenant_provider" not in uniques


def test_sso_global_migration_is_reversible(alembic_config, admin_pg_dsn: str) -> None:
    """Downgrading past 0076 restores the per-tenant shape; re-upgrading
    returns to the global shape (up/down/up)."""
    command.upgrade(alembic_config, "head")
    assert not _column_exists(admin_pg_dsn, "sso_configurations", "tenant_id")

    command.downgrade(alembic_config, _SSO_BEFORE_0076)
    # Per-tenant shape is back.
    assert _column_exists(admin_pg_dsn, "sso_configurations", "tenant_id")
    assert not _column_exists(admin_pg_dsn, "sso_configurations", "button_label")
    assert "sso_configurations" in _rls_enabled_tables(admin_pg_dsn)
    assert ("sso_configurations", "tenant_isolation") in _policies(admin_pg_dsn)
    uniques = _unique_constraints(admin_pg_dsn, "sso_configurations")
    assert "uq_sso_config_tenant_provider" in uniques
    assert "uq_sso_config_provider" not in uniques

    # Re-upgrade restores the global shape.
    command.upgrade(alembic_config, "head")
    assert not _column_exists(admin_pg_dsn, "sso_configurations", "tenant_id")
    assert _column_exists(admin_pg_dsn, "sso_configurations", "button_label")
    assert "uq_sso_config_provider" in _unique_constraints(admin_pg_dsn, "sso_configurations")


def test_sso_global_migration_consolidates_per_tenant_rows(
    alembic_config, admin_pg_dsn: str
) -> None:
    """Two tenants with the same provider before 0076 -> one global row
    after (the most-recently-updated wins); the surviving row's data is
    intact and the global unique constraint then holds."""
    # Downgrade to the per-tenant shape so we can seed two tenant rows.
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, _SSO_BEFORE_0076)

    # Clean slate for this table.
    asyncio.run(_exec(admin_pg_dsn, "TRUNCATE sso_configurations RESTART IDENTITY CASCADE"))

    tenant_old = uuid4()
    tenant_new = uuid4()
    config_old = uuid4()
    config_new = uuid4()
    for tid in (tenant_old, tenant_new):
        asyncio.run(
            _exec(
                admin_pg_dsn,
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                tid,
                f"Org {tid.hex[:6]}",
                f"org-{tid.hex[:12]}",
            )
        )
    # Older row (updated_at in the past) — should LOSE consolidation.
    asyncio.run(
        _exec(
            admin_pg_dsn,
            "INSERT INTO sso_configurations "
            "(id, tenant_id, provider, display_name, enabled, issuer, client_id, "
            " scopes, claim_mappings, updated_at) "
            "VALUES ($1, $2, 'oidc', 'OLD', true, $3, $4, $5::jsonb, $6::jsonb, "
            " now() - interval '2 days')",
            config_old,
            tenant_old,
            "https://old.example.test",
            "old-client",
            json.dumps(["openid"]),
            json.dumps({}),
        )
    )
    # Newer row — should WIN consolidation.
    asyncio.run(
        _exec(
            admin_pg_dsn,
            "INSERT INTO sso_configurations "
            "(id, tenant_id, provider, display_name, enabled, issuer, client_id, "
            " scopes, claim_mappings, updated_at) "
            "VALUES ($1, $2, 'oidc', 'NEW', true, $3, $4, $5::jsonb, $6::jsonb, now())",
            config_new,
            tenant_new,
            "https://new.example.test",
            "new-client",
            json.dumps(["openid"]),
            json.dumps({}),
        )
    )

    # Upgrade -> consolidation runs.
    command.upgrade(alembic_config, "head")

    remaining = asyncio.run(
        _fetch_all(
            admin_pg_dsn,
            "SELECT id, display_name, issuer FROM sso_configurations WHERE provider = 'oidc'",
        )
    )
    assert len(remaining) == 1, f"expected one global oidc row, got {remaining}"
    surviving_id, display_name, issuer = remaining[0]
    assert surviving_id == config_new, "the most-recently-updated row must win"
    assert display_name == "NEW"
    assert issuer == "https://new.example.test"
