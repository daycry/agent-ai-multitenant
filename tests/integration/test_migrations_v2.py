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


def test_junctions_do_have_rls_since_migration_0124(alembic_config, admin_pg_dsn: str) -> None:
    """Las cuatro junctions SÍ llevan RLS. Este test afirmaba lo contrario.

    Se llamaba ``test_junctions_do_not_have_rls`` y su docstring razonaba que
    «una junction no tiene columna tenant_id, así que ponerle RLS sería
    redundante o incorrecto». Ese razonamiento fue correcto hasta que la
    migración **0124** (plan prod-14, hallazgo tenancy-1, 2026-07-30) les puso
    `tenant_id`, un trigger que lo DERIVA del padre, y RLS `ENABLE`+`FORCE` con
    su policy de aislamiento.

    No fue un descuido, fue el objetivo: sin RLS, cualquiera con una sesión de
    tenant podía **leer** las asignaciones de otro —incluido
    `agent_tools.config_override`— e **insertar** una fila apuntando a un padre
    ajeno, porque las comprobaciones de clave ajena se ejecutan como el
    propietario de la tabla e IGNORAN la RLS. Que no hubiera fuga explotable
    dependía de que cada router hiciera su chequeo antes de escribir; la 0124
    convirtió esa disciplina en un invariante de la base de datos.

    Así que el test se **invierte**, no se retira: pasa a ser la guarda de que
    nadie deshaga la 0124. Quedó rojo desde el 2026-07-30 —contradiciendo a
    ``test_rls_invariant.py::test_junction_tables_need_no_exception``, que
    afirmaba lo correcto y pasaba— y no se vio porque `tests/integration/` son
    517 ficheros que nadie corre enteros.
    """
    command.upgrade(alembic_config, "head")
    enabled = _rls_enabled_tables(admin_pg_dsn)
    sin_rls = NEW_JUNCTION_TABLES - enabled
    assert not sin_rls, (
        f"junctions sin RLS: {sin_rls}. La migración 0124 se la puso a propósito; "
        "quitarla reabre la lectura y la inserción cruzada entre tenants"
    )


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
