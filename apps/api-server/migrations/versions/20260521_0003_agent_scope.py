"""Add scope + linked-vs-forked columns to agents (spec §5.7.5).

Five new columns:

  scope                  enum-like text (global_builtin / global_tenant_template
                         / project_local). NOT NULL, default 'project_local'.
  project_id             nullable FK -> projects.id ON DELETE CASCADE.
  forked_from_agent_id   self-FK -> agents.id ON DELETE SET NULL.
  forked_from_version    text, nullable. Semver of the source at fork time.
  anchored_version       text, nullable. Semver the linked agent is pinned to.

Plus a CHECK constraint enforcing the spec invariant:

  scope='project_local'    => project_id IS NOT NULL
  scope LIKE 'global_%%'   => project_id IS NULL

Revision ID: 0003_agent_scope
Revises: 0002_domain_minimum
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_agent_scope"
down_revision: str | Sequence[str] | None = "0002_domain_minimum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "scope",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'project_local'"),
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "forked_from_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agents",
        sa.Column("forked_from_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("anchored_version", sa.String(length=32), nullable=True),
    )

    op.create_index("ix_agents_scope_project", "agents", ["scope", "project_id"])
    op.create_index("ix_agents_forked_from", "agents", ["forked_from_agent_id"])

    op.create_check_constraint(
        "ck_agents_scope_project_consistency",
        "agents",
        "(scope = 'project_local' AND project_id IS NOT NULL)"
        " OR (scope IN ('global_builtin', 'global_tenant_template')"
        "     AND project_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agents_scope_project_consistency", "agents", type_="check")
    op.drop_index("ix_agents_forked_from", table_name="agents")
    op.drop_index("ix_agents_scope_project", table_name="agents")
    op.drop_column("agents", "anchored_version")
    op.drop_column("agents", "forked_from_version")
    op.drop_column("agents", "forked_from_agent_id")
    op.drop_column("agents", "project_id")
    op.drop_column("agents", "scope")
