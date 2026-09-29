"""`documents (kb_id, created_at DESC, id)`: el índice del listado paginado (`task_ui_21`).

`GET /knowledge-bases/{id}/documents` ordena por `created_at DESC, id` y el único
índice por `kb_id` (`ix_documents_kb_status`) no sirve ese orden: en una base de
miles de documentos era un sort completo por página. ADITIVA y reversible; parcial
sobre `deleted_at IS NULL`, igual que el resto de índices del listado.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0150_documents_kb_created_index"
down_revision: str | Sequence[str] | None = "0149_project_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_documents_kb_created",
        "documents",
        ["kb_id", sa.text("created_at DESC"), "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_documents_kb_created", table_name="documents")
