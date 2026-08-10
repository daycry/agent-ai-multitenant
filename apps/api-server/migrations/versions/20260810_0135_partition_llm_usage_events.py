"""llm_usage_events → tabla PARTICIONADA por rango mensual (part-01, ADR 0151).

Tercera de las cinco conversiones. Mismo patrón que la 0131 y la 0134: tabla
nueva particionada → copia con recuento y **columnas** verificadas → intercambio
→ índices en el padre → RLS en el padre y en cada partición, con `downgrade` real.

Sin FK, ni entrantes ni salientes: es la conversión más limpia de las cinco. Lo
único propio de esta tabla es una trampa de nullabilidad y una respuesta que el
plan pedía por escrito.

`created_at` era NULLABLE, y en la PK no puede serlo
----------------------------------------------------
La migración 0109 creó la columna como
``sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"))``
— sin ``nullable=False``. El modelo ORM sí lo declara obligatorio
(:class:`TimestampMixin`), así que ninguna fila escrita por la aplicación tiene
la fecha vacía; pero **la columna lo permite**, y una fila con ``created_at IS
NULL`` no cae en ninguna partición.

Se rellena en vez de reventar, con este orden de preferencia y por este motivo:

1. ``updated_at`` — la mejor aproximación disponible a cuándo ocurrió el turno;
2. ``now()`` — si tampoco lo hay, porque una fila sin ninguna marca de tiempo ya
   estaba rota y perderla sería peor que fecharla mal.

La alternativa (abortar la migración) se descarta a conciencia: el fallo llegaría
a mitad del despliegue, en producción, por una fila de basura que nadie sabe de
dónde salió. Queda dicho aquí y probado en
``test_partition_llm_usage_events.py::test_a_legacy_row_without_created_at_survives_the_conversion``.

La pregunta que el plan dejó abierta: ¿las consultas de facturación mejoran?
-----------------------------------------------------------------------------
`part-01` pedía comprobarlo antes de convertir —*«si alguna agrega sin filtrar
por `created_at`, el particionado no la mejora y hay que decir por qué se
acepta»*—. Las dos agregaciones que leen esta tabla viven en
``workers/maintenance/queue_sampler.py`` y las dos llevan
``WHERE created_at > now() - interval '24 hours'``.

La respuesta, medida con ``EXPLAIN`` y no supuesta, es **sí pero con matiz, y el
matiz importa**: el filtro descarta las particiones del PASADO —que es donde
crece el historial— y **no** las tres del colchón futuro, porque una fila con
fecha futura satisfaría la condición y PostgreSQL no puede saber que no la hay.
O sea: el plan toca como mucho el mes en curso más el colchón (4 relaciones,
constante) mientras el pasado crece sin límite. Ésa es la ganancia, y decirla así
evita la versión bonita y falsa («escanea una sola partición»), que es como
estaba escrito este párrafo hasta que el test la desmintió.

Revision ID: 0135_partition_llm_usage_events
Revises: 0134_partition_notification_logs
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0135_partition_llm_usage_events"
down_revision: str | Sequence[str] | None = "0134_partition_notification_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "llm_usage_events"
LEGACY = "llm_usage_events_legacy"
PK = "llm_usage_events_pkey"

#: Ver el comentario homónimo de la 0134: duplicado a propósito, vigilado por test.
HEADROOM_MONTHS = 3

COLUMNS = (
    "id",
    "tenant_id",
    "user_id",
    "source",
    "provider_kind",
    "model",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "calls",
    "created_at",
    "updated_at",
)

INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_llm_usage_events_tenant_created", "tenant_id, created_at"),
)

_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

#: El cuerpo, salvo la PK. ``created_at`` sale de aquí **NOT NULL** (entra en la
#: clave primaria); ``updated_at`` se queda tal como estaba, nullable, porque
#: nada obliga a lo contrario y endurecerla sería un cambio de esquema colado.
_BODY = """
    id uuid NOT NULL,
    tenant_id uuid,
    user_id uuid,
    source varchar(16) NOT NULL,
    provider_kind varchar(32),
    model varchar(128),
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_usd numeric(12,6),
    calls integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT ck_llm_usage_events_source
        CHECK (source IN ('assistant', 'cortex', 'planning'))
"""

#: El cuerpo de VUELTA: idéntico salvo que ``created_at`` recupera su
#: nullabilidad original. Un downgrade que dejara la columna endurecida no
#: devolvería el esquema de la 0109.
_BODY_FLAT = _BODY.replace(
    "created_at timestamptz NOT NULL DEFAULT now()", "created_at timestamptz DEFAULT now()"
)


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
    oldest, newest, present = bind.execute(
        sa.text(f"SELECT min(created_at), max(created_at), now() FROM {source}")
    ).one()
    now_month = _month_start(present)
    first = _month_start(oldest) if oldest is not None else now_month
    last_data = _month_start(newest) if newest is not None else now_month
    last = max(last_data, _add_months(now_month, HEADROOM_MONTHS))
    return _months_between(min(first, now_month), last)


def _assert_same_columns(bind: sa.engine.Connection, *, source: str, target: str) -> None:
    """Origen y destino tienen EXACTAMENTE las mismas columnas (ver 0134)."""
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
    _assert_same_columns(bind, source=source, target=target)
    columns = ", ".join(COLUMNS)
    expected = bind.execute(sa.text(f"SELECT count(*) FROM {source}")).scalar_one()
    bind.execute(sa.text(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {source}"))
    copied = bind.execute(sa.text(f"SELECT count(*) FROM {target}")).scalar_one()
    if copied != expected:
        raise RuntimeError(
            f"la copia {source} → {target} se dejó filas por el camino: {expected} en"
            f" origen, {copied} en destino. NO se continúa: terminar aquí deja la"
            " tabla nueva incompleta."
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


def _rls_statements(relation: str) -> tuple[str, ...]:
    policy = f"{relation}_tenant_isolation"
    return (
        f"ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {relation} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy} ON {relation} FOR ALL"
        f" USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})",
    )


# ---------------------------------------------------------------------------
# Ida
# ---------------------------------------------------------------------------
def upgrade() -> None:
    bind = op.get_bind()

    # 1. El FORCE se aplica al DUEÑO: sin quitarlo, la copia leería cero filas.
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")

    # 2. Las filas sin fecha, antes de calcular los meses: si quedara una NULL,
    #    `_planned_months` la ignoraría (min/max saltan los NULL) y la copia
    #    reventaría con «no partition of relation found for row».
    op.execute(
        f"UPDATE {TABLE} SET created_at = COALESCE(updated_at, now()) WHERE created_at IS NULL"
    )
    months = _planned_months(bind, source=TABLE)

    # 3. Apartar la vieja y liberar los nombres.
    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {PK}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT ck_llm_usage_events_source")

    # 4. La nueva, particionada, con la PK compuesta que PostgreSQL exige.
    op.execute(
        f"CREATE TABLE {TABLE} ({_BODY}, CONSTRAINT {PK} PRIMARY KEY (id, created_at))"
        " PARTITION BY RANGE (created_at)"
    )

    # 5. Una partición por mes.
    for first_of_month in months:
        op.execute(
            f"CREATE TABLE {_partition_name(first_of_month)} PARTITION OF {TABLE}"
            f" FOR VALUES FROM ('{first_of_month.isoformat()}')"
            f" TO ('{_add_months(first_of_month, 1).isoformat()}')"
        )

    # 6. Copiar y VERIFICAR.
    _copy_verified(bind, source=LEGACY, target=TABLE)

    # 7. Fuera la vieja, índices sobre el padre y RLS en el padre y en cada
    #    partición.
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

    for relation in [TABLE, *partitions]:
        op.execute(f"ALTER TABLE {relation} NO FORCE ROW LEVEL SECURITY")

    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {PK}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT ck_llm_usage_events_source")

    op.execute(f"CREATE TABLE {TABLE} ({_BODY_FLAT}, CONSTRAINT {PK} PRIMARY KEY (id))")
    _copy_verified(bind, source=LEGACY, target=TABLE)

    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns in INDEXES:
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns})")
    for statement in _rls_statements(TABLE):
        op.execute(statement)
