"""part-01 · task_part01_06 — `audit_log` particionada, contra PostgreSQL.

Cuarta de las cinco conversiones del ADR 0151. El contrato genérico vive en
`_partition_contract.py`; lo propio de esta tabla:

1. **El `downgrade` importa aquí más que en ninguna.** El ADR 0151 la describe
   como la que puede ser «la única prueba de quién aprobó un despliegue»: una
   conversión que no sabe volver atrás sobre la tabla de auditoría es un riesgo de
   cumplimiento, no de rendimiento. Por eso el round-trip de este fichero es el
   más exigente de los cuatro — comprueba las filas **una a una con su contenido**
   (`action`, `changes`, `ip_address`), no solo el recuento: una vuelta atrás que
   conserva el número de filas pero pierde el JSONB del cambio no ha conservado la
   prueba de nada.

2. **La policy no tiene `WITH CHECK`.** La migración 0001 la creó solo con
   `USING`, y PostgreSQL entonces usa esa misma expresión como `WITH CHECK`. Se
   reproduce **literal**: «endurecerla de paso» sería un cambio de comportamiento
   colado en una migración de particionado.

3. **`tenant_id` es NULLABLE**: las acciones cross-tenant del System Admin se
   registran sin tenant y solo las leen los roles BYPASSRLS. La conversión no lo
   toca, y el test lo mide con una sesión `app_user` de verdad.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._partition_contract import (
    add_months,
    headroom_offenders,
    index_propagation_offenders,
    job_creates_the_missing_month,
    month_start,
    new_partition_offenders,
    partition_name,
    partition_rls_offenders,
    primary_key_columns,
    relkind,
    run,
    shape_offenders,
    with_connection,
)

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

TABLE = "audit_log"
PREVIOUS = "0135_partition_llm_usage_events"


async def _seed_tenant(conn: asyncpg.Connection, slug: str) -> str:
    tenant_id = str(uuid4())
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", tenant_id, slug, slug
    )
    return tenant_id


async def _insert_entry(
    conn: asyncpg.Connection,
    tenant_id: str | None,
    *,
    action: str = "project.update",
    changes: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> str:
    entry_id = str(uuid4())
    payload = json.dumps(changes if changes is not None else {"before": 1, "after": 2})
    if created_at is None:
        await conn.execute(
            f"INSERT INTO {TABLE} (id, tenant_id, action, resource_type, changes, ip_address)"
            " VALUES ($1, $2, $3, 'project', CAST($4 AS jsonb), '10.0.0.1')",
            entry_id,
            tenant_id,
            action,
            payload,
        )
    else:
        await conn.execute(
            f"INSERT INTO {TABLE}"
            " (id, tenant_id, action, resource_type, changes, ip_address, created_at)"
            " VALUES ($1, $2, $3, 'project', CAST($4 AS jsonb), '10.0.0.1', $5)",
            entry_id,
            tenant_id,
            action,
            payload,
            created_at,
        )
    return entry_id


@pytest.fixture()
def migrated(alembic_config: object, migrations_pg_dsn: str) -> str:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    return migrations_pg_dsn


# ---------------------------------------------------------------------------
# 1. El contrato genérico
# ---------------------------------------------------------------------------
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


def test_isolation_holds_for_a_real_app_user_session(migrated: str, app_database_url: str) -> None:
    """Las entradas de otro tenant y las de plataforma (tenant NULL) no se ven."""

    async def _seed(conn: asyncpg.Connection) -> tuple[str, str, str]:
        mine = await _seed_tenant(conn, f"part-audit-mine-{uuid4().hex[:8]}")
        theirs = await _seed_tenant(conn, f"part-audit-theirs-{uuid4().hex[:8]}")
        await _insert_entry(conn, mine)
        await _insert_entry(conn, theirs)
        await _insert_entry(conn, None, action="admin.cross_tenant")
        return mine, theirs, partition_name(TABLE, month_start(await conn.fetchval("SELECT now()")))

    mine, theirs, partition = run(with_connection(migrated, _seed))
    dsn = app_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def _read(conn: asyncpg.Connection) -> tuple[list[str], list[str]]:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", mine)
        parent = [str(r["tenant_id"]) for r in await conn.fetch(f"SELECT tenant_id FROM {TABLE}")]
        direct = [
            str(r["tenant_id"]) for r in await conn.fetch(f"SELECT tenant_id FROM {partition}")
        ]
        return parent, direct

    parent, direct = run(with_connection(dsn, _read))
    assert parent == [mine], f"el padre expuso entradas ajenas o de plataforma: {parent}"
    assert direct == [mine], (
        "leyendo DIRECTAMENTE la partición se ven entradas de otro tenant: la"
        " partición no tiene su propia policy"
    )
    assert theirs not in direct


def test_the_policy_keeps_its_original_shape(migrated: str) -> None:
    """La 0001 la creó SIN `WITH CHECK`; reproducirla es parte de no cambiar nada.

    PostgreSQL usa la expresión de `USING` también como `WITH CHECK` cuando esta
    falta, así que el comportamiento es el mismo — pero escribirlo distinto en el
    catálogo haría que un diff de esquema entre una instalación migrada y una
    nueva mostrara una diferencia que nadie sabría explicar.
    """

    async def _check(conn: asyncpg.Connection) -> asyncpg.Record | None:
        return await conn.fetchrow(
            "SELECT qual, with_check FROM pg_policies"
            " WHERE schemaname = 'public' AND tablename = $1 AND policyname = $2",
            TABLE,
            "audit_log_tenant_isolation",
        )

    row = run(with_connection(migrated, _check))
    assert row is not None, "la policy del padre desapareció en la conversión"
    assert "app.tenant_id" in row["qual"]
    assert row["with_check"] is None, (
        f"la conversión le añadió un WITH CHECK explícito que la 0001 no tenía: {row['with_check']}"
    )


# ---------------------------------------------------------------------------
# 2. Enrutado y rechazo
# ---------------------------------------------------------------------------
def test_a_row_lands_in_the_partition_of_its_month(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> None:
        tenant = await _seed_tenant(conn, f"part-audit-route-{uuid4().hex[:8]}")
        entry = await _insert_entry(conn, tenant)
        expected = partition_name(TABLE, month_start(await conn.fetchval("SELECT now()")))
        where = await conn.fetchval(
            f"SELECT tableoid::regclass::text FROM {TABLE} WHERE id = $1", entry
        )
        assert where == expected

    run(with_connection(migrated, _check))


def test_a_row_outside_every_partition_is_rejected(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> None:
        tenant = await _seed_tenant(conn, f"part-audit-far-{uuid4().hex[:8]}")
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await _insert_entry(conn, tenant, created_at=datetime.now(UTC) + timedelta(days=1825))
        assert "no partition of relation" in str(excinfo.value).lower()

    run(with_connection(migrated, _check))


def test_upgrade_covers_the_months_of_the_pre_existing_data(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _seed(conn: asyncpg.Connection) -> str:
        tenant = await _seed_tenant(conn, f"part-audit-hist-{uuid4().hex[:8]}")
        now_month = month_start(await conn.fetchval("SELECT now()"))
        for offset in (6, 2):
            first = add_months(now_month, -offset)
            await _insert_entry(
                conn, tenant, created_at=datetime(first.year, first.month, 9, 8, tzinfo=UTC)
            )
        return tenant

    tenant = run(with_connection(migrations_pg_dsn, _seed))
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _check(conn: asyncpg.Connection) -> None:
        now_month = month_start(await conn.fetchval("SELECT now()"))
        rows = await conn.fetch(
            f"SELECT tableoid::regclass::text AS part FROM {TABLE}"
            " WHERE tenant_id = $1 ORDER BY created_at",
            tenant,
        )
        assert [str(r["part"]) for r in rows] == [
            partition_name(TABLE, add_months(now_month, -n)) for n in (6, 2)
        ], "la migración no cubrió los meses de los datos históricos"

    run(with_connection(migrations_pg_dsn, _check))


# ---------------------------------------------------------------------------
# 3. La ida y vuelta, comprobando el CONTENIDO (ver el docstring del módulo)
# ---------------------------------------------------------------------------
def test_downgrade_and_upgrade_round_trip_preserves_every_field(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    marker = uuid4().hex
    expected_changes = {"deploy": marker, "approved_by": "operador"}

    async def _seed(conn: asyncpg.Connection) -> tuple[str, str]:
        tenant = await _seed_tenant(conn, f"part-audit-trip-{uuid4().hex[:8]}")
        entry = await _insert_entry(
            conn, tenant, action="deployment.approve", changes=expected_changes
        )
        return tenant, entry

    tenant, entry = run(with_connection(migrations_pg_dsn, _seed))

    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _flat(conn: asyncpg.Connection) -> None:
        assert await relkind(conn, TABLE) == "r", "el downgrade dejó la tabla particionada"
        assert await primary_key_columns(conn, TABLE) == ["id"]
        row = await conn.fetchrow(
            f"SELECT action, changes, ip_address::text AS ip, resource_type FROM {TABLE}"
            " WHERE id = $1",
            entry,
        )
        assert row is not None, (
            "el downgrade perdió la entrada de auditoría. Es la que puede ser la"
            " única prueba de quién aprobó un despliegue (ADR 0151)"
        )
        assert row["action"] == "deployment.approve"
        assert json.loads(row["changes"]) == expected_changes, (
            "las filas siguen ahí pero el JSONB del cambio se perdió: eso no es"
            " haber conservado la prueba de nada"
        )
        # `inet` se renderiza con su máscara (`10.0.0.1/32`) según por dónde
        # salga; lo que se comprueba es que la dirección sobrevivió, no su forma.
        assert str(row["ip"]).startswith("10.0.0.1")
        assert row["resource_type"] == "project"

    run(with_connection(migrations_pg_dsn, _flat))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _again(conn: asyncpg.Connection) -> None:
        assert await relkind(conn, TABLE) == "p"
        row = await conn.fetchrow(f"SELECT changes FROM {TABLE} WHERE id = $1", entry)
        assert row is not None
        assert json.loads(row["changes"]) == expected_changes

    run(with_connection(migrations_pg_dsn, _again))
    assert tenant


# ---------------------------------------------------------------------------
# 4. El job
# ---------------------------------------------------------------------------
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
