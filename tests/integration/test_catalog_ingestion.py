"""Integration tests for the catalog ingestion seed (Plan 06.13
task_06_13_02).

Drives :func:`seed_catalog_ingestion` end-to-end against the real
Postgres schema using a deterministic :class:`HashEmbedder` (no network,
no docling-serve, no Ollama). Asserts:

  * each built-in KB ends up with >0 chunks under ``PLATFORM_TENANT_ID``;
  * every persisted chunk carries ``tenant_id == PLATFORM_TENANT_ID``;
  * running the seed twice does NOT increase the chunk count
    (idempotency — the second run is a skip-unchanged no-op);
  * editing the corpus re-chunks (delete-and-reinsert, no stale rows).

Uses ``admin_database_url`` (BYPASSRLS) to seed and inspect, mirroring
``test_docling_ingestion.py`` / ``test_promote_to_kb.py``.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from api_server.ingestion.embeddings import HashEmbedder
from api_server.seeds import PLATFORM_TENANT_ID
from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
from api_server.seeds.builtin_kbs import BUILTIN_KBS, kb_id_for_slug, seed_builtin_kbs
from api_server.seeds.catalog_ingestion import (
    catalog_document_id_for_slug,
    chunk_markdown,
    seed_catalog_ingestion,
)
from api_server.seeds.platform import ensure_platform_tenant
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _reset_and_seed_kbs(dsn: str) -> None:
    """Truncate KB tables and seed the platform tenant + built-in KBs.

    The catalog seed needs the KB rows to exist first (FK on
    ``documents.kb_id``); the project-template-dependent seeds are not
    needed for this test.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " kb_categories, organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _open_admin_session(admin_database_url: str):
    engine = create_async_engine(admin_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory()


async def _seed_prerequisites(session) -> None:
    await ensure_platform_tenant(session)
    await seed_builtin_kb_categories(session)
    await seed_builtin_kbs(session)


async def _total_chunk_count(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE tenant_id = $1", PLATFORM_TENANT_ID
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Happy path + idempotency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_catalog_seed_populates_each_builtin_kb(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_and_seed_kbs(migrations_pg_dsn)

    engine, session = await _open_admin_session(admin_database_url)
    try:
        await _seed_prerequisites(session)
        results = await seed_catalog_ingestion(session, embedder=HashEmbedder())
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    # One result per built-in KB that has a corpus file (all 6 do).
    assert len(results) == len(BUILTIN_KBS)
    assert all(not r.skipped for r in results)
    assert all(r.chunks_persisted > 0 for r in results)

    # Each built-in KB has >0 chunks under the platform tenant.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        for kb in BUILTIN_KBS:
            kb_id = kb_id_for_slug(kb.slug)
            document_id = catalog_document_id_for_slug(kb.slug)
            n_docs = await conn.fetchval(
                "SELECT count(*) FROM documents"
                " WHERE kb_id = $1 AND tenant_id = $2 AND status = 'indexed'",
                kb_id,
                PLATFORM_TENANT_ID,
            )
            assert n_docs == 1, kb.slug
            n_chunks = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
            )
            assert n_chunks > 0, kb.slug
            # Every chunk is tenant-scoped to the platform tenant.
            n_wrong_tenant = await conn.fetchval(
                "SELECT count(*) FROM chunks" " WHERE document_id = $1 AND tenant_id <> $2",
                document_id,
                PLATFORM_TENANT_ID,
            )
            assert n_wrong_tenant == 0, kb.slug
            # Embeddings landed (HashEmbedder never returns NULL).
            n_embedded = await conn.fetchval(
                "SELECT count(*) FROM chunks" " WHERE document_id = $1 AND embedding IS NOT NULL",
                document_id,
            )
            assert n_embedded == n_chunks, kb.slug
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_catalog_seed_is_idempotent(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Re-running the seed must not change the chunk count."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_and_seed_kbs(migrations_pg_dsn)

    engine, session = await _open_admin_session(admin_database_url)
    try:
        await _seed_prerequisites(session)
        first = await seed_catalog_ingestion(session, embedder=HashEmbedder())
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    count_after_first = await _total_chunk_count(migrations_pg_dsn)
    assert count_after_first > 0
    assert sum(r.chunks_persisted for r in first) == count_after_first

    # Second run on a fresh session — must be a skip-unchanged no-op.
    engine, session = await _open_admin_session(admin_database_url)
    try:
        second = await seed_catalog_ingestion(session, embedder=HashEmbedder())
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    count_after_second = await _total_chunk_count(migrations_pg_dsn)
    assert count_after_second == count_after_first
    assert all(r.skipped for r in second)
    # Document ids are stable across runs (no duplicate documents).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n_docs = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE tenant_id = $1", PLATFORM_TENANT_ID
        )
        assert n_docs == len(BUILTIN_KBS)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_editing_corpus_rechunks_without_stale_rows(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str, tmp_path: Path
) -> None:
    """A changed corpus deletes the old chunks and inserts the new set —
    re-running with the same edited content is then idempotent again."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _reset_and_seed_kbs(migrations_pg_dsn)

    # Build a one-KB corpus dir whose file matches a real built-in slug.
    slug = BUILTIN_KBS[0].slug
    corpus_dir = tmp_path / "catalog"
    corpus_dir.mkdir()
    short = corpus_dir / f"{slug}.md"
    short.write_text("# A\n\nfirst section.\n", encoding="utf-8")

    document_id = catalog_document_id_for_slug(slug)

    engine, session = await _open_admin_session(admin_database_url)
    try:
        await _seed_prerequisites(session)
        await seed_catalog_ingestion(session, embedder=HashEmbedder(), catalog_dir=corpus_dir)
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n_short = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
        )
    finally:
        await conn.close()
    assert n_short == 1

    # Edit the corpus: more sections → more chunks.
    short.write_text(
        "# A\n\nfirst section.\n\n# B\n\nsecond section.\n\n# C\n\nthird.\n",
        encoding="utf-8",
    )
    engine, session = await _open_admin_session(admin_database_url)
    try:
        results = await seed_catalog_ingestion(
            session, embedder=HashEmbedder(), catalog_dir=corpus_dir
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    assert results[0].skipped is False
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n_long = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
        )
        # No stale rows survived the re-chunk.
        ordinals = await conn.fetch(
            "SELECT ordinal FROM chunks WHERE document_id = $1 ORDER BY ordinal",
            document_id,
        )
    finally:
        await conn.close()
    assert n_long == 3
    assert [r["ordinal"] for r in ordinals] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Unit-ish: the chunker itself (no DB)
# ---------------------------------------------------------------------------
def test_chunk_markdown_splits_on_headings() -> None:
    text = "# Title\n\nintro paragraph.\n\n## Section A\n\nbody a.\n\n## Section B\n\nbody b.\n"
    chunks = chunk_markdown(text)
    assert len(chunks) == 3
    assert chunks[0].startswith("# Title")
    assert chunks[1].startswith("## Section A")
    assert chunks[2].startswith("## Section B")
    assert all(c.strip() for c in chunks)


def test_chunk_markdown_caps_long_sections() -> None:
    # One heading, a body far larger than the cap, split on paragraphs.
    paras = "\n\n".join(f"paragraph {i} " + "x" * 400 for i in range(10))
    text = f"# Big\n\n{paras}\n"
    chunks = chunk_markdown(text, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 + 200 for c in chunks)  # slack for the join overhead


def test_chunk_markdown_ignores_empty_input() -> None:
    assert chunk_markdown("") == []
    assert chunk_markdown("\n\n   \n") == []


def test_catalog_document_id_is_stable() -> None:
    a = catalog_document_id_for_slug("python-fastapi-conventions")
    b = catalog_document_id_for_slug("python-fastapi-conventions")
    c = catalog_document_id_for_slug("node-express-conventions")
    assert isinstance(a, UUID)
    assert a == b
    assert a != c
