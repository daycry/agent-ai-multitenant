"""Catalog visibility for skills and tools.

Adds `is_builtin BOOLEAN NOT NULL DEFAULT false` to `skills` and
`tools`, and the matching SELECT-only RLS policy so any tenant session
can read built-in rows even though their `tenant_id` points to the
platform tenant. Writes stay scoped via the existing tenant-isolation
policy (which uses USING in 0002 -- writes still leak past it on
those tables; see Plan-01 tech debt note).

The full linked-vs-forked fields (scope / forked_from_*) for skills
and tools land in task_01_15 once their fork endpoints exist. This
migration is the minimal substrate so /skills and /tools can return
"catalog + custom" results in task_01_05.

Revision ID: 0005_skills_tools_builtin
Revises: 0004_agents_builtin_visibility
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_skills_tools_builtin"
down_revision: str | Sequence[str] | None = "0004_agents_builtin_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("skills", "tools"):
        op.add_column(
            table,
            sa.Column(
                "is_builtin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.create_index(
            f"ix_{table}_is_builtin",
            table,
            ["is_builtin"],
            postgresql_where=sa.text("is_builtin = true"),
        )
        op.execute(
            f"CREATE POLICY {table}_builtin_read ON {table} FOR SELECT" " USING (is_builtin = true)"
        )


def downgrade() -> None:
    for table in ("tools", "skills"):
        op.execute(f"DROP POLICY IF EXISTS {table}_builtin_read ON {table}")
        op.drop_index(f"ix_{table}_is_builtin", table_name=table)
        op.drop_column(table, "is_builtin")
