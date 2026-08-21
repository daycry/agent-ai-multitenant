"""Agregado de aprobacion humana: el catalogo de politicas y las peticiones vivas.

`ApprovalPolicyTemplate` es el catalogo --las 4 plantillas del principio n.11 de
CLAUDE.md sobre las 13 categorias de accion sensible-- y `ApprovalRequest` es una
accion concreta que un agente paro a esperar. Van juntas porque son las dos caras
de la misma decision, aunque una sea catalogo y la otra trafico.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    Index,
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
# ApprovalPolicyTemplate (catalog of named human-approval policies)
# =============================================================================
class ApprovalPolicyTemplate(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """Named bundle of per-category approval decisions (spec §7.7-7.8).

    `categories` shape::

        {"categories": {"code_changes": "auto", "git_push": "human_required", ...}}

    Stored as JSONB so adding a new category later doesn't require a
    migration. The policy *referenced* by a project lives copy-pasted
    in `projects.human_approval_policy` -- this table is just the
    catalog of pickable presets.
    """

    __tablename__ = "approval_policy_templates"
    __table_args__ = (
        Index(
            "ix_approval_policy_templates_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# ApprovalRequest (a human_approval_policy decision an agent is waiting on)
# =============================================================================
class ApprovalRequest(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One sensitive action an agent paused on, awaiting a human (spec §7.7).

    The approval engine (Plan 02 Fase F) intercepts an action whose
    category the project's `human_approval_policy` marks `human_required`,
    persists it here as `pending`, and parks the execution in
    `awaiting_human_approval`. A reviewer approves or rejects it; an
    unanswered request times out (task_02_27).
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_tenant_status", "tenant_id", "status"),
        Index("ix_approval_requests_execution_id", "execution_id"),
    )

    # NO foreign key since part-01 / ADR 0154 (migration 0137): ``executions`` is
    # partitioned by month, so its primary key is ``(id, created_at)`` and a FK
    # cannot reference it without carrying both columns. The ``ON DELETE CASCADE``
    # it used to have was redundant — the only event that deletes an execution is
    # deleting its task, and ``task_id`` below cascades on that same event. That
    # is the condition this decision rests on, and
    # ``test_partition_executions.py::test_deleting_a_task_still_removes_its_approval_requests``
    # is what will go red the day it stops holding.
    execution_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # One of the 13 sensitive-action categories (spec §7.7).
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    # The proposed action the agent paused on (tool + args, …).
    action: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    # The reviewer's optional note; on a timeout, why it expired.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
