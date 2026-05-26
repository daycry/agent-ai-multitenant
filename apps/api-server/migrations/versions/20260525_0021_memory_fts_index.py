"""GIN index for full-text search on memory_entries.content (Plan 04 task_04_04).

The `memory_recall` tool ranks memories by a hybrid score: BM25-like
text relevance (`ts_rank_cd`) + vector cosine similarity (pgvector),
combined with Reciprocal Rank Fusion. The text path uses PostgreSQL's
built-in `tsvector` machinery — no extra extension needed.

The index is **expression-based** (`to_tsvector('simple', content)`),
so a query has to use the exact same expression to hit it:

    WHERE to_tsvector('simple', content) @@ plainto_tsquery('simple', :q)

We use the `simple` configuration (lowercase + tokenise on
non-alphanumeric) rather than `english` so we keep behaviour
deterministic across stop-word lists when memories carry Spanish or
mixed content. ts_rank_cd still gives a usable ordering.

Revision ID: 0021_memory_fts_index
Revises: 0020_memory_entries
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_memory_fts_index"
down_revision: str | Sequence[str] | None = "0020_memory_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_memory_entries_content_fts"
        " ON memory_entries"
        " USING GIN (to_tsvector('simple', content))"
        " WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_content_fts")
