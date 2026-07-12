"""Capa PROYECTO de los guardrails declarativos (ADR 0102 D3).

``projects.guardrails_config`` (JSONB nullable): la config declarativa
``{guardrails: {hook: [checks…]}}`` que el worker fusiona con la capa
plataforma (``platform_settings.guardrails_config``) vía
``shared_guardrails.layers.resolve_config`` — los checks ``locked`` de
plataforma no pueden relajarse aquí. NULL = sin capa proyecto. Reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0110_projects_guardrails"
down_revision: str | Sequence[str] | None = "0109_llm_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("guardrails_config", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "guardrails_config")
