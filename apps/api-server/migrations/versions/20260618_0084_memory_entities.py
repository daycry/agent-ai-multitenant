"""memory_entries: add the ``entities`` JSONB array + GIN index (ADR 0059 A).

La idea nativa de mem0 (sin la librería): la distilación extrae entidades
normalizadas (personas, proyectos, componentes, tecnologías…) y se guardan como
un array JSONB en cada memoria. El recall las usa como TERCERA señal
(entity-match) fusionada con BM25 + vector vía RRF. El índice GIN (jsonb_ops por
defecto) soporta el operador de solape ``?|`` que usa el path de entity-match.

Aditiva y reversible: ``upgrade`` añade la columna NOT NULL con default
``'[]'::jsonb`` (las filas existentes quedan con array vacío) + el índice GIN;
``downgrade`` los retira. La tabla tiene RLS (TenantScopedMixin) — la columna no
cambia las políticas.

Revision ID: 0084_memory_entities
Revises: 0083_llm_provider_slug
Create Date: 2026-06-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0084_memory_entities"
down_revision: str | Sequence[str] | None = "0083_llm_provider_slug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_memory_entries_entities_gin"


def upgrade() -> None:
    op.add_column(
        "memory_entries",
        sa.Column("entities", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_index(
        _INDEX,
        "memory_entries",
        ["entities"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="memory_entries")
    op.drop_column("memory_entries", "entities")
