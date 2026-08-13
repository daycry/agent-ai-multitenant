"""Shared seed helpers for Plan 04 Fase D integration tests.

Spins up a tenant + project + KB + 4 documents with 4 chunks each so
every BM25 / vector / hybrid test starts from the same fixture. Each
chunk carries a deterministic 768-d embedding via `HashEmbedder`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg
from api_server.ingestion.embeddings import HashEmbedder

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_DOCUMENT_FIXTURES: list[dict[str, Any]] = [
    {
        "title": "asyncpg manual",
        "chunks": [
            "Project uses asyncpg, not psycopg3, for PostgreSQL access.",
            "Connection pooling is configured in db/session.py.",
            "Migrations run under the migrations_user role with BYPASSRLS.",
            "Every query passes through SQLAlchemy 2.x async sessions.",
        ],
    },
    {
        "title": "RAG architecture",
        "chunks": [
            "RAG combines BM25 text search with vector similarity.",
            "Reciprocal Rank Fusion merges the two ranked lists.",
            "The reranker is bge-reranker-v2-m3 running locally.",
            "Documents land in MinIO then pass through Docling.",
        ],
    },
    {
        "title": "Frontend conventions",
        "chunks": [
            "Admin panel is Next.js 14 with App Router.",
            "Tailwind for styling, shadcn/ui primitives for components.",
            "TanStack Query owns server state; no Redux.",
            "Playwright drives E2E with the fake WebSocket pattern.",
        ],
    },
    {
        "title": "Empty playbook",
        "chunks": [
            "On Fridays we never deploy unless an incident demands it.",
            "Use the kanban board to track work in progress.",
            "Plans drive everything; ad-hoc tasks live in the conversation.",
        ],
    },
]


async def seed_rag_corpus(dsn: str) -> dict[str, Any]:
    """Seed an isolated tenant + project + KB + 4 docs with chunks
    and pre-computed embeddings. Returns the ids tests need to drive
    queries."""
    tenant_id = uuid4()
    project_id = uuid4()
    other_project_id = uuid4()
    kb_id = uuid4()
    embedder = HashEmbedder()

    doc_ids: list[UUID] = [uuid4() for _ in _DOCUMENT_FIXTURES]
    chunk_specs: list[tuple[UUID, UUID, int, str, list[float]]] = []
    for doc_idx, fixture in enumerate(_DOCUMENT_FIXTURES):
        vectors = await embedder.embed(fixture["chunks"])
        for ord_idx, (text, vec) in enumerate(zip(fixture["chunks"], vectors, strict=True)):
            chunk_specs.append((uuid4(), doc_ids[doc_idx], ord_idx, text, vec))

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant RAG",
            "tenant-rag",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-rag",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            project_id,
            tenant_id,
            "Project Granted",
            other_project_id,
            tenant_id,
            "Project Ungranted",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            kb_id,
            tenant_id,
            "RAG KB",
        )
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES ($1, $2, $3)",
            kb_id,
            project_id,
            tenant_id,
        )
        for doc_id, fixture in zip(doc_ids, _DOCUMENT_FIXTURES, strict=True):
            await conn.execute(
                "INSERT INTO documents"
                " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
                "  source_storage_key, source_size_bytes, status)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'indexed')",
                doc_id,
                tenant_id,
                kb_id,
                fixture["title"],
                f"{fixture['title']}.md",
                "text/markdown",
                f"kb/{tenant_id}/{kb_id}/{doc_id}/source.md",
                1000,
            )
        for chunk_id, doc_id, ordinal, text, vec in chunk_specs:
            vec_str = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
            await conn.execute(
                "INSERT INTO chunks"
                " (id, tenant_id, document_id, ordinal, content, embedding)"
                " VALUES ($1, $2, $3, $4, $5, $6::vector)",
                chunk_id,
                tenant_id,
                doc_id,
                ordinal,
                text,
                vec_str,
            )
    finally:
        await conn.close()

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "other_project_id": other_project_id,
        "kb_id": kb_id,
        "document_ids": doc_ids,
        "chunks_by_content": {text: cid for cid, _, _, text, _ in chunk_specs},
    }
