"""projects.human_task_review_mode (Plan 16 Fase D, task_16_11).

The human-task review flow needs a per-project knob that says what happens
when a Human Agent submits a deliverable (the task_16_09 path that creates a
``HumanWorkSession`` and moves the Task ``in_progress -> in_review``):

  * ``auto_approve`` (DEFAULT) — the task transitions straight to ``done``;
    no extra review step. This is the MVP default (Plan 16 Decisiones Clave),
    so every existing project gets it and the behaviour for current projects
    is unchanged from task_16_09 (submit -> in_review -> done).
  * ``peer_human_reviewer`` — the task stays ``in_review`` and a SECOND
    ``human_task_assignment`` is created for another Human Agent (the
    reviewer), who approves (-> ``done``) or rejects with feedback (-> back to
    ``backlog`` with ``retry_count`` bumped; after ``max_retries`` the §7.9
    retry/escalation infra parks it in ``awaiting_human_approval``).

The ``ai_reviewer`` mode is explicitly out of scope this plan (Plan 16 Alcance).

  - ``human_task_review_mode``  TEXT NOT NULL DEFAULT 'auto_approve' — mirrors
                                the model's :class:`HumanTaskReviewMode` StrEnum
                                column, the same string-backed-enum convention
                                as ``agents.agent_type`` / ``projects.status``.
  - ``ck_projects_human_task_review_mode``  CHECK enforcing the value set
                                (``auto_approve`` | ``peer_human_reviewer``) at
                                the DB level — same shape as the existing
                                ``ck_agents_agent_type`` CHECK (0066).

No backfill is needed: the NOT NULL DEFAULT 'auto_approve' applies to every
existing row, which already satisfies the constraint. The column inherits the
existing ``projects`` tenant RLS (no policy change). Additive +
backward-compatible.

Single head before this migration is ``0072_projects_command_config``; this is
``0073_human_task_review_mode`` (kept <= 32 chars to fit
``alembic_version.version_num``). Fully reversible: ``downgrade`` drops the
CHECK and the column, restoring 0072 exactly.

Revision ID: 0073_human_task_review_mode
Revises: 0072_projects_command_config
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_human_task_review_mode"
down_revision: str | Sequence[str] | None = "0072_projects_command_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL DEFAULT 'auto_approve' so existing projects keep the MVP
    # default behaviour (submit -> in_review -> done, no extra review step).
    op.add_column(
        "projects",
        sa.Column(
            "human_task_review_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'auto_approve'"),
        ),
    )
    # DB-enforced value set (mirrors HumanTaskReviewMode StrEnum).
    op.create_check_constraint(
        "ck_projects_human_task_review_mode",
        "projects",
        "human_task_review_mode IN ('auto_approve', 'peer_human_reviewer')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_human_task_review_mode", "projects", type_="check")
    op.drop_column("projects", "human_task_review_mode")
