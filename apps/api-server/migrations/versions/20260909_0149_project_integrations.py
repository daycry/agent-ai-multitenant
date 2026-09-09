"""`projects.integrations`: las anclas de integración del proyecto (`task_mk_20`).

Plan `remediacion-marketplace-mcp-2026-09-02` (MK-05). Hoy «el epic padre de Jira»
y «la página raíz de Confluence» viven, si acaso, en la descripción de cada plan,
y el agente las adivina. Con esta columna son un AJUSTE del proyecto que el run
recibe en su preámbulo (`task_mk_21`) y que las skills `atlassian-*` leen antes
de caer a la descripción.

JSONB validado por proveedor en `schemas/integrations.py` (`jira`, `confluence`;
extensible). NOT NULL con default `{}`: ninguna lectura falla si está vacío y el
preámbulo se omite sin anclas. Sin secretos aquí — las credenciales siguen en el
servidor MCP (Vault/OAuth). RLS: la heredada de `projects`. El `downgrade` retira
la columna.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0149_project_integrations"
down_revision: str | Sequence[str] | None = "0148_task_claim_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "integrations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "integrations")
