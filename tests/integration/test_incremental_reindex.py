"""Integration tests for the incremental docs reindex (Plan 07 task_07_10).

Drives :func:`api_server.docs_structure.kb_sync.reindex_changed_docs`
end-to-end against the real Postgres schema with a deterministic
:class:`HashEmbedder` (no network, no Ollama). Where
``test_docs_kb_sync.py`` exercises the *full* tree sync, this proves the
incremental path touches ONLY the caller-supplied change set:

  * a changed subset re-ingests only those Documents — the rest are left
    byte-for-byte untouched (asserted by chunk timestamps **and** content
    hashes that must not move);
  * a deleted path in the change set soft-deletes its Document and drops its
    chunks, while files NOT in the change set keep their docs live;
  * an unchanged-but-listed file is a content-hash no-op (skipped, chunks
    untouched);
  * non-markdown / out-of-docs entries in the diff are ignored;
  * cross-tenant isolation — a reindex of project A never writes under
    tenant B (``@pytest.mark.cross_tenant``).

Reuses the ``test_docs_kb_sync`` harness (same seed + admin-session
helpers); the git-webhook / PR-merge trigger that would supply the change
set is DEFERRED to Plan 13 — :func:`reindex_changed_docs` is the callable it
will invoke, tested here directly.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.docs_structure.kb_sync import (
    internal_doc_id,
    internal_docs_kb_id,
    reindex_changed_docs,
    sync_project_docs,
)
from api_server.ingestion.embeddings import HashEmbedder
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Reuse the sibling harness — same seed + reset + tree helpers.
from tests.integration.test_docs_kb_sync import (
    _reset_tables,
    _seed_tenant_and_project,
    _write_docs_tree,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Local helpers (admin session + incremental driver + inspectors)
# ---------------------------------------------------------------------------
async def _open_admin_session(admin_database_url: str):
    engine = create_async_engine(admin_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory()


async def _run_full_sync(
    admin_database_url: str, *, project_id: UUID, tenant_id: UUID, docs_root: Path
):
    """Seed the KB with the whole tree (the precondition for incremental)."""
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


async def _run_reindex(
    admin_database_url: str,
    *,
    project_id: UUID,
    tenant_id: UUID,
    docs_root: Path,
    changed_paths: list[str],
):
    engine, session = await _open_admin_session(admin_database_url)
    try:
        result = await reindex_changed_docs(
            session,
            project_id=project_id,
            tenant_id=tenant_id,
            docs_root=docs_root,
            changed_paths=changed_paths,
            embedder=HashEmbedder(),
        )
        await session.commit()
        return result
    finally:
        await session.close()
        await engine.dispose()


async def _doc_fingerprint(dsn: str, document_id: UUID) -> tuple[int, str | None, list]:
    """Return (chunk_count, content_hash, [(ordinal, updated_at)]) for a doc.

    The content_hash + updated_at timestamps are the witnesses for
    "untouched": a skipped/unrelated file must keep all three identical
    across a reindex.
    """
    conn = await asyncpg.connect(dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
        )
        content_hash = await conn.fetchval(
            "SELECT metadata->>'content_hash' FROM chunks"
            " WHERE document_id = $1 ORDER BY ordinal LIMIT 1",
            document_id,
        )
        rows = await conn.fetch(
            "SELECT ordinal, updated_at FROM chunks WHERE document_id = $1 ORDER BY ordinal",
            document_id,
        )
        return count, content_hash, [(r["ordinal"], r["updated_at"]) for r in rows]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Happy path: only the changed file in the set is re-ingested
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reindex_touches_only_changed_subset(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="inc-a", project_id=project_id
    )
    _write_docs_tree(tmp_path)
    await _run_full_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    changed_rel = "03-guides/setup.md"
    untouched_rel = "index.md"
    other_untouched_rel = "01-overview/README.md"
    changed_doc_id = internal_doc_id(project_id, changed_rel)
    untouched_doc_id = internal_doc_id(project_id, untouched_rel)
    other_doc_id = internal_doc_id(project_id, other_untouched_rel)

    before_untouched = await _doc_fingerprint(migrations_pg_dsn, untouched_doc_id)
    before_other = await _doc_fingerprint(migrations_pg_dsn, other_doc_id)
    before_changed_count = (await _doc_fingerprint(migrations_pg_dsn, changed_doc_id))[0]

    # Edit ONLY the changed file (grow it → more chunks) and hand the
    # reindex a change set that names just that one file.
    (tmp_path / "docs" / changed_rel).write_text(
        "# Setup\n\nInstall.\n\n## Prereqs\n\nDocker.\n\n"
        "## Build\n\nmake.\n\n## Run\n\nrun it.\n\n## Teardown\n\nstop it.\n",
        encoding="utf-8",
    )

    result = await _run_reindex(
        admin_database_url,
        project_id=project_id,
        tenant_id=tenant_id,
        docs_root=tmp_path,
        changed_paths=[f"docs/{changed_rel}"],
    )

    assert result.kb_created is False
    assert result.ingested == [changed_rel]
    assert result.skipped == []
    assert result.removed == []
    assert result.chunks_persisted > 0

    after_changed_count = (await _doc_fingerprint(migrations_pg_dsn, changed_doc_id))[0]
    after_untouched = await _doc_fingerprint(migrations_pg_dsn, untouched_doc_id)
    after_other = await _doc_fingerprint(migrations_pg_dsn, other_doc_id)

    # The changed file grew its chunk set.
    assert after_changed_count > before_changed_count
    # The files NOT in the change set are byte-for-byte untouched: same
    # count, same content_hash, same per-chunk updated_at timestamps.
    assert after_untouched == before_untouched
    assert after_other == before_other


# ---------------------------------------------------------------------------
# Unchanged-but-listed file is a content-hash no-op
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reindex_unchanged_but_listed_is_noop(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="inc-b", project_id=project_id
    )
    _write_docs_tree(tmp_path)
    await _run_full_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    listed_rel = "index.md"
    listed_doc_id = internal_doc_id(project_id, listed_rel)
    before = await _doc_fingerprint(migrations_pg_dsn, listed_doc_id)

    # The file is in the change set but its bytes did NOT change → hash match.
    result = await _run_reindex(
        admin_database_url,
        project_id=project_id,
        tenant_id=tenant_id,
        docs_root=tmp_path,
        changed_paths=[f"docs/{listed_rel}"],
    )

    assert result.skipped == [listed_rel]
    assert result.ingested == []
    assert result.removed == []
    assert result.chunks_persisted == 0

    after = await _doc_fingerprint(migrations_pg_dsn, listed_doc_id)
    # Identical: count, content_hash and per-chunk updated_at (no re-embed).
    assert after == before


# ---------------------------------------------------------------------------
# A deleted path in the change set removes only its chunks
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reindex_deleted_path_removes_only_its_chunks(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="inc-c", project_id=project_id
    )
    files = _write_docs_tree(tmp_path)
    await _run_full_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    removed_rel = "01-overview/README.md"
    removed_doc_id = internal_doc_id(project_id, removed_rel)
    survivor_rel = "index.md"
    survivor_doc_id = internal_doc_id(project_id, survivor_rel)

    before_survivor = await _doc_fingerprint(migrations_pg_dsn, survivor_doc_id)
    assert (await _doc_fingerprint(migrations_pg_dsn, removed_doc_id))[0] > 0

    # Delete the file from disk and feed its path as the only change.
    (tmp_path / "docs" / removed_rel).unlink()

    result = await _run_reindex(
        admin_database_url,
        project_id=project_id,
        tenant_id=tenant_id,
        docs_root=tmp_path,
        changed_paths=[f"docs/{removed_rel}"],
    )

    assert result.removed == [removed_rel]
    assert result.ingested == []
    assert result.skipped == []

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n_chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", removed_doc_id
        )
        deleted_at = await conn.fetchval(
            "SELECT deleted_at FROM documents WHERE id = $1", removed_doc_id
        )
        n_live_docs = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE kb_id = $1 AND deleted_at IS NULL",
            internal_docs_kb_id(project_id),
        )
    finally:
        await conn.close()

    assert n_chunks == 0
    assert deleted_at is not None
    # Only the one doc was removed; the rest stay live.
    assert n_live_docs == len(files) - 1
    # The survivor (not in the change set) is byte-for-byte untouched.
    assert await _doc_fingerprint(migrations_pg_dsn, survivor_doc_id) == before_survivor


# ---------------------------------------------------------------------------
# A mixed change set: new file + edit + delete + non-markdown noise
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reindex_mixed_change_set(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_id = uuid4()
    project_id = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_id, tenant_slug="inc-d", project_id=project_id
    )
    _write_docs_tree(tmp_path)
    await _run_full_sync(
        admin_database_url, project_id=project_id, tenant_id=tenant_id, docs_root=tmp_path
    )

    docs = tmp_path / "docs"
    # New markdown file.
    new_rel = "04-reference/api.md"
    (docs / "04-reference").mkdir(parents=True)
    (docs / new_rel).write_text("# API\n\nEndpoints.\n", encoding="utf-8")
    # Edit an existing file.
    edited_rel = "03-guides/setup.md"
    (docs / edited_rel).write_text("# Setup\n\nNew install steps entirely.\n", encoding="utf-8")
    # Delete an existing file.
    deleted_rel = "01-overview/README.md"
    (docs / deleted_rel).unlink()

    result = await _run_reindex(
        admin_database_url,
        project_id=project_id,
        tenant_id=tenant_id,
        docs_root=tmp_path,
        changed_paths=[
            f"docs/{new_rel}",
            f"docs/{edited_rel}",
            f"docs/{deleted_rel}",
            "src/api_server/main.py",  # non-markdown → ignored
            "README.md",  # markdown but not under docs/ → no such doc, treated as relpath
        ],
    )

    assert sorted(result.ingested) == sorted([new_rel, edited_rel])
    assert result.removed == [deleted_rel]
    # The non-markdown path is ignored; the top-level README.md (not under
    # docs/) is markdown so it is processed but absent on disk → no-op
    # removal (never ingested) so it appears in neither ingested nor removed.
    assert "src/api_server/main.py" in result.ignored
    assert result.skipped == []

    new_doc_id = internal_doc_id(project_id, new_rel)
    assert (await _doc_fingerprint(migrations_pg_dsn, new_doc_id))[0] > 0


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_reindex_never_leaks_across_tenants(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    """An incremental reindex of project A must never write under tenant B."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_tables(migrations_pg_dsn)

    tenant_a = uuid4()
    project_a = uuid4()
    tenant_b = uuid4()
    project_b = uuid4()
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_a, tenant_slug="ia", project_id=project_a
    )
    await _seed_tenant_and_project(
        migrations_pg_dsn, tenant_id=tenant_b, tenant_slug="ib", project_id=project_b
    )

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

    # Full sync both, then edit A's file and incrementally reindex ONLY A.
    await _run_full_sync(
        admin_database_url, project_id=project_a, tenant_id=tenant_a, docs_root=root_a
    )
    await _run_full_sync(
        admin_database_url, project_id=project_b, tenant_id=tenant_b, docs_root=root_b
    )
    kb_b = internal_docs_kb_id(project_b)
    before_b_total = await _kb_chunk_total(migrations_pg_dsn, kb_b)

    (root_a / "docs" / "secret-a.md").write_text(
        "# Secret A\n\nTenant A confidential UPDATED.\n", encoding="utf-8"
    )
    await _run_reindex(
        admin_database_url,
        project_id=project_a,
        tenant_id=tenant_a,
        docs_root=root_a,
        changed_paths=["docs/secret-a.md"],
    )

    kb_a = internal_docs_kb_id(project_a)
    assert kb_a != kb_b

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Every KB-A chunk carries tenant_a, none carry tenant_b.
        leak_into_b = await conn.fetchval(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.kb_id = $1 AND c.tenant_id = $2",
            kb_a,
            tenant_b,
        )
        assert leak_into_b == 0
        # Tenant B's content never appears under tenant A.
        b_content_under_a = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE tenant_id = $1 AND content ILIKE '%Tenant B%'",
            tenant_a,
        )
        assert b_content_under_a == 0
    finally:
        await conn.close()

    # KB B is completely untouched by the reindex of A.
    assert await _kb_chunk_total(migrations_pg_dsn, kb_b) == before_b_total


async def _kb_chunk_total(dsn: str, kb_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.kb_id = $1",
            kb_id,
        )
    finally:
        await conn.close()
