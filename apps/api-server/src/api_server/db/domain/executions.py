"""Agregado de ejecucion: una vuelta del bucle del agente sobre una tarea.

`executions` es la tabla pesada del sistema --el 76 % de su tamano es `steps_log`,
medido en el ADR 0151-- y desde la migracion 0137 esta particionada, lo que le
costo cuatro FK entrantes (ADR 0154).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from api_server.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# Execution (one run of the agent loop against a task)
# =============================================================================
class Execution(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One run of the agent loop against a task (spec §13).

    The `steps_log` JSONB column is the heart of the table: an
    append-only array of step records — one per graph node, model call,
    tool call and memory read — produced by `agent_runtime` (Plan 02
    Fase C). It drives the execution Timeline UI. The `total_*` and
    `*_count` columns are denormalised roll-ups of the loop's usage so a
    dashboard need not scan `steps_log`.

    Executions are NOT soft-deleted — they are an immutable audit record
    of what an agent did. A task can have several (retries).
    """

    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_task_id", "task_id"),
        Index("ix_executions_tenant_status", "tenant_id", "status"),
        # Ventanas de gasto por tenant (`budgets/consumption.py`), listados de
        # runs y el sweep de presupuestos: igualdad por tenant + rango por
        # fecha. Migración 0126. El orden NO es intercambiable.
        Index("ix_executions_tenant_created_at", "tenant_id", "created_at"),
        # El dashboard de calidad agrupa el histórico de un tenant por etiqueta
        # de prompts (migración 0119, recreado por la 0137 al particionar).
        # `executions` es la tabla que más crece del sistema: sin este índice el
        # filtro es un scan completo.
        Index("ix_executions_prompt_version", "tenant_id", "prompt_version"),
        CheckConstraint("iterations >= 0", name="ck_executions_iterations_non_negative"),
        CheckConstraint("total_tokens >= 0", name="ck_executions_total_tokens_non_negative"),
        CheckConstraint("total_cost_usd >= 0", name="ck_executions_total_cost_non_negative"),
        # part-01 / ADR 0151: monthly RANGE partitioning on ``created_at``
        # (migration 0137, the last of the five). Declared on the model too
        # because the guard in ``tests/unit/test_partition_planner.py`` discovers
        # the partitioned tables from here and demands the maintenance job knows
        # about them — a table converted in a migration but missing from
        # ``PARTITIONED_TABLES`` would silently have no partition next month.
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 32 chars — wide enough for 'awaiting_human_approval' (Plan 02 Fase F).
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'running'")
    )
    # Set when status='aborted' — a SafeguardCode (max_iterations_exceeded,
    # repetitive_loop_detected, …). NULL on a clean run. ADR 0087 also uses it as
    # the escalation reason (review_inconclusive / max_review_retries_exhausted).
    abort_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The agent's self-reported finish status (ADR 0087): 'success'|'failed'|
    # 'partial' when it finished via the `submit_result` tool, else NULL (prose
    # finish / claude_sdk). A HINT shown in the UI + given to the reviewer —
    # distinct from `status` (the execution lifecycle outcome).
    finish_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # `task_wf_52`: etiqueta del conjunto de PROMPTS del runtime que produjo el
    # run. `EvalRun.subject_prompt_version` existía desde el Plan 14 y nadie lo
    # poblaba, así que el dashboard de calidad agrupaba todo bajo «(sin
    # versión)»: se medía la calidad sin poder atribuirla a un cambio. NULL en
    # los runs anteriores al versionado y en los que no lo reportan.
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # `task_wf_62`: digest de la IMAGEN del runtime que corrió. La etiqueta
    # (`agent-runtime-php-phpunit:v1`) es flotante: reconstruirla cambia en
    # silencio lo que ejecuta toda tarea PHP, sin forma de saber qué build
    # produjo un resultado ni de volver atrás. NULL en los runs anteriores y
    # cuando el daemon no lo reporta — es trazabilidad, nunca bloquea un run.
    runtime_image_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # `task_wf_71`: guía que un humano escribe sobre un run EN MARCHA. Hasta
    # ahora la única intervención posible era matarlo: si el agente iba por mal
    # camino se tiraba todo el trabajo y se relanzaba a ciegas. El bucle la
    # consulta una vez por iteración y la inyecta como sticky del turno
    # siguiente. Se BORRA al entregarla — es una intervención puntual, no una
    # instrucción permanente que se repita cada turno.
    pending_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Por qué el Memorizer NO produjo memoria a partir de este run, como código
    # canónico (:class:`~api_server.memorizer.policy.MemorizeSkipReason`):
    # ``not_done`` / ``skip_private`` / ``no_team`` / ``no_scope`` / ``llm_empty``
    # (Plan 06.17 task_06_17_04). NULL cuando se memorizó OK o el Memorizer aún no
    # ha corrido. Lo escribe el worker dedicado (``workers.memorize_execution``);
    # un endpoint lo expone para que la UI explique el "por qué no hay memoria".
    memorize_skip_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # The steps_log: one dict per step (node / model_call / tool_call /
    # memory_read). Stored as JSONB so the shape can evolve migration-free.
    steps_log: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # --- proyección de `steps_log` (prod-13 task_prod13_18, migración 0139) ---
    # Las tres son una PROYECCIÓN del steps_log, no una fuente nueva: el mismo
    # patrón que `total_tokens` / `total_cost_usd` de aquí abajo. Existen para
    # que el explorador de runs y el panel de estadísticas dejen de resolver
    # «¿con qué modelo terminó?» y «¿cuántos tokens de entrada/salida?» con un
    # `jsonb_array_elements(steps_log)` por fila. Lo que impide que se
    # desincronicen es que TODO el que asigna `steps_log` llama acto seguido a
    # `db/execution_repo.py::apply_steps_rollup`. Escritores hoy: ese mismo
    # repositorio (`record_execution` / `finalize_execution` /
    # `create_running_execution`) y `workers.execution._mark_commit_failed`, que
    # anexa el paso del conflicto de rebase en su propia sesión BYPASSRLS. No hay
    # trigger ni columna generada que lo haga por su cuenta: si un escritor nuevo
    # se salta el helper, estas columnas mienten sin que nada falle. Lo fija
    # `tests/unit/test_execution_steps_rollup.py`.
    #
    # `last_model` es NULL cuando el run no llamó a ningún modelo (un run
    # abortado antes del primer turno) — no es «desconocido», es «ninguno».
    last_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))

    iterations: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=6), nullable=False, server_default=text("0")
    )
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # When the agent-runtime CONTAINER was created (M1). The row is `running` from
    # the moment it is inserted — before model resolution (Vault), worktree
    # provisioning (git) and `docker create`. The orphan sweeper must not reap a run
    # still provisioning (no container to leak yet): it only treats a row as orphaned
    # when this is set (a container did exist) and the daemon no longer lists it. A
    # row still provisioning (NULL) is protected from the early reap and only falls to
    # the conservative 7 h age backstop. NULL for a run that never launched / predates.
    container_launched_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Cooperative cancellation (auditoría / task_prod06_cancel_01). The operator's
    # POST /executions/{id}/cancel stamps `cancel_requested_at`; the worker polls it
    # to kill the container and finalises the row as `cancelled`. `celery_task_id` is
    # stamped by the worker when it picks the job up, so the cancel endpoint can
    # `revoke(terminate=True)` the still-queued/running task. Both nullable: a run
    # that was never cancelled (or predates this column) simply leaves them NULL.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(155), nullable=True)

    # --- per-call price snapshot (Plan 11 Fase C, task_11_13) --------------
    # The catalog price that was IN EFFECT when this run's model calls were
    # recorded, frozen here (and per-call in steps_log[*].price_snapshot) so
    # historical billing stays correct even after the model_prices catalog
    # changes. These columns mirror the LAST priced model call of the run
    # (the snapshot the dashboards/billing read without scanning JSONB);
    # the authoritative per-call snapshots live in steps_log. All nullable
    # / backfill-safe: pre-task runs and runs with no priced model call
    # leave them NULL (an UNKNOWN price is recorded as NULL cost, never a
    # fake 0). Canonical USD. The columns inherit executions' tenant RLS.
    price_snapshot_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    price_snapshot_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_input_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=10), nullable=True
    )
    price_output_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=10), nullable=True
    )
    price_cached_input_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=10), nullable=True
    )
    price_snapshot_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=6), nullable=True
    )

    # PART OF THE PRIMARY KEY since part-01 (ADR 0151, migration 0137), which
    # forces the redeclaration here over ``TimestampMixin``'s: PostgreSQL requires
    # the primary key of a partitioned table to include the partition key, so the
    # PK is ``(id, created_at)``. This is the change that dragged four foreign
    # keys — a FK cannot reference a composite PK without carrying both columns —
    # and ADR 0154 retired all four rather than widening the children.
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        primary_key=True,
        nullable=False,
        server_default=text("now()"),
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805
        """The MAPPER keeps ``id`` alone as the identity key. The TABLE does not.

        Two different notions of "primary key" that part-01 pulled apart, and the
        distinction is the whole point:

        * The **table** has ``PRIMARY KEY (id, created_at)`` because PostgreSQL
          requires a partitioned table's PK to include the partition key. That is
          what the DDL and the migration emit, and what
          ``test_partition_executions.py`` asserts against the catalogue.
        * The **mapper** is told to keep using ``id`` as the ORM identity, because
          ``id`` is still unique in fact: it is an application-generated UUIDv7.
          Without this, every ``session.get(Execution, some_uuid)`` in the codebase
          — two in ``approval_repo`` and ~35 across the integration suite — would
          raise ``InvalidRequestError: Incorrect number of values in identifier``.

        What this buys and what it does not: it makes the conversion a non-event
        for every caller that looks a run up by id, at **no** extra query cost (a
        lookup by id with no time filter has to consult every partition either
        way — with or without this override). What it does NOT buy is a database
        guarantee that ``id`` is unique: the only unique index is now the composite
        one. The guarantee is UUIDv7, the same one every ``session.get`` already
        relied on before this migration.
        """
        return {"primary_key": [cls.__table__.c.id]}
