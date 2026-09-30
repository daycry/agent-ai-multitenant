"""`documents (kb_id, created_at DESC, id)`: el índice del listado paginado (`task_ui_21`).

`GET /knowledge-bases/{id}/documents` ordena por `created_at DESC, id` y el único
índice por `kb_id` (`ix_documents_kb_status`) no sirve ese orden: en una base de
miles de documentos era un sort completo por página. ADITIVA y reversible; parcial
sobre `deleted_at IS NULL`, igual que el resto de índices del listado.

**Sin `CONCURRENTLY` a propósito**, como la migración 0142 (que lo descartó con el
mismo argumento): `CONCURRENTLY` exige `autocommit_block()`, que comitea la
transacción externa y libera el `pg_advisory_xact_lock` que `migrations/env.py`
toma para serializar `alembic upgrade` concurrentes (prod-01 task_12); un segundo
upgrade podría correr esta migración a la vez y fallar. `documents` pesa miles a
decenas de miles de filas: el `CREATE INDEX` bloquea las escrituras menos de un
segundo. `IF [NOT] EXISTS` lo hace reintentable.
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
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_kb_created", table_name="documents", if_exists=True)
