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
    literal_column,
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
        # Write path of the cortex's forgetting sweep (Cortex F5 D3, ADR 0077):
        # `workers.cortex_maintenance` scans the owner's LIVE, private, episodic
        # cortex memories ordered by `created_at` and stops at a limit. Without
        # this index the plan sorts the owner's whole private memory before the
        # limit applies — and that saco includes the assistant's memories too,
        # because the only usable index is keyed on `user_id` alone. The four
        # fixed conditions live in the predicate; `created_at` is the sort key.
        # NOTE: the DB column behind `metadata_` is named `metadata`.
        Index(
            "ix_memory_entries_cortex_sweep",
            "user_id",
            "created_at",
            postgresql_where=text(
                "deleted_at IS NULL"
                " AND scope = 'private'"
                " AND type = 'episodic'"
                " AND (metadata ->> 'cortex') = 'true'"
            ),
        ),
        # Read path: "the memories distilled from this human work session"
        # (Plan 16 task_16_15). Partial — only set on human-distilled rows.
        Index(
            "ix_memory_entries_source_hws",
            "source_human_work_session_id",
            postgresql_where=text(
                "source_human_work_session_id IS NOT NULL AND deleted_at IS NULL"
            ),
        ),
        # Mitad vectorial del recall híbrido (migración 0020). Declarado aquí
        # además de en la migración porque, mientras el modelo no lo conocía,
        # un `alembic revision --autogenerate` proponía `DROP INDEX
        # ix_memory_entries_embedding_hnsw` dentro de cualquier migración de
        # columna, y el recall habría pasado a escaneo secuencial sin avisar.
        # Los kwargs son parte del índice: `hnsw` es el método de acceso,
        # `vector_cosine_ops` la clase de operador que hace que `<=>` lo use, y
        # `m` / `ef_construction` los parámetros con los que se construyó.
        Index(
            "ix_memory_entries_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": "16", "ef_construction": "64"},
        ),
        # Mitad BM25 del recall. Índice DE EXPRESIÓN y PARCIAL: la consulta
        # tiene que repetir el mismo `to_tsvector` para acertarlo, y el
        # `WHERE deleted_at IS NULL` lo mantiene del tamaño de la memoria viva.
        # La configuración `es_unaccent` la puso la migración 0079 sobre el
        # `'simple'` original de la 0021 (acentos e inflexiones del castellano).
        Index(
            "ix_memory_entries_content_fts",
            literal_column("to_tsvector('es_unaccent'::regconfig, content)"),
            postgresql_using="gin",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Tercera señal del recall (entity-match, ADR 0059): GIN sobre el JSONB
        # para el `?|` de solapamiento (migración 0084).
        Index(
            "ix_memory_entries_entities_gin",
            "entities",
            postgresql_using="gin",
        ),
        # A memory cites at most ONE source — an Execution XOR a
        # HumanWorkSession (or neither, for human-curated entries). Plan 16
        # task_16_15: forbid both being set so the citation is unambiguous.
        CheckConstraint(
            "source_execution_id IS NULL OR source_human_work_session_id IS NULL",
            name="ck_memory_entries_single_source",
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

    # `TenantScopedMixin` trae `index=True`, pero la BD desplegada NO tiene
    # `ix_memory_entries_tenant_id` y nunca lo tuvo: la migración 0020 creó en
    # su lugar `ix_memory_entries_tenant_scope_type (tenant_id, scope, type)
    # WHERE deleted_at IS NULL`, cuya columna guía es `tenant_id`, así que ya
    # sirve las lecturas por tenant. Y no hay ninguna que mire filas
    # soft-borradas: `memory_entries` está excluida de la purga a propósito
    # (`workers.maintenance.purge`), de modo que el índice plano no cubriría
    # ninguna consulta real. Declararlo dejaba `alembic check` en rojo por un
    # item que no describe nada. Crearlo de verdad sería una migración.
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=False)

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
    # when the entry came from `memory_store` (human-curated) or from a
    # human work session (see source_human_work_session_id).
    # NO foreign key since part-01 / ADR 0154 (migration 0137): ``executions`` is
    # partitioned by month, so its PK is ``(id, created_at)`` and a FK cannot
    # reference it without carrying both columns. The column stays as a loose
    # reference — it was already nullable and nothing assumes the run still
    # exists, which is the same shape ``guardrail_events.execution_id`` has had
    # on purpose since Plan 11.
    source_execution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )

    # Back-link to the HumanWorkSession this memory was distilled from (Plan 16
    # task_16_15). NULL for AI-distilled / human-curated memories. SET NULL so
    # dropping the work session keeps the memory (only the citation is lost).
    # Mutually exclusive with source_execution_id (ck_memory_entries_single_source).
    source_human_work_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("human_work_sessions.id", ondelete="SET NULL"),
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
    # Normalised entities (people, projects, components, technologies…) the
    # distilation extracts (ADR 0059 Opción A — la idea nativa de mem0). Used
    # as a THIRD recall signal (entity-match) fused with BM25 + vector via RRF.
    # JSONB array of lowercased strings; GIN-indexed (migración 0084) for the
    # `?|` overlap lookup. Empty `[]` when nothing was extracted.
    entities: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Anything else the Memorizer wants to stash (model id used to
    # distil, token cost, source step ids, etc.).
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


__all__ = ["EMBEDDING_DIM", "MemoryEntry"]
