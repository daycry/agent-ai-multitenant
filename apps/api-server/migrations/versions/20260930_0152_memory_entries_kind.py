"""`memory_entries.kind`: tipo de observación de vocabulario cerrado (task_mem_04).

ADITIVA y reversible. ``kind`` (``varchar(16)`` NULL) dice DE QUÉ va la memoria:
``summary`` (el resumen de una ejecución), ``decision``, ``bugfix``, ``pattern``,
``gotcha``, ``preference`` o ``fact``. Es ortogonal a ``type`` (episodic /
semantic), que se conserva. Las filas existentes quedan en ``NULL`` («sin
clasificar» en el panel): no se reclasifica nada.

``ADD COLUMN`` nullable sin DEFAULT no reescribe la tabla, y el ``CHECK`` se crea
en la misma transacción (la columna es toda NULL: el escaneo de validación es
trivial). Sin ``CREATE INDEX CONCURRENTLY``: rompería el cerrojo de migraciones
(ver el docstring de la 0150); ``kind`` es de baja cardinalidad y se filtra junto
a ``tenant_id`` y ``scope``, que ya tienen índice.

Revision ID: 0152_memory_entries_kind
Revises: 0151_memory_recall_usage
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0152_memory_entries_kind"
down_revision: str | Sequence[str] | None = "0151_memory_recall_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_memory_entries_kind"

#: LITERAL del vocabulario (`api_server.memorizer.kinds.MEMORY_KINDS`): una
#: migración no importa código de aplicación que puede cambiar después.
_KINDS = ("summary", "decision", "bugfix", "pattern", "gotcha", "preference", "fact")


def upgrade() -> None:
    op.add_column("memory_entries", sa.Column("kind", sa.String(length=16), nullable=True))
    quoted = ", ".join(f"'{k}'" for k in _KINDS)
    op.create_check_constraint(CONSTRAINT, "memory_entries", f"kind IS NULL OR kind IN ({quoted})")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "memory_entries", type_="check")
    op.drop_column("memory_entries", "kind")
