"""notification_logs → tabla PARTICIONADA por rango mensual (part-01, ADR 0151).

Segunda de las cinco conversiones. El patrón es el de la migración **0131**
(`guardrail_events`) y aquí no se reinventa: tabla nueva particionada → copia con
recuento verificado → intercambio → índices en el padre → RLS en el padre y en
cada partición, y un `downgrade` que hace el camino inverso de verdad.

Lo que esta tabla tiene y `guardrail_events` no
-----------------------------------------------
1. **Una FK entrante que el plan `part-01` no había contado.**
   ``notification_log_reads.log_id`` referencia ``notification_logs.id`` con
   ``ON DELETE CASCADE`` y ``NOT NULL``. Una FK no puede apuntar a una PK
   compuesta sin llevar las dos columnas, así que **se retira** — decisión
   [ADR 0154](../../../../docs/05-architecture-decisions/0154-fk-hacia-tablas-particionadas.md),
   con su porqué: la cascada existía «por si algún día se purgan los logs» y el
   ADR 0151 acaba de decidir que ese día no llega. El ``downgrade`` la restaura:
   una vuelta atrás que no devuelve la integridad referencial no es una vuelta
   atrás.

2. **DOS policies en el padre.** ``notification_logs_tenant_isolation`` (FOR ALL)
   y ``notification_logs_platform_read`` (FOR SELECT sobre ``tenant_id IS NULL``,
   el inbox de plataforma del System Admin). Las dos se conservan.

   **En las particiones va SOLO la canónica**, y es deliberado: es exactamente lo
   que crea el job ``workers.ensure_partitions`` para las particiones futuras, y
   dos formas distintas de partición según quién la creara sería la clase de
   diferencia que nadie mira hasta que explica un bug. Las lecturas de la
   aplicación van por el padre, donde las dos policies aplican; una lectura
   DIRECTA contra una partición es una operación de mantenimiento, y allí el
   comportamiento más restrictivo es el correcto.

3. **Es la que más PII por fila lleva.** Desde la migración 0113 guarda
   ``subject``/``body``, o sea el contenido del mensaje. La RLS por partición aquí
   no es ceremonia.

4. **Una guarda nueva respecto a la 0131: los conjuntos de columnas.** El riesgo
   real de escribir el cuerpo de la tabla a mano es olvidar una columna que añadió
   una migración posterior (aquí, ``subject``/``body`` de la 0113). El recuento de
   filas NO lo detecta —cuadra igual—, y el resultado es la pérdida silenciosa de
   una columna entera. :func:`_assert_same_columns` compara origen y destino antes
   de copiar y revienta si difieren.

Revision ID: 0134_partition_notification_logs
Revises: 0133_complete_approval_policies
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0134_partition_notification_logs"
down_revision: str | Sequence[str] | None = "0133_complete_approval_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "notification_logs"
LEGACY = "notification_logs_legacy"

#: La FK entrante que se retira (ADR 0154). El `downgrade` la vuelve a poner.
CHILD_TABLE = "notification_log_reads"
CHILD_FK = "fk_notification_log_reads_log"

#: Meses de colchón hacia adelante que crea la propia migración. Coincide con
#: ``workers.maintenance.partitions.PARTITION_HEADROOM_MONTHS`` a propósito, y NO
#: se importa de allí: una migración no puede depender del código de los workers,
#: que vive en otro despliegue. Lo vigila
#: `test_partition_notification_logs.py::test_the_headroom_constant_did_not_drift`.
HEADROOM_MONTHS = 3

#: Las columnas, en orden, para el ``INSERT … SELECT``. Explícitas y no ``*``.
COLUMNS = (
    "id",
    "channel_id",
    "tenant_id",
    "event_type",
    "channel_type",
    "status",
    "target",
    "attempt",
    "error",
    "sent_at",
    "created_at",
    "subject",
    "body",
)

#: Los índices del padre con el nombre EXACTO que tienen hoy (migraciones 0045 y
#: 0113). Al crearlos sobre una tabla particionada, PostgreSQL crea el equivalente
#: en cada partición —presente y futura—, así que no se repiten por partición.
#: El último es PARCIAL: la vista del operador de envíos atascados.
INDEXES: tuple[tuple[str, str, str | None], ...] = (
    ("ix_notification_logs_tenant_id", "tenant_id", None),
    ("ix_notification_logs_tenant_created", "tenant_id, created_at", None),
    ("ix_notification_logs_channel_created", "channel_id, created_at", None),
    ("ix_notification_logs_event_created", "event_type, created_at", None),
    (
        "ix_notification_logs_status",
        "status, created_at",
        "status IN ('retrying', 'dead_letter', 'failed')",
    ),
)

#: El predicado canónico de aislamiento por tenant, LITERAL de la migración 0045.
_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

#: El cuerpo de la tabla, compartido por la forma particionada y la plana: las
#: dos son idénticas salvo por la PK y el ``PARTITION BY``. La FK saliente hacia
#: ``notification_channels`` va dentro porque una tabla particionada SÍ puede
#: tener FK salientes (PostgreSQL ≥ 12) y perderla sería un cambio de esquema
#: colado de rondón.
_BODY = """
    id uuid NOT NULL,
    channel_id uuid,
    tenant_id uuid,
    event_type varchar(64) NOT NULL,
    channel_type varchar(16) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'queued',
    target varchar(512),
    attempt integer NOT NULL DEFAULT 1,
    error text,
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    subject varchar(200),
    body varchar(2000),
    CONSTRAINT fk_notification_logs_channel FOREIGN KEY (channel_id)
        REFERENCES notification_channels (id) ON DELETE SET NULL
"""


# ---------------------------------------------------------------------------
# Aritmética de calendario (local: una migración es autocontenida)
# ---------------------------------------------------------------------------
def _month_start(moment: datetime | date) -> date:
    return date(moment.year, moment.month, 1)


def _add_months(start: date, months: int) -> date:
    total = (start.year * 12 + (start.month - 1)) + months
    return date(total // 12, total % 12 + 1, 1)


def _months_between(first: date, last: date) -> list[date]:
    out: list[date] = []
    cursor = first
    while cursor <= last:
        out.append(cursor)
        cursor = _add_months(cursor, 1)
    return out


def _partition_name(first_of_month: date) -> str:
    return f"{TABLE}_{first_of_month.year:04d}_{first_of_month.month:02d}"


def _planned_months(bind: sa.engine.Connection, *, source: str) -> list[date]:
    """Los meses a crear: los que cubren los datos + el colchón hacia adelante.

    El «ahora» se pide a PostgreSQL, no al reloj del proceso que corre Alembic: es
    el mismo reloj que pone el ``DEFAULT now()`` de las filas que van a llegar.
    """
    oldest, newest, present = bind.execute(
        sa.text(f"SELECT min(created_at), max(created_at), now() FROM {source}")
    ).one()
    now_month = _month_start(present)
    first = _month_start(oldest) if oldest is not None else now_month
    last_data = _month_start(newest) if newest is not None else now_month
    last = max(last_data, _add_months(now_month, HEADROOM_MONTHS))
    return _months_between(min(first, now_month), last)


def _assert_same_columns(bind: sa.engine.Connection, *, source: str, target: str) -> None:
    """Origen y destino tienen EXACTAMENTE las mismas columnas.

    La guarda que la 0131 no tenía. El recuento de filas no ve una columna
    olvidada: cuadra igual y la tabla nueva sale sin ella. Aquí eso habría sido
    perder ``subject``/``body`` —el contenido de cada notificación— en silencio.
    """
    query = sa.text(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = :t"
    )
    origin = {r[0] for r in bind.execute(query, {"t": source})}
    destination = {r[0] for r in bind.execute(query, {"t": target})}
    if origin != destination:
        raise RuntimeError(
            f"{source} y {target} no tienen las mismas columnas. Sobran en origen:"
            f" {sorted(origin - destination)}; sobran en destino:"
            f" {sorted(destination - origin)}. Una columna olvidada NO la detecta el"
            " recuento de filas: se pierde entera y en silencio."
        )


def _copy_verified(bind: sa.engine.Connection, *, source: str, target: str) -> None:
    """Copia ``source`` → ``target`` y REVIENTA si los recuentos no cuadran."""
    _assert_same_columns(bind, source=source, target=target)
    columns = ", ".join(COLUMNS)
    expected = bind.execute(sa.text(f"SELECT count(*) FROM {source}")).scalar_one()
    bind.execute(sa.text(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {source}"))
    copied = bind.execute(sa.text(f"SELECT count(*) FROM {target}")).scalar_one()
    if copied != expected:
        raise RuntimeError(
            f"la copia {source} → {target} se dejó filas por el camino: {expected} en"
            f" origen, {copied} en destino. La causa habitual es la RLS FORCE del"
            " origen ocultando filas al propio dueño de la tabla. NO se continúa:"
            " terminar aquí deja la tabla nueva incompleta."
        )


def _partitions_of(bind: sa.engine.Connection, parent: str) -> list[str]:
    rows = bind.execute(
        sa.text(
            "SELECT child.relname FROM pg_inherits"
            " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
            " JOIN pg_class p ON p.oid = pg_inherits.inhparent"
            " WHERE p.relname = :parent AND p.relnamespace = 'public'::regnamespace"
        ),
        {"parent": parent},
    ).all()
    return [str(row[0]) for row in rows]


def _canonical_rls(relation: str) -> tuple[str, ...]:
    """ENABLE + FORCE + la policy canónica de aislamiento por tenant."""
    policy = f"{relation}_tenant_isolation"
    return (
        f"ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {relation} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy} ON {relation} FOR ALL"
        f" USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})",
    )


#: La segunda policy del PADRE (solo del padre — ver el punto 2 del docstring).
_PLATFORM_READ = (
    f"CREATE POLICY {TABLE}_platform_read ON {TABLE} FOR SELECT USING (tenant_id IS NULL)"
)

_DROP_CHILD_FK = f"ALTER TABLE {CHILD_TABLE} DROP CONSTRAINT IF EXISTS {CHILD_FK}"
_ADD_CHILD_FK = (
    f"ALTER TABLE {CHILD_TABLE} ADD CONSTRAINT {CHILD_FK}"
    f" FOREIGN KEY (log_id) REFERENCES {TABLE} (id) ON DELETE CASCADE"
)


# ---------------------------------------------------------------------------
# Ida
# ---------------------------------------------------------------------------
def upgrade() -> None:
    bind = op.get_bind()

    # 1. La FK entrante, ANTES que nada: sin retirarla no se puede tirar la PK
    #    simple a la que apunta (ADR 0154).
    op.execute(_DROP_CHILD_FK)

    # 2. El FORCE se aplica al DUEÑO: sin quitarlo, la copia lee cero filas y no
    #    da error. La tabla se destruye al final, así que no debilita nada.
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    months = _planned_months(bind, source=TABLE)

    # 3. Apartar la vieja y liberar los nombres (índices y constraints comparten
    #    el espacio de nombres del esquema).
    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns, _where in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT pk_{TABLE}")
    # La FK saliente hacia notification_channels viaja con la tabla renombrada y
    # su nombre choca con el de la tabla nueva: hay que soltarlo también.
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT fk_{TABLE}_channel")

    # 4. La nueva, particionada. PK COMPUESTA porque PostgreSQL lo exige.
    op.execute(
        f"CREATE TABLE {TABLE} ({_BODY},"
        f" CONSTRAINT pk_{TABLE} PRIMARY KEY (id, created_at))"
        " PARTITION BY RANGE (created_at)"
    )

    # 5. Una partición por mes: las que cubren los datos existentes + el colchón.
    for first_of_month in months:
        op.execute(
            f"CREATE TABLE {_partition_name(first_of_month)} PARTITION OF {TABLE}"
            f" FOR VALUES FROM ('{first_of_month.isoformat()}')"
            f" TO ('{_add_months(first_of_month, 1).isoformat()}')"
        )

    # 6. Copiar y VERIFICAR (columnas y filas).
    _copy_verified(bind, source=LEGACY, target=TABLE)

    # 7. Fuera la vieja, índices sobre el padre y RLS: las dos policies en el
    #    padre, la canónica en cada partición.
    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns, where in INDEXES:
        clause = f" WHERE {where}" if where else ""
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns}){clause}")
    for relation in [TABLE, *[_partition_name(m) for m in months]]:
        for statement in _canonical_rls(relation):
            op.execute(statement)
    op.execute(_PLATFORM_READ)


# ---------------------------------------------------------------------------
# Vuelta
# ---------------------------------------------------------------------------
def downgrade() -> None:
    bind = op.get_bind()
    partitions = _partitions_of(bind, TABLE)

    # 1. Mismo motivo que en la ida, ahora en el padre y en cada partición.
    for relation in [TABLE, *partitions]:
        op.execute(f"ALTER TABLE {relation} NO FORCE ROW LEVEL SECURITY")

    # 2. Apartar la particionada y liberar los nombres.
    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns, _where in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT pk_{TABLE}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT fk_{TABLE}_channel")

    # 3. La tabla PLANA de siempre, con la PK simple de la 0045.
    op.execute(f"CREATE TABLE {TABLE} ({_BODY}, CONSTRAINT pk_{TABLE} PRIMARY KEY (id))")

    # 4. Copiar y VERIFICAR: un downgrade que pierde filas es peor que uno que
    #    falla, porque el fallo se ve.
    _copy_verified(bind, source=LEGACY, target=TABLE)

    # 5. Tirar la particionada (se lleva sus particiones), rehacer índices y RLS.
    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns, where in INDEXES:
        clause = f" WHERE {where}" if where else ""
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns}){clause}")
    for statement in _canonical_rls(TABLE):
        op.execute(statement)
    op.execute(_PLATFORM_READ)

    # 6. Y la FK entrante vuelve: la integridad referencial que había antes de
    #    esta migración es parte de lo que hay que restaurar (ADR 0154).
    #    Los receipts que hubieran quedado huérfanos mientras tanto impedirían
    #    crearla, así que se limpian primero — son marcadores de lectura, no dato.
    op.execute(
        f"DELETE FROM {CHILD_TABLE} c WHERE NOT EXISTS"
        f" (SELECT 1 FROM {TABLE} l WHERE l.id = c.log_id)"
    )
    op.execute(_ADD_CHILD_FK)
