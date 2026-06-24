"""teams.forked_from_team_id + forked_from_version — adopción de equipos (Ola C / ADR 0066).

Espejo de los campos forked_from de ``agents``: cuando un tenant ADOPTA un equipo
built-in, el equipo copia enlaza al origen para diff/re-sync. Aditiva y
reversible: ``upgrade`` añade ambas columnas (nullable) + un índice parcial sobre
``forked_from_team_id``; ``downgrade`` las retira. FK self-referencial a
``teams.id`` con ``ON DELETE SET NULL`` (si se borra el built-in origen, el
adoptado sobrevive sin enlace).

Revision ID: 0086_team_forked_from
Revises: 0085_team_project_model_config
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0086_team_forked_from"
down_revision: str | Sequence[str] | None = "0085_team_project_model_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("forked_from_team_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "teams",
        sa.Column("forked_from_version", sa.String(length=32), nullable=True),
    )
    op.create_foreign_key(
        "fk_teams_forked_from_team_id",
        "teams",
        "teams",
        ["forked_from_team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_teams_forked_from",
        "teams",
        ["forked_from_team_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_teams_forked_from", table_name="teams")
    op.drop_constraint("fk_teams_forked_from_team_id", "teams", type_="foreignkey")
    op.drop_column("teams", "forked_from_version")
    op.drop_column("teams", "forked_from_team_id")
