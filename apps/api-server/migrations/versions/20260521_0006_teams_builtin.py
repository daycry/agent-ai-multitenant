"""Catalog visibility for teams (task_01_12 substrate).

Mirrors migration 0005 (skills + tools) but for teams: adds
`is_builtin BOOLEAN NOT NULL DEFAULT false` and a SELECT-only policy
so tenant sessions can read platform-owned team templates without
BYPASSRLS.

Full linked-vs-forked machinery for teams (forked_from_team_id etc.)
arrives in task_01_15+. For task_01_12 we only need the visibility
substrate.

Revision ID: 0006_teams_builtin
Revises: 0005_skills_tools_builtin
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_teams_builtin"
down_revision: str | Sequence[str] | None = "0005_skills_tools_builtin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_teams_is_builtin",
        "teams",
        ["is_builtin"],
        postgresql_where=sa.text("is_builtin = true"),
    )
    op.execute("CREATE POLICY teams_builtin_read ON teams FOR SELECT" " USING (is_builtin = true)")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS teams_builtin_read ON teams")
    op.drop_index("ix_teams_is_builtin", table_name="teams")
    op.drop_column("teams", "is_builtin")
