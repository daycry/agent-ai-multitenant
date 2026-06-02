"""Integration test for the WebScorpo KB seed (task_demo_ws_03).

Exercises the KB half of ``scripts/setup_webscorpo.py`` against a REAL migrated
PostgreSQL (the throwaway ``agentic_platform_test`` DB the integration conftest
builds). Asserts that after running the seed:

  * one ``team_shared`` KB ("WebScorpo — Conocimiento del equipo") exists,
    carries the 10 ``team/*.md`` documents, and is granted to the project
    (``kb_projects``) AND to all 10 team agents (``agent_knowledge_bases``);
  * each of the 10 agents has its own ``private`` KB granted to it (and to no
    other agent), carrying its single role document;
  * everything is tenant-scoped (KBs/documents/chunks/grants all carry the
    Mediapro ``tenant_id``);
  * ingestion does NOT crash when the embedder is unavailable — chunks are
    persisted with NULL embeddings and ``embeddings_deferred`` is True;
  * with a working (fake) embedder, chunks carry real embedding vectors;
  * re-running is idempotent — KB/document/chunk counts do not grow.

The embedder is injected (``HashEmbedder`` / ``embedder_ok=False``) so the test
never touches the network and the deterministic assertions hold regardless of
whether Ollama is reachable in the dev stack.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# Make ``scripts/`` importable (mirrors test_setup_webscorpo_entities.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import setup_webscorpo as ws  # noqa: E402  (import after sys.path tweak)

# The team KB must carry exactly the 10 team-shared corpus docs (analysis §8).
_EXPECTED_TEAM_DOCS = 10
_EXPECTED_AGENTS = 10


@pytest.fixture()
def migrated_db(alembic_config, admin_database_url: str):
    """Upgrade the throwaway test DB to head + grant the app role. Yields the
    admin (BYPASSRLS) URL the seed writes through."""
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    yield admin_database_url


async def _run_full_seed(admin_url: str, *, embedder_ok: bool, use_fake: bool) -> ws.SeedResult:
    """Run entities + KB seed once in one transaction, with the embedder
    injected so the test is deterministic and offline.

    ``use_fake`` injects a :class:`HashEmbedder` (real, NULL-free vectors);
    ``embedder_ok=False`` with no fake exercises the graceful-degradation path
    (documents persisted, embeddings deferred to NULL).
    """
    from api_server.ingestion.embeddings import HashEmbedder

    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            embedder = HashEmbedder() if use_fake else None
            return await ws.seed_webscorpo(session, embedder=embedder, embedder_ok=embedder_ok)
    finally:
        await engine.dispose()


async def _scalar(admin_url: str, sql: str, params: dict[str, object]) -> object:
    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            return (await session.execute(text(sql), params)).scalar_one()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Team-shared KB: 10 docs + granted to project + all agents, tenant-scoped
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_team_kb_has_ten_docs_and_is_granted(migrated_db) -> None:
    result = await _run_full_seed(migrated_db, embedder_ok=True, use_fake=True)
    tid = ws.tenant_id()
    tkb = ws.team_kb_id()
    assert result.team_kb_id == tkb

    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            kb = (
                await session.execute(
                    text(
                        "SELECT tenant_id, name FROM knowledge_bases"
                        " WHERE id = :id AND deleted_at IS NULL"
                    ),
                    {"id": str(tkb)},
                )
            ).first()
            assert kb is not None
            assert kb.name == "WebScorpo — Conocimiento del equipo"
            # tenant-scoped under Mediapro.
            assert UUID(str(kb.tenant_id)) == tid

            # The 10 team-shared documents, all tenant-scoped + indexed.
            docs = (
                await session.execute(
                    text(
                        "SELECT tenant_id, status FROM documents"
                        " WHERE kb_id = :kb AND deleted_at IS NULL"
                    ),
                    {"kb": str(tkb)},
                )
            ).all()
            assert len(docs) == _EXPECTED_TEAM_DOCS
            for d in docs:
                assert UUID(str(d.tenant_id)) == tid
                assert d.status == "indexed"

            # Granted to the project (kb_projects), tenant-scoped.
            kbp = (
                await session.execute(
                    text(
                        "SELECT tenant_id FROM kb_projects"
                        " WHERE kb_id = :kb AND project_id = :pid"
                    ),
                    {"kb": str(tkb), "pid": str(ws.project_id())},
                )
            ).first()
            assert kbp is not None
            assert UUID(str(kbp.tenant_id)) == tid

            # Granted to ALL 10 team agents (agent_knowledge_bases).
            granted_agents = {
                UUID(str(r.agent_id))
                for r in (
                    await session.execute(
                        text("SELECT agent_id FROM agent_knowledge_bases WHERE kb_id = :kb"),
                        {"kb": str(tkb)},
                    )
                ).all()
            }
            assert granted_agents == set(result.agent_ids.values())
            assert len(granted_agents) == _EXPECTED_AGENTS
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Per-agent private KBs: one each, granted only to its own agent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_each_agent_has_its_private_kb(migrated_db) -> None:
    result = await _run_full_seed(migrated_db, embedder_ok=True, use_fake=True)
    tid = ws.tenant_id()
    assert len(result.agent_kb_ids) == _EXPECTED_AGENTS

    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            for slug, aid in result.agent_ids.items():
                akb = result.agent_kb_ids[slug]
                # The private KB exists, tenant-scoped, NOT the team KB.
                kb = (
                    await session.execute(
                        text(
                            "SELECT tenant_id, name FROM knowledge_bases"
                            " WHERE id = :id AND deleted_at IS NULL"
                        ),
                        {"id": str(akb)},
                    )
                ).first()
                assert kb is not None, f"{slug} missing private KB"
                assert UUID(str(kb.tenant_id)) == tid
                assert akb != result.team_kb_id

                # It carries the single role document.
                n_docs = int(
                    (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM documents"
                                " WHERE kb_id = :kb AND deleted_at IS NULL"
                            ),
                            {"kb": str(akb)},
                        )
                    ).scalar_one()
                )
                assert n_docs == 1, f"{slug} private KB should have 1 role doc"

                # Granted to its own agent.
                own_grant = (
                    await session.execute(
                        text(
                            "SELECT 1 FROM agent_knowledge_bases"
                            " WHERE kb_id = :kb AND agent_id = :aid"
                        ),
                        {"kb": str(akb), "aid": str(aid)},
                    )
                ).first()
                assert own_grant is not None, f"{slug} not granted its own private KB"

                # NOT granted to any OTHER agent (private = single grant).
                grant_count = int(
                    (
                        await session.execute(
                            text("SELECT count(*) FROM agent_knowledge_bases WHERE kb_id = :kb"),
                            {"kb": str(akb)},
                        )
                    ).scalar_one()
                )
                assert grant_count == 1, f"{slug} private KB granted to >1 agent"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Graceful degradation: ingestion must not crash without an embedder
# ---------------------------------------------------------------------------
async def _wipe_kb_chunks(admin_url: str, kb_id: str) -> None:
    """Drop all chunks of a KB so the next seed re-ingests from scratch.

    The integration DB is session-scoped: earlier tests may have stamped real
    embeddings + the corpus hash, which would trip the idempotent skip-fast
    path. Wiping the chunks forces a fresh ingestion so this test exercises the
    embedder-unavailable branch deterministically regardless of test order.
    """
    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await session.execute(
                text(
                    "DELETE FROM chunks WHERE document_id IN"
                    " (SELECT id FROM documents WHERE kb_id = :kb)"
                ),
                {"kb": kb_id},
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_survives_missing_embedder(migrated_db) -> None:
    # Force a fresh ingestion (clear any chunks an earlier test left behind so
    # the corpus-hash skip-fast path does not mask the degradation branch).
    await _wipe_kb_chunks(migrated_db, str(ws.team_kb_id()))
    # embedder_ok=False, no fake embedder → must persist docs/chunks with NULL
    # embeddings and not raise.
    result = await _run_full_seed(migrated_db, embedder_ok=False, use_fake=False)
    assert result.embeddings_deferred is True
    # The team KB still has its 10 documents (content persisted regardless).
    n_docs = int(
        await _scalar(  # type: ignore[arg-type]
            migrated_db,
            "SELECT count(*) FROM documents WHERE kb_id = :kb AND deleted_at IS NULL",
            {"kb": str(ws.team_kb_id())},
        )
    )
    assert n_docs == _EXPECTED_TEAM_DOCS

    # Chunks exist but every embedding is NULL (deferred to re-index).
    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            total_chunks = int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
                            " WHERE d.kb_id = :kb"
                        ),
                        {"kb": str(ws.team_kb_id())},
                    )
                ).scalar_one()
            )
            assert total_chunks > 0, "documents were not chunked"
            null_embeddings = int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
                            " WHERE d.kb_id = :kb AND c.embedding IS NULL"
                        ),
                        {"kb": str(ws.team_kb_id())},
                    )
                ).scalar_one()
            )
            assert null_embeddings == total_chunks, "embeddings should all be deferred (NULL)"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_embeddings_written_with_working_embedder(migrated_db) -> None:
    # Force a fresh ingestion so the (fake) embedder re-embeds regardless of
    # what an earlier test left in the session-scoped DB.
    await _wipe_kb_chunks(migrated_db, str(ws.team_kb_id()))
    await _run_full_seed(migrated_db, embedder_ok=True, use_fake=True)
    engine = create_async_engine(migrated_db)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            non_null = int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
                            " WHERE d.kb_id = :kb AND c.embedding IS NOT NULL"
                        ),
                        {"kb": str(ws.team_kb_id())},
                    )
                ).scalar_one()
            )
            assert non_null > 0, "fake embedder should have written real vectors"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Idempotency: re-running never duplicates KBs / documents / chunks
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kb_seed_is_idempotent(migrated_db) -> None:
    await _run_full_seed(migrated_db, embedder_ok=True, use_fake=True)

    tkb = str(ws.team_kb_id())
    kbs_1 = int(
        await _scalar(  # type: ignore[arg-type]
            migrated_db,
            "SELECT count(*) FROM knowledge_bases WHERE tenant_id = :t AND deleted_at IS NULL",
            {"t": str(ws.tenant_id())},
        )
    )
    docs_1 = int(
        await _scalar(  # type: ignore[arg-type]
            migrated_db,
            "SELECT count(*) FROM documents WHERE kb_id = :kb AND deleted_at IS NULL",
            {"kb": tkb},
        )
    )
    chunks_1 = int(
        await _scalar(  # type: ignore[arg-type]
            migrated_db,
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.kb_id = :kb",
            {"kb": tkb},
        )
    )
    grants_1 = int(
        await _scalar(  # type: ignore[arg-type]
            migrated_db,
            "SELECT count(*) FROM agent_knowledge_bases akb"
            " JOIN agents a ON a.id = akb.agent_id WHERE a.tenant_id = :t",
            {"t": str(ws.tenant_id())},
        )
    )

    # Re-run twice — nothing must grow.
    await _run_full_seed(migrated_db, embedder_ok=True, use_fake=True)
    await _run_full_seed(migrated_db, embedder_ok=True, use_fake=True)

    # 1 team KB + 10 per-agent KBs = 11.
    assert kbs_1 == 11
    assert (
        int(
            await _scalar(  # type: ignore[arg-type]
                migrated_db,
                "SELECT count(*) FROM knowledge_bases WHERE tenant_id = :t AND deleted_at IS NULL",
                {"t": str(ws.tenant_id())},
            )
        )
        == kbs_1
    )
    assert docs_1 == _EXPECTED_TEAM_DOCS
    assert (
        int(
            await _scalar(  # type: ignore[arg-type]
                migrated_db,
                "SELECT count(*) FROM documents WHERE kb_id = :kb AND deleted_at IS NULL",
                {"kb": tkb},
            )
        )
        == docs_1
    )
    assert chunks_1 > 0
    assert (
        int(
            await _scalar(  # type: ignore[arg-type]
                migrated_db,
                "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
                " WHERE d.kb_id = :kb",
                {"kb": tkb},
            )
        )
        == chunks_1
    )
    # team KB grant (10 agents) + 10 private KB grants = 20.
    assert grants_1 == 20
    assert (
        int(
            await _scalar(  # type: ignore[arg-type]
                migrated_db,
                "SELECT count(*) FROM agent_knowledge_bases akb"
                " JOIN agents a ON a.id = akb.agent_id WHERE a.tenant_id = :t",
                {"t": str(ws.tenant_id())},
            )
        )
        == grants_1
    )
