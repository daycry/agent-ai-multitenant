"""task_mem_02 — `memory_recall_log` y las columnas de uso: forma, RLS y up→down→up."""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._partition_contract import (
    headroom_offenders,
    index_propagation_offenders,
    job_creates_the_missing_month,
    new_partition_offenders,
    partition_rls_offenders,
    relkind,
    run,
    shape_offenders,
    with_connection,
)

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

TABLE = "memory_recall_log"
PREVIOUS = "0150_documents_kb_created_index"


@pytest.fixture()
def migrated(alembic_config: object, migrations_pg_dsn: str) -> str:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    return migrations_pg_dsn


def test_the_table_is_partitioned_with_a_composite_primary_key(migrated: str) -> None:
    offenders = run(with_connection(migrated, lambda c: shape_offenders(c, TABLE)))
    assert not offenders, offenders


def test_the_current_month_and_the_headroom_are_covered(migrated: str) -> None:
    offenders = run(with_connection(migrated, lambda c: headroom_offenders(c, TABLE)))
    assert not offenders, offenders


def test_every_partition_carries_its_own_rls(migrated: str) -> None:
    offenders = run(with_connection(migrated, lambda c: partition_rls_offenders(c, TABLE)))
    assert not offenders, offenders


def test_parent_indexes_propagate_to_every_partition(migrated: str) -> None:
    offenders = run(with_connection(migrated, lambda c: index_propagation_offenders(c, TABLE)))
    assert not offenders, offenders


def test_the_job_knows_about_this_table(migrated: str) -> None:
    from workers.maintenance.partitions import PARTITIONED_TABLES

    assert TABLE in PARTITIONED_TABLES


def test_ensure_partitions_creates_the_missing_month_with_its_rls(
    migrated: str, admin_database_url: str
) -> None:
    target, report, published = run(
        job_creates_the_missing_month(migrated, admin_database_url, TABLE)
    )
    assert target in report["created"]
    assert report["gaps"] == {}
    assert published == []
    offenders = run(with_connection(migrated, lambda c: new_partition_offenders(c, TABLE, target)))
    assert not offenders, offenders


def test_a_real_app_user_session_only_sees_its_tenant(migrated: str, app_database_url: str) -> None:
    async def _seed(conn: asyncpg.Connection) -> tuple[str, str]:
        mine, theirs = str(uuid4()), str(uuid4())
        for t in (mine, theirs):
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$2)", t, f"log-{t[:8]}"
            )
            await conn.execute(
                f"INSERT INTO {TABLE} (id, tenant_id, memory_id, kind) VALUES ($1,$2,$3,'index')",
                str(uuid4()),
                t,
                str(uuid4()),
            )
        return mine, theirs

    mine, _ = run(with_connection(migrated, _seed))
    dsn = app_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def _read(conn: asyncpg.Connection) -> list[str]:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", mine)
        return [str(r["tenant_id"]) for r in await conn.fetch(f"SELECT tenant_id FROM {TABLE}")]

    assert run(with_connection(dsn, _read)) == [mine]


def test_the_check_rejects_an_unknown_kind(migrated: str) -> None:
    async def _bad(conn: asyncpg.Connection) -> None:
        t = str(uuid4())
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$2)", t, f"k-{t[:8]}"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                f"INSERT INTO {TABLE} (id, tenant_id, memory_id, kind) VALUES ($1,$2,$3,'raro')",
                str(uuid4()),
                t,
                str(uuid4()),
            )

    run(with_connection(migrated, _bad))


def test_upgrade_downgrade_upgrade_round_trip(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _state(conn: asyncpg.Connection) -> tuple[list[str], str | None]:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'memory_entries'"
            " AND column_name IN ('recall_count', 'last_recalled_at') ORDER BY 1"
        )
        cols = [r["column_name"] for r in rows]
        exists = await conn.fetchval("SELECT to_regclass($1)", f"public.{TABLE}")
        return cols, (await relkind(conn, TABLE) if exists else None)

    assert run(with_connection(migrations_pg_dsn, _state)) == (
        ["last_recalled_at", "recall_count"],
        "p",
    )
    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]
    assert run(with_connection(migrations_pg_dsn, _state)) == ([], None)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert run(with_connection(migrations_pg_dsn, _state)) == (
        ["last_recalled_at", "recall_count"],
        "p",
    )
