"""El contrato común de una tabla particionada (part-01, ADR 0151).

`tests/integration/test_partition_guardrail_events.py` escribió el patrón entero
para la primera de las cinco conversiones. Copiar esos 570 renglones cuatro veces
tendría un coste que no es el teclado: **cuatro copias divergen**. La cuarta vez
que alguien arregle un descubrimiento roto lo arreglará en un fichero y dejará
tres mintiendo en verde, que es justo el modo de fallo que el ADR 0151 teme en la
propia RLS por partición.

Así que aquí viven las comprobaciones que valen para **cualquiera** de las cinco
tablas, en forma de funciones que devuelven una lista de *ofensores* (vacía = OK)
en vez de asertar: quien asierta es el test, que es quien sabe poner el mensaje
en su contexto. Lo específico de cada tabla —qué FK se retiró, qué consulta de
facturación tiene que seguir yendo, el round-trip del `downgrade`— se queda en su
fichero, porque no se comparte.

El fichero de `guardrail_events` NO se reescribió sobre esto a propósito: es la
prueba del patrón entero escrita en largo, y sirve de referencia legible. Este
módulo es su destilado, y el test
``test_the_contract_module_matches_the_reference_file`` no existe — si algún día
divergen, lo que manda es el catálogo de PostgreSQL, que es lo que los dos leen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any, TypeVar

import asyncpg

T = TypeVar("T")

#: Meses de colchón que dejan tanto la migración como el job. Duplicado aquí (y
#: no importado de `workers.maintenance.partitions`) para que el test compruebe
#: el número esperado y no el número que el código haya decidido hoy.
HEADROOM = 3


# ---------------------------------------------------------------------------
# Aritmética de calendario y conexión
# ---------------------------------------------------------------------------
def month_start(moment: datetime | date) -> date:
    return date(moment.year, moment.month, 1)


def add_months(start: date, months: int) -> date:
    total = (start.year * 12 + (start.month - 1)) + months
    return date(total // 12, total % 12 + 1, 1)


def partition_name(table: str, first_of_month: date) -> str:
    return f"{table}_{first_of_month.year:04d}_{first_of_month.month:02d}"


def run[T](coro: Awaitable[T]) -> T:
    """`asyncio.run` sobre una corrutina ya construida (azúcar para los tests)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


async def connect(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn)


async def with_connection[T](dsn: str, body: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
    conn = await connect(dsn)
    try:
        return await body(conn)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Lecturas del catálogo
# ---------------------------------------------------------------------------
async def relkind(conn: asyncpg.Connection, table: str) -> str:
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


async def partitions_of(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT child.relname FROM pg_inherits"
        " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
        " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
        " WHERE parent.relname = $1 AND parent.relnamespace = 'public'::regnamespace",
        table,
    )
    return {str(r["relname"]) for r in rows}


async def primary_key_columns(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT a.attname FROM pg_constraint con"
        " JOIN pg_class c ON c.oid = con.conrelid"
        " JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true"
        " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum"
        " WHERE c.relname = $1 AND c.relnamespace = 'public'::regnamespace"
        " AND con.contype = 'p' ORDER BY k.ord",
        table,
    )
    return [str(r["attname"]) for r in rows]


async def foreign_keys_pointing_at(conn: asyncpg.Connection, table: str) -> list[str]:
    """`tabla_hija.conname` de cada FK que referencia ``table``."""
    rows = await conn.fetch(
        "SELECT con.conrelid::regclass::text AS child, con.conname FROM pg_constraint con"
        " WHERE con.contype = 'f' AND con.confrelid = $1::regclass",
        table,
    )
    return sorted(f"{r['child']}.{r['conname']}" for r in rows)


# ---------------------------------------------------------------------------
# Las comprobaciones del contrato — devuelven ofensores, no asertan
# ---------------------------------------------------------------------------
async def shape_offenders(conn: asyncpg.Connection, table: str) -> list[str]:
    """Particionada por RANGE (created_at) y con PK `(id, created_at)`.

    La PK compuesta no es una preferencia de modelado: PostgreSQL **exige** que la
    clave primaria de una tabla particionada incluya la clave de partición. Si el
    modelo ORM y la migración se desalinearan aquí, el ORM insertaría contra una
    PK que no existe.
    """
    offenders: list[str] = []
    kind = await relkind(conn, table)
    if kind != "p":
        offenders.append(f"{table} no es una tabla particionada (relkind={kind!r})")
        return offenders

    strategy = await conn.fetchval(
        "SELECT pg_get_partkeydef(c.oid) FROM pg_class c"
        " WHERE c.relname = $1 AND c.relnamespace = 'public'::regnamespace",
        table,
    )
    if strategy != "RANGE (created_at)":
        offenders.append(f"{table}: clave de partición {strategy!r}, esperaba 'RANGE (created_at)'")

    pk = await primary_key_columns(conn, table)
    if pk != ["id", "created_at"]:
        offenders.append(f"{table}: PK {pk}, esperaba ['id', 'created_at']")
    return offenders


async def headroom_offenders(conn: asyncpg.Connection, table: str) -> list[str]:
    """El mes en curso y `HEADROOM` más tienen partición.

    Sin la del mes que viene, la primera inserción de ese mes falla con «no
    partition of relation found for row» — el incidente que el ADR 0151 nombra.
    """
    present = await partitions_of(conn, table)
    now_month = month_start(await conn.fetchval("SELECT now()"))
    expected = {partition_name(table, add_months(now_month, n)) for n in range(HEADROOM + 1)}
    missing = sorted(expected - present)
    return [f"{table}: colchón sin cubrir, faltan {missing}"] if missing else []


async def partition_rls_offenders(conn: asyncpg.Connection, table: str) -> list[str]:
    """Cada partición lleva ENABLE + FORCE + policy que cita `app.tenant_id`.

    Al consultar por el padre se aplica la policy del padre, pero una consulta
    DIRECTA contra la partición solo pasa por las suyas: una partición sin policy
    es una puerta lateral al aislamiento entre tenants.
    """
    rows = await conn.fetch(
        "SELECT child.relname, child.relrowsecurity, child.relforcerowsecurity"
        " FROM pg_inherits"
        " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
        " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
        " WHERE parent.relname = $1 AND parent.relnamespace = 'public'::regnamespace",
        table,
    )
    if len(rows) < HEADROOM + 1:
        return [f"{table}: el descubrimiento solo vio {len(rows)} particiones"]

    policies: dict[str, str] = {}
    for row in await conn.fetch(
        "SELECT tablename, coalesce(qual, '') AS qual FROM pg_policies"
        " WHERE schemaname = 'public' AND tablename LIKE $1",
        f"{table}%",
    ):
        policies[str(row["tablename"])] = policies.get(str(row["tablename"]), "") + str(row["qual"])

    offenders: list[str] = []
    for row in rows:
        name = str(row["relname"])
        if not row["relrowsecurity"]:
            offenders.append(f"{name}: sin ENABLE ROW LEVEL SECURITY")
        elif not row["relforcerowsecurity"]:
            offenders.append(f"{name}: sin FORCE ROW LEVEL SECURITY")
        elif "app.tenant_id" not in policies.get(name, ""):
            offenders.append(f"{name}: sin policy que cite app.tenant_id")
    return offenders


async def index_propagation_offenders(conn: asyncpg.Connection, table: str) -> list[str]:
    """Cada partición tiene al menos tantos índices como el padre.

    Los índices se declaran UNA vez sobre el padre y PostgreSQL crea el
    equivalente en cada partición, presente y futura. Esta comprobación es la que
    delata una migración que los declarara por partición (y se dejara las nuevas).
    """
    parent_indexes = await conn.fetchval(
        "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid"
        " WHERE c.relname = $1 AND c.relnamespace = 'public'::regnamespace",
        table,
    )
    if not parent_indexes:
        return [f"{table}: el padre se quedó sin índices"]
    # Sin esta guarda el test pasa VACÍAMENTE sobre una tabla todavía sin
    # particionar: `short` sale vacío porque no hay hijas que comparar.
    present = await partitions_of(conn, table)
    if len(present) < HEADROOM + 1:
        return [f"{table}: solo {len(present)} particiones, la comparación no probaría nada"]
    rows = await conn.fetch(
        "SELECT child.relname,"
        " (SELECT count(*) FROM pg_index i WHERE i.indrelid = child.oid) AS n"
        " FROM pg_inherits"
        " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
        " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
        " WHERE parent.relname = $1 AND parent.relnamespace = 'public'::regnamespace",
        table,
    )
    short = [str(r["relname"]) for r in rows if r["n"] < parent_indexes]
    if not short:
        return []
    return [f"{table}: particiones con menos índices que el padre ({parent_indexes}): {short}"]


# ---------------------------------------------------------------------------
# El job, contra la base real
# ---------------------------------------------------------------------------
async def job_creates_the_missing_month(
    dsn: str, admin_database_url: str, table: str
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Corre `ensure_partitions` sobre un mes MÁS ALLÁ del colchón de la migración.

    Devuelve `(nombre_objetivo, reporte, publicaciones)` para que el test asierte.
    Que el mes objetivo no exista antes se comprueba aquí: si existiera, el test
    pasaría sin probar nada.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from workers.maintenance.partitions import SqlPartitionStore, ensure_partitions

    class _Recorder:
        def __init__(self) -> None:
            self.published: list[dict[str, Any]] = []

        def publish(self, event: dict[str, Any]) -> None:
            self.published.append(event)

    conn = await connect(dsn)
    try:
        now_month = month_start(await conn.fetchval("SELECT now()"))
        target_month = add_months(now_month, HEADROOM + 1)
        target = partition_name(table, target_month)
        already = await conn.fetchval("SELECT to_regclass($1)", target)
        if already is not None:
            raise AssertionError(f"{target} ya existía: el test no probaría nada")
    finally:
        await conn.close()

    engine = create_async_engine(admin_database_url)
    recorder = _Recorder()
    try:
        report = await ensure_partitions(
            SqlPartitionStore(engine),
            recorder,
            tables=(table,),
            now=datetime(target_month.year, target_month.month, 10, tzinfo=UTC),
        )
    finally:
        await engine.dispose()
    return target, report, recorder.published


async def new_partition_offenders(conn: asyncpg.Connection, table: str, name: str) -> list[str]:
    """La partición que acaba de crear el job: enganchada y protegida."""
    offenders: list[str] = []
    flags = await conn.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
        " WHERE relname = $1 AND relnamespace = 'public'::regnamespace",
        name,
    )
    if flags is None:
        return [f"{name}: no existe"]
    if not (flags["relrowsecurity"] and flags["relforcerowsecurity"]):
        offenders.append(f"{name} nació sin RLS: sería una puerta lateral entre tenants")
    qual = await conn.fetchval(
        "SELECT qual FROM pg_policies WHERE schemaname = 'public' AND tablename = $1", name
    )
    if not qual or "app.tenant_id" not in qual:
        offenders.append(f"{name}: sin policy que cite app.tenant_id")
    attached = await conn.fetchval(
        "SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
        " JOIN pg_class p ON p.oid = i.inhparent"
        " WHERE p.relname = $1 AND c.relname = $2",
        table,
        name,
    )
    if attached != 1:
        offenders.append(f"{name} no quedó enganchada a {table}")
    return offenders
