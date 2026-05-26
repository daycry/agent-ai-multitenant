"""Unit tests for the KnowledgeBase / Document / Chunk / kb_projects
ORM contract (Plan 04 task_04_07).

The migration + RLS are exercised in
`tests/integration/test_kb_migration.py` (task_04_08). Here we stay
in-process and pin the column shape + constraints the rest of Plan
04 will depend on.
"""

from __future__ import annotations

import pytest
from api_server.db.domain import DocumentStatus
from api_server.db.knowledge import (
    CHUNK_EMBEDDING_DIM,
    Chunk,
    Document,
    KnowledgeBase,
    KnowledgeBaseProject,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Document status enum
# ---------------------------------------------------------------------------
def test_document_status_has_four_canonical_values() -> None:
    assert {s.value for s in DocumentStatus} == {
        "pending",
        "processing",
        "indexed",
        "failed",
    }


# ---------------------------------------------------------------------------
# KnowledgeBase
# ---------------------------------------------------------------------------
def test_knowledge_base_table_name_and_columns() -> None:
    assert KnowledgeBase.__tablename__ == "knowledge_bases"
    cols = {c.name for c in KnowledgeBase.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "name",
        "description",
        "embedding_model_id",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_knowledge_base_has_unique_name_per_tenant_partial_index() -> None:
    """Two non-deleted KBs in the same tenant cannot share a name."""
    idx_names = {idx.name: idx for idx in KnowledgeBase.__table__.indexes}
    assert "ix_knowledge_bases_tenant_name" in idx_names
    idx = idx_names["ix_knowledge_bases_tenant_name"]
    assert idx.unique is True


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
def test_document_table_name_and_columns() -> None:
    assert Document.__tablename__ == "documents"
    cols = {c.name for c in Document.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "kb_id",
        "title",
        "source_filename",
        "source_mime_type",
        "source_storage_key",
        "source_size_bytes",
        "status",
        "error_message",
        "page_count",
        "indexed_at",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_document_check_constraints_present() -> None:
    from sqlalchemy import CheckConstraint

    ck_names = {c.name for c in Document.__table__.constraints if isinstance(c, CheckConstraint)}
    assert "ck_documents_status" in ck_names
    assert "ck_documents_size_non_negative" in ck_names
    assert "ck_documents_page_count_non_negative" in ck_names


def test_document_kb_fk_cascades() -> None:
    """Dropping a KB drops all its documents."""
    kb_col = Document.__table__.columns["kb_id"]
    fks = list(kb_col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


# ---------------------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------------------
def test_chunk_table_name_and_columns() -> None:
    assert Chunk.__tablename__ == "chunks"
    cols = {c.name for c in Chunk.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "document_id",
        "ordinal",
        "content",
        "embedding",
        "bbox",
        "metadata",
        "created_at",
        "updated_at",
    } <= cols
    # Chunks are NOT soft-deletable — they are derived state.
    assert "deleted_at" not in cols


def test_chunk_embedding_dim_matches_memory() -> None:
    """Same embedder for memories and chunks — keep the dim aligned."""
    assert CHUNK_EMBEDDING_DIM == 768
    col = Chunk.__table__.columns["embedding"]
    assert getattr(col.type, "dim", None) == CHUNK_EMBEDDING_DIM
    assert col.nullable is True


def test_chunk_uniqueness_constraint_per_document_ordinal() -> None:
    from sqlalchemy import UniqueConstraint

    uniques = {c.name for c in Chunk.__table__.constraints if isinstance(c, UniqueConstraint)}
    assert "uq_chunks_document_ordinal" in uniques


def test_chunk_document_fk_cascades() -> None:
    fk_col = Chunk.__table__.columns["document_id"]
    fks = list(fk_col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


# ---------------------------------------------------------------------------
# KnowledgeBaseProject (M:N junction)
# ---------------------------------------------------------------------------
def test_kb_projects_table_name() -> None:
    assert KnowledgeBaseProject.__tablename__ == "kb_projects"


def test_kb_projects_has_composite_primary_key() -> None:
    pk = KnowledgeBaseProject.__table__.primary_key
    cols = {c.name for c in pk.columns}
    assert cols == {"kb_id", "project_id"}


def test_kb_projects_columns_present() -> None:
    cols = {c.name for c in KnowledgeBaseProject.__table__.columns}
    assert {"kb_id", "project_id", "tenant_id", "granted_at", "granted_by"} <= cols


def test_kb_projects_kb_fk_cascades() -> None:
    """Dropping a KB cleans up its grants."""
    fks = list(KnowledgeBaseProject.__table__.columns["kb_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


def test_kb_projects_project_fk_cascades() -> None:
    """Dropping a project cleans up its KB subscriptions."""
    fks = list(KnowledgeBaseProject.__table__.columns["project_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"
