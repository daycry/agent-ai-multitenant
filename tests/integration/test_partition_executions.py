"""part-01 · task_part01_08 — `executions` particionada, la de las FK (ADR 0151).

Quinta y última conversión, y la única cuya PK compuesta se nota **fuera** de su
propia tabla: cuatro claves foráneas apuntaban a `executions.id`, y una FK no
puede referenciar una PK compuesta sin llevar las dos columnas. El
[ADR 0154](../../docs/05-architecture-decisions/0154-fk-hacia-tablas-particionadas.md)
decidió retirarlas las cuatro (más una quinta, hacia `notification_logs`, que se
fue en la ola 2).

Lo que este fichero prueba además del contrato genérico:

1. **Las cuatro FK se han ido** — y las cuatro hijas siguen escribiéndose.
2. **La condición de validez del ADR 0154, medida.** La decisión se apoya en que
   el único evento que borra una `execution` es el borrado de su `task`, y que ese
   mismo evento ya borra la `approval_request` por su propio `task_id`. Eso no es
   una creencia: `test_deleting_a_task_still_removes_its_approval_requests` lo
   ejecuta. Es el test que el ADR nombra por su nombre, y el que se pondrá rojo el
   día que alguien cambie esa cascada — que es el día en que la decisión deja de
   ser correcta.
3. **El `downgrade` restaura las cuatro FK**, no solo la forma de la tabla.
4. **Las FK salientes sobreviven**: `task_id → tasks` (CASCADE) y
   `agent_id → agents` (SET NULL). Una tabla particionada puede tenerlas
   (PostgreSQL ≥ 12) y perderlas sería un cambio de esquema colado de rondón.
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
    foreign_keys_pointing_at,
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

TABLE = "executions"
PREVIOUS = "0136_partition_audit_log"

#: Las cuatro que el ADR 0154 retira, con el nombre exacto que tienen en el
#: catálogo. Enumeradas y no descubiertas a propósito: descubrirlas haría que el
#: test pasara igual si alguna dejara de existir por otro motivo.
RETIRED_FKS = (
    "approval_requests.approval_requests_execution_id_fkey",
    "memory_entries.memory_entries_source_execution_id_fkey",
    "eval_dataset_items.fk_eval_dataset_items_source_execution",
    "eval_shadow_records.fk_eval_shadow_records_source_execution",
)


# ---------------------------------------------------------------------------
# Siembra: el árbol mínimo para que exista una execution
# ---------------------------------------------------------------------------
async def _seed_tree(conn: asyncpg.Connection, slug: str) -> tuple[str, str, str]:
    """Devuelve `(tenant_id, project_id, task_id)`."""
    tenant = str(uuid4())
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", tenant, slug, slug
    )
    project = str(uuid4())
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
        project,
        tenant,
        f"proj-{slug}",
    )
    task = str(uuid4())
    await conn.execute(
        "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
        " VALUES ($1, $2, $3, $4, 'backlog', 'medium')",
        task,
        tenant,
        project,
        f"task-{slug}",
    )
    return tenant, project, task


async def _insert_execution(
    conn: asyncpg.Connection,
    tenant: str,
    task: str,
    *,
    created_at: datetime | None = None,
) -> str:
    execution = str(uuid4())
    if created_at is None:
        await conn.execute(
            f"INSERT INTO {TABLE} (id, tenant_id, task_id, status, steps_log)"
            " VALUES ($1, $2, $3, 'completed', '[]'::jsonb)",
            execution,
            tenant,
            task,
        )
    else:
        await conn.execute(
            f"INSERT INTO {TABLE} (id, tenant_id, task_id, status, steps_log, created_at)"
            " VALUES ($1, $2, $3, 'completed', '[]'::jsonb, $4)",
            execution,
            tenant,
            task,
            created_at,
        )
    return execution


async def _insert_approval_request(
    conn: asyncpg.Connection, tenant: str, project: str, task: str, execution: str
) -> str:
    request = str(uuid4())
    await conn.execute(
        "INSERT INTO approval_requests"
        " (id, tenant_id, execution_id, task_id, project_id, category, action, status)"
        " VALUES ($1, $2, $3, $4, $5, 'deploy', '{}'::jsonb, 'pending')",
        request,
        tenant,
        execution,
        task,
        project,
    )
    return request


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
    async def _seed(conn: asyncpg.Connection) -> tuple[str, str, str]:
        mine, _, my_task = await _seed_tree(conn, f"part-ex-mine-{uuid4().hex[:8]}")
        theirs, _, their_task = await _seed_tree(conn, f"part-ex-theirs-{uuid4().hex[:8]}")
        await _insert_execution(conn, mine, my_task)
        await _insert_execution(conn, theirs, their_task)
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
    assert theirs not in parent
    assert mine in parent
    assert theirs not in direct, (
        "leyendo DIRECTAMENTE la partición se ven runs de otro tenant: la"
        " partición no tiene su propia policy"
    )
    assert set(direct) == {mine}


# ---------------------------------------------------------------------------
# 2. Las cuatro FK retiradas, y las cuatro hijas siguen funcionando
# ---------------------------------------------------------------------------
def test_the_four_incoming_foreign_keys_are_gone(migrated: str) -> None:
    pointing = run(with_connection(migrated, lambda c: foreign_keys_pointing_at(c, TABLE)))
    still_there = [fk for fk in RETIRED_FKS if fk in pointing]
    assert not still_there, (
        f"siguen apuntando a {TABLE}: {still_there}. Una FK no puede referenciar"
        " una PK compuesta sin llevar las dos columnas (ADR 0154)"
    )
    assert not pointing, f"apareció una FK hacia {TABLE} que nadie ha decidido: {pointing}"


def test_the_children_still_accept_writes(migrated: str) -> None:
    """Las cuatro hijas escriben su referencia suelta sin FK que la valide."""

    async def _check(conn: asyncpg.Connection) -> None:
        tenant, project, task = await _seed_tree(conn, f"part-ex-child-{uuid4().hex[:8]}")
        execution = await _insert_execution(conn, tenant, task)

        await _insert_approval_request(conn, tenant, project, task, execution)
        await conn.execute(
            # `ck_memory_entries_scope_pointer` exige el puntero que corresponde
            # al scope: `project_shared` va con su `project_id`.
            "INSERT INTO memory_entries"
            " (id, tenant_id, scope, type, content, project_id, source_execution_id)"
            " VALUES ($1, $2, 'project_shared', 'semantic', 'x', $3, $4)",
            str(uuid4()),
            tenant,
            project,
            execution,
        )

        pending = await conn.fetchval(
            "SELECT count(*) FROM approval_requests WHERE execution_id = $1", execution
        )
        assert pending == 1
        memories = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE source_execution_id = $1", execution
        )
        assert memories == 1

    run(with_connection(migrated, _check))


def test_deleting_a_task_still_removes_its_approval_requests(migrated: str) -> None:
    """La condición de validez del ADR 0154, ejecutada.

    Retirar la FK `approval_requests.execution_id` (CASCADE, NOT NULL) solo es
    correcto porque el ÚNICO evento que borra una `execution` es el borrado de su
    `task`, y ese mismo evento ya se lleva la `approval_request` por su propio
    `task_id` (también CASCADE). Si algún día se añade un borrado de `executions`
    que no venga de borrar su `task`, esta decisión deja de valer — y este test es
    el que lo dice en voz alta.
    """

    async def _check(conn: asyncpg.Connection) -> None:
        tenant, project, task = await _seed_tree(conn, f"part-ex-cascade-{uuid4().hex[:8]}")
        execution = await _insert_execution(conn, tenant, task)
        request = await _insert_approval_request(conn, tenant, project, task, execution)

        await conn.execute("DELETE FROM tasks WHERE id = $1", task)

        surviving_execution = await conn.fetchval(
            f"SELECT count(*) FROM {TABLE} WHERE id = $1", execution
        )
        assert surviving_execution == 0, (
            "borrar la tarea ya no se lleva sus ejecuciones: la cascada"
            " `executions.task_id` cambió y el ADR 0154 hay que reabrirlo"
        )
        surviving_request = await conn.fetchval(
            "SELECT count(*) FROM approval_requests WHERE id = $1", request
        )
        assert surviving_request == 0, (
            "la aprobación pendiente sobrevivió a su tarea. El ADR 0154 retiró la"
            " FK `execution_id` PORQUE la cascada de `task_id` hacía el mismo"
            " trabajo; si esa cascada ya no está, hay filas huérfanas y la"
            " decisión debe revisarse"
        )

    run(with_connection(migrated, _check))


def test_the_outgoing_foreign_keys_survived(migrated: str) -> None:
    """`task_id → tasks` (CASCADE) y `agent_id → agents` (SET NULL) siguen ahí."""

    async def _check(conn: asyncpg.Connection) -> dict[str, str]:
        rows = await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid = $1::regclass AND contype = 'f'",
            TABLE,
        )
        return {str(r["conname"]): str(r["def"]) for r in rows}

    fks = run(with_connection(migrated, _check))
    assert "ON DELETE CASCADE" in fks.get("executions_task_id_fkey", "")
    assert "ON DELETE SET NULL" in fks.get("executions_agent_id_fkey", "")


# ---------------------------------------------------------------------------
# 3. Enrutado, rechazo, meses históricos
# ---------------------------------------------------------------------------
def test_a_row_lands_in_the_partition_of_its_month(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> None:
        tenant, _, task = await _seed_tree(conn, f"part-ex-route-{uuid4().hex[:8]}")
        execution = await _insert_execution(conn, tenant, task)
        expected = partition_name(TABLE, month_start(await conn.fetchval("SELECT now()")))
        where = await conn.fetchval(
            f"SELECT tableoid::regclass::text FROM {TABLE} WHERE id = $1", execution
        )
        assert where == expected

    run(with_connection(migrated, _check))


def test_a_row_outside_every_partition_is_rejected(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> None:
        tenant, _, task = await _seed_tree(conn, f"part-ex-far-{uuid4().hex[:8]}")
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await _insert_execution(
                conn, tenant, task, created_at=datetime.now(UTC) + timedelta(days=1825)
            )
        assert "no partition of relation" in str(excinfo.value).lower()

    run(with_connection(migrated, _check))


def test_upgrade_covers_the_months_of_the_pre_existing_data(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """La tabla grande con historia dentro: el camino real del despliegue."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _seed(conn: asyncpg.Connection) -> str:
        tenant, _, task = await _seed_tree(conn, f"part-ex-hist-{uuid4().hex[:8]}")
        now_month = month_start(await conn.fetchval("SELECT now()"))
        for offset in (5, 2):
            first = add_months(now_month, -offset)
            await _insert_execution(
                conn, tenant, task, created_at=datetime(first.year, first.month, 7, 6, tzinfo=UTC)
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
            partition_name(TABLE, add_months(now_month, -n)) for n in (5, 2)
        ]

    run(with_connection(migrations_pg_dsn, _check))


# ---------------------------------------------------------------------------
# 4. Ida y vuelta: filas, steps_log y las CUATRO FK restauradas
# ---------------------------------------------------------------------------
def test_downgrade_and_upgrade_round_trip_preserves_rows_and_restores_the_fks(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    steps = [{"node": "plan", "tokens": 42}]

    async def _seed(conn: asyncpg.Connection) -> tuple[str, str]:
        tenant, _, task = await _seed_tree(conn, f"part-ex-trip-{uuid4().hex[:8]}")
        execution = await _insert_execution(conn, tenant, task)
        await conn.execute(
            f"UPDATE {TABLE} SET steps_log = CAST($1 AS jsonb), total_tokens = 42 WHERE id = $2",
            json.dumps(steps),
            execution,
        )
        return tenant, execution

    tenant, execution = run(with_connection(migrations_pg_dsn, _seed))

    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _flat(conn: asyncpg.Connection) -> None:
        assert await relkind(conn, TABLE) == "r", "el downgrade dejó la tabla particionada"
        assert await primary_key_columns(conn, TABLE) == ["id"]
        row = await conn.fetchrow(
            f"SELECT steps_log, total_tokens FROM {TABLE} WHERE id = $1", execution
        )
        assert row is not None, "el downgrade perdió el run"
        assert json.loads(row["steps_log"]) == steps, (
            "el `steps_log` es tres cuartas partes de esta tabla (medida del ADR"
            " 0151) y es lo que cuenta qué hizo el agente: perderlo en la vuelta"
            " atrás sería perder el run entero"
        )
        assert row["total_tokens"] == 42
        pointing = set(await foreign_keys_pointing_at(conn, TABLE))
        missing = [fk for fk in RETIRED_FKS if fk not in pointing]
        assert not missing, (
            f"el downgrade no restauró estas FK: {missing}. Volver a la tabla plana"
            " tiene que devolver la integridad referencial tal como estaba"
        )

    run(with_connection(migrations_pg_dsn, _flat))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _again(conn: asyncpg.Connection) -> None:
        assert await relkind(conn, TABLE) == "p"
        row = await conn.fetchrow(f"SELECT steps_log FROM {TABLE} WHERE id = $1", execution)
        assert row is not None
        assert json.loads(row["steps_log"]) == steps

    run(with_connection(migrations_pg_dsn, _again))
    assert tenant


# ---------------------------------------------------------------------------
# 5. El job
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


def test_all_five_tables_of_the_adr_are_registered() -> None:
    """El cierre del ADR 0151: las CINCO, no cuatro y media.

    Con el plan terminado, la lista del job tiene que ser exactamente la del ADR.
    Si algún día se convierte una sexta (`task_audit_events` es la candidata que
    el ADR dejó fuera a propósito), este test obliga a decidirlo aquí y no a
    descubrirlo el mes que viene.
    """
    from workers.maintenance.partitions import PARTITIONED_TABLES

    assert set(PARTITIONED_TABLES) == {
        "guardrail_events",
        "notification_logs",
        "llm_usage_events",
        "audit_log",
        "executions",
    }
