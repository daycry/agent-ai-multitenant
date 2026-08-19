"""Agregado de agente humano (Plan 16).

Un agente humano es un `Agent` con `agent_type` humano y una fila 1:1 de
`HumanAgentConfig`. `HumanWorkSession` es su equivalente de `Execution` --la pista
de auditoria, con horas imputadas en vez de tokens-- y `HumanTaskAssignment` lleva
el ciclo de aceptacion: con quien esta la tarea ahora mismo y desde cuando.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# HumanAgentConfig (the human-specific fields of an agent_type=human Agent)
# =============================================================================
class HumanAgentConfig(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Human-specific configuration of a human agent (Plan 16 task_16_02).

    Plan 16 Decisiones Clave: ``agent_type`` extends the EXISTING Agent
    entity rather than introducing a separate one. The columns that are
    meaningless for an AI agent (who the human is, their rate, how to reach
    them, the acceptance timeout / escalation target) live here, one row per
    ``agent_type='human'`` agent, instead of widening the ``agents`` table
    with a dozen always-NULL-for-AI columns. The ``agent_id`` FK is UNIQUE so
    the relationship is strictly 1:1.

    Tenant-owned: the row carries ``tenant_id`` (TenantScopedMixin) and the DB
    enforces isolation with the SAME RLS policy shape as ``agents``
    (``{table}_tenant_isolation`` FOR ALL, ``tenant_id = NULLIF(
    current_setting('app.tenant_id', true), '')::uuid``). The
    ``assigned_user_id`` is intrinsically a tenant concept — a global Human
    Agent template MUST be forked to the tenant before it can name a User
    (Plan 16 Decisiones Clave), which is why this table is never global.

    MVP constrains ``assignment_mode`` to ``specific_user`` at the DB level
    (``ck_human_agent_config_assignment_mode``); the :class:`AssignmentMode`
    enum models the future ``role_queue`` / ``team_pool`` modes but the CHECK
    rejects them this plan.
    """

    __tablename__ = "human_agent_config"
    __table_args__ = (
        # 1:1 with the human agent — at most one config row per agent.
        UniqueConstraint("agent_id", name="uq_human_agent_config_agent"),
        Index("ix_human_agent_config_tenant_id", "tenant_id"),
        Index("ix_human_agent_config_assigned_user", "assigned_user_id"),
        # MVP: only specific_user. Model the enum, constrain the column.
        CheckConstraint(
            "assignment_mode = 'specific_user'",
            name="ck_human_agent_config_assignment_mode",
        ),
        # A non-negative rate when present (NULL = no rate configured).
        CheckConstraint(
            "hourly_rate IS NULL OR hourly_rate >= 0",
            name="ck_human_agent_config_hourly_rate_non_negative",
        ),
        # Timeouts / expected times are positive when present.
        CheckConstraint(
            "acceptance_timeout_hours > 0",
            name="ck_human_agent_config_acceptance_timeout_positive",
        ),
    )

    # The human agent this config belongs to (agent_type='human'). UNIQUE via
    # __table_args__; CASCADE so deleting the agent removes its config.
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # MVP: specific_user. Stored as the :class:`AssignmentMode` value (TEXT) —
    # same string-backed-enum convention as agent_type/scope. DB-constrained
    # to 'specific_user' by ck_human_agent_config_assignment_mode.
    assignment_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'specific_user'")
    )

    # The concrete User this human agent resolves to (assignment_mode=
    # specific_user). SET NULL so the config survives a user deletion (the
    # orchestrator then surfaces an unassigned human agent). Nullable so a
    # config can be created before the User is picked.
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Cost inputs: rate + ISO-4217 currency (mirrors organizations.hourly_rate
    # / hourly_rate_currency, migration 0019). Coste humano = rate * horas
    # (imputed in Plan 16 Fase D). NULL = no rate configured yet.
    hourly_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    hourly_rate_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Preferred notification channels for the assigned user — a JSONB list of
    # channel identifiers (e.g. ["email", "in_app"]). JSONB so the shape can
    # evolve migration-free.
    notification_channels: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # How long the assigned user has to accept before escalation (Plan 16
    # Decisiones Clave: 24h default). Acceptance-timeout job lands in Fase B.
    acceptance_timeout_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("24")
    )

    # Who the task escalates to if the assigned user does not accept in time.
    # SET NULL so the config survives that user's deletion.
    escalation_target_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Planning estimates the PM agent uses (Plan 16 Fase E). NULL = unknown.
    expected_response_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_execution_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"HumanAgentConfig(id={self.id!r}, agent_id={self.agent_id!r},"
            f" assigned_user_id={self.assigned_user_id!r})"
        )


# =============================================================================
# HumanWorkSession (the Execution-equivalent audit trail for human tasks)
# =============================================================================
class HumanWorkSession(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One session of a human working on a task (Plan 16 task_16_03).

    Plan 16 Decisiones Clave: ``HumanWorkSession`` replaces ``Execution`` for
    ``agent_type='human'`` tasks. Where an AI task records a row in
    :class:`Execution` per run of the agent loop, a human task records a row
    here per work session — who did the work (``user_id``), when (``start_at``
    / ``end_at``), how many hours it took (``hours_logged``), free-form
    ``comments``, and the deliverables the human attached
    (``output_files_attached``). Like :class:`Execution`, sessions are NOT
    soft-deleted — they are an immutable audit record of what a human did; a
    task can have several.

    Tenant-owned: the row carries ``tenant_id`` (TenantScopedMixin) and the DB
    enforces isolation with the SAME RLS policy shape as ``executions``
    (``{table}_tenant_isolation`` FOR ALL, ``tenant_id = NULLIF(
    current_setting('app.tenant_id', true), '')::uuid``). A work session is
    intrinsically tenant-scoped (it references a ``users`` row, which is
    tenant-owned), so this table is never global.
    """

    __tablename__ = "human_work_sessions"
    __table_args__ = (
        Index("ix_human_work_sessions_tenant_id", "tenant_id"),
        # Audit-trail read path: "the sessions of this task" (mirrors
        # ix_executions_task_id, the Execution table this replaces).
        Index("ix_human_work_sessions_task_id", "task_id"),
        # Logged hours non-negative when present (NULL = not logged).
        CheckConstraint(
            "hours_logged IS NULL OR hours_logged >= 0",
            name="ck_human_work_sessions_hours_non_negative",
        ),
        # A finished session cannot end before it started.
        CheckConstraint(
            "end_at IS NULL OR end_at >= start_at",
            name="ck_human_work_sessions_end_after_start",
        ),
    )

    # The human task this session belongs to. CASCADE so deleting the task
    # removes its sessions (mirrors executions.task_id).
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The human who worked. SET NULL so the audit record survives a user
    # deletion (the session stays, the attribution is lost — same trade-off
    # executions.agent_id makes for a deleted agent). Nullable for that reason.
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # When the session began. Defaults to now() so a freshly-created session
    # is timestamped without the caller having to set it.
    start_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    # When the session finished. NULL while the human is still working.
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Optional logged hours; feeds coste humano = rate * hours (Plan 16 Fase
    # D). NULL = the human did not log hours for this session.
    hours_logged: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=8, scale=2), nullable=True
    )

    # The human's free-form notes / output text for this session.
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Deliverables the human attached — a JSONB list of attachment descriptors
    # (files, URLs, screenshots). JSONB so the shape can evolve migration-free.
    output_files_attached: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"HumanWorkSession(id={self.id!r}, task_id={self.task_id!r}, user_id={self.user_id!r})"
        )


# =============================================================================
# HumanTaskAssignment (who a human task is currently with + accept cycle)
# =============================================================================
class HumanTaskAssignment(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One assignment of a human task to a concrete User (Plan 16 task_16_05).

    When the orchestrator routes a ``ready`` task whose assignee Agent is
    ``agent_type='human'`` it does NOT request a runtime container from the
    pool (the AI path). Instead it creates one of these rows — recording the
    human Agent (``human_agent_id``) and the concrete User the work landed on
    (``assigned_to_user_id``, resolved from
    ``human_agent_config.assigned_user_id``) — and transitions the Task to
    ``assigned_to_human`` via the §7.2 state machine (task_16_04). The
    acceptance-timeout job (task_16_06) reads the ``pending_acceptance`` rows
    and, on expiry, creates a fresh assignment for the
    ``escalation_target_user_id`` (marking this one ``reassigned``).

    Tenant-owned: the row carries ``tenant_id`` (TenantScopedMixin) and the DB
    enforces isolation with the SAME RLS policy shape as ``human_work_sessions``
    / ``agents`` (``{table}_tenant_isolation`` FOR ALL, ``tenant_id = NULLIF(
    current_setting('app.tenant_id', true), '')::uuid``). An assignment is
    intrinsically tenant-scoped (it names a tenant ``users`` row and a tenant
    ``agents`` row), so this table is never global.

    Assignments are an append-only audit-style trail (no soft-delete): a task
    may accrue several rows over its life (initial assignment, escalation
    reassignment, …); ``status`` records each one's outcome.
    """

    __tablename__ = "human_task_assignments"
    __table_args__ = (
        Index("ix_human_task_assignments_tenant_id", "tenant_id"),
        # Read path: "the assignments of this task" (detail view / audit) and
        # "the live assignments of this user" (the personal inbox, Fase C).
        Index("ix_human_task_assignments_task_id", "task_id"),
        Index("ix_human_task_assignments_assigned_user", "assigned_to_user_id"),
        # The acceptance-timeout sweep (task_16_06) scans the open
        # pending_acceptance rows by age — a partial index keeps that scan
        # cheap as accepted/expired rows accumulate.
        Index(
            "ix_human_task_assignments_pending",
            "assigned_at",
            postgresql_where=text("status = 'pending_acceptance'"),
        ),
        # The DB enforces the HumanTaskAssignmentStatus value set (mirrors the
        # ck_agents_agent_type CHECK shape).
        CheckConstraint(
            "status IN ('pending_acceptance', 'accepted', 'reassigned', 'declined', 'expired')",
            name="ck_human_task_assignments_status",
        ),
    )

    # The human task this assignment is for. CASCADE so deleting the task
    # removes its assignments (mirrors human_work_sessions.task_id).
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The human Agent (agent_type='human') the task is assigned to. SET NULL so
    # the assignment record survives the agent's (soft) removal.
    human_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The concrete User the work landed on (resolved from
    # human_agent_config.assigned_user_id). SET NULL so the record survives a
    # user deletion (same trade-off human_work_sessions.user_id makes).
    assigned_to_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # When the assignment was created. Defaults to now() so a freshly-created
    # row is timestamped; the acceptance-timeout sweep ages off this column.
    assigned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    # Where this assignment is in the accept/work cycle. Stored as the
    # :class:`HumanTaskAssignmentStatus` value (TEXT) — same string-backed-enum
    # convention as agent_type/scope. DB-constrained by
    # ck_human_task_assignments_status.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending_acceptance'")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"HumanTaskAssignment(id={self.id!r}, task_id={self.task_id!r},"
            f" assigned_to_user_id={self.assigned_to_user_id!r}, status={self.status!r})"
        )
