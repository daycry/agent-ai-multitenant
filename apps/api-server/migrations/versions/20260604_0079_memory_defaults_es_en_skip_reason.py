"""memory defaults: BM25 ES+EN + unaccent + executions.memorize_skip_reason
(Plan 06.17 task_06_17_04).

Tres cambios reversibles que cierran la promesa de memoria de extremo a extremo:

  1. **BM25 español/inglés + unaccent**. El path de texto de
     ``api_server.memorizer.recall`` usaba la configuración ``'simple'``
     (solo minúsculas + tokenización, SIN stemming ni acentos), de modo que
     ``arquitectura`` NO casaba ``arquitecturas`` ni ``decision`` casaba
     ``decisión``. Esta migración:

       * instala la extensión ``unaccent`` (idempotente, ``IF NOT EXISTS``);
       * crea una configuración de búsqueda de texto ``public.es_unaccent`` que
         encadena el diccionario ``unaccent`` con el stemmer ``spanish_stem``
         para los tipos de token de palabra (asciiword/word/hword/…). El español
         es el idioma principal del proyecto (CLAUDE.md), y su stemmer reduce
         ``arquitectura``/``arquitecturas`` al mismo lexema; el inglés sigue
         tokenizando bien (``database``/``databases`` también colapsan vía
         snowball español, suficiente para recall de memoria). ``unaccent`` hace
         la búsqueda insensible a acentos en ambos idiomas;
       * reconstruye el índice GIN expresión-based de ``memory_entries.content``
         con la nueva configuración (el índice y la query DEBEN usar la MISMA
         expresión para que el índice se use).

  2. **``executions.memorize_skip_reason``** (TEXT/``String(32)`` nullable). El
     worker del Memorizer persiste aquí el CÓDIGO canónico del motivo por el que
     no produjo memoria (``not_done``/``skip_private``/``no_team``/``no_scope``/
     ``llm_empty``), antes solo visible en logs. NULL = memorizó OK o el
     Memorizer aún no corrió. Un endpoint lo expone a la UI.

Reversible: ``downgrade`` restaura EXACTAMENTE el estado de 0078 — vuelve a crear
el índice GIN con ``'simple'``, elimina la configuración ``public.es_unaccent`` y
la columna. La extensión ``unaccent`` se deja instalada (DROP EXTENSION podría
romper otros objetos que dependan de ella en el futuro; es inerte si no se usa),
misma postura que 0020 con ``vector``.

Revision ID: 0079_memory_defaults
Revises: 0078_skills_category_check
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079_memory_defaults"
down_revision: str | Sequence[str] | None = "0078_skills_category_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Nombre de la configuración TS (esquema-cualificado para que index y query la
# referencien de forma estable sea cual sea el ``search_path`` del rol).
_TS_CONFIG = "public.es_unaccent"

# Tipos de token sobre los que aplicamos unaccent + stemming español. Son los que
# PostgreSQL clasifica como "palabra" en su parser por defecto.
_WORD_TOKEN_TYPES = (
    "asciiword",
    "asciihword",
    "hword_asciipart",
    "word",
    "hword",
    "hword_part",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    # Configuración de búsqueda de texto español + unaccent. La basamos en
    # 'spanish' (copia su parser + reglas) y reescribimos los mapeos de los
    # tokens de palabra a 'unaccent, spanish_stem': unaccent normaliza acentos,
    # spanish_stem reduce a lexemas (arquitectura ~ arquitecturas).
    op.execute(f"DROP TEXT SEARCH CONFIGURATION IF EXISTS {_TS_CONFIG}")
    op.execute(f"CREATE TEXT SEARCH CONFIGURATION {_TS_CONFIG} ( COPY = spanish )")
    for token_type in _WORD_TOKEN_TYPES:
        op.execute(
            f"ALTER TEXT SEARCH CONFIGURATION {_TS_CONFIG}"
            f" ALTER MAPPING FOR {token_type}"
            f" WITH unaccent, spanish_stem"
        )

    # Reconstruir el índice GIN con la nueva configuración (DROP + CREATE: la
    # expresión del índice cambia).
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_content_fts")
    op.execute(
        f"CREATE INDEX ix_memory_entries_content_fts"
        f" ON memory_entries"
        f" USING GIN (to_tsvector('{_TS_CONFIG}', content))"
        f" WHERE deleted_at IS NULL"
    )

    op.add_column(
        "executions",
        sa.Column("memorize_skip_reason", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "memorize_skip_reason")

    # Restaurar el índice 'simple' exactamente como lo dejó 0021/0078.
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_content_fts")
    op.execute(
        "CREATE INDEX ix_memory_entries_content_fts"
        " ON memory_entries"
        " USING GIN (to_tsvector('simple', content))"
        " WHERE deleted_at IS NULL"
    )

    op.execute(f"DROP TEXT SEARCH CONFIGURATION IF EXISTS {_TS_CONFIG}")
    # La extensión unaccent se deja instalada (inerte), misma postura que 0020
    # con la extensión vector.
