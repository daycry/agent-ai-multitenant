"""knowledge_bases + documents + chunks + kb_projects (Plan 04 task_04_08).

Creates the four KB tables, wires the same vector(768) + HNSW
cosine-ops pattern we used for memory_entries (migration 0020) on
`chunks.embedding`, and enables RLS on every new table with the
canonical `tenant_id = current_setting('app.tenant_id')` policy.

Revision ID: 0022_knowledge_bases
Revises: 0021_memory_fts_index
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_knowledge_bases"
down_revision: str | Sequence[str] | None = "0021_memory_fts_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TIMESTAMP_COLS = [
    sa.Column(
        "created_at",
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column(
        "updated_at",
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
]


def _enable_rls(table_name: str, policy_name: str) -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {policy_name}"
        f" ON {table_name} FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    # pgvector was enabled by migration 0020 — no need to re-run
    # `CREATE EXTENSION`. Calling it again would still be safe (idempotent).

    # ---- knowledge_bases ---------------------------------------------------
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "embedding_model_id",
            sa.String(length=120),
            nullable=False,
            server_default=sa.text("'nomic-embed-text-v1.5'"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_TIMESTAMP_COLS,
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_knowledge_bases_tenant_name",
        "knowledge_bases",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _enable_rls("knowledge_bases", "knowledge_bases_tenant_isolation")

    # ---- documents ---------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kb_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_mime_type", sa.String(length=120), nullable=False),
        sa.Column("source_storage_key", sa.String(length=500), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("indexed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_TIMESTAMP_COLS,
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'indexed', 'failed')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint("source_size_bytes >= 0", name="ck_documents_size_non_negative"),
        sa.CheckConstraint("page_count >= 0", name="ck_documents_page_count_non_negative"),
    )
    op.create_index(
        "ix_documents_kb_status",
        "documents",
        ["kb_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    _enable_rls("documents", "documents_tenant_isolation")

    # ---- chunks ------------------------------------------------------------
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float()),  # placeholder, see ALTER below
            nullable=True,
        ),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_TIMESTAMP_COLS,
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_non_negative"),
    )
    # Swap the placeholder to pgvector's `vector(768)`. Same dim as
    # memory_entries (Ollama default). Changing dimensionality
    # requires a fresh migration + re-embedding every row.
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(768)")

    op.create_index("ix_chunks_document_id_ordinal", "chunks", ["document_id", "ordinal"])
    op.create_index("ix_chunks_tenant_id", "chunks", ["tenant_id"])
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw"
        " ON chunks USING hnsw (embedding vector_cosine_ops)"
        " WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX ix_chunks_content_fts" " ON chunks USING GIN (to_tsvector('simple', content))"
    )
    _enable_rls("chunks", "chunks_tenant_isolation")

    # ---- kb_projects (M:N junction) ----------------------------------------
    op.create_table(
        "kb_projects",
        sa.Column(
            "kb_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "granted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("kb_id", "project_id", name="pk_kb_projects"),
    )
    op.create_index("ix_kb_projects_project_id", "kb_projects", ["project_id"])
    op.create_index("ix_kb_projects_tenant_id", "kb_projects", ["tenant_id"])
    _enable_rls("kb_projects", "kb_projects_tenant_isolation")


def downgrade() -> None:
    for tbl, policy in (
        ("kb_projects", "kb_projects_tenant_isolation"),
        ("chunks", "chunks_tenant_isolation"),
        ("documents", "documents_tenant_isolation"),
        ("knowledge_bases", "knowledge_bases_tenant_isolation"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {tbl}")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_kb_projects_tenant_id", table_name="kb_projects")
    op.drop_index("ix_kb_projects_project_id", table_name="kb_projects")
    op.drop_table("kb_projects")
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_fts")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_index("ix_chunks_tenant_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id_ordinal", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_documents_tenant_id", table_name="documents")
    op.drop_index("ix_documents_kb_status", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_knowledge_bases_tenant_name", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
