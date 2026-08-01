"""part-01 · task_part01_01/02 — `guardrail_events` particionada, contra PostgreSQL.

La primera de las cinco conversiones del ADR 0151, y por eso este fichero prueba
**el patrón entero**, no solo esta tabla: si algo del patrón no funciona, mejor
saberlo aquí que en `executions`, que es la de las cuatro FK.

Lo que se comprueba, y por qué cada cosa:

1. **Forma**: la tabla es `relkind = 'p'` y su PK es `(id, created_at)`.
   PostgreSQL exige que la PK incluya la clave de partición; si el modelo y la
   migración se desalinearan, el ORM insertaría contra una PK que no existe.
2. **Cobertura**: hay partición para el mes en curso y para los tres siguientes.
   Es lo que impide el incidente del día 1 del mes que viene.
3. **RLS por partición**: cada partición tiene `ENABLE` + `FORCE` + policy. Al
   consultar por el padre se aplica la policy del padre, pero una consulta
   DIRECTA contra la partición solo pasa por las suyas: una partición sin policy
   es una puerta lateral al aislamiento entre tenants, y es el error más fácil de
   cometer en las cuatro olas que quedan.
4. **Aislamiento real**, no declarado: con dos tenants y una sesión `app_user`
   (NOBYPASSRLS) atada a uno, se ve una fila y no la otra — leyendo por el padre
   y leyendo directamente contra la partición.
5. **Índices propagados**: los cuatro del padre existen en cada partición sin
   haberlos declarado por partición.
6. **Enrutado**: una fila insertada por el padre aterriza en la partición de su
   mes, y una fila de un mes sin partición es RECHAZADA (la prueba de que el modo
   de fallo que el job previene es real y no una suposición).
7. **Ida y vuelta con datos dentro**: `downgrade` → `upgrade` conservando las
   filas. El ADR pide un downgrade «que se prueba de verdad, no que se escribe».
8. **El job**: `ensure_partitions` crea de verdad la partición que falta, con su
   RLS, y avisa cuando no puede.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

TABLE = "guardrail_events"
HEADROOM = 3


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _month_start(moment: datetime | date) -> date:
    return date(moment.year, moment.month, 1)


def _add_months(start: date, months: int) -> date:
    total = (start.year * 12 + (start.month - 1)) + months
    return date(total // 12, total % 12 + 1, 1)


def _partition_name(first_of_month: date) -> str:
    return f"{TABLE}_{first_of_month.year:04d}_{first_of_month.month:02d}"


async def _connect(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn)


async def _relkind(conn: asyncpg.Connection, table: str) -> str:
    """`'p'` (particionada) o `'r'` (plana).

    Se normaliza porque asyncpg devuelve el tipo `"char"` de PostgreSQL como
    `bytes`, y `b'p' == 'p'` es False: comparar en crudo da un test que falla
    diciendo que la tabla no está particionada cuando sí lo está.
    """
    value = await conn.fetchval(
        "SELECT relkind FROM pg_class WHERE relname = $1 AND relnamespace = 'public'::regnamespace",
        table,
    )
    return value.decode() if isinstance(value, bytes) else str(value)


async def _seed_tenant(conn: asyncpg.Connection, slug: str) -> str:
    tenant_id = str(uuid4())
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
        tenant_id,
        slug,
        slug,
    )
    return tenant_id


async def _insert_event(
    conn: asyncpg.Connection, tenant_id: str, *, created_at: datetime | None = None
) -> str:
    event_id = str(uuid4())
    if created_at is None:
        await conn.execute(
            f"INSERT INTO {TABLE} (id, tenant_id, guardrail_type, hook_point, severity, detail)"
            " VALUES ($1, $2, 'pii', 'pre_llm', 'high', 'x')",
            event_id,
            tenant_id,
        )
    else:
        await conn.execute(
            f"INSERT INTO {TABLE}"
            " (id, tenant_id, guardrail_type, hook_point, severity, detail, created_at)"
            " VALUES ($1, $2, 'pii', 'pre_llm', 'high', 'x', $3)",
            event_id,
            tenant_id,
            created_at,
        )
    return event_id


@pytest.fixture()
def migrated(alembic_config: object, migrations_pg_dsn: str) -> str:
    """Esquema en `head` (o sea, con la 0131 aplicada). Devuelve el DSN admin."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    return migrations_pg_dsn


# ---------------------------------------------------------------------------
# 1-2. Forma y cobertura
# ---------------------------------------------------------------------------
def test_the_table_is_partitioned_with_a_composite_primary_key(migrated: str) -> None:
    async def _check() -> None:
        conn = await _connect(migrated)
        try:
            relkind = await _relkind(conn, TABLE)
            assert relkind == "p", f"{TABLE} no es una tabla particionada (relkind={relkind!r})"

            strategy = await conn.fetchval(
                "SELECT pg_get_partkeydef(c.oid) FROM pg_class c"
                " WHERE c.relname = $1 AND c.relnamespace = 'public'::regnamespace",
                TABLE,
            )
            assert strategy == "RANGE (created_at)"

            pk_columns = await conn.fetch(
                "SELECT a.attname FROM pg_constraint con"
                " JOIN pg_class c ON c.oid = con.conrelid"
                " JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true"
                " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum"
                " WHERE c.relname = $1 AND con.contype = 'p' ORDER BY k.ord",
                TABLE,
            )
            names = [row["attname"] for row in pk_columns]
            pk_message = (
                "PostgreSQL exige que la PK de una tabla particionada incluya la "
                f"clave de partición; encontrada {names}"
            )
            assert names == ["id", "created_at"], pk_message
        finally:
            await conn.close()

    asyncio.run(_check())


def test_the_current_month_and_the_headroom_are_covered(migrated: str) -> None:
    """Sin la partición del mes que viene, la primera inserción de ese mes falla."""

    async def _check() -> None:
        conn = await _connect(migrated)
        try:
            present = {
                row["relname"]
                for row in await conn.fetch(
                    "SELECT child.relname FROM pg_inherits"
                    " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
                    " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
                    " WHERE parent.relname = $1",
                    TABLE,
                )
            }
            now_month = _month_start(await conn.fetchval("SELECT now()"))
            expected = {_partition_name(_add_months(now_month, n)) for n in range(HEADROOM + 1)}
            missing = sorted(expected - present)
            assert not missing, f"la migración no dejó cubierto el colchón: faltan {missing}"
        finally:
            await conn.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# 3. RLS declarada en cada partición
# ---------------------------------------------------------------------------
def test_every_partition_carries_its_own_rls(migrated: str) -> None:
    async def _check() -> None:
        conn = await _connect(migrated)
        try:
            rows = await conn.fetch(
                "SELECT child.relname, child.relrowsecurity, child.relforcerowsecurity"
                " FROM pg_inherits"
                " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
                " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
                " WHERE parent.relname = $1",
                TABLE,
            )
            assert len(rows) >= HEADROOM + 1, "el descubrimiento no vio las particiones"

            policies = {
                row["tablename"]: row["qual"] or ""
                for row in await conn.fetch(
                    "SELECT tablename, qual FROM pg_policies WHERE schemaname = 'public'"
                    " AND tablename LIKE $1",
                    f"{TABLE}%",
                )
            }
            offenders: list[str] = []
            for row in rows:
                name = row["relname"]
                if not row["relrowsecurity"]:
                    offenders.append(f"{name}: sin ENABLE ROW LEVEL SECURITY")
                elif not row["relforcerowsecurity"]:
                    offenders.append(f"{name}: sin FORCE ROW LEVEL SECURITY")
                elif "app.tenant_id" not in policies.get(name, ""):
                    offenders.append(f"{name}: sin policy que cite app.tenant_id")
            message = (
                "particiones sin aislamiento propio. Una consulta directa contra la "
                f"partición NO pasa por la policy del padre: {offenders}"
            )
            assert not offenders, message
        finally:
            await conn.close()

    asyncio.run(_check())


def test_isolation_holds_for_a_real_app_user_session(migrated: str, app_database_url: str) -> None:
    """Aislamiento MEDIDO, no declarado: por el padre y por la partición."""

    async def _check() -> None:
        dsn = app_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        admin = await _connect(migrated)
        try:
            mine = await _seed_tenant(admin, f"part-mine-{uuid4().hex[:8]}")
            theirs = await _seed_tenant(admin, f"part-theirs-{uuid4().hex[:8]}")
            await _insert_event(admin, mine)
            await _insert_event(admin, theirs)
            partition = _partition_name(_month_start(await admin.fetchval("SELECT now()")))
        finally:
            await admin.close()

        app = await _connect(dsn)
        try:
            await app.execute("SELECT set_config('app.tenant_id', $1, false)", mine)
            through_parent = await app.fetch(f"SELECT tenant_id FROM {TABLE}")
            assert [str(r["tenant_id"]) for r in through_parent] == [mine]

            direct = await app.fetch(f"SELECT tenant_id FROM {partition}")
            direct_message = (
                "leyendo DIRECTAMENTE la partición se ven filas de otro tenant: la "
                "partición no tiene su propia policy"
            )
            assert [str(r["tenant_id"]) for r in direct] == [mine], direct_message
        finally:
            await app.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# 5. Índices propagados sin declararlos por partición
# ---------------------------------------------------------------------------
def test_parent_indexes_propagate_to_every_partition(migrated: str) -> None:
    async def _check() -> None:
        conn = await _connect(migrated)
        try:
            parent_indexes = await conn.fetchval(
                "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid"
                " WHERE c.relname = $1",
                TABLE,
            )
            assert parent_indexes >= 5, f"el padre perdió índices (vio {parent_indexes})"

            rows = await conn.fetch(
                "SELECT child.relname,"
                " (SELECT count(*) FROM pg_index i WHERE i.indrelid = child.oid) AS n"
                " FROM pg_inherits"
                " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
                " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
                " WHERE parent.relname = $1",
                TABLE,
            )
            short = [r["relname"] for r in rows if r["n"] < parent_indexes]
            assert not short, f"particiones con menos índices que el padre: {short}"
        finally:
            await conn.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# 6. Enrutado + el modo de fallo que el job previene
# ---------------------------------------------------------------------------
def test_a_row_lands_in_the_partition_of_its_month(migrated: str) -> None:
    async def _check() -> None:
        conn = await _connect(migrated)
        try:
            tenant = await _seed_tenant(conn, f"part-route-{uuid4().hex[:8]}")
            event_id = await _insert_event(conn, tenant)
            partition = _partition_name(_month_start(await conn.fetchval("SELECT now()")))
            found = await conn.fetchval(f"SELECT count(*) FROM {partition} WHERE id = $1", event_id)
            assert found == 1, f"la fila no aterrizó en {partition}"

            where = await conn.fetchval(
                f"SELECT tableoid::regclass::text FROM {TABLE} WHERE id = $1", event_id
            )
            assert where == partition
        finally:
            await conn.close()

    asyncio.run(_check())


def test_a_row_outside_every_partition_is_rejected(migrated: str) -> None:
    """El incidente que el job existe para evitar, provocado a propósito.

    Sin esta comprobación, «hace falta un job que cree la partición» sería una
    creencia. Con ella es un hecho medido: PostgreSQL rechaza la fila.
    """

    async def _check() -> None:
        conn = await _connect(migrated)
        try:
            tenant = await _seed_tenant(conn, f"part-far-{uuid4().hex[:8]}")
            far_future = datetime.now(UTC) + timedelta(days=365 * 5)
            with pytest.raises(asyncpg.PostgresError) as excinfo:
                await _insert_event(conn, tenant, created_at=far_future)
            assert "no partition of relation" in str(excinfo.value).lower()
        finally:
            await conn.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# 6b. El camino de PRODUCCIÓN: convertir una tabla que YA tiene meses de datos
# ---------------------------------------------------------------------------
def test_upgrade_covers_the_months_of_the_pre_existing_data(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Con datos viejos dentro, la migración crea también SUS meses.

    Éste es el camino que se va a ejecutar de verdad: en la instancia viva la
    tabla arrastra meses de historia. Todo lo demás de este fichero corre sobre
    una tabla vacía, donde `_planned_months` solo produce el mes en curso + el
    colchón — o sea, el bucle que recorre los meses de los datos **no se ejercita
    nunca**. Si estuviera roto, se descubriría en producción: o la migración
    revienta con «no partition of relation found for row» a mitad de la copia, o
    (peor) el recuento no cuadra y aborta después de haber tirado los índices.
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, "0130_deploy_disabled_reason")  # type: ignore[arg-type]

    # Cuatro meses hacia atrás, en la tabla PLANA (que acepta cualquier fecha).
    async def _seed() -> tuple[str, list[date]]:
        conn = await _connect(migrations_pg_dsn)
        try:
            tenant = await _seed_tenant(conn, f"part-hist-{uuid4().hex[:8]}")
            now_month = _month_start(await conn.fetchval("SELECT now()"))
            months = [_add_months(now_month, -n) for n in (4, 3, 1)]
            for first_of_month in months:
                moment = datetime(first_of_month.year, first_of_month.month, 15, 12, 0, tzinfo=UTC)
                await _insert_event(conn, tenant, created_at=moment)
            return tenant, months
        finally:
            await conn.close()

    tenant, months = asyncio.run(_seed())

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _check() -> None:
        conn = await _connect(migrations_pg_dsn)
        try:
            present = {
                row["relname"]
                for row in await conn.fetch(
                    "SELECT child.relname FROM pg_inherits"
                    " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
                    " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
                    " WHERE parent.relname = $1",
                    TABLE,
                )
            }
            # Los meses de los datos, INCLUIDO el hueco de en medio: las
            # particiones tienen que ser contiguas, no solo las de los meses con
            # filas — un rango con agujeros rechaza la primera fila del agujero.
            now_month = _month_start(await conn.fetchval("SELECT now()"))
            expected = {_partition_name(_add_months(now_month, -n)) for n in range(5)}
            missing = sorted(expected - present)
            assert not missing, f"la migración no cubrió los meses de los datos: {missing}"

            rows = await conn.fetch(
                f"SELECT created_at, tableoid::regclass::text AS part FROM {TABLE}"
                " WHERE tenant_id = $1 ORDER BY created_at",
                tenant,
            )
            assert len(rows) == len(months), "la copia perdió filas históricas"
            for row, first_of_month in zip(rows, months, strict=True):
                assert row["part"] == _partition_name(first_of_month)
        finally:
            await conn.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# 7. La ida y vuelta, con datos dentro
# ---------------------------------------------------------------------------
def test_downgrade_and_upgrade_round_trip_preserves_rows(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """`downgrade` → `upgrade` sin perder filas ni el aislamiento.

    Con datos dentro a propósito: un round-trip sobre una tabla vacía prueba que
    el SQL parsea, no que la copia funciona — que es justo lo que el ADR pide
    («un downgrade que se prueba de verdad, no que se escribe»).
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _seed() -> tuple[str, list[str]]:
        conn = await _connect(migrations_pg_dsn)
        try:
            tenant = await _seed_tenant(conn, f"part-trip-{uuid4().hex[:8]}")
            ids = [await _insert_event(conn, tenant) for _ in range(3)]
            return tenant, sorted(ids)
        finally:
            await conn.close()

    tenant, ids = asyncio.run(_seed())

    # Vuelta a la tabla PLANA.
    command.downgrade(alembic_config, "0130_deploy_disabled_reason")  # type: ignore[arg-type]

    async def _check_flat() -> None:
        conn = await _connect(migrations_pg_dsn)
        try:
            relkind = await _relkind(conn, TABLE)
            assert relkind == "r", "el downgrade dejó la tabla particionada"
            rows = await conn.fetch(f"SELECT id FROM {TABLE} WHERE tenant_id = $1", tenant)
            survived = sorted(str(r["id"]) for r in rows)
            assert survived == ids, "el downgrade perdió filas por el camino"
            pk = await conn.fetchval(
                "SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid"
                " WHERE c.relname = $1 AND con.contype = 'p'",
                TABLE,
            )
            assert pk == 1, "la tabla plana se quedó sin PK"
        finally:
            await conn.close()

    asyncio.run(_check_flat())

    # Y de nuevo a particionada, con las mismas filas.
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _check_partitioned_again() -> None:
        conn = await _connect(migrations_pg_dsn)
        try:
            relkind = await _relkind(conn, TABLE)
            assert relkind == "p"
            rows = await conn.fetch(f"SELECT id FROM {TABLE} WHERE tenant_id = $1", tenant)
            assert sorted(str(r["id"]) for r in rows) == ids
        finally:
            await conn.close()

    asyncio.run(_check_partitioned_again())


# ---------------------------------------------------------------------------
# 8. El job, contra la base de verdad
# ---------------------------------------------------------------------------
def test_ensure_partitions_creates_the_missing_month_with_its_rls(
    migrated: str, admin_database_url: str
) -> None:
    """El job hace su trabajo: crea la partición que falta Y la protege."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from workers.maintenance.partitions import SqlPartitionStore, ensure_partitions

    class Recorder:
        def __init__(self) -> None:
            self.published: list[dict[str, Any]] = []

        def publish(self, event: dict[str, Any]) -> None:
            self.published.append(event)

    async def _check() -> None:
        conn = await _connect(migrated)
        try:
            now_month = _month_start(await conn.fetchval("SELECT now()"))
            # Un mes MÁS ALLÁ del colchón que dejó la migración: el hueco es real.
            target_month = _add_months(now_month, HEADROOM + 1)
            target = _partition_name(target_month)
            assert (
                await conn.fetchval("SELECT to_regclass($1)", target)
            ) is None, "el mes objetivo ya existía: el test no probaría nada"
        finally:
            await conn.close()

        engine = create_async_engine(admin_database_url)
        recorder = Recorder()
        try:
            report = await ensure_partitions(
                SqlPartitionStore(engine),
                recorder,
                tables=(TABLE,),
                now=datetime(target_month.year, target_month.month, 10, tzinfo=UTC),
            )
        finally:
            await engine.dispose()

        assert target in report["created"]
        assert report["gaps"] == {}
        assert recorder.published == []

        conn = await _connect(migrated)
        try:
            flags = await conn.fetchrow(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1",
                target,
            )
            rls_message = f"{target} nació sin RLS: sería una puerta lateral entre tenants"
            assert flags["relrowsecurity"] and flags["relforcerowsecurity"], rls_message
            qual = await conn.fetchval("SELECT qual FROM pg_policies WHERE tablename = $1", target)
            assert qual and "app.tenant_id" in qual

            attached = await conn.fetchval(
                "SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
                " JOIN pg_class p ON p.oid = i.inhparent"
                " WHERE p.relname = $1 AND c.relname = $2",
                TABLE,
                target,
            )
            assert attached == 1, f"{target} no quedó enganchada al padre"
        finally:
            await conn.close()

    asyncio.run(_check())


def test_ensure_partitions_is_idempotent_against_the_real_database(
    migrated: str, admin_database_url: str
) -> None:
    """El beat corre a diario: la segunda pasada del día no puede fallar."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from workers.maintenance.partitions import SqlPartitionStore, ensure_partitions

    class Recorder:
        def __init__(self) -> None:
            self.published: list[dict[str, Any]] = []

        def publish(self, event: dict[str, Any]) -> None:
            self.published.append(event)

    async def _check() -> None:
        engine = create_async_engine(admin_database_url)
        recorder = Recorder()
        try:
            first = await ensure_partitions(SqlPartitionStore(engine), recorder, tables=(TABLE,))
            second = await ensure_partitions(SqlPartitionStore(engine), recorder, tables=(TABLE,))
        finally:
            await engine.dispose()

        assert first["gaps"] == {}
        assert second["created"] == [], "la segunda pasada volvió a crear particiones"
        assert second["gaps"] == {}
        assert recorder.published == []

    asyncio.run(_check())
