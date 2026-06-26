"""worktree-en-ejecución: projects.slug + plans.slug

prod-18 task_prod18_design_01 / ADR 0085: stable kebab slugs for the git worktree
path (`BareRepoLayout`) and plan branch name (`make_plan_branch_name`). The
execution engine cannot use UUIDs in paths (git_repos: "slugs estables, nunca
UUIDs"), and a slug must survive a rename — so it is persisted, generated once.

Nullable + additive; existing rows are backfilled in-migration with a SQL kebab
slugify of name/title (deterministic, matches api_server.slug.slugify closely
enough for legacy rows; new rows get the canonical slug at creation). Reversible.

Revision ID: 0099_project_plan_slug
Revises: 0098_project_execution_budgets
Create Date: 2026-06-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099_project_plan_slug"
down_revision: str | Sequence[str] | None = "0098_project_execution_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQL kebab slugify: lower, non-alnum → '-', collapse, trim, cap 80; fallback
# 'untitled' for an empty result. Mirrors api_server.slug.slugify for backfill.
_BACKFILL = """
UPDATE {table} SET slug = COALESCE(
    NULLIF(
        trim(both '-' from
            left(regexp_replace(lower({source}), '[^a-z0-9]+', '-', 'g'), 80)
        ),
        ''
    ),
    'untitled'
)
WHERE slug IS NULL
"""


def upgrade() -> None:
    op.add_column("projects", sa.Column("slug", sa.String(length=80), nullable=True))
    op.add_column("plans", sa.Column("slug", sa.String(length=80), nullable=True))
    op.execute(_BACKFILL.format(table="projects", source="name"))
    op.execute(_BACKFILL.format(table="plans", source="title"))


def downgrade() -> None:
    op.drop_column("plans", "slug")
    op.drop_column("projects", "slug")
