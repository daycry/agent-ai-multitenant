"""Add `default_kb_grants` column to projects (Plan 06.9 task_06_9_07).

A **template** project (`is_template=true`) declares which canonical
KBs it wants pre-granted to every project derived from it. The value
is a list of KB **slugs** (not UUIDs) — slugs are stable across seed
re-runs, UUIDs are derived deterministically from the slug via
`uuid5(KB_SLUG_NAMESPACE, slug)`. The wizard endpoint reads the
column on the template and applies grants on the newly created
project (Plan 03 follow-up wires the wizard).

On non-template projects this column is allowed but the system
ignores it — only the template's value matters.

The column is `TEXT[]` (PostgreSQL array of slugs) instead of JSONB
because we never need to query its structure beyond "membership" and
arrays index slightly better with GIN if it ever needs filtering.

Revision ID: 0027_projects_default_kb_grants
Revises: 0026_agent_knowledge_bases
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_projects_default_kb_grants"
down_revision: str | Sequence[str] | None = "0026_agent_knowledge_bases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "default_kb_grants",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "default_kb_grants")
