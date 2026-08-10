"""executions → tabla PARTICIONADA por rango mensual (part-01, ADR 0151).

Quinta y última conversión, y la única cuya PK compuesta se nota **fuera** de su
propia tabla. Va la última por eso: `executions.id` era el destino de cuatro
claves foráneas, y una FK no puede referenciar una PK compuesta sin llevar las
dos columnas.

Las cuatro FK entrantes, y qué se hizo con ellas
------------------------------------------------
Decidido en el
[ADR 0154](../../../../docs/05-architecture-decisions/0154-fk-hacia-tablas-particionadas.md):
**se retiran las cuatro** y la columna se queda como referencia suelta —
exactamente lo que ya hacía a propósito ``guardrail_events.execution_id``.

* ``approval_requests.execution_id`` (CASCADE, NOT NULL) es la que costaba. Se
  retira porque la cascada era **redundante**: el único evento que borra una
  ``execution`` es el borrado de su ``task`` (``executions.task_id`` es CASCADE), y
  ese mismo evento ya se lleva la ``approval_request`` por su propio ``task_id``,
  también CASCADE. Nada borra ``executions`` directamente. Esa condición no se
  deja escrita y ya: la ejecuta
  ``test_partition_executions.py::test_deleting_a_task_still_removes_its_approval_requests``,
  que se pondrá rojo el día que deje de ser cierta.
* Las tres ``source_execution_id`` (``memory_entries``, ``eval_dataset_items``,
  ``eval_shadow_records``) eran ``SET NULL`` sobre columnas nullable: nadie asume
  que el run exista.

El ``downgrade`` **restaura las cuatro**. Antes de recrearlas limpia lo que
hubiera quedado colgando —borra las ``approval_requests`` sin run (su FK es NOT
NULL, no admite otra cosa) y pone a NULL las tres referencias sueltas—, porque una
fila huérfana impediría crear la constraint y dejaría la vuelta atrás a medias.

La tabla grande: qué se paga al ejecutar esto
---------------------------------------------
Es la tabla pesada del sistema —el 76 % de su tamaño es ``steps_log`` según la
medida del ADR 0151— y la migración la **copia entera** dentro de una sola
transacción. No se trocea la copia por lotes a propósito, y conviene decir por
qué en vez de dejarlo a la imaginación: el ``ALTER TABLE … RENAME`` del paso 3 ya
toma un ``ACCESS EXCLUSIVE`` sobre la tabla durante toda la migración, así que
trocear el ``INSERT … SELECT`` no acortaría ni un segundo la ventana de bloqueo —
solo repartiría el mismo trabajo en más viajes. **Lo que sí hay que hacer es
medir antes y parar el stack**; está en el runbook
(`docs/06-runbooks/particiones-append-only.md`, § «Convertir la tabla grande»)
con el `SELECT pg_size_pretty(pg_total_relation_size('executions'))` que da la
cifra.

Revision ID: 0137_partition_executions
Revises: 0136_partition_audit_log
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0137_partition_executions"
down_revision: str | Sequence[str] | None = "0136_partition_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "executions"
LEGACY = "executions_legacy"
PK = "executions_pkey"
TASK_FK = "executions_task_id_fkey"
AGENT_FK = "executions_agent_id_fkey"

#: Ver el comentario homónimo de la 0134: duplicado a propósito, vigilado por test.
HEADROOM_MONTHS = 3

#: Las cuatro FK entrantes retiradas por el ADR 0154:
#: `(tabla hija, constraint, columna, regla original)`. El `downgrade` las
#: recrea desde aquí, así que ida y vuelta no pueden desincronizarse.
INCOMING_FKS: tuple[tuple[str, str, str, str], ...] = (
    (
        "approval_requests",
        "approval_requests_execution_id_fkey",
        "execution_id",
        "ON DELETE CASCADE",
    ),
    (
        "memory_entries",
        "memory_entries_source_execution_id_fkey",
        "source_execution_id",
        "ON DELETE SET NULL",
    ),
    (
        "eval_dataset_items",
        "fk_eval_dataset_items_source_execution",
        "source_execution_id",
        "ON DELETE SET NULL",
    ),
    (
        "eval_shadow_records",
        "fk_eval_shadow_records_source_execution",
        "source_execution_id",
        "ON DELETE SET NULL",
    ),
)

COLUMNS = (
    "id",
    "tenant_id",
    "task_id",
    "agent_id",
    "status",
    "abort_code",
    "output",
    "steps_log",
    "iterations",
    "total_tokens",
    "total_cost_usd",
    "tool_call_count",
    "model_call_count",
    "started_at",
    "completed_at",
    "created_at",
    "updated_at",
    "price_snapshot_at",
    "price_snapshot_currency",
    "price_input_usd",
    "price_output_usd",
    "price_cached_input_usd",
    "price_snapshot_cost_usd",
    "memorize_skip_reason",
    "cancel_requested_at",
    "celery_task_id",
    "finish_status",
    "container_launched_at",
    "prompt_version",
    "runtime_image_digest",
    "pending_guidance",
)

INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_executions_tenant_id", "tenant_id"),
    ("ix_executions_task_id", "task_id"),
    ("ix_executions_tenant_status", "tenant_id, status"),
    ("ix_executions_prompt_version", "tenant_id, prompt_version"),
    ("ix_executions_tenant_created_at", "tenant_id, created_at"),
)

_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

#: Treinta y una columnas escritas a mano, y por eso :func:`_assert_same_columns`
#: corre antes de cada copia: es justo el tamaño en el que una se cae de la lista
#: sin que nadie lo note. El recuento de filas no lo detectaría.
_BODY = f"""
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    task_id uuid NOT NULL,
    agent_id uuid,
    status varchar(32) NOT NULL DEFAULT 'running',
    abort_code varchar(64),
    output text,
    steps_log jsonb NOT NULL DEFAULT '[]'::jsonb,
    iterations integer NOT NULL DEFAULT 0,
    total_tokens integer NOT NULL DEFAULT 0,
    total_cost_usd numeric(14,6) NOT NULL DEFAULT 0,
    tool_call_count integer NOT NULL DEFAULT 0,
    model_call_count integer NOT NULL DEFAULT 0,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    price_snapshot_at timestamptz,
    price_snapshot_currency varchar(3),
    price_input_usd numeric(18,10),
    price_output_usd numeric(18,10),
    price_cached_input_usd numeric(18,10),
    price_snapshot_cost_usd numeric(14,6),
    memorize_skip_reason varchar(32),
    cancel_requested_at timestamptz,
    celery_task_id varchar(155),
    finish_status varchar(16),
    container_launched_at timestamptz,
    prompt_version varchar(64),
    runtime_image_digest varchar(80),
    pending_guidance text,
    CONSTRAINT ck_executions_iterations_non_negative CHECK (iterations >= 0),
    CONSTRAINT ck_executions_total_tokens_non_negative CHECK (total_tokens >= 0),
    CONSTRAINT ck_executions_total_cost_non_negative CHECK (total_cost_usd >= 0),
    CONSTRAINT {TASK_FK} FOREIGN KEY (task_id)
        REFERENCES tasks (id) ON DELETE CASCADE,
    CONSTRAINT {AGENT_FK} FOREIGN KEY (agent_id)
        REFERENCES agents (id) ON DELETE SET NULL
"""

_CHECKS = (
    "ck_executions_iterations_non_negative",
    "ck_executions_total_tokens_non_negative",
    "ck_executions_total_cost_non_negative",
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
            f" {sorted(destination - origin)}. Con 31 columnas escritas a mano esto"
            " es el fallo probable, y el recuento de filas NO lo ve."
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


def _convert(bind: sa.engine.Connection, *, partitioned: bool) -> None:
    """El intercambio, idéntico en los dos sentidos salvo la PK y el PARTITION BY.

    Ida y vuelta comparten cuerpo a propósito: dos copias del mismo baile es como
    un `downgrade` se queda atrás sin que nadie lo note hasta el día que se
    necesita.
    """
    months = _planned_months(bind, source=TABLE) if partitioned else []

    op.execute(f"ALTER TABLE {TABLE} RENAME TO {LEGACY}")
    for index_name, _columns in INDEXES:
        op.execute(f"DROP INDEX {index_name}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {PK}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {TASK_FK}")
    op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {AGENT_FK}")
    for check in _CHECKS:
        op.execute(f"ALTER TABLE {LEGACY} DROP CONSTRAINT {check}")

    if partitioned:
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
    else:
        op.execute(f"CREATE TABLE {TABLE} ({_BODY}, CONSTRAINT {PK} PRIMARY KEY (id))")

    _copy_verified(bind, source=LEGACY, target=TABLE)

    op.execute(f"DROP TABLE {LEGACY}")
    for index_name, columns in INDEXES:
        op.execute(f"CREATE INDEX {index_name} ON {TABLE} ({columns})")
    for relation in [TABLE, *[_partition_name(m) for m in months]]:
        for statement in _rls_statements(relation):
            op.execute(statement)


# ---------------------------------------------------------------------------
# Ida
# ---------------------------------------------------------------------------
def upgrade() -> None:
    bind = op.get_bind()

    # 1. Las cuatro FK entrantes, ANTES que nada: sin retirarlas no se puede
    #    tirar la PK simple a la que apuntan (ADR 0154).
    for child, constraint, _column, _rule in INCOMING_FKS:
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {constraint}")

    # 2. El FORCE se aplica al DUEÑO: sin quitarlo, la copia leería cero filas.
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")

    _convert(bind, partitioned=True)


# ---------------------------------------------------------------------------
# Vuelta
# ---------------------------------------------------------------------------
def downgrade() -> None:
    bind = op.get_bind()

    for relation in [TABLE, *_partitions_of(bind, TABLE)]:
        op.execute(f"ALTER TABLE {relation} NO FORCE ROW LEVEL SECURITY")

    _convert(bind, partitioned=False)

    # Y las cuatro FK vuelven. Antes hay que dejar el terreno limpio: una fila
    # que apunte a un run inexistente impediría crear la constraint y dejaría la
    # vuelta atrás a medias, que es peor que no haberla intentado.
    for child, constraint, column, rule in INCOMING_FKS:
        if rule == "ON DELETE CASCADE":
            # NOT NULL: no hay «poner a NULL». La cascada habría borrado estas
            # filas en su día; hacerlo ahora es reproducir ese efecto.
            op.execute(
                f"DELETE FROM {child} c WHERE NOT EXISTS"
                f" (SELECT 1 FROM {TABLE} e WHERE e.id = c.{column})"
            )
        else:
            op.execute(
                f"UPDATE {child} c SET {column} = NULL WHERE c.{column} IS NOT NULL"
                f" AND NOT EXISTS (SELECT 1 FROM {TABLE} e WHERE e.id = c.{column})"
            )
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {constraint}"
            f" FOREIGN KEY ({column}) REFERENCES {TABLE} (id) {rule}"
        )
