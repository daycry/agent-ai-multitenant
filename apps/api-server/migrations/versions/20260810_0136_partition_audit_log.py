"""audit_log → tabla PARTICIONADA por rango mensual (part-01, ADR 0151).

Cuarta de las cinco conversiones. Mismo patrón que las tres anteriores; lo que
cambia es **cuánto pesa equivocarse**. El ADR 0151 describe esta tabla como la
que puede ser «la única prueba de quién aprobó un despliegue»: aquí una
conversión que no sepa volver atrás no es un problema de rendimiento, es un
problema de cumplimiento. De ahí que el round-trip de
``tests/integration/test_partition_audit_log.py`` compare las filas **campo a
campo** —incluido el JSONB de ``changes``— y no solo el recuento: conservar el
número de filas y perder el contenido del cambio no es conservar la prueba de
nada.

Dos cosas que se reproducen LITERALES, y por qué
------------------------------------------------
1. **La policy del padre no lleva ``WITH CHECK``.** La migración 0001 la creó
   solo con ``USING``; PostgreSQL usa entonces esa misma expresión para el
   ``WITH CHECK``, así que el comportamiento es idéntico. Se copia tal cual
   igualmente: escribirla «mejor» dejaría un esquema migrado distinto de un
   esquema nuevo, y esa diferencia la descubre alguien dentro de un año haciendo
   un diff, sin nadie que sepa explicarla. Las particiones sí llevan la forma
   canónica (``USING`` + ``WITH CHECK``), que es la que crea el job
   ``workers.ensure_partitions`` para las futuras: entre parecerse al padre y
   parecerse a sus hermanas, una partición debe parecerse a sus hermanas.

2. **La FK saliente ``user_id → users`` con ``ON DELETE SET NULL``.** Sobrevive a
   la conversión (una tabla particionada puede tener FK salientes desde
   PostgreSQL 12): al borrar un usuario, su rastro en la auditoría se queda con
   el autor anónimo en vez de desaparecer. Perderla por descuido convertiría el
   borrado de un usuario en un fallo de FK, o peor, la dejaría sin efecto.

``tenant_id`` sigue siendo NULLABLE: las acciones cross-tenant del System Admin se
registran sin tenant y solo las leen los roles BYPASSRLS. La conversión no lo
toca.

Revision ID: 0136_partition_audit_log
Revises: 0135_partition_llm_usage_events
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0136_partition_audit_log"
down_revision: str | Sequence[str] | None = "0135_partition_llm_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "audit_log"
LEGACY = "audit_log_legacy"
PK = "audit_log_pkey"
USER_FK = "audit_log_user_id_fkey"

#: Ver el comentario homónimo de la 0134: duplicado a propósito, vigilado por test.
HEADROOM_MONTHS = 3

COLUMNS = (
    "id",
    "tenant_id",
    "user_id",
    "action",
    "resource_type",
    "resource_id",
    "changes",
    "ip_address",
    "user_agent",
    "created_at",
)

INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_audit_log_tenant_id", "tenant_id"),
    ("ix_audit_log_user_id", "user_id"),
    ("ix_audit_log_tenant_created", "tenant_id, created_at"),
    ("ix_audit_log_action_created", "action, created_at"),
)

_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

_BODY = f"""
    id uuid NOT NULL,
    tenant_id uuid,
    user_id uuid,
    action varchar(64) NOT NULL,
    resource_type varchar(64),
    resource_id uuid,
    changes jsonb,
    ip_address inet,
    user_agent varchar(512),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT {USER_FK} FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE SET NULL
"""

#: La policy del PADRE, literal de la migración 0001 (sin `WITH CHECK`).
_PARENT_POLICY = f"CREATE POLICY {TABLE}_tenant_isolation ON {TABLE} FOR ALL USING ({_PREDICATE})"


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
            f" origen, {copied} en destino. En la tabla de AUDITORÍA una copia"
            " incompleta es una prueba perdida: NO se continúa."
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


def _partition_rls(relation: str) -> tuple[str, ...]:
    """La forma CANÓNICA (con `WITH CHECK`), la misma que crea el job."""
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

    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    months = _planned_months(bind, source=TABLE)

    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {PK}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {USER_FK}")

    op.execute(
        f"CREATE TABLE {TABLE} ({_BODY}, CONSTRAINT {PK} PRIMARY KEY (id, created_at))"
        " PARTITION BY RANGE (created_at)"
    )

    for first_of_month in months:
        op.execute(
            f"CREATE TABLE {_partition_name(first_of_month)} PARTITION OF {TABLE}"
            f" FOR VALUES FROM ('{first_of_month.isoformat()}')"
            f" TO ('{_add_months(first_of_month, 1).isoformat()}')"
        )

    _copy_verified(bind, source=LEGACY, target=TABLE)

    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns in INDEXES:
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns})")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(_PARENT_POLICY)
    for first_of_month in months:
        for statement in _partition_rls(_partition_name(first_of_month)):
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
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {USER_FK}")

    op.execute(f"CREATE TABLE {TABLE} ({_BODY}, CONSTRAINT {PK} PRIMARY KEY (id))")
    _copy_verified(bind, source=LEGACY, target=TABLE)

    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns in INDEXES:
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns})")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(_PARENT_POLICY)
