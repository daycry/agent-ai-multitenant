"""chunks FTS: índice GIN con public.es_unaccent (P0-4, investigación 2026-07-11).

El índice ``ix_chunks_content_fts`` se creó en 0022 con el tokenizador
``'simple'`` (sin unaccent ni stemming español) y las rutas de búsqueda
divergieron: el preview del dueño de la KB consultaba con ``public.es_unaccent``
(migración 0079 — configuración spanish + unaccent) SIN índice que la sirviera,
y la ruta que consumen los agentes y el planning (``bm25_chunks``) consultaba
con ``'simple'`` — indexada pero ciega a acentos e inflexiones en castellano
("categorización" ≠ "categorizacion").

Esta migración reconstruye el índice con ``public.es_unaccent`` (la config ya
existe desde 0079) y ``rag/search.py`` unifica TODAS las rutas sobre ella.

Reversible: el downgrade restaura el índice ``'simple'`` exactamente como lo
dejó 0022.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0107_chunks_fts_es_unaccent"
down_revision: str | Sequence[str] | None = "0106_documents_pending_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS_CONFIG = "public.es_unaccent"


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_fts")
    op.execute(
        f"CREATE INDEX ix_chunks_content_fts"
        f" ON chunks USING GIN (to_tsvector('{_TS_CONFIG}', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_fts")
    op.execute(
        "CREATE INDEX ix_chunks_content_fts" " ON chunks USING GIN (to_tsvector('simple', content))"
    )
