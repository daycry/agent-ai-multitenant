"""Catalog visibility for project templates (task_01_13 substrate).

Adds `is_template BOOLEAN NOT NULL DEFAULT false` to `projects`. A
template is a project row owned by the platform tenant that serves as
a blueprint -- tenants fork from it rather than linking, per spec
§5.7.4 ("Plantillas de proyecto: siempre forkeadas al crear").

The matching SELECT-only RLS policy exposes template rows to every
tenant session. Writes stay scoped via the existing tenant-isolation
policy from migration 0002, so tenants can only create/edit their own
non-template projects through the standard API.

Routers must filter `is_template=false` by default when listing
real projects (templates have their own catalog UX in task_01_21).

Revision ID: 0007_project_templates
Revises: 0006_teams_builtin
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_project_templates"
down_revision: str | Sequence[str] | None = "0006_teams_builtin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "is_template",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_projects_is_template",
        "projects",
        ["is_template"],
        postgresql_where=sa.text("is_template = true"),
    )
    op.execute(
        "CREATE POLICY projects_template_read ON projects FOR SELECT USING (is_template = true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS projects_template_read ON projects")
    op.drop_index("ix_projects_is_template", table_name="projects")
    op.drop_column("projects", "is_template")
