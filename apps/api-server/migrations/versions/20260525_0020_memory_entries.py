"""memory_entries table + pgvector extension + HNSW index (Plan 04 task_04_02).

The Memorizer (`task_04_03`) and `memory_recall` / `memory_store`
tools (`task_04_04` / `task_04_05`) read and write this table. Four
orthogonal axes per row — scope, type, content, embedding — plus the
owner pointer trio (user/team/project) constrained per scope. See the
model module `api_server.db.memory` for the rationale.

The embedding is a `vector(768)` column (default Ollama model
`nomic-embed-text-v1.5`) indexed with **HNSW** (Hierarchical
Navigable Small World) using `vector_cosine_ops`. HNSW gives better
recall than IVFFlat at the cost of slower build time and more
memory; for memory-recall — where every hit affects an agent's
behaviour — recall matters more than build cost.

Revision ID: 0020_memory_entries
Revises: 0019_tenant_hourly_rate
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_memory_entries"
down_revision: str | Sequence[str] | None = "0019_tenant_hourly_rate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector extension. Idempotent (`IF NOT EXISTS`) so re-running
    # the migration against a partially-upgraded DB is safe.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "memory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scope",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'private'"),
        ),
        sa.Column(
            "type",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'episodic'"),
        ),
        sa.Column("content", sa.Text(), nullable=False),
        # The embedding column type is declared as the raw SQL the
        # pgvector extension provides; Alembic's autogenerate doesn't
        # know about pgvector.sqlalchemy.Vector, but plain DDL works.
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float()),  # placeholder, replaced just below
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "source_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope IN ('private', 'team_shared', 'project_shared', 'global')",
            name="ck_memory_entries_scope",
        ),
        sa.CheckConstraint(
            "type IN ('episodic', 'semantic')",
            name="ck_memory_entries_type",
        ),
        sa.CheckConstraint(
            "(scope = 'private' AND user_id IS NOT NULL)"
            " OR (scope = 'team_shared' AND team_id IS NOT NULL)"
            " OR (scope = 'project_shared' AND project_id IS NOT NULL)"
            " OR (scope = 'global')",
            name="ck_memory_entries_scope_pointer",
        ),
    )

    # Swap the placeholder embedding column to pgvector's `vector(768)`.
    # ALTER TABLE keeps the FK / constraint definitions above intact.
    op.execute("ALTER TABLE memory_entries ALTER COLUMN embedding TYPE vector(768)")

    # Plain B-tree indexes for the hot filter paths.
    op.create_index(
        "ix_memory_entries_tenant_scope_type",
        "memory_entries",
        ["tenant_id", "scope", "type"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_memory_entries_project_id",
        "memory_entries",
        ["project_id"],
        postgresql_where=sa.text("project_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_memory_entries_team_id",
        "memory_entries",
        ["team_id"],
        postgresql_where=sa.text("team_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_memory_entries_user_id",
        "memory_entries",
        ["user_id"],
        postgresql_where=sa.text("user_id IS NOT NULL AND deleted_at IS NULL"),
    )

    # HNSW index for vector similarity search (cosine distance, since
    # we'll always L2-normalise the query vector at recall time).
    op.execute(
        "CREATE INDEX ix_memory_entries_embedding_hnsw"
        " ON memory_entries USING hnsw (embedding vector_cosine_ops)"
        " WITH (m = 16, ef_construction = 64)"
    )

    # RLS — every read / write must carry the active tenant via the
    # `app.tenant_id` session GUC set by the auth middleware. Matches
    # the rest of the domain (see migration 0017 for the canonical
    # form).
    op.execute("ALTER TABLE memory_entries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_entries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY memory_entries_tenant_isolation"
        " ON memory_entries FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS memory_entries_tenant_isolation ON memory_entries")
    op.execute("ALTER TABLE memory_entries DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_embedding_hnsw")
    op.drop_index("ix_memory_entries_user_id", table_name="memory_entries")
    op.drop_index("ix_memory_entries_team_id", table_name="memory_entries")
    op.drop_index("ix_memory_entries_project_id", table_name="memory_entries")
    op.drop_index("ix_memory_entries_tenant_scope_type", table_name="memory_entries")
    op.drop_table("memory_entries")
    # Keep the pgvector extension installed — other Plan 04 tables
    # (chunks, future RAG stores) will use it. Dropping it on a
    # per-table downgrade would break those if they came online first.
