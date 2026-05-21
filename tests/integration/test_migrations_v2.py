"""Verify migration 0002 lays down all eleven domain tables with RLS.

Mirrors `tests/integration/test_migrations.py` (phase 0) but for the
Plan-01 domain. Asserts:

  - All eleven new tables exist after `upgrade head`.
  - The seven tenant-scoped tables have RLS enabled.
  - Each has the expected `<table>_tenant_isolation` policy.
  - Junction tables (agent_skills/agent_tools/team_members/task_dependencies)
    do NOT have RLS -- they rely on parent visibility via ON DELETE CASCADE.
  - Round-trip head -> base -> head leaves the schema identical (the
    downgrade is fully reversible).
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
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


def _foreign_keys(dsn: str, table: str) -> set[tuple[str, str, str]]:
    """Return (column, referenced_table, referenced_column) tuples."""
    rows = asyncio.run(
        _fetch_all(
            dsn,
            f"""
            SELECT kcu.column_name, ccu.table_name, ccu.column_name
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
              JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
             WHERE tc.constraint_type = 'FOREIGN KEY'
               AND tc.table_name = '{table}'
            """,
        )
    )
    return {tuple(r) for r in rows}


# ---------------------------------------------------------------------------
# expected sets
# ---------------------------------------------------------------------------
NEW_TENANT_SCOPED_TABLES = {
    "agents",
    "skills",
    "tools",
    "teams",
    "projects",
    "plans",
    "tasks",
}

NEW_JUNCTION_TABLES = {
    "agent_skills",
    "agent_tools",
    "team_members",
    "task_dependencies",
}

NEW_TABLES = NEW_TENANT_SCOPED_TABLES | NEW_JUNCTION_TABLES

NEW_POLICIES = {(t, f"{t}_tenant_isolation") for t in NEW_TENANT_SCOPED_TABLES}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_upgrade_head_creates_all_domain_tables(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    tables = _tables(admin_pg_dsn)
    missing = NEW_TABLES - tables
    assert not missing, f"upgrade head left these domain tables missing: {missing}"


def test_upgrade_head_enables_rls_on_tenant_scoped(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    enabled = _rls_enabled_tables(admin_pg_dsn)
    missing = NEW_TENANT_SCOPED_TABLES - enabled
    assert not missing, f"RLS not enabled on: {missing}"


def test_junctions_do_not_have_rls(alembic_config, admin_pg_dsn: str) -> None:
    """Junctions rely on parent visibility via ON DELETE CASCADE. Adding
    RLS on a junction would either be redundant (same tenant_id check
    via a join) or wrong (junction has no tenant_id column)."""
    command.upgrade(alembic_config, "head")
    enabled = _rls_enabled_tables(admin_pg_dsn)
    rls_on_junctions = NEW_JUNCTION_TABLES & enabled
    assert not rls_on_junctions, f"RLS unexpectedly enabled on junction tables: {rls_on_junctions}"


def test_upgrade_head_creates_expected_policies(alembic_config, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    policies = _policies(admin_pg_dsn)
    missing = NEW_POLICIES - policies
    assert not missing, f"policies missing: {missing}"


def test_tasks_foreign_keys(alembic_config, admin_pg_dsn: str) -> None:
    """Tasks are the most-referenced table -- make sure its FKs are
    actually constraints (not just ORM relationships)."""
    command.upgrade(alembic_config, "head")
    fks = _foreign_keys(admin_pg_dsn, "tasks")
    targets = {(col, tgt) for (col, tgt, _) in fks}
    assert ("project_id", "projects") in targets
    assert ("plan_id", "plans") in targets
    assert ("assigned_agent_id", "agents") in targets
    assert ("reviewer_agent_id", "agents") in targets


def test_task_dependency_self_loop_blocked(alembic_config, admin_pg_dsn: str) -> None:
    """ck_task_dependencies_no_self_loop must actually exist at the DB
    level so even raw INSERTs hit the constraint."""
    command.upgrade(alembic_config, "head")
    rows = asyncio.run(
        _fetch_all(
            admin_pg_dsn,
            """
            SELECT conname
              FROM pg_constraint
             WHERE conrelid = 'task_dependencies'::regclass
               AND contype = 'c'
            """,
        )
    )
    names = {r[0] for r in rows}
    assert "ck_task_dependencies_no_self_loop" in names


def test_downgrade_to_phase0_drops_domain_tables(alembic_config, admin_pg_dsn: str) -> None:
    """Downgrading to phase-0 (revision 0001_initial) removes only the
    Plan-01 tables; phase-0 tables (organizations/users/...) survive."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0001_initial")

    tables = _tables(admin_pg_dsn)
    leaked_domain = NEW_TABLES & tables
    assert not leaked_domain, f"domain tables leaked after downgrade: {leaked_domain}"

    # Phase-0 should still be there.
    assert "organizations" in tables
    assert "users" in tables


def test_round_trip_upgrade_downgrade_upgrade(alembic_config, admin_pg_dsn: str) -> None:
    """head -> 0001_initial -> head leaves all domain tables and policies
    intact. Catches any non-idempotent DDL the second time around."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0001_initial")
    command.upgrade(alembic_config, "head")

    tables = _tables(admin_pg_dsn)
    assert tables >= NEW_TABLES, "second upgrade missing domain tables"

    enabled = _rls_enabled_tables(admin_pg_dsn)
    assert enabled >= NEW_TENANT_SCOPED_TABLES, "RLS state not restored"

    policies = _policies(admin_pg_dsn)
    assert policies >= NEW_POLICIES, "policies not restored"
