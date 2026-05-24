"""Inline comments on a plan (Plan 03 task_03_21).

A reviewer can leave a comment scoped to the whole plan, a specific
phase (by index in `Plan.specification.phases`), or a specific task
(by `Plan.specification.tasks[*].id`). The team picks them up if the
plan is refined — they are reference-only, the plan body is the source
of truth.

The shape stays simple: one row per comment, soft-deletable so a
reviewer can retract it without losing the audit trail.
"""

from __future__ import annotations

import enum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class PlanCommentTarget(enum.StrEnum):
    PLAN = "plan"
    PHASE = "phase"
    TASK = "task"


class PlanComment(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """One comment attached to a plan.

    ``target_kind`` decides what ``target_ref`` means:
      - ``plan``   → ``target_ref`` is empty.
      - ``phase``  → ``target_ref`` is the phase index as a string
                     (e.g. "0", "1"), matching its position in
                     `Plan.specification.phases`.
      - ``task``   → ``target_ref`` is the task id from
                     `Plan.specification.tasks[*].id`.
    """

    __tablename__ = "plan_comments"
    __table_args__ = (
        Index(
            "ix_plan_comments_plan_id",
            "plan_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_plan_comments_tenant_id", "tenant_id"),
        CheckConstraint(
            "(target_kind = 'plan' AND (target_ref IS NULL OR target_ref = ''))"
            " OR (target_kind <> 'plan' AND target_ref IS NOT NULL AND length(target_ref) > 0)",
            name="ck_plan_comments_target_ref_consistency",
        ),
    )

    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)

    author_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = ["PlanComment", "PlanCommentTarget"]
