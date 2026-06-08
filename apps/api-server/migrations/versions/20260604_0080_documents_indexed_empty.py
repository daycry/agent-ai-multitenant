"""documents.status: añadir 'indexed_empty' a la CHECK (Plan 06.17 task_06_17_05).

El pipeline de ingestión marcaba ``indexed`` incluso cuando Docling no
producía ningún chunk — un documento "indexado vacío" aparecía verde en
la UI aunque el agente no podía recuperar nada de él. La honestidad de
estado introduce el valor terminal ``indexed_empty`` (0 chunks, pero
procesado sin error). Esta migración amplía la CHECK ``ck_documents_status``
para admitir el nuevo valor.

Reversible: el ``downgrade`` restaura EXACTAMENTE la CHECK de 0022. Antes
de re-aplicar la CHECK estrecha, normaliza cualquier fila ``indexed_empty``
a ``indexed`` (el valor más cercano del set antiguo) para no romper la
restauración del constraint.

Revision ID: 0080_documents_indexed_empty
Revises: 0079_memory_defaults
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0080_documents_indexed_empty"
down_revision: str | Sequence[str] | None = "0079_memory_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_documents_status"
_OLD = "status IN ('pending', 'processing', 'indexed', 'failed')"
_NEW = "status IN ('pending', 'processing', 'indexed', 'indexed_empty', 'failed')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "documents", _NEW)


def downgrade() -> None:
    # Normaliza el valor nuevo al más cercano del set antiguo antes de
    # re-estrechar la CHECK, así el constraint vuelve a validar.
    op.execute("UPDATE documents SET status = 'indexed' WHERE status = 'indexed_empty'")
    op.drop_constraint(_CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "documents", _OLD)
