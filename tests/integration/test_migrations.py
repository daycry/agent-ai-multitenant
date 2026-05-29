"""Verify the phase-0 Alembic migration is reversible AND turns RLS on.

This suite intentionally avoids importing api_server.db.models so the
"down" path can recreate a clean schema without metadata leakage.

It relies on the docker-compose Postgres being healthy on localhost
(see tests/integration/conftest.py for connection knobs).
"""

from __future__ import annotations

import asyncio

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
    """`upgrade head` then `downgrade -1` drops exactly the two new
    indexes and nothing else — the migration is cleanly reversible."""
    command.upgrade(alembic_config, "head")
    assert _indexes(admin_pg_dsn) >= _FK_CLEANUP_INDEXES

    command.downgrade(alembic_config, "-1")
    after = _indexes(admin_pg_dsn)
    leaked = _FK_CLEANUP_INDEXES & after
    assert not leaked, f"downgrade -1 left the new indexes behind: {leaked}"
    # The sibling indexes from earlier migrations survive the downgrade.
    assert "ix_projects_tenant_id" in after
    assert "ix_review_sessions_plan_id" in after

    # Re-upgrading restores them (idempotent round-trip).
    command.upgrade(alembic_config, "head")
    assert _indexes(admin_pg_dsn) >= _FK_CLEANUP_INDEXES
