"""projects.mcp_tool_roles — política OPCIONAL rol→tool de las MCP del proyecto (ADR 0128 fase 2).

Mapea el nombre de una tool MCP (`<server>.<tool>`) → los roles de agente
autorizados a usarla. `{}` (default) = sin política: todo agente del proyecto ve
toda tool MCP del proyecto. Un tool con entrada se restringe a esos roles; uno sin
entrada queda abierto a todos. No afecta a builtins/tools de rol (siguen por-agente).

Reversible: el downgrade elimina la columna (no hay datos que preservar — es una
capa de política opcional).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0116_projects_mcp_tool_roles"
down_revision: str | Sequence[str] | None = "0115_sso_multi_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "mcp_tool_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "mcp_tool_roles")
