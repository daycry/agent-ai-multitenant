"""Agent memory (Plan 04 task_04_01).

A `MemoryEntry` is a short, retrievable fact the agent platform keeps
across runs. The Memorizer (`task_04_03`) writes them after each
`Execution`; the `memory_recall` tool (`task_04_04`) retrieves them
by hybrid search (BM25 + vector + RRF).

Four orthogonal axes shape every row:

1. **scope** (:class:`MemoryScope`) — who can read it
   (``private`` / ``team_shared`` / ``project_shared`` / ``global``).
2. **type** (:class:`MemoryType`) — ``episodic`` (concrete event) vs
   ``semantic`` (extracted rule).
3. **content** — the human-readable text. Short by design (a paragraph,
   not a transcript). Long-form goes to the KB, not to memory.
4. **embedding** — a `vector(768)` (pgvector). Populated asynchronously
   by the Memorizer once the Ollama call finishes. NULL on freshly
   inserted rows so we never block a write on the embedder.

Owner pointers (one of ``user_id`` / ``team_id`` / ``project_id``) are
nullable on the column itself; a CHECK constraint enforces the
scope→pointer pairing per row. ``source_execution_id`` is the
back-link to the `Execution` the entry was distilled from (NULL for
manually-stored entries via `memory_store`).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Embedding dimensionality. Default model is `nomic-embed-text-v1.5`
# via Ollama (ADR pending in Plan 04 Fase C / D) which produces 768
# floats. Changing this requires a migration + re-embedding every row.
EMBEDDING_DIM = 768


class MemoryEntry(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """One memory the agent platform persists across runs.

    See module docstring for the four-axis model. The
    ``ck_memory_entries_scope_pointer`` constraint encodes which owner
    column must be NOT NULL for each scope, so the database refuses
    obviously broken rows (e.g. ``scope='private'`` without a
    ``user_id``).
    """

    __tablename__ = "memory_entries"
    __table_args__ = (
        Index(
            "ix_memory_entries_tenant_scope_type",
            "tenant_id",
            "scope",
            "type",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_memory_entries_project_id",
            "project_id",
            postgresql_where=text("project_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "ix_memory_entries_team_id",
            "team_id",
            postgresql_where=text("team_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "ix_memory_entries_user_id",
            "user_id",
            postgresql_where=text("user_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint(
            "(scope = 'private' AND user_id IS NOT NULL)"
            " OR (scope = 'team_shared' AND team_id IS NOT NULL)"
            " OR (scope = 'project_shared' AND project_id IS NOT NULL)"
            " OR (scope = 'global')",
            name="ck_memory_entries_scope_pointer",
        ),
        CheckConstraint(
            "type IN ('episodic', 'semantic')",
            name="ck_memory_entries_type",
        ),
        CheckConstraint(
            "scope IN ('private', 'team_shared', 'project_shared', 'global')",
            name="ck_memory_entries_scope",
        ),
    )

    scope: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'private'"))
    type: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'episodic'"))

    # The text the agent reads back. Short by convention; the KB is
    # where long documents belong (Plan 04 Fase B onward).
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # NULL on insert; the Memorizer / memory_store back-fills it once
    # the Ollama embedder responds. Nullable so a write never blocks
    # on the embedder service (idempotent re-embed on model change).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    # Owner pointers. Exactly one is NOT NULL per scope (except
    # `global`, where all three are NULL). See ck_memory_entries_scope_pointer.
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Back-link to the Execution this memory was distilled from. NULL
    # when the entry came from `memory_store` (human-curated).
    source_execution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("executions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Which agent produced this memory (NULL for human-curated).
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Free-form tags (e.g. ["sqlalchemy", "asyncpg"]) for filter queries.
    tags: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Anything else the Memorizer wants to stash (model id used to
    # distil, token cost, source step ids, etc.).
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


__all__ = ["EMBEDDING_DIM", "MemoryEntry"]
