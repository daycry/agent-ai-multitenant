"""Integration tests for the per-project internal-docs KB sync (Plan 07
task_07_09).

Drives :func:`api_server.docs_structure.kb_sync.sync_project_docs`
end-to-end against the real Postgres schema using a deterministic
:class:`HashEmbedder` (no network, no Ollama). Asserts:

  * syncing a tmp ``/docs`` tree creates the deterministic internal-docs KB
    + a project grant and ingests every ``.md`` as chunks under the right
    tenant;
  * re-running with unchanged files is a skip-unchanged no-op (idempotent,
    no duplicate chunks);
  * editing a file re-chunks it (delete-and-reinsert, no stale rows);
  * deleting a file soft-deletes its Document and drops its chunks;
  * cross-tenant isolation — project A's internal docs never appear under
    tenant B (``@pytest.mark.cross_tenant``).

Uses ``admin_database_url`` (BYPASSRLS) to seed + drive + inspect, mirroring
``test_catalog_ingestion.py``.

The git-webhook / PR-merge trigger is DEFERRED to Plan 13 (webhook infra):
:func:`sync_project_docs` is the callable that hook will invoke, tested here
directly.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.docs_structure.kb_sync import (
    INTERNAL_DOCS_KB_NAME,
    internal_doc_id,
    internal_docs_kb_id,
    sync_project_docs,
)
from api_server.ingestion.embeddings import HashEmbedder
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _reset_tables(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " projects, organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_tenant_and_project(
    dsn: str, *, tenant_id: UUID, tenant_slug: str, project_id: UUID
) -> None:
    """Insert one organization + one project so the KB/document FKs hold."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)"
            " ON CONFLICT (id) DO NOTHING",
            tenant_id,
            f"Tenant {tenant_slug}",
            tenant_slug,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)"
            " ON CONFLICT (id) DO NOTHING",
            project_id,
            tenant_id,
            f"Project {tenant_slug}",
        )
    finally:
        await conn.close()


async def _open_admin_session(admin_database_url: str):
    engine = create_async_engine(admin_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory()


async def _run_sync(
    admin_database_url: str,
    *,
    project_id: UUID,
    tenant_id: UUID,
    docs_root: Path,
):
    engine, session = await _open_admin_session(admin_database_url)
    try:
        result = await sync_project_docs(
            session,
            project_id=project_id,
            tenant_id=tenant_id,
            docs_root=docs_root,
            embedder=HashEmbedder(),
        )
        await session.commit()
        return result
    finally:
        await session.close()
        await engine.dispose()


async def _live_chunk_count(dsn: str, document_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
        )
    finally:
        await conn.close()


def _write_docs_tree(root: Path) -> dict[str, str]:
    """Lay down a small ``docs/`` tree. Returns {relpath: content}."""
    docs = root / "docs"
    (docs / "01-overview").mkdir(parents=True)
    (docs / "03-guides").mkdir(parents=True)
    files = {
        "01-overview/README.md": "# Overview\n\nWhat this project is.\n",
        "03-guides/setup.md": ("# Setup\n\nInstall steps.\n\n## Prereqs\n\nDocker + Python.\n"),
        "index.md": "# Index\n\nTop-level page.\n",
    }
    for relpath, content in files.items():
        (docs / relpath).write_text(content, encoding="utf-8")
    return files


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_creates_internal_kb_with_chunks_under_tenant(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="alpha", project_id=project_id
    )
    files = _write_docs_tree(tmp_path)

    result = await _run_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    assert result.kb_created is True
    assert sorted(result.ingested) == sorted(files)
    assert result.skipped == []
    assert result.removed == []
    assert result.chunks_persisted > 0

    kb_id = internal_docs_kb_id(project_id)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # The KB exists under the project tenant, not builtin, with the
        # reserved name.
        row = await conn.fetchrow(
            "SELECT tenant_id, name, is_builtin FROM knowledge_bases WHERE id = $1", kb_id
        )
        assert row is not None
        assert row["tenant_id"] == tenant_id
        assert row["name"] == INTERNAL_DOCS_KB_NAME
        assert row["is_builtin"] is False

        # It is granted to its project via the junction.
        n_grants = await conn.fetchval(
            "SELECT count(*) FROM kb_projects WHERE kb_id = $1 AND project_id = $2",
            kb_id,
            project_id,
        )
        assert n_grants == 1

        # One indexed document per .md, all under the project tenant.
        n_docs = await conn.fetchval(
            "SELECT count(*) FROM documents"
            " WHERE kb_id = $1 AND tenant_id = $2 AND status = 'indexed'"
            " AND deleted_at IS NULL",
            kb_id,
            tenant_id,
        )
        assert n_docs == len(files)

        # Every chunk is tenant-scoped to the project tenant and embedded.
        for relpath in files:
            document_id = internal_doc_id(project_id, relpath)
            n_chunks = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
            )
            assert n_chunks > 0, relpath
            n_wrong_tenant = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE document_id = $1 AND tenant_id <> $2",
                document_id,
                tenant_id,
            )
            assert n_wrong_tenant == 0, relpath
            n_embedded = await conn.fetchval(
                "SELECT count(*) FROM chunks" " WHERE document_id = $1 AND embedding IS NOT NULL",
                document_id,
            )
            assert n_embedded == n_chunks, relpath
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resync_unchanged_is_idempotent(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="beta", project_id=project_id
    )
    files = _write_docs_tree(tmp_path)

    first = await _run_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )
    assert first.kb_created is True
    assert len(first.ingested) == len(files)

    kb_id = internal_docs_kb_id(project_id)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        total_after_first = await conn.fetchval(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.kb_id = $1",
            kb_id,
        )
    finally:
        await conn.close()
    assert total_after_first > 0

    # Second run, fresh session — every file unchanged → all skipped.
    second = await _run_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )
    assert second.kb_created is False
    assert sorted(second.skipped) == sorted(files)
    assert second.ingested == []
    assert second.chunks_persisted == 0

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        total_after_second = await conn.fetchval(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.kb_id = $1",
            kb_id,
        )
        # No duplicate KB / documents either.
        n_kbs = await conn.fetchval("SELECT count(*) FROM knowledge_bases WHERE id = $1", kb_id)
        n_docs = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE kb_id = $1 AND deleted_at IS NULL", kb_id
        )
    finally:
        await conn.close()
    assert total_after_second == total_after_first
    assert n_kbs == 1
    assert n_docs == len(files)


# ---------------------------------------------------------------------------
# Incremental: only the changed file re-chunks
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_editing_one_file_rechunks_only_that_file(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="gamma", project_id=project_id
    )
    _write_docs_tree(tmp_path)
    await _run_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    edited_rel = "03-guides/setup.md"
    edited_doc_id = internal_doc_id(project_id, edited_rel)
    unchanged_rel = "index.md"
    unchanged_doc_id = internal_doc_id(project_id, unchanged_rel)

    chunks_before = await _live_chunk_count(migrations_pg_dsn, edited_doc_id)

    # Grow the edited file: more headings → more chunks.
    (tmp_path / "docs" / edited_rel).write_text(
        "# Setup\n\nInstall steps.\n\n## Prereqs\n\nDocker.\n\n"
        "## Build\n\nrun make.\n\n## Run\n\nrun it.\n",
        encoding="utf-8",
    )

    result = await _run_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    # Only the edited file is re-ingested; the rest are skipped.
    assert result.ingested == [edited_rel]
    assert unchanged_rel in result.skipped
    assert result.removed == []

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        chunks_after = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", edited_doc_id
        )
        ordinals = await conn.fetch(
            "SELECT ordinal FROM chunks WHERE document_id = $1 ORDER BY ordinal",
            edited_doc_id,
        )
        # The unchanged file's chunks are untouched.
        n_unchanged = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", unchanged_doc_id
        )
    finally:
        await conn.close()

    assert chunks_after > chunks_before
    # No stale rows: ordinals are a dense 0..n-1 sequence.
    assert [r["ordinal"] for r in ordinals] == list(range(chunks_after))
    assert n_unchanged > 0


# ---------------------------------------------------------------------------
# Removal: a deleted file soft-deletes its Document + drops chunks
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deleting_file_removes_its_chunks(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="delta", project_id=project_id
    )
    files = _write_docs_tree(tmp_path)
    await _run_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    removed_rel = "01-overview/README.md"
    removed_doc_id = internal_doc_id(project_id, removed_rel)
    assert await _live_chunk_count(migrations_pg_dsn, removed_doc_id) > 0

    # Delete the file from disk and re-sync.
    (tmp_path / "docs" / removed_rel).unlink()

    result = await _run_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    assert removed_rel in result.removed
    assert removed_rel not in result.ingested

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Chunks gone, Document soft-deleted (deleted_at set).
        n_chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", removed_doc_id
        )
        deleted_at = await conn.fetchval(
            "SELECT deleted_at FROM documents WHERE id = $1", removed_doc_id
        )
        # The surviving files keep their docs live.
        n_live_docs = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE kb_id = $1 AND deleted_at IS NULL",
            internal_docs_kb_id(project_id),
        )
    finally:
        await conn.close()

    assert n_chunks == 0
    assert deleted_at is not None
    assert n_live_docs == len(files) - 1


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_internal_docs_never_leak_across_tenants(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    """Project A (tenant A)'s internal docs must never land under tenant B."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_a = uuid4()
    project_a = uuid4()
    tenant_b = uuid4()
    project_b = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_a, tenant_slug="a", project_id=project_a
    )
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_b, tenant_slug="b", project_id=project_b
    )

    # Distinct docs trees for each project.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    (root_a / "docs").mkdir(parents=True)
    (root_b / "docs").mkdir(parents=True)
    (root_a / "docs" / "secret-a.md").write_text(
        "# Secret A\n\nTenant A confidential.\n", encoding="utf-8"
    )
    (root_b / "docs" / "secret-b.md").write_text(
        "# Secret B\n\nTenant B confidential.\n", encoding="utf-8"
    )

    await _run_sync(admin_database_url, project_id=project_a, tenant_id=tenant_a, docs_root=root_a)
    await _run_sync(admin_database_url, project_id=project_b, tenant_id=tenant_b, docs_root=root_b)

    kb_a = internal_docs_kb_id(project_a)
    kb_b = internal_docs_kb_id(project_b)
    assert kb_a != kb_b

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Tenant A owns only KB A; every KB-A chunk carries tenant_a, none
        # carry tenant_b — and vice versa.
        a_tenant = await conn.fetchval("SELECT tenant_id FROM knowledge_bases WHERE id = $1", kb_a)
        b_tenant = await conn.fetchval("SELECT tenant_id FROM knowledge_bases WHERE id = $1", kb_b)
        assert a_tenant == tenant_a
        assert b_tenant == tenant_b

        leak_into_b = await conn.fetchval(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.kb_id = $1 AND c.tenant_id = $2",
            kb_a,
            tenant_b,
        )
        leak_into_a = await conn.fetchval(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.kb_id = $1 AND c.tenant_id = $2",
            kb_b,
            tenant_a,
        )
        assert leak_into_b == 0
        assert leak_into_a == 0

        # Tenant B's content text never appears under tenant A's chunks.
        b_content_under_a = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE tenant_id = $1 AND content ILIKE '%Tenant B%'",
            tenant_a,
        )
        assert b_content_under_a == 0
    finally:
        await conn.close()
