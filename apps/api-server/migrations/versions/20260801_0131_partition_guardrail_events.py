"""guardrail_events → tabla PARTICIONADA por rango mensual (part-01, ADR 0151).

Primera de las cinco conversiones que firmó el operador el 2026-08-01 (opción C
del ADR 0151: retención infinita con particionado nativo, **no se borra nada**).
Va la primera por ser la de menor riesgo: ``guardrail_events`` no tiene ninguna
clave foránea entrante, así que la PK compuesta que PostgreSQL exige —la clave
primaria de una tabla particionada **debe incluir la clave de partición**— se
queda dentro de esta tabla. En ``executions``, la última de la serie, ese mismo
cambio arrastra cuatro FK.

Por qué esto no es un `ALTER`
-----------------------------
No existe ``ALTER TABLE … PARTITION BY``. Convertir una tabla con datos es:
tabla nueva particionada → copia → intercambio. Aquí, en orden:

1. quitar el ``FORCE`` de la tabla vieja (ver la trampa de abajo) y renombrarla;
2. liberar los nombres de sus índices y de su PK (son globales al esquema);
3. crear la nueva ``guardrail_events`` particionada, con PK ``(id, created_at)``;
4. crear las particiones mensuales que cubren los datos que hay **más tres meses
   de colchón** hacia adelante;
5. copiar, **contar a los dos lados y reventar si no cuadra**;
6. tirar la vieja, crear los cuatro índices sobre el padre (se propagan solos a
   cada partición) y activar la RLS en el padre y en **cada** partición.

Las dos guardas de la copia, y por qué están puestas hoy
-------------------------------------------------------
La forma de perder datos aquí es que el ``INSERT … SELECT`` lea menos filas de las
que hay y nadie se entere: la migración termina en verde con la tabla nueva
incompleta. Hay un mecanismo que lo produce — ``FORCE ROW LEVEL SECURITY`` se
aplica **también al dueño de la tabla**, así que un dueño sin ``app.tenant_id``
fijado leería cero filas sin error.

**Hoy eso no pasa, y conviene decirlo en vez de adornarlo**: ``migrations_user``
—el rol con el que corre Alembic— tiene ``BYPASSRLS``
(``docker/postgres/init/02-roles.sh``), y ``BYPASSRLS`` gana a ``FORCE``. Las dos
guardas están igualmente:

* el paso 1 quita el ``FORCE`` de la tabla que se va a destruir, para que la
  migración siga siendo correcta el día que el rol de migraciones **deje** de ser
  ``BYPASSRLS``. No es hipotético: prod-14 ya movió los servicios a un
  ``service_user`` con menos privilegios por ese mismo criterio;
* el paso 5 compara los recuentos de origen y destino y lanza ``RuntimeError`` si
  difieren. Ésta no depende de ninguna teoría sobre la RLS: cubre igual un
  ``INSERT`` truncado, una fila rechazada por falta de partición o un ``WHERE``
  que alguien añada mal en el futuro.

La nueva tabla se crea SIN RLS y se activa al final (mismo orden que la migración
0052 original: *«RLS last so the table exists»*).

El ``downgrade`` es real
------------------------
Hace el camino inverso completo —tabla plana, copia, intercambio, índices, RLS—
con el mismo recuento verificado. Lo prueba
``tests/integration/test_partition_guardrail_events.py::test_downgrade_and_upgrade_round_trip_preserves_rows``
con filas dentro, no solo con la tabla vacía.

Revision ID: 0131_partition_guardrail_events
Revises: 0130_deploy_disabled_reason
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0131_partition_guardrail_events"
down_revision: str | Sequence[str] | None = "0130_deploy_disabled_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "guardrail_events"
LEGACY = "guardrail_events_legacy"

#: Meses de colchón hacia adelante creados por la propia migración. Coincide con
#: ``workers.maintenance.partitions.PARTITION_HEADROOM_MONTHS`` a propósito: si el
#: beat no llegara a correr nunca, el sistema aguanta igual tres meses. NO se
#: importa de allí — una migración no puede depender del código de los workers,
#: que vive en otro despliegue y puede cambiar bajo sus pies.
HEADROOM_MONTHS = 3

#: Las columnas, en orden, para el ``INSERT … SELECT``. Explícitas y no ``*``:
#: un ``SELECT *`` copia bien hoy y copia mal el día que alguien añada una columna
#: en medio.
COLUMNS = (
    "id",
    "tenant_id",
    "guardrail_type",
    "hook_point",
    "severity",
    "action",
    "project_id",
    "agent_id",
    "execution_id",
    "agent_label",
    "detail",
    "detail_payload",
    "created_at",
)

#: Los cuatro índices del padre, con el nombre EXACTO que declara el ORM
#: (`db/guardrail_event.py`) y que creó la migración 0052. Al crearlos sobre una
#: tabla particionada, PostgreSQL crea el equivalente en cada partición —presente
#: y futura—, así que no hay que repetirlos por partición.
INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_guardrail_events_tenant_id", "tenant_id"),
    ("ix_guardrail_events_tenant_created", "tenant_id, created_at"),
    ("ix_guardrail_events_tenant_type_created", "tenant_id, guardrail_type, created_at"),
    ("ix_guardrail_events_tenant_severity_created", "tenant_id, severity, created_at"),
)

#: El predicado canónico de aislamiento por tenant, LITERAL de la migración 0052.
_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

#: Las columnas de la tabla, sin la PK, compartidas por el `CREATE TABLE` de ida
#: y el de vuelta: las dos formas (particionada y plana) tienen exactamente el
#: mismo cuerpo y solo se diferencian en la PK y en el `PARTITION BY`.
_BODY = """
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    guardrail_type varchar(64) NOT NULL,
    hook_point varchar(16) NOT NULL,
    severity varchar(16) NOT NULL,
    action varchar(32),
    project_id uuid,
    agent_id uuid,
    execution_id uuid,
    agent_label varchar(160),
    detail text NOT NULL DEFAULT '',
    detail_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
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
    """Los primeros de mes de ``first`` a ``last``, ambos incluidos."""
    out: list[date] = []
    cursor = first
    while cursor <= last:
        out.append(cursor)
        cursor = _add_months(cursor, 1)
    return out


def _partition_name(first_of_month: date) -> str:
    return f"{TABLE}_{first_of_month.year:04d}_{first_of_month.month:02d}"


def _rls_statements(relation: str) -> tuple[str, ...]:
    policy = f"{relation}_tenant_isolation"
    return (
        f"ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {relation} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy} ON {relation} FOR ALL"
        f" USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})",
    )


def _planned_months(bind: sa.engine.Connection, *, source: str) -> list[date]:
    """Los meses a crear: los que cubren los datos + el colchón hacia adelante.

    Con la tabla vacía (instalación nueva, base de tests) el resultado es el mes
    en curso más :data:`HEADROOM_MONTHS`. El «ahora» se pide a PostgreSQL, no al
    reloj del proceso que corre Alembic: es el mismo reloj que pone el
    ``DEFAULT now()`` de las filas que van a llegar.
    """
    bounds = bind.execute(
        sa.text(f"SELECT min(created_at), max(created_at), now() FROM {source}")
    ).one()
    oldest, newest, present = bounds
    now_month = _month_start(present)
    first = _month_start(oldest) if oldest is not None else now_month
    last_data = _month_start(newest) if newest is not None else now_month
    last = max(last_data, _add_months(now_month, HEADROOM_MONTHS))
    return _months_between(min(first, now_month), last)


def _copy_verified(bind: sa.engine.Connection, *, source: str, target: str) -> None:
    """Copia ``source`` → ``target`` y REVIENTA si los recuentos no cuadran.

    El recuento del origen se toma DESPUÉS de quitarle el ``FORCE``. Es una
    precaución con truco conocido: si el rol de migraciones perdiera su
    ``BYPASSRLS``, con el ``FORCE`` puesto el recuento y la copia darían cero
    los dos y «cuadrarían» mintiendo. Quitarlo antes hace que la comparación
    signifique algo en los dos escenarios. Ver el docstring del módulo.
    """
    columns = ", ".join(COLUMNS)
    expected = bind.execute(sa.text(f"SELECT count(*) FROM {source}")).scalar_one()
    bind.execute(sa.text(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {source}"))
    copied = bind.execute(sa.text(f"SELECT count(*) FROM {target}")).scalar_one()
    if copied != expected:
        message = (
            f"la copia {source} → {target} se dejó filas por el camino: "
            f"{expected} en origen, {copied} en destino. La causa habitual es la "
            "RLS FORCE del origen ocultando filas al propio dueño de la tabla. "
            "NO se continúa: terminar aquí deja la tabla nueva incompleta."
        )
        raise RuntimeError(message)


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


# ---------------------------------------------------------------------------
# Ida
# ---------------------------------------------------------------------------
def upgrade() -> None:
    bind = op.get_bind()

    # 1. El FORCE se aplica al DUEÑO: sin quitarlo, la copia lee cero filas y no
    #    da error. La tabla se destruye al final, así que no debilita nada.
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    months = _planned_months(bind, source=TABLE)

    # 2. Apartar la vieja y liberar los nombres. Índices y constraints comparten
    #    el espacio de nombres del esquema: sin esto, el CREATE de abajo choca.
    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT pk_{TABLE}")

    # 3. La nueva, particionada. PK COMPUESTA porque PostgreSQL lo exige: la
    #    clave primaria de una tabla particionada debe incluir la clave de
    #    partición. Sin RLS todavía (paso 6).
    op.execute(
        f"CREATE TABLE {TABLE} ({_BODY},"
        f" CONSTRAINT pk_{TABLE} PRIMARY KEY (id, created_at))"
        " PARTITION BY RANGE (created_at)"
    )

    # 4. Una partición por mes: las que cubren los datos existentes + el colchón.
    for first_of_month in months:
        name = _partition_name(first_of_month)
        start = first_of_month.isoformat()
        end = _add_months(first_of_month, 1).isoformat()
        op.execute(
            f"CREATE TABLE {name} PARTITION OF {TABLE} FOR VALUES FROM ('{start}') TO ('{end}')"
        )

    # 5. Copiar y VERIFICAR.
    _copy_verified(bind, source=LEGACY, target=TABLE)

    # 6. Fuera la vieja, índices sobre el padre (se propagan a las particiones) y
    #    RLS en el padre y en CADA partición: una consulta directa contra una
    #    partición solo pasa por las policies de esa relación.
    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns in INDEXES:
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns})")
    for relation in [TABLE, *[_partition_name(m) for m in months]]:
        for statement in _rls_statements(relation):
            op.execute(statement)


# ---------------------------------------------------------------------------
# Vuelta
# ---------------------------------------------------------------------------
def downgrade() -> None:
    bind = op.get_bind()
    partitions = _partitions_of(bind, TABLE)

    # 1. Mismo motivo que en la ida, ahora en el padre y en cada partición: al
    #    leer por el padre se aplican las policies de las relaciones escaneadas.
    for relation in [TABLE, *partitions]:
        op.execute(f"ALTER TABLE {relation} NO FORCE ROW LEVEL SECURITY")

    # 2. Apartar la particionada y liberar los nombres.
    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT pk_{TABLE}")

    # 3. La tabla PLANA de siempre, con la PK simple que tenía la 0052.
    op.execute(f"CREATE TABLE {TABLE} ({_BODY}, CONSTRAINT pk_{TABLE} PRIMARY KEY (id))")

    # 4. Copiar y VERIFICAR: un downgrade que pierde filas es peor que uno que
    #    falla, porque el fallo se ve.
    _copy_verified(bind, source=LEGACY, target=TABLE)

    # 5. Tirar la particionada (se lleva sus particiones), rehacer índices y RLS.
    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns in INDEXES:
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns})")
    for statement in _rls_statements(TABLE):
        op.execute(statement)
