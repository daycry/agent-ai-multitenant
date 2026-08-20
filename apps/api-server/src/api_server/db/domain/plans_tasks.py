"""Agregado de planificacion: `Plan`, `Task` y las aristas del DAG.

El Plan es la unidad de cambio del sistema (principio n.5 de CLAUDE.md): se
materializa como rama git y agrupa tareas cuyo orden lo fijan las aristas
`TaskDependency`, un self-M:N con `tenant_id` denormalizado.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# Plan
# =============================================================================
class Plan(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "plans"
    __table_args__ = (
        Index(
            "ix_plans_tenant_project_status",
            "tenant_id",
            "project_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # La FK que la migración 0014 promovió desde la soft-FK de la 0001, una
        # vez existían las dos tablas. Sin declararla aquí, un autogenerate
        # propone BORRARLA, y con ella el `SET NULL` que deja el plan huérfano en
        # vez de bloquear el borrado de la conversación.
        #
        # `use_alter=True` porque ésta y `conversations.related_plan_id` se
        # apuntan mutuamente: es el ciclo que la 0014 resolvió creando ambas con
        # `op.create_foreign_key` DESPUÉS de los dos `create_table`, y es lo que
        # aquí le dice a SQLAlchemy que puede romperlo al ordenar las tablas. Sin
        # él, `Base.metadata.sorted_tables` avisa de un ciclo irresoluble («this
        # warning may raise an error in a future release») y descarta las dos FK.
        ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_plans_conversation_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # prod-18 / ADR 0085: stable kebab slug for the plan's git branch
    # (`make_plan_branch_name` → plan/{id8}-{slug}). Generated once at creation, never
    # changes when `title` does. Nullable: backfilled by migration 0099.
    slug: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Auto-PR result (ADR 0072 fase 2 / cadena-pr-plan). Populated by the
    # `open_plan_pr` worker task at plan close so the URL/branch of the opened PR
    # are visible in the API/UI instead of living only in worker logs (audit
    # 2026-07-03, P6). `pr_error` records why a best-effort auto-PR failed.
    # Migration 0102.
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pr_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 32 chars so the wide ten-state machine (pending_approval,
    # pending_human_validation, ...) introduced in task_03_16 fits.
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'draft'"))

    # Was a soft-FK in the Plan 01 migration; promoted to a real FK in
    # migration 0014 once the conversations table existed. The constraint is
    # declared at table level in ``__table_args__`` above (it needs
    # ``use_alter`` for the cycle, which only ForeignKeyConstraint has).
    conversation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # The canonical-template specification (Plan 03 §8.5). JSONB so the
    # shape can evolve without migrations:
    #   {
    #     "summary":      {...},
    #     "phases":       [{name, description, tasks: [{...}]}],
    #     "tasks":        [task_spec],   # flat, dependencies by task_id
    #     "estimates":    {...},
    #     "tests_humans": [{...}],
    #     "metadata":     {...}          # template version, generator, etc.
    #   }
    # Empty `{}` for a freshly created draft until the team fills it in.
    specification: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # `ON DELETE SET NULL` + `nullable=True`: el tipo TIENE que admitir None
    # (db-9). Se declaraba `Mapped[UUID]` sobre una columna nullable, así que
    # mypy creía imposible el caso que la propia FK provoca al borrar al autor.
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # First-signature trail for the double-firma flow (task_03_25).
    # NULL on single-signature plans. The state machine asserts the
    # second signer is a different user than `first_approved_by`.
    first_approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    first_approved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


# =============================================================================
# Task
# =============================================================================
class Task(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Tasks are NOT soft-deleted -- they're terminal (done/cancelled) instead.
    Add SoftDeleteMixin in a later phase if the product requires it."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_tenant_status", "tenant_id", "status"),
        Index("ix_tasks_project_plan", "project_id", "plan_id"),
        CheckConstraint("retry_count >= 0", name="ck_tasks_retry_count_non_negative"),
        CheckConstraint("max_retries >= 0", name="ck_tasks_max_retries_non_negative"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 32 chars — wide enough for `awaiting_human_approval` (ADR 0020).
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'backlog'")
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'medium'")
    )

    assigned_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewer_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    acceptance_criteria: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    estimated_complexity: Mapped[str | None] = mapped_column(String(4), nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))

    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


# =============================================================================
# TaskDependency (self-M:N: task depends on tasks)
# =============================================================================
class TaskDependency(Base, TenantScopedMixin):
    """Aristas del DAG + `tenant_id` denormalizado (migración 0124).

    Su trigger exige además que AMBAS tareas sean del mismo tenant: una
    dependencia cross-tenant es un DAG imposible y ni un servicio BYPASSRLS
    debería poder crearla.
    """

    __tablename__ = "task_dependencies"
    __table_args__ = (
        PrimaryKeyConstraint("task_id", "depends_on_task_id", name="pk_task_dependencies"),
        # A task can't depend on itself. Cycles among different tasks are
        # caught at application level (DAG check before plan execution).
        CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_task_dependencies_no_self_loop",
        ),
        # AQUÍ NO VA un `UniqueConstraint("task_id", "depends_on_task_id")`.
        #
        # La unicidad del par ya la garantiza la PK compuesta de arriba, y el
        # UNIQUE extra que la migración 0002 declaraba (`uq_task_dependencies_pair`)
        # NUNCA existió en ninguna base de datos: PostgreSQL descarta en silencio,
        # dentro del mismo `CREATE TABLE`, una constraint UNIQUE cuyas columnas
        # son exactamente las de la PRIMARY KEY. Verificado en PostgreSQL 16.13 —
        # el DDL que Alembic emite la incluye y `pg_constraint` sólo devuelve
        # `pk_task_dependencies`.
        #
        # Declararla en el modelo, por tanto, no protegía nada: hacía que
        # `alembic check` propusiera CREARLA para siempre, y una migración que la
        # añadiera de verdad (con `CREATE UNIQUE INDEX`, el único camino que
        # PostgreSQL no deduplica) sería un segundo índice idéntico al de la PK,
        # todo coste de escritura y cero garantía nueva. El mapa de conflictos ya
        # nombra a la PK, no al UNIQUE: ver `pk_task_dependencies` en
        # `routers/_integrity.py` («Esa dependencia entre tareas ya existe»).
    )

    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
