"""projects.allowed_domains — allowlist de dominios de las tools HTTP (prod-12 Fase B).

El runtime siempre recibió ``frozenset()`` (deny-all accidental, gap4-2) porque
el proyecto NUNCA tuvo dónde declarar sus dominios permitidos. La columna
espeja la forma de ``allowed_commands`` (TEXT[], deny-by-default: ``{}`` = las
tools HTTP no alcanzan nada). El cableado proyecto → spec del agente está
GATEADO a la defensa SSRF de la Fase A (ya mergeada: ssrf_guard + pinning).

Revision ID: 0105_project_allowed_domains
Revises: 0104_exec_container_launched
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0105_project_allowed_domains"
down_revision: str | Sequence[str] | None = "0104_exec_container_launched"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "allowed_domains",
            sa.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "allowed_domains")
