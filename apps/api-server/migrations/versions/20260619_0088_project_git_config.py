"""projects.git_config — config git tipada del proyecto (ADR 0072).

{provider, remote_url, default_branch, auth_mode}. El secreto (PAT/clave SSH) NO
va en la BD (vive en Vault). Aditiva y reversible.

Revision ID: 0088_project_git_config
Revises: 0087_team_memory_scope
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0088_project_git_config"
down_revision: str | Sequence[str] | None = "0087_team_memory_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("git_config", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "git_config")
