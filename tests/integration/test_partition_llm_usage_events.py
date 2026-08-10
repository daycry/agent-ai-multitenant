"""part-01 · task_part01_05 — `llm_usage_events` particionada, contra PostgreSQL.

Tercera de las cinco conversiones del ADR 0151. El contrato genérico vive en
`_partition_contract.py`; aquí, lo propio de esta tabla:

1. **`created_at` era NULLABLE.** La migración 0109 la creó sin `NOT NULL` (el
   modelo ORM sí lo declara, así que ninguna fila escrita por la aplicación lo
   tiene vacío — pero la columna lo permite). Al entrar en la PK pasa a ser
   obligatoria, y una fila con `created_at IS NULL` **no cabe en ninguna
   partición**. La migración la rellena en vez de reventar, y esto lo comprueba.

2. **Las consultas de facturación.** El plan `part-01` pedía mirarlas antes de
   convertir: *«si alguna agrega sin filtrar por `created_at`, el particionado no
   la mejora y hay que decir por qué se acepta, en vez de suponer que mejora todo
   por defecto»*. Las dos que existen (`workers/maintenance/queue_sampler.py`,
   ventana de 24 h) **sí** filtran, y aquí se mide: el plan de ejecución toca una
   sola partición, no todas. Es la diferencia entre creer que el particionado
   ayuda y saberlo.

3. **El round-trip del `downgrade`** con filas dentro.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._partition_contract import (
    HEADROOM,
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

TABLE = "llm_usage_events"
PREVIOUS = "0134_partition_notification_logs"


async def _seed_tenant(conn: asyncpg.Connection, slug: str) -> str:
    tenant_id = str(uuid4())
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", tenant_id, slug, slug
    )
    return tenant_id


async def _insert_event(
    conn: asyncpg.Connection,
    tenant_id: str | None,
    *,
    created_at: datetime | None = None,
    source: str = "assistant",
    cost: str = "1.5",
) -> str:
    event_id = str(uuid4())
    if created_at is None:
        await conn.execute(
            f"INSERT INTO {TABLE}"
            " (id, tenant_id, source, provider_kind, input_tokens, output_tokens, cost_usd)"
            " VALUES ($1, $2, $3, 'ollama', 10, 20, $4::numeric)",
            event_id,
            tenant_id,
            source,
            cost,
        )
    else:
        await conn.execute(
            f"INSERT INTO {TABLE}"
            " (id, tenant_id, source, provider_kind, input_tokens, output_tokens, cost_usd,"
            "  created_at)"
            " VALUES ($1, $2, $3, 'ollama', 10, 20, $4::numeric, $5)",
            event_id,
            tenant_id,
            source,
            cost,
            created_at,
        )
    return event_id


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
    """`tenant_id` es nullable aquí (los turnos del córtex son de plataforma)."""

    async def _seed(conn: asyncpg.Connection) -> tuple[str, str, str]:
        mine = await _seed_tenant(conn, f"part-llm-mine-{uuid4().hex[:8]}")
        theirs = await _seed_tenant(conn, f"part-llm-theirs-{uuid4().hex[:8]}")
        await _insert_event(conn, mine)
        await _insert_event(conn, theirs)
        await _insert_event(conn, None, source="cortex")
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
    assert parent == [mine], f"el padre expuso filas ajenas o de plataforma: {parent}"
    assert direct == [mine], (
        "leyendo DIRECTAMENTE la partición se ven filas de otro tenant: la"
        " partición no tiene su propia policy"
    )


# ---------------------------------------------------------------------------
# 2. `created_at` era nullable: la fila vieja sin fecha no puede reventar
# ---------------------------------------------------------------------------
def test_a_legacy_row_without_created_at_survives_the_conversion(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """La columna admitía NULL hasta esta migración; una fila así no cabe en
    ninguna partición.

    Reventar sería lo peor de los dos mundos: la migración muere a mitad, en
    producción, por una fila de basura. Se rellena con `updated_at` si lo hay y
    con `now()` si no, y **se dice** (docstring de la migración) en vez de fingir
    que el caso no existe.
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _seed(conn: asyncpg.Connection) -> tuple[str, str, datetime]:
        tenant = await _seed_tenant(conn, f"part-llm-null-{uuid4().hex[:8]}")
        event_id = str(uuid4())
        stamp = await conn.fetchval("SELECT now() - interval '40 days'")
        await conn.execute(
            f"INSERT INTO {TABLE} (id, tenant_id, source, created_at, updated_at)"
            " VALUES ($1, $2, 'assistant', NULL, $3)",
            event_id,
            tenant,
            stamp,
        )
        return tenant, event_id, stamp

    tenant, event_id, stamp = run(with_connection(migrations_pg_dsn, _seed))
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _check(conn: asyncpg.Connection) -> None:
        row = await conn.fetchrow(
            f"SELECT created_at, tableoid::regclass::text AS part FROM {TABLE} WHERE id = $1",
            event_id,
        )
        assert row is not None, "la fila sin created_at se perdió en la conversión"
        assert row["created_at"] == stamp, (
            "el relleno debe usar `updated_at` cuando existe: es la mejor"
            f" aproximación disponible a cuándo ocurrió. Puso {row['created_at']}"
        )
        assert row["part"] == partition_name(TABLE, month_start(stamp))

    run(with_connection(migrations_pg_dsn, _check))
    assert tenant  # el seed pertenece al tenant creado; sin uso, ruff se queja


def test_created_at_is_now_not_null(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> str:
        return str(
            await conn.fetchval(
                "SELECT is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = $1 AND column_name = 'created_at'",
                TABLE,
            )
        )

    assert run(with_connection(migrated, _check)) == "NO"


# ---------------------------------------------------------------------------
# 3. Las consultas de facturación: el particionado las PODA, medido
# ---------------------------------------------------------------------------
def test_the_billing_window_query_skips_the_partitions_of_past_months(migrated: str) -> None:
    """La consulta real de `queue_sampler._collect_llm_cost`, con su ventana.

    Responde por medida la pregunta que el plan `part-01` dejó abierta, y la
    respuesta es más matizada que «poda»: lo que el filtro `created_at > now() -
    24h` descarta son **las particiones del pasado**, que es donde crece el
    historial. Las del futuro (el colchón de tres meses) NO se pueden descartar
    —una fila con fecha futura las satisface— y aparecen en el plan; son tres, y
    tres es constante mientras el pasado crece sin límite. Ésa es exactamente la
    ganancia que compra el particionado aquí.

    Escrito así a propósito tras verlo fallar con la afirmación fácil («escanea
    una sola partición»): el `EXPLAIN` decía cuatro, y la afirmación era falsa,
    no el código.
    """
    from workers.maintenance.partitions import PartitionSpec, partition_statements

    async def _check(conn: asyncpg.Connection) -> None:
        tenant = await _seed_tenant(conn, f"part-llm-plan-{uuid4().hex[:8]}")
        await _insert_event(conn, tenant)
        now_month = month_start(await conn.fetchval("SELECT now()"))

        # Meses PASADOS que la migración no creó (la tabla estaba vacía). Se
        # crean con el DDL del propio job para que nazcan con su RLS: dejarlas
        # sin policy rompería `test_rls_invariant.py` desde otro fichero, que es
        # la peor forma de descubrir un descuido.
        past = [add_months(now_month, -n) for n in (2, 1)]
        for first in past:
            spec = PartitionSpec(
                table=TABLE,
                name=partition_name(TABLE, first),
                start=first,
                end=add_months(first, 1),
            )
            for statement in partition_statements(spec):
                await conn.execute(statement)

        plan = "\n".join(
            str(r["QUERY PLAN"])
            for r in await conn.fetch(
                "EXPLAIN SELECT COALESCE(provider_kind, 'unknown'),"
                " SUM(input_tokens + output_tokens), COALESCE(SUM(cost_usd), 0)"
                f" FROM {TABLE}"
                " WHERE created_at > now() - interval '24 hours'"
                " GROUP BY 1"
            )
        )
        scanned = {
            line.split(f"on {TABLE}_")[1].split()[0]
            for line in plan.splitlines()
            if f"on {TABLE}_" in line
        }
        leaked = sorted(
            partition_name(TABLE, first).removeprefix(f"{TABLE}_")
            for first in past
            if partition_name(TABLE, first).removeprefix(f"{TABLE}_") in scanned
        )
        assert not leaked, (
            "la agregación de facturación sigue escaneando particiones de meses"
            f" ya cerrados ({leaked}): el filtro por `created_at` dejó de podar y"
            f" el particionado no le está comprando nada.\n{plan}"
        )
        assert len(scanned) <= HEADROOM + 1, (
            f"el plan toca {len(scanned)} particiones; el techo es el mes en curso"
            f" más el colchón ({HEADROOM}).\n{plan}"
        )

    run(with_connection(migrated, _check))


# ---------------------------------------------------------------------------
# 4. Enrutado, rechazo y round-trip
# ---------------------------------------------------------------------------
def test_a_row_lands_in_the_partition_of_its_month(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> None:
        tenant = await _seed_tenant(conn, f"part-llm-route-{uuid4().hex[:8]}")
        event_id = await _insert_event(conn, tenant)
        expected = partition_name(TABLE, month_start(await conn.fetchval("SELECT now()")))
        where = await conn.fetchval(
            f"SELECT tableoid::regclass::text FROM {TABLE} WHERE id = $1", event_id
        )
        assert where == expected

    run(with_connection(migrated, _check))


def test_a_row_outside_every_partition_is_rejected(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> None:
        tenant = await _seed_tenant(conn, f"part-llm-far-{uuid4().hex[:8]}")
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await _insert_event(conn, tenant, created_at=datetime.now(UTC) + timedelta(days=1825))
        assert "no partition of relation" in str(excinfo.value).lower()

    run(with_connection(migrated, _check))


def test_upgrade_covers_the_months_of_the_pre_existing_data(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _seed(conn: asyncpg.Connection) -> str:
        tenant = await _seed_tenant(conn, f"part-llm-hist-{uuid4().hex[:8]}")
        now_month = month_start(await conn.fetchval("SELECT now()"))
        for offset in (4, 3, 1):
            first = add_months(now_month, -offset)
            await _insert_event(
                conn, tenant, created_at=datetime(first.year, first.month, 15, 12, tzinfo=UTC)
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
            partition_name(TABLE, add_months(now_month, -n)) for n in (4, 3, 1)
        ]

    run(with_connection(migrations_pg_dsn, _check))


def test_downgrade_and_upgrade_round_trip_preserves_rows(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _seed(conn: asyncpg.Connection) -> tuple[str, list[str]]:
        tenant = await _seed_tenant(conn, f"part-llm-trip-{uuid4().hex[:8]}")
        ids = [await _insert_event(conn, tenant) for _ in range(3)]
        return tenant, sorted(ids)

    tenant, ids = run(with_connection(migrations_pg_dsn, _seed))
    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _flat(conn: asyncpg.Connection) -> None:
        assert await relkind(conn, TABLE) == "r"
        assert await primary_key_columns(conn, TABLE) == ["id"]
        rows = await conn.fetch(f"SELECT id FROM {TABLE} WHERE tenant_id = $1", tenant)
        assert sorted(str(r["id"]) for r in rows) == ids

    run(with_connection(migrations_pg_dsn, _flat))
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _again(conn: asyncpg.Connection) -> None:
        assert await relkind(conn, TABLE) == "p"
        rows = await conn.fetch(f"SELECT id FROM {TABLE} WHERE tenant_id = $1", tenant)
        assert sorted(str(r["id"]) for r in rows) == ids

    run(with_connection(migrations_pg_dsn, _again))


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
