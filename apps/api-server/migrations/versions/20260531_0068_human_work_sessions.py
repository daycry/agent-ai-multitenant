"""human_work_sessions table + RLS (Plan 16 Fase A, task_16_03).

The Execution-equivalent audit trail for ``agent_type='human'`` tasks. Plan 16
Alcance / Decisiones Clave: ``HumanWorkSession`` replaces ``Execution`` for
human tasks. Where an AI task records one row in ``executions`` per run of the
agent loop, a human task records one row here per work session — who did the
work (``user_id``), when (``start_at`` / ``end_at``), how many hours it took
(``hours_logged``), free-form ``comments``, and the files/URLs the human
attached as deliverables (``output_files_attached``). The fields come verbatim
from the task block: ``task_id``, ``user_id``, ``start_at``, ``end_at``
(nullable until finished), ``hours_logged`` (Numeric, nullable),
``comments``, ``output_files_attached`` (JSONB), ``tenant_id``.

Columns (from the task block):

  - ``task_id``               FK tasks (CASCADE) — the human task worked on.
  - ``user_id``               FK users (SET NULL) — the human who worked.
  - ``start_at``              TIMESTAMPTZ NOT NULL DEFAULT now() — when work began.
  - ``end_at``                TIMESTAMPTZ nullable — when finished (NULL = in progress).
  - ``hours_logged``          Numeric(8,2) nullable — optional logged hours;
                              feeds coste humano = rate * hours (Plan 16 Fase D).
  - ``comments``              TEXT nullable — the human's notes / output text.
  - ``output_files_attached`` JSONB list, default '[]' — attachments
                              (files, URLs, screenshots).

Tenant-owned: ``tenant_id`` NOT NULL + RLS. The RLS DDL copies the EXACT shape
the ``agents`` / ``executions`` tables use: ``ENABLE`` + ``FORCE`` ROW LEVEL
SECURITY, then a ``{table}_tenant_isolation`` FOR ALL policy with
``tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`` — the
NULLIF guard turns the empty string an unset GUC returns into NULL before the
``::uuid`` cast, so an unset session deterministically matches zero tenant rows
(safe default). A work session is intrinsically tenant-scoped (it references a
``users`` row, which is tenant-owned), so this table is never global.

Single head before this migration is ``0067_human_agent_config``; this is
``0068_human_work_sessions``. Fully reversible: ``downgrade`` drops the policy
then the table, restoring 0067 exactly. The plan-wide reversibility proof
target is ``0040_sso_email_domains``.

Revision ID: 0068_human_work_sessions
Revises: 0067_human_agent_config
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068_human_work_sessions"
down_revision: str | Sequence[str] | None = "0067_human_agent_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# RLS DDL — sent one statement at a time (asyncpg refuses multi-statement
# strings). Mirrors the executions tenant-isolation shape verbatim (the
# Execution table this replaces for human tasks): ENABLE + FORCE + a FOR ALL
# USING policy with the NULLIF + ::uuid cast.
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE human_work_sessions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE human_work_sessions FORCE ROW LEVEL SECURITY",
    "CREATE POLICY human_work_sessions_tenant_isolation ON human_work_sessions FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS human_work_sessions_tenant_isolation ON human_work_sessions",
    "ALTER TABLE human_work_sessions DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "human_work_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "start_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # NULL until the session is finished.
        sa.Column("end_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        # Optional logged hours; feeds coste humano (Plan 16 Fase D).
        sa.Column("hours_logged", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column(
            "output_files_attached",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_human_work_sessions_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_human_work_sessions_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_human_work_sessions"),
        # Logged hours non-negative when present (NULL = not logged).
        sa.CheckConstraint(
            "hours_logged IS NULL OR hours_logged >= 0",
            name="ck_human_work_sessions_hours_non_negative",
        ),
        # A finished session cannot end before it started.
        sa.CheckConstraint(
            "end_at IS NULL OR end_at >= start_at",
            name="ck_human_work_sessions_end_after_start",
        ),
    )
    op.create_index(
        "ix_human_work_sessions_tenant_id",
        "human_work_sessions",
        ["tenant_id"],
    )
    # Audit-trail read path: "the sessions of this task" (mirrors
    # ix_executions_task_id, the Execution table this replaces).
    op.create_index(
        "ix_human_work_sessions_task_id",
        "human_work_sessions",
        ["task_id"],
    )

    # RLS — applied last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)

    op.drop_index("ix_human_work_sessions_task_id", table_name="human_work_sessions")
    op.drop_index("ix_human_work_sessions_tenant_id", table_name="human_work_sessions")
    op.drop_table("human_work_sessions")
