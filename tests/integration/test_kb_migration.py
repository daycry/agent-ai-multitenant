"""Integration tests for migration 0022 — knowledge_bases + documents
+ chunks + kb_projects (Plan 04 task_04_08).

Mirrors the structure of `test_memory_migration.py`: verifies the
schema is what the ORM expects against the real Postgres + pgvector.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_two_tenants(dsn: str) -> tuple[UUID, UUID, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    project_a = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents,"
            " teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a-kb",
            tenant_b,
            "Tenant B",
            "tenant-b-kb",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-kb",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_a,
            tenant_a,
            "Project A",
        )
    finally:
        await conn.close()
    return tenant_a, tenant_b, project_a


# ---------------------------------------------------------------------------
# Schema presence
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_all_four_tables_exist(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = (
            await conn.fetch(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema()"
                "   AND table_name = ANY(:names::text[])",
            )
            if False
            else await conn.fetch(  # asyncpg doesn't bind list params here; use IN
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema()"
                "   AND table_name IN ('knowledge_bases', 'documents', 'chunks', 'kb_projects')"
            )
        )
        present = {r["table_name"] for r in rows}
        assert present == {"knowledge_bases", "documents", "chunks", "kb_projects"}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_chunk_embedding_is_vector_768(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT format_type(atttypid, atttypmod) AS t"
            " FROM pg_attribute"
            " WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        )
        assert row is not None
        assert row["t"] == "vector(768)"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_chunks_hnsw_index_present(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT indexdef FROM pg_indexes"
            " WHERE tablename = 'chunks'"
            "   AND indexname = 'ix_chunks_embedding_hnsw'"
        )
        assert row is not None
        assert "hnsw" in row["indexdef"]
        assert "vector_cosine_ops" in row["indexdef"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_chunks_fts_index_present(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT indexdef FROM pg_indexes"
            " WHERE tablename = 'chunks'"
            "   AND indexname = 'ix_chunks_content_fts'"
        )
        assert row is not None
        assert "to_tsvector" in row["indexdef"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_kb_projects_has_composite_pk(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT a.attname"
            " FROM pg_index i"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = 'kb_projects'::regclass AND i.indisprimary"
        )
        names = {r["attname"] for r in rows}
        assert names == {"kb_id", "project_id"}
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# RLS across tables
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_kb_reads(schema_at_head, migrations_pg_dsn: str) -> None:
    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
        _grant_app_user_existing_tables,
    )

    await _grant_app_user_existing_tables()
    tenant_a, tenant_b, _ = await _seed_two_tenants(migrations_pg_dsn)

    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await mig_conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name)"
            " VALUES ($1, $2, 'KB Alice'), ($3, $4, 'KB Bob')",
            uuid4(),
            tenant_a,
            uuid4(),
            tenant_b,
        )
    finally:
        await mig_conn.close()

    app_conn = await asyncpg.connect(
        f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"
    )
    try:
        # No tenant set → zero rows.
        assert await app_conn.fetch("SELECT id FROM knowledge_bases") == []
        # Tenant A → only KB Alice.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_a))
        rows = await app_conn.fetch("SELECT name FROM knowledge_bases")
        assert [r["name"] for r in rows] == ["KB Alice"]
        # Tenant B → only KB Bob.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_b))
        rows = await app_conn.fetch("SELECT name FROM knowledge_bases")
        assert [r["name"] for r in rows] == ["KB Bob"]
    finally:
        await app_conn.close()


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dropping_a_kb_cascades_to_documents_and_chunks(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    tenant_a, _, _ = await _seed_two_tenants(migrations_pg_dsn)
    kb_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            kb_id,
            tenant_a,
            "KB Cascade",
        )
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type, source_storage_key)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            doc_id,
            tenant_a,
            kb_id,
            "doc",
            "doc.pdf",
            "application/pdf",
            f"kb/{tenant_a}/{kb_id}/{doc_id}/doc.pdf",
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, $4, $5)",
            chunk_id,
            tenant_a,
            doc_id,
            0,
            "hello",
        )
        # Drop the KB — everything underneath must vanish.
        await conn.execute("DELETE FROM knowledge_bases WHERE id = $1", kb_id)
        assert await conn.fetchval("SELECT count(*) FROM documents WHERE id = $1", doc_id) == 0
        assert await conn.fetchval("SELECT count(*) FROM chunks WHERE id = $1", chunk_id) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_chunk_ordinal_uniqueness_within_a_document(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    tenant_a, _, _ = await _seed_two_tenants(migrations_pg_dsn)
    kb_id = uuid4()
    doc_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            kb_id,
            tenant_a,
            "KB Ordinal",
        )
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type, source_storage_key)"
            " VALUES ($1, $2, $3, 'doc', 'd.pdf', 'application/pdf', 'k/a/b/c/d.pdf')",
            doc_id,
            tenant_a,
            kb_id,
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0, 'first')",
            uuid4(),
            tenant_a,
            doc_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
                " VALUES ($1, $2, $3, 0, 'collides')",
                uuid4(),
                tenant_a,
                doc_id,
            )
    finally:
        await conn.close()
