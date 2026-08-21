"""part-01 · task_part01_04 — `notification_logs` particionada, contra PostgreSQL.

Segunda de las cinco conversiones del ADR 0151. El contrato genérico (forma, PK
compuesta, colchón, RLS por partición, propagación de índices, el job) vive en
`_partition_contract.py`; aquí se prueba **lo que esta tabla tiene y las otras
no**:

1. **La FK entrante que el plan no había contado.**
   `notification_log_reads.log_id` referencia `notification_logs.id` con
   `ON DELETE CASCADE` y `NOT NULL`. Una FK no puede apuntar a una PK compuesta
   sin llevar las dos columnas, así que la conversión la retira (ADR 0154). Se
   comprueba que se fue **y que los receipts siguen ahí**: retirar una FK CASCADE
   sin querer podría haberse llevado las filas por delante.
2. **Las DOS policies del padre.** A diferencia de `guardrail_events`, esta tabla
   tiene `notification_logs_tenant_isolation` (FOR ALL) y
   `notification_logs_platform_read` (FOR SELECT, `tenant_id IS NULL`): el inbox
   de plataforma del System Admin lee los envíos sin tenant. La conversión tiene
   que conservar las dos, no solo la canónica.
3. **`tenant_id` es NULLABLE aquí**, así que el aislamiento se mide con las tres
   clases de fila: la mía, la de otro tenant y la de plataforma.
4. **El round-trip del `downgrade`** con filas dentro, incluida la restauración
   de la FK: una vuelta atrás que no devuelve la integridad referencial no es una
   vuelta atrás.

Y una razón de más para la RLS por partición en ESTA tabla: desde la migración
0113 guarda `subject`/`body`, o sea **el contenido del mensaje**. Es la que más
PII por fila lleva de las cinco.
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
    foreign_keys_pointing_at,
    headroom_offenders,
    index_propagation_offenders,
    job_creates_the_missing_month,
    month_start,
    new_partition_offenders,
    partition_name,
    partition_rls_offenders,
    run,
    shape_offenders,
    with_connection,
)

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

TABLE = "notification_logs"
PREVIOUS = "0133_complete_approval_policies"
RETIRED_FK = "notification_log_reads.fk_notification_log_reads_log"


# ---------------------------------------------------------------------------
# Utilidades propias de la tabla
# ---------------------------------------------------------------------------
async def _seed_tenant(conn: asyncpg.Connection, slug: str) -> str:
    tenant_id = str(uuid4())
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", tenant_id, slug, slug
    )
    return tenant_id


async def _insert_log(
    conn: asyncpg.Connection,
    tenant_id: str | None,
    *,
    created_at: datetime | None = None,
) -> str:
    log_id = str(uuid4())
    if created_at is None:
        await conn.execute(
            f"INSERT INTO {TABLE} (id, tenant_id, event_type, channel_type, status, subject)"
            " VALUES ($1, $2, 'infra_alert', 'in_app', 'sent', 'asunto')",
            log_id,
            tenant_id,
        )
    else:
        await conn.execute(
            f"INSERT INTO {TABLE}"
            " (id, tenant_id, event_type, channel_type, status, subject, created_at)"
            " VALUES ($1, $2, 'infra_alert', 'in_app', 'sent', 'asunto', $3)",
            log_id,
            tenant_id,
            created_at,
        )
    return log_id


async def _seed_user(conn: asyncpg.Connection) -> str:
    user_id = str(uuid4())
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, full_name)"
        " VALUES ($1, $2, 'x', 'Part01 Tester')",
        user_id,
        f"part01-{uuid4().hex[:8]}@example.test",
    )
    return user_id


@pytest.fixture()
def migrated(alembic_config: object, migrations_pg_dsn: str) -> str:
    """Esquema en `head`. Devuelve el DSN de migraciones (BYPASSRLS)."""
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
    assert not offenders, (
        "particiones sin aislamiento propio. Una consulta directa contra la"
        f" partición NO pasa por la policy del padre: {offenders}"
    )


def test_parent_indexes_propagate_to_every_partition(migrated: str) -> None:
    offenders = run(with_connection(migrated, lambda c: index_propagation_offenders(c, TABLE)))
    assert not offenders, offenders


def test_the_job_knows_about_this_table(migrated: str) -> None:
    """Sin entrada en `PARTITIONED_TABLES`, nadie crea la partición del mes que viene."""
    from workers.maintenance.partitions import PARTITIONED_TABLES

    assert TABLE in PARTITIONED_TABLES


# ---------------------------------------------------------------------------
# 2. La FK entrante que el plan no había contado (ADR 0154)
# ---------------------------------------------------------------------------
def test_the_incoming_foreign_key_is_gone_and_the_receipts_survived(migrated: str) -> None:
    """La FK CASCADE se retira; las filas que colgaban de ella siguen ahí.

    Es la comprobación que distingue «retirar la constraint» de «tirar la tabla
    hija por delante»: si la conversión hubiera borrado `notification_logs` con la
    FK todavía puesta, la cascada se habría llevado los receipts en silencio y el
    inbox de todo el mundo habría vuelto a estar «sin leer».
    """

    async def _check(conn: asyncpg.Connection) -> None:
        pointing = await foreign_keys_pointing_at(conn, TABLE)
        assert RETIRED_FK not in pointing, (
            f"{RETIRED_FK} sigue apuntando a {TABLE}: una FK no puede referenciar"
            " una PK compuesta sin llevar las dos columnas (ADR 0154)"
        )

        tenant = await _seed_tenant(conn, f"part-fk-{uuid4().hex[:8]}")
        user = await _seed_user(conn)
        log_id = await _insert_log(conn, tenant)
        receipt = str(uuid4())
        await conn.execute(
            "INSERT INTO notification_log_reads (id, tenant_id, user_id, log_id)"
            " VALUES ($1, $2, $3, $4)",
            receipt,
            tenant,
            user,
            log_id,
        )
        survived = await conn.fetchval(
            "SELECT count(*) FROM notification_log_reads WHERE id = $1", receipt
        )
        assert survived == 1, "el receipt no se pudo escribir tras retirar la FK"

    run(with_connection(migrated, _check))


# ---------------------------------------------------------------------------
# 3. Las DOS policies del padre + aislamiento medido con las tres clases de fila
# ---------------------------------------------------------------------------
def test_the_parent_keeps_both_policies(migrated: str) -> None:
    """`tenant_isolation` (FOR ALL) y `platform_read` (FOR SELECT, tenant NULL)."""

    async def _check(conn: asyncpg.Connection) -> set[str]:
        rows = await conn.fetch(
            "SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = $1",
            TABLE,
        )
        return {str(r["policyname"]) for r in rows}

    policies = run(with_connection(migrated, _check))
    assert policies == {
        "notification_logs_tenant_isolation",
        "notification_logs_platform_read",
    }, (
        "la conversión perdió una policy del padre. `platform_read` es la que deja"
        f" al System Admin ver los envíos sin tenant: {sorted(policies)}"
    )


def test_isolation_holds_for_a_real_app_user_session(migrated: str, app_database_url: str) -> None:
    """Aislamiento MEDIDO, no declarado: por el padre y por la partición.

    Con `tenant_id` nullable hay tres clases de fila y las tres importan: la mía
    (visible), la de otro tenant (nunca) y la de plataforma (visible solo por el
    padre, gracias a `platform_read` — la partición no lleva esa policy, ver el
    docstring de la migración).
    """

    async def _seed(conn: asyncpg.Connection) -> tuple[str, str, str]:
        mine = await _seed_tenant(conn, f"part-nl-mine-{uuid4().hex[:8]}")
        theirs = await _seed_tenant(conn, f"part-nl-theirs-{uuid4().hex[:8]}")
        await _insert_log(conn, mine)
        await _insert_log(conn, theirs)
        await _insert_log(conn, None)
        partition = partition_name(TABLE, month_start(await conn.fetchval("SELECT now()")))
        return mine, theirs, partition

    mine, theirs, partition = run(with_connection(migrated, _seed))
    dsn = app_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def _read(conn: asyncpg.Connection) -> tuple[list[str], list[str]]:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", mine)
        parent = [
            "platform" if r["tenant_id"] is None else str(r["tenant_id"])
            for r in await conn.fetch(f"SELECT tenant_id FROM {TABLE}")
        ]
        direct = [
            "platform" if r["tenant_id"] is None else str(r["tenant_id"])
            for r in await conn.fetch(f"SELECT tenant_id FROM {partition}")
        ]
        return parent, direct

    parent, direct = run(with_connection(dsn, _read))

    assert theirs not in parent, "se ven filas de otro tenant leyendo por el padre"
    assert mine in parent
    assert theirs not in direct, (
        "leyendo DIRECTAMENTE la partición se ven filas de otro tenant: la"
        " partición no tiene su propia policy"
    )
    assert direct == [mine], (
        "la partición debe exponer SOLO las filas del tenant de la sesión: su"
        f" policy es la canónica, sin la excepción de plataforma. Vio {direct}"
    )


# ---------------------------------------------------------------------------
# 4. Enrutado y el modo de fallo que el job previene
# ---------------------------------------------------------------------------
def test_a_row_lands_in_the_partition_of_its_month(migrated: str) -> None:
    async def _check(conn: asyncpg.Connection) -> None:
        tenant = await _seed_tenant(conn, f"part-nl-route-{uuid4().hex[:8]}")
        log_id = await _insert_log(conn, tenant)
        partition = partition_name(TABLE, month_start(await conn.fetchval("SELECT now()")))
        where = await conn.fetchval(
            f"SELECT tableoid::regclass::text FROM {TABLE} WHERE id = $1", log_id
        )
        assert where == partition, f"la fila no aterrizó en {partition}"

    run(with_connection(migrated, _check))


def test_a_row_outside_every_partition_is_rejected(migrated: str) -> None:
    """El incidente que el job existe para evitar, provocado a propósito."""

    async def _check(conn: asyncpg.Connection) -> None:
        tenant = await _seed_tenant(conn, f"part-nl-far-{uuid4().hex[:8]}")
        far_future = datetime.now(UTC) + timedelta(days=365 * 5)
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await _insert_log(conn, tenant, created_at=far_future)
        assert "no partition of relation" in str(excinfo.value).lower()

    run(with_connection(migrated, _check))


# ---------------------------------------------------------------------------
# 5. El camino de PRODUCCIÓN: convertir una tabla que YA tiene meses de datos
# ---------------------------------------------------------------------------
def test_upgrade_covers_the_months_of_the_pre_existing_data(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Con datos viejos dentro, la migración crea también SUS meses.

    Es el camino que se va a ejecutar de verdad: en la instancia viva la tabla
    arrastra meses de historia. Todo lo demás de este fichero corre sobre una
    tabla vacía, donde el bucle que recorre los meses de los datos **no se
    ejercita nunca**.
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _seed(conn: asyncpg.Connection) -> str:
        tenant = await _seed_tenant(conn, f"part-nl-hist-{uuid4().hex[:8]}")
        now_month = month_start(await conn.fetchval("SELECT now()"))
        for offset in (4, 3, 1):
            first = add_months(now_month, -offset)
            await _insert_log(
                conn, tenant, created_at=datetime(first.year, first.month, 15, 12, tzinfo=UTC)
            )
        return tenant

    tenant = run(with_connection(migrations_pg_dsn, _seed))
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _check(conn: asyncpg.Connection) -> None:
        present = await conn.fetch(
            "SELECT child.relname FROM pg_inherits"
            " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
            " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
            " WHERE parent.relname = $1",
            TABLE,
        )
        names = {str(r["relname"]) for r in present}
        now_month = month_start(await conn.fetchval("SELECT now()"))
        # Contiguas, incluido el mes SIN filas de en medio: un rango con agujeros
        # rechaza la primera fila del agujero.
        expected = {partition_name(TABLE, add_months(now_month, -n)) for n in range(5)}
        assert not sorted(expected - names), (
            f"la migración no cubrió los meses de los datos: {sorted(expected - names)}"
        )
        rows = await conn.fetch(
            f"SELECT tableoid::regclass::text AS part FROM {TABLE}"
            " WHERE tenant_id = $1 ORDER BY created_at",
            tenant,
        )
        assert len(rows) == 3, "la copia perdió filas históricas"
        assert [str(r["part"]) for r in rows] == [
            partition_name(TABLE, add_months(now_month, -n)) for n in (4, 3, 1)
        ]

    run(with_connection(migrations_pg_dsn, _check))


# ---------------------------------------------------------------------------
# 6. La ida y vuelta, con datos dentro Y con la FK restaurada
# ---------------------------------------------------------------------------
def test_downgrade_and_upgrade_round_trip_preserves_rows_and_restores_the_fk(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _seed(conn: asyncpg.Connection) -> tuple[str, list[str]]:
        tenant = await _seed_tenant(conn, f"part-nl-trip-{uuid4().hex[:8]}")
        ids = [await _insert_log(conn, tenant) for _ in range(3)]
        return tenant, sorted(ids)

    tenant, ids = run(with_connection(migrations_pg_dsn, _seed))

    command.downgrade(alembic_config, PREVIOUS)  # type: ignore[arg-type]

    async def _check_flat(conn: asyncpg.Connection) -> None:
        from tests.integration._partition_contract import primary_key_columns, relkind

        assert await relkind(conn, TABLE) == "r", "el downgrade dejó la tabla particionada"
        assert await primary_key_columns(conn, TABLE) == ["id"]
        rows = await conn.fetch(f"SELECT id FROM {TABLE} WHERE tenant_id = $1", tenant)
        assert sorted(str(r["id"]) for r in rows) == ids, "el downgrade perdió filas"
        pointing = await foreign_keys_pointing_at(conn, TABLE)
        assert RETIRED_FK in pointing, (
            "el downgrade no restauró la FK. Volver a la tabla plana tiene que"
            " devolver la integridad referencial tal como estaba, o no es una"
            f" vuelta atrás: {pointing}"
        )

    run(with_connection(migrations_pg_dsn, _check_flat))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _check_partitioned(conn: asyncpg.Connection) -> None:
        from tests.integration._partition_contract import relkind

        assert await relkind(conn, TABLE) == "p"
        rows = await conn.fetch(f"SELECT id FROM {TABLE} WHERE tenant_id = $1", tenant)
        assert sorted(str(r["id"]) for r in rows) == ids

    run(with_connection(migrations_pg_dsn, _check_partitioned))


# ---------------------------------------------------------------------------
# 7. El job, contra la base de verdad
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


def test_ensure_partitions_is_idempotent_against_the_real_database(
    migrated: str, admin_database_url: str
) -> None:
    """El beat corre a diario: la segunda pasada del día no puede fallar."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from workers.maintenance.partitions import SqlPartitionStore, ensure_partitions

    class _Recorder:
        def __init__(self) -> None:
            self.published: list[dict[str, object]] = []

        def publish(self, event: dict[str, object]) -> None:
            self.published.append(event)

    async def _both() -> tuple[dict[str, object], dict[str, object], _Recorder]:
        engine = create_async_engine(admin_database_url)
        recorder = _Recorder()
        try:
            first = await ensure_partitions(SqlPartitionStore(engine), recorder, tables=(TABLE,))
            second = await ensure_partitions(SqlPartitionStore(engine), recorder, tables=(TABLE,))
        finally:
            await engine.dispose()
        return first, second, recorder

    first, second, recorder = run(_both())
    assert first["gaps"] == {}
    assert second["created"] == [], "la segunda pasada volvió a crear particiones"
    assert recorder.published == []


def test_the_headroom_constant_did_not_drift(migrated: str) -> None:
    """La migración y el job tienen que dejar el MISMO colchón.

    Están duplicados a propósito (una migración no puede importar código de los
    workers), y un duplicado que se desincroniza es cobertura falsa: la migración
    dejaría dos meses y el test seguiría comprobando tres.
    """
    from workers.maintenance.partitions import PARTITION_HEADROOM_MONTHS

    assert PARTITION_HEADROOM_MONTHS == HEADROOM
