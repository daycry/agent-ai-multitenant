"""Idempotent catalog ingestion seed (Plan 06.13 task_06_13_02).

The 6 built-in KBs (:mod:`api_server.seeds.builtin_kbs`) are seeded as
**empty containers** under ``PLATFORM_TENANT_ID``. This seed fills them
with content: for each built-in KB slug it reads the curated markdown
corpus (``seeds/catalog/<slug>.md`` — task_06_13_01), chunks it with a
lightweight markdown chunker, embeds each chunk with an *injectable*
embedder, and persists a ``documents`` row + ``chunks`` rows under
``tenant_id = PLATFORM_TENANT_ID`` and ``kb_id = kb_id_for_slug(slug)``.

Why a build-time seed and not a cron (ADR 0030):

  * The corpus is curated markdown versioned in the repo. It changes
    only when a human edits a ``.md`` and ships a release — there is no
    external source to poll, so a cron would do nothing 99.9 % of the
    time. The seed runs alongside the other built-in seeds.
  * It reuses the existing ``chunks``/``documents`` schema and the same
    ``Embedder`` Protocol the Plan-04 pipeline uses (injectable, so
    tests pass :class:`HashEmbedder` and never touch the network).
  * **No docling-serve**: docling-serve is an external HTTP service that
    is down in CI. For curated markdown a heading/paragraph splitter is
    plenty and keeps the seed offline-testable.

Idempotency:

  * One stable document per KB:
    ``document_id = uuid5(CATALOG_DOC_NAMESPACE, slug)``. Re-running
    upserts that single row instead of creating a new one each time.
  * A SHA-256 of the corpus is stamped on every chunk's metadata
    (``corpus_hash``). On re-run, if the existing chunks already carry
    the current hash, the document is **skipped** (no re-embed, no
    duplication). If the ``.md`` changed, the old chunks are deleted and
    fresh ones inserted (the ``(document_id, ordinal)`` unique
    constraint guarantees no stale duplicates survive).

Caller must hold an ``AsyncSession`` on the BYPASSRLS admin engine (it
writes under the platform tenant) and must run *after*
``seed_builtin_kbs`` so the KB rows exist (the runner enforces order).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.knowledge import Chunk, Document
from api_server.ingestion.embeddings import Embedder, EmbeddingError, OllamaEmbedder
from api_server.seeds import PLATFORM_TENANT_ID
from api_server.seeds.builtin_kbs import BUILTIN_KBS, kb_id_for_slug

logger = structlog.get_logger(__name__)

# Namespace for uuid5("catalog_doc:<slug>") — one stable document id per
# built-in KB. Separate from the KB (0015) and category (0016) namespaces.
CATALOG_DOC_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000017")

# Directory holding the curated corpus (one `<slug>.md` per built-in KB).
CATALOG_DIR: Path = Path(__file__).resolve().parent / "catalog"

# MIME type stamped on the catalog documents.
CATALOG_MIME_TYPE = "text/markdown"

# Storage key is synthetic: the catalog corpus lives in the repo, not in
# MinIO. We still set a stable, descriptive key so the Document row is
# well-formed and a future re-ingest path can find the source.
_CATALOG_STORAGE_KEY_TEMPLATE = "catalog/{slug}.md"

# --- Markdown chunker tunables -------------------------------------------
# A chunk that grows past this many characters is split on paragraph
# boundaries so no single embedding input is unbounded. Curated sections
# are short, so this rarely triggers; it caps pathological cases.
MAX_CHUNK_CHARS = 1500
# Headings (``#`` … ``######``) start a new chunk; the chunker keeps each
# section self-contained, matching how the corpus README tells authors to
# structure the files.
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")


@dataclass(frozen=True)
class CatalogIngestionResult:
    """Per-KB outcome of one :func:`seed_catalog_ingestion` run."""

    slug: str
    kb_id: UUID
    document_id: UUID
    chunks_persisted: int
    skipped: bool  # True when the corpus was unchanged (idempotent no-op)


# ---------------------------------------------------------------------------
# Lightweight markdown chunker
# ---------------------------------------------------------------------------
def chunk_markdown(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split curated markdown into self-contained chunks.

    Strategy (no docling-serve required):

      1. Start a new chunk at every heading (``#`` … ``######``); the
         heading line stays attached to its body so a chunk reads as one
         section.
      2. Within a section, if the accumulated text exceeds ``max_chars``,
         flush on the nearest blank-line (paragraph) boundary so chunks
         stay roughly sized without cutting mid-sentence.

    Returns the list of non-empty, stripped chunk strings in document
    order. Idempotent: same input → same chunks.
    """
    lines = text.splitlines()
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _HEADING_RE.match(line) and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)

    chunks: list[str] = []
    for section in sections:
        chunks.extend(_split_section("\n".join(section), max_chars=max_chars))
    return [c for c in (chunk.strip() for chunk in chunks) if c]


def _split_section(section: str, *, max_chars: int) -> list[str]:
    """Split one section into <=``max_chars`` pieces on paragraph breaks."""
    if len(section) <= max_chars:
        return [section]
    paragraphs = re.split(r"\n\s*\n", section)
    pieces: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > max_chars and buf:
            pieces.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        pieces.append(buf)
    return pieces


def _corpus_hash(text: str) -> str:
    """Stable SHA-256 of the corpus, used as the idempotency token."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
def catalog_document_id_for_slug(slug: str) -> UUID:
    """Deterministic id of the single catalog document for a KB slug."""
    return uuid5(CATALOG_DOC_NAMESPACE, f"catalog_doc:{slug}")


async def seed_catalog_ingestion(
    session: AsyncSession,
    *,
    embedder: Embedder | None = None,
    catalog_dir: Path | None = None,
) -> list[CatalogIngestionResult]:
    """Ingest the curated corpus for every built-in KB. Idempotent.

    Args:
        session: BYPASSRLS admin session (writes under the platform
            tenant). The caller owns the transaction.
        embedder: injectable embedder. Defaults to the real
            :class:`OllamaEmbedder`; tests pass a deterministic fake.
        catalog_dir: override the corpus directory (tests).

    Returns one :class:`CatalogIngestionResult` per KB that has a corpus
    file. KBs without a ``.md`` are skipped silently (logged).
    """
    own_embedder = embedder is None
    active_embedder: Embedder = embedder or OllamaEmbedder()
    base_dir = catalog_dir or CATALOG_DIR

    results: list[CatalogIngestionResult] = []
    try:
        for kb in BUILTIN_KBS:
            md_path = base_dir / f"{kb.slug}.md"
            if not md_path.is_file():
                logger.warning("catalog_ingestion.missing_corpus", slug=kb.slug, path=str(md_path))
                continue
            result = await _ingest_one(
                session,
                slug=kb.slug,
                title=kb.name,
                corpus=md_path.read_text(encoding="utf-8"),
                embedder=active_embedder,
            )
            results.append(result)
    finally:
        if own_embedder:
            await active_embedder.aclose()

    logger.info(
        "catalog_ingestion.completed",
        documents=len(results),
        skipped=sum(1 for r in results if r.skipped),
        chunks=sum(r.chunks_persisted for r in results),
    )
    return results


async def _ingest_one(
    session: AsyncSession,
    *,
    slug: str,
    title: str,
    corpus: str,
    embedder: Embedder,
) -> CatalogIngestionResult:
    """Upsert the document + chunks for one built-in KB. Idempotent."""
    kb_id = kb_id_for_slug(slug)
    document_id = catalog_document_id_for_slug(slug)
    corpus_hash = _corpus_hash(corpus)

    # Skip-existing fast path: if the document already carries chunks with
    # the current corpus hash, this is a no-op (re-run does not duplicate).
    existing_hash = await _existing_corpus_hash(session, document_id)
    if existing_hash == corpus_hash:
        count = await _chunk_count(session, document_id)
        logger.info("catalog_ingestion.skip_unchanged", slug=slug, chunks=count)
        return CatalogIngestionResult(
            slug=slug,
            kb_id=kb_id,
            document_id=document_id,
            chunks_persisted=count,
            skipped=True,
        )

    chunk_texts = chunk_markdown(corpus)
    embeddings = await _embed(embedder, chunk_texts)

    await _upsert_document(
        session,
        document_id=document_id,
        kb_id=kb_id,
        title=title,
        slug=slug,
        size_bytes=len(corpus.encode("utf-8")),
    )

    # Replace chunks atomically: drop stale rows, insert the fresh set.
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    for ordinal, (content, embedding) in enumerate(zip(chunk_texts, embeddings, strict=True)):
        session.add(
            Chunk(
                tenant_id=PLATFORM_TENANT_ID,
                document_id=document_id,
                ordinal=ordinal,
                content=content,
                embedding=embedding,
                bbox=None,
                metadata_={"corpus_hash": corpus_hash, "source": "catalog", "slug": slug},
            )
        )
    await session.flush()

    logger.info("catalog_ingestion.indexed", slug=slug, chunks=len(chunk_texts))
    return CatalogIngestionResult(
        slug=slug,
        kb_id=kb_id,
        document_id=document_id,
        chunks_persisted=len(chunk_texts),
        skipped=False,
    )


async def _embed(embedder: Embedder, texts: list[str]) -> list[list[float] | None]:
    """Embed in one batch. On embedder failure, persist NULL embeddings
    (BM25 still surfaces the chunks) — same recipe as the Plan-04 pipeline."""
    if not texts:
        return []
    try:
        vectors = await embedder.embed(texts)
        return list(vectors)
    except EmbeddingError as exc:
        logger.warning("catalog_ingestion.embedder_failed", error=str(exc))
        return [None] * len(texts)


async def _existing_corpus_hash(session: AsyncSession, document_id: UUID) -> str | None:
    """Corpus hash stamped on the existing chunks, or None if no chunks."""
    row = await session.execute(
        select(Chunk.metadata_).where(Chunk.document_id == document_id).limit(1)
    )
    meta = row.scalar_one_or_none()
    if meta is None:
        return None
    value = meta.get("corpus_hash")
    return value if isinstance(value, str) else None


async def _chunk_count(session: AsyncSession, document_id: UUID) -> int:
    rows = await session.execute(select(Chunk.id).where(Chunk.document_id == document_id))
    return len(rows.scalars().all())


async def _upsert_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    kb_id: UUID,
    title: str,
    slug: str,
    size_bytes: int,
) -> None:
    """Insert or refresh the single catalog document for this KB."""
    storage_key = _CATALOG_STORAGE_KEY_TEMPLATE.format(slug=slug)
    now = datetime.now(tz=UTC)
    existing = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Document(
                id=document_id,
                tenant_id=PLATFORM_TENANT_ID,
                kb_id=kb_id,
                title=title,
                source_filename=f"{slug}.md",
                source_mime_type=CATALOG_MIME_TYPE,
                source_storage_key=storage_key,
                source_size_bytes=size_bytes,
                status="indexed",
                page_count=0,
                indexed_at=now,
            )
        )
    else:
        existing.title = title
        existing.source_size_bytes = size_bytes
        existing.status = "indexed"
        existing.indexed_at = now
        existing.deleted_at = None
        existing.error_message = None
    await session.flush()


__all__ = [
    "CATALOG_DIR",
    "CATALOG_DOC_NAMESPACE",
    "MAX_CHUNK_CHARS",
    "CatalogIngestionResult",
    "catalog_document_id_for_slug",
    "chunk_markdown",
    "seed_catalog_ingestion",
]
