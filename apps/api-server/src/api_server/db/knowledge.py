"""Knowledge Base ORM models (Plan 04 task_04_07).

Four tables make up the KB substrate:

  - **`knowledge_bases`** — top-level container, owned by a tenant.
    A KB carries its embedding model id so we can mix-and-match
    (one KB on `nomic-embed-text-v1.5`, another on a future
    `text-embedding-3-small`); changing it triggers a re-embed
    pipeline (out of scope until Plan 12).
  - **`documents`** — one row per uploaded source file. Pointers to
    the MinIO object holding the raw bytes + lifecycle status
    (:class:`DocumentStatus`) the ingestion worker drives.
  - **`chunks`** — structural chunks Docling produces (Fase C). Each
    carries a `vector(768)` embedding indexed by HNSW (migration
    0022) so the RAG search (Fase D) can do nearest-neighbour
    retrieval.
  - **`kb_projects`** — M:N junction between KB and Project. A KB is
    invisible to a project until a row in this table grants access.
    No row = no access (explicit grants only; see ADR notes in the
    Plan 04 changelog).

All four tables are tenant-scoped (RLS policy mirrors the canonical
one in migration 0001) and use the same UUID + timestamps mixins as
the rest of the domain. Chunks are NOT soft-deleted — they are
derived data, regenerated when a document is re-ingested.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Same dim as memory entries: nomic-embed-text-v1.5 default
# (ADR pending in Plan 04 Fase C / D). Changing it requires a
# migration + re-embedding every chunk.
CHUNK_EMBEDDING_DIM = 768


# =============================================================================
# KnowledgeBase
# =============================================================================
class KnowledgeBase(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A named container of `Document` rows.

    Owned by a tenant; visible to a project only via the
    `kb_projects` junction. Soft-deletable so a destructive UI action
    can be reverted before the cleanup job kicks in.
    """

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        Index(
            "ix_knowledge_bases_tenant_name",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form so the operator can choose between Ollama models
    # without a migration. Defaults to the platform-wide pick.
    embedding_model_id: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=text("'nomic-embed-text-v1.5'")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Plan 06.12 (ADR 0029): catálogo global. true = KB built-in
    # sembrada bajo PLATFORM_TENANT_ID, visible a todos los tenants via
    # la policy knowledge_bases_builtin_read y read-only para ellos.
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Plan 06.10 task_06_10_01: KB categorization. Nullable — borrar
    # una categoría no borra las KBs (ON DELETE SET NULL); el tenant
    # las re-categoriza después.
    category_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("kb_categories.id", ondelete="SET NULL"),
        nullable=True,
    )


# =============================================================================
# Document
# =============================================================================
class Document(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """One source file uploaded into a KB.

    `source_storage_key` is the MinIO object key
    ``kb/{tenant_id}/{kb_id}/{document_id}/{filename}``. The bytes
    never live in Postgres — `chunks.content` carries the
    distilled, searchable text.
    """

    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "ix_documents_kb_status",
            "kb_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_documents_tenant_id", "tenant_id"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'indexed', 'indexed_empty', 'failed')",
            name="ck_documents_status",
        ),
        CheckConstraint("source_size_bytes >= 0", name="ck_documents_size_non_negative"),
        CheckConstraint("page_count >= 0", name="ck_documents_page_count_non_negative"),
    )

    kb_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    # MinIO object key — never the raw bytes.
    source_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Page count after Docling parses (Fase C). 0 until then; the
    # uploader sets it to whatever they know upfront (PDF reader,
    # etc.) and the ingestion worker rewrites it post-parse.
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    indexed_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# =============================================================================
# Chunk
# =============================================================================
class Chunk(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One chunk of a `Document` (Plan 04 task_04_07 / Fase D).

    Structural chunks Docling produces preserving the document's
    hierarchy (heading → paragraph → list). One chunk = one
    embedding = one retrievable unit during RAG search.

    NOT soft-deleted — chunks are derived state. Re-ingestion drops
    the existing rows for that document and inserts new ones.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document_id_ordinal", "document_id", "ordinal"),
        Index("ix_chunks_tenant_id", "tenant_id"),
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_non_negative"),
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 0-indexed position inside the document (preserves Docling's
    # structural order so the citation viewer can scroll to it).
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(CHUNK_EMBEDDING_DIM), nullable=True
    )

    # Bounding box for the citation viewer (Plan 04 task_04_25).
    # Shape: {"page": 4, "x": 0.1, "y": 0.2, "w": 0.7, "h": 0.05} in
    # normalised page coords. NULL for chunks that don't come from a
    # paginated source (HTML / Markdown).
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Free-form per-chunk metadata: heading path, section level,
    # source page number, etc.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


# =============================================================================
# KnowledgeBaseProject (M:N junction)
# =============================================================================
class KnowledgeBaseProject(Base):
    """Junction granting a project access to a KB (Plan 04 task_04_07).

    A KB with zero rows here is **invisible** to every project — the
    grant is explicit (see Plan 04 changelog for the rationale).
    """

    __tablename__ = "kb_projects"
    __table_args__ = (
        PrimaryKeyConstraint("kb_id", "project_id", name="pk_kb_projects"),
        Index("ix_kb_projects_project_id", "project_id"),
        Index("ix_kb_projects_tenant_id", "tenant_id"),
    )

    kb_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalised tenant_id so RLS can enforce isolation on the
    # junction without a join to the parent rows.
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    granted_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    granted_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# =============================================================================
# AgentKnowledgeBase — Plan 06.9 task_06_9_01
# =============================================================================
class AgentKnowledgeBase(Base):
    """Junction granting an **agent template** access to a KB.

    Mirror image of `KnowledgeBaseProject`: same composite PK shape,
    same denormalised `tenant_id` so RLS isolates the junction without
    a join. Difference: the foreign side is `agents`, not `projects`.

    Rules (enforced at the endpoint layer):
      * Only agents with scope `global_tenant_template` or
        `project_local` can receive grants. `global_builtin` rows
        are managed by the system via seeds.
      * The `kb_id` must belong to the same tenant as the agent —
        RLS guarantees that, but the endpoint surfaces a 404 explicit
        instead of a silent miss.
    """

    __tablename__ = "agent_knowledge_bases"
    __table_args__ = (
        PrimaryKeyConstraint("agent_id", "kb_id", name="pk_agent_knowledge_bases"),
        Index("ix_agent_kbs_kb_id", "kb_id"),
        Index("ix_agent_kbs_tenant_id", "tenant_id"),
    )

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    kb_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    granted_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    granted_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = [
    "CHUNK_EMBEDDING_DIM",
    "AgentKnowledgeBase",
    "Chunk",
    "Document",
    "KbCategory",
    "KnowledgeBase",
    "KnowledgeBaseProject",
]


# =============================================================================
# KbCategory — Plan 06.10 task_06_10_01
# =============================================================================
class KbCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """Categoría agrupadora para KBs.

    Dos sabores en la misma tabla:
      * **Built-in** (`tenant_id IS NULL`) — seedeada por el platform,
        visible a todos los tenants via la policy `kb_categories_builtin_read`.
        El tenant API NUNCA puede editarlas ni borrarlas.
      * **Custom** (`tenant_id IS NOT NULL`) — creada por un
        `tenant_admin` desde la UI. Aislamiento de tenant estándar.

    El `slug` es único per-scope (built-in scope o per-tenant), usando
    el índice partial con `COALESCE(tenant_id, '')` definido en la
    migración 0028.

    Borrar una categoría es **soft-delete** (`deleted_at`); las KBs
    que la usaban quedan con `category_id = NULL` via el FK
    `ON DELETE SET NULL` del lado de `knowledge_bases`. Recuperarla
    es trivial (UPDATE deleted_at = NULL).
    """

    __tablename__ = "kb_categories"

    # Plan 06.12 (ADR 0029): patrón (A). Built-ins viven bajo
    # PLATFORM_TENANT_ID con is_builtin=true; custom bajo el tenant con
    # is_builtin=false. tenant_id sigue NULLABLE solo por compat con la
    # 0028 (ya no hay filas NULL tras la 0030). NO usamos TenantScopedMixin
    # porque históricamente esta columna fue nullable.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Hex color opcional para el badge en la UI (`#3b82f6`, etc.).
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # true = categoría de catálogo (read-only para el tenant), visible a
    # todos via la policy kb_categories_builtin_read.
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
