"""Sync a project's ``/docs`` tree into a per-project internal-docs KB
(Plan 07 task_07_09).

Every project gets one **internal-docs Knowledge Base** that mirrors the
canonical ``/docs`` folder of its repository. The viewer (Fase D) and the
project's agents query it for semantic search over the project's own
documentation, alongside the built-in catalog KBs (Plan 06.13) and any
user-uploaded KBs.

Design (lead decision — reuse, do not reinvent):

  * **No parallel schema.** The internal-docs KB is a plain
    :class:`~api_server.db.knowledge.KnowledgeBase` row living under the
    *project's tenant* (NOT ``PLATFORM_TENANT_ID``; it is private project
    data, not catalog). It is **not** ``is_builtin`` and it is granted to
    its project via the existing :class:`KnowledgeBaseProject` junction so
    the viewer surfaces it like any other project KB.
  * **Deterministic id, no DB lookup needed.** The KB id is
    ``uuid5(INTERNAL_DOCS_KB_NAMESPACE, "internal_docs:<project_id>")`` and
    every Document id is
    ``uuid5(INTERNAL_DOC_NAMESPACE, "internal_doc:<project_id>:<relpath>")``.
    Anyone holding the ``project_id`` can recompute these without a query,
    which is what makes the sync idempotent and the marker convention
    sufficient (no extra column / migration).
  * **Reuses the Plan 06.13 markdown chunker + content-hash idempotency**
    (:func:`api_server.seeds.catalog_ingestion.chunk_markdown`). The
    embedder is injectable: production passes the real
    :class:`OllamaEmbedder`, tests pass a deterministic fake (Ollama is
    down in CI).

Idempotency:

  * Re-running with unchanged files is a no-op: each Document stamps a
    SHA-256 ``content_hash`` on every chunk's metadata; if the existing
    chunks already carry the current hash the file is skipped (no re-embed,
    no duplication).
  * A changed file deletes its old chunks and inserts the fresh set (the
    ``(document_id, ordinal)`` unique constraint guarantees no stale rows).
  * A removed ``.md`` soft-deletes its Document (``deleted_at``) and drops
    its chunks (chunks are derived data — hard-deleted, per the model
    contract).

**Two entry points:**

  * :func:`sync_project_docs` — full sync: walk the whole ``/docs`` tree,
    ingest every ``.md``, soft-delete any document whose source vanished.
  * :func:`reindex_changed_docs` (Plan 07 task_07_10) — incremental: take a
    caller-supplied change set (a ``git diff --name-only``) and touch ONLY
    those paths. Cheap for projects with thousands of docs. Reuses the same
    content-hash idempotency, so an unchanged-but-listed file is still a
    no-op.

**Trigger is deferred (Plan 13).** The git-webhook / PR-merge hook that
should call either function after a merge depends on the webhook-dispatcher
app (empty until Plan 13). Both functions are the callables that hook will
invoke; here we expose and test them directly. :func:`changed_markdown_relpaths`
is an optional thin helper that derives the change set from two refs.

The caller owns the transaction and must hold an ``AsyncSession`` scoped to
the project's tenant (RLS sets ``app.tenant_id``) or a BYPASSRLS admin
session writing ``tenant_id = project tenant``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.knowledge import (
    Chunk,
    Document,
    KnowledgeBase,
    KnowledgeBaseProject,
)
from api_server.docs_structure.constants import DOCS_DIRNAME
from api_server.ingestion.embed_client import shared_ollama_embedder
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.seeds.catalog_ingestion import chunk_markdown

logger = structlog.get_logger(__name__)

# --- Deterministic id namespaces -----------------------------------------
# Distinct from the built-in KB (0015), category (0016) and catalog-doc
# (0017) namespaces so an internal-docs id can never collide with a catalog
# id even if a project_id happened to equal a slug-derived value.
INTERNAL_DOCS_KB_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000018")
INTERNAL_DOC_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000019")

# Reserved, stable name/description for the per-project internal-docs KB.
# The name is part of the (tenant_id, name) unique index, so it must be
# stable across re-syncs of the same project.
INTERNAL_DOCS_KB_NAME = "Project internal docs"
INTERNAL_DOCS_KB_DESCRIPTION = (
    "Auto-synced mirror of this project's /docs canonical folder. "
    "Maintained by the docs-sync hook on PR merge (Plan 07)."
)

# MIME type stamped on the synced documents (curated markdown).
INTERNAL_DOCS_MIME_TYPE = "text/markdown"

# Synthetic storage key: the markdown lives in the project's git worktree,
# not in MinIO. A stable, descriptive key keeps the Document row well-formed
# and lets a future re-ingest path locate the source.
_INTERNAL_DOCS_STORAGE_KEY_TEMPLATE = "internal-docs/{project_id}/{relpath}"

# Glob for the markdown files to ingest, relative to ``docs_root``.
_MARKDOWN_GLOB = "**/*.md"

# Suffix (lower-cased) of the files we ingest. Incremental reindex filters
# the caller-supplied changed set down to markdown only — a changed
# ``.png`` or ``.py`` is irrelevant to the docs KB.
_MARKDOWN_SUFFIX = ".md"


@dataclass
class DocSyncResult:
    """Outcome of one :func:`sync_project_docs` run.

    Counts are mutually exclusive per file: a file is either *ingested*
    (new or changed → chunks (re)written), *skipped* (unchanged), or
    *removed* (its on-disk source disappeared → Document soft-deleted).

    Not frozen: :func:`sync_project_docs` accumulates into it as it walks
    the tree, then returns it to the caller.
    """

    kb_id: UUID
    project_id: UUID
    tenant_id: UUID
    kb_created: bool
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    chunks_persisted: int = 0


@dataclass
class IncrementalReindexResult:
    """Outcome of one :func:`reindex_changed_docs` run.

    Mirrors :class:`DocSyncResult` but scoped to the caller-supplied change
    set: only the listed paths are ever touched. Counts are mutually
    exclusive per relpath — a path is either *ingested* (present on disk &
    content changed → chunks rewritten), *skipped* (present but content hash
    unchanged → no-op), or *removed* (no longer on disk → Document
    soft-deleted). ``ignored`` lists caller-supplied paths that were neither
    markdown nor under the docs root (silently dropped, surfaced for
    observability).
    """

    kb_id: UUID
    project_id: UUID
    tenant_id: UUID
    kb_created: bool
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    chunks_persisted: int = 0


def internal_docs_kb_id(project_id: UUID) -> UUID:
    """Deterministic id of a project's internal-docs KB."""
    return uuid5(INTERNAL_DOCS_KB_NAMESPACE, f"internal_docs:{project_id}")


def internal_doc_id(project_id: UUID, relpath: str) -> UUID:
    """Deterministic id of one synced document, keyed by its repo-relative
    POSIX path (so the same file always maps to the same row)."""
    return uuid5(INTERNAL_DOC_NAMESPACE, f"internal_doc:{project_id}:{relpath}")


def _content_hash(text: str) -> str:
    """Stable SHA-256 of a file's content — the idempotency token."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalise_docs_root(docs_root: Path) -> Path:
    """Resolve the directory the markdown walk starts from.

    Accepts either the repo working tree (we then descend into ``docs/``)
    or the ``docs/`` directory itself, so callers need not care which they
    hold.
    """
    candidate = docs_root / DOCS_DIRNAME
    return candidate if candidate.is_dir() else docs_root


async def sync_project_docs(
    session: AsyncSession,
    *,
    project_id: UUID,
    tenant_id: UUID,
    docs_root: Path,
    embedder: Embedder | None = None,
) -> DocSyncResult:
    """Sync ``docs_root``'s markdown into the project's internal-docs KB.

    This is the callable a future git-webhook / PR-merge hook (Plan 13)
    invokes. It is idempotent and safe to re-run.

    Args:
        session: session scoped to ``tenant_id`` (RLS) or a BYPASSRLS admin
            session. The caller owns the transaction (we ``flush`` but never
            ``commit``).
        project_id: the project whose ``/docs`` we mirror.
        tenant_id: the project's tenant — every row we write is scoped to it.
        docs_root: the repo working tree or its ``docs/`` directory. Walked
            recursively for ``*.md`` files.
        embedder: injectable embedder. Defaults to the real
            :class:`OllamaEmbedder`; tests pass a deterministic fake.

    Returns a :class:`DocSyncResult` summarising what changed.
    """
    own_embedder = embedder is None
    # Sobre el cliente httpx COMPARTIDO del proceso (task_prod13_05, perf-9):
    # esto corre en cada sincronización de `docs/`, no una vez por arranque.
    # `aclose()` sobre un cliente inyectado es un no-op, así que el cierre de
    # más abajo ya no puede llevarse por delante el pool de los demás.
    active_embedder: Embedder = embedder or shared_ollama_embedder()
    base_dir = _normalise_docs_root(docs_root)

    try:
        kb_created = await _ensure_internal_docs_kb(
            session, project_id=project_id, tenant_id=tenant_id
        )
        kb_id = internal_docs_kb_id(project_id)

        present_relpaths = _walk_markdown(base_dir)

        result = DocSyncResult(
            kb_id=kb_id,
            project_id=project_id,
            tenant_id=tenant_id,
            kb_created=kb_created,
        )

        # 1) Ingest / skip every file currently on disk.
        for relpath in present_relpaths:
            corpus = (base_dir / relpath).read_text(encoding="utf-8")
            chunks_written, skipped = await _ingest_one(
                session,
                project_id=project_id,
                tenant_id=tenant_id,
                kb_id=kb_id,
                relpath=relpath,
                corpus=corpus,
                embedder=active_embedder,
            )
            if skipped:
                result.skipped.append(relpath)
            else:
                result.ingested.append(relpath)
                result.chunks_persisted += chunks_written

        # 2) Soft-delete documents whose source file vanished.
        removed = await _remove_absent_documents(
            session,
            project_id=project_id,
            kb_id=kb_id,
            tenant_id=tenant_id,
            present_relpaths=present_relpaths,
        )
        result.removed.extend(removed)

        await session.flush()
    finally:
        if own_embedder:
            await active_embedder.aclose()

    logger.info(
        "docs_kb_sync.completed",
        project_id=str(project_id),
        tenant_id=str(tenant_id),
        kb_created=result.kb_created,
        ingested=len(result.ingested),
        skipped=len(result.skipped),
        removed=len(result.removed),
        chunks=result.chunks_persisted,
    )
    return result


async def reindex_changed_docs(
    session: AsyncSession,
    *,
    project_id: UUID,
    tenant_id: UUID,
    docs_root: Path,
    changed_paths: list[str],
    embedder: Embedder | None = None,
) -> IncrementalReindexResult:
    """Re-ingest ONLY the given changed markdown paths (incremental sync).

    Where :func:`sync_project_docs` walks the entire ``/docs`` tree, this
    touches **only** the files the caller names in ``changed_paths`` — the
    "changed since the last commit" set a git-webhook / PR-merge hook
    (Plan 13) computes from a ``git diff --name-only`` between two refs. For
    a project with thousands of docs that turns a full re-embed into a
    handful of file ingests.

    The function is **pure with respect to git**: it never shells out. The
    caller supplies the change set (see :func:`changed_markdown_relpaths`
    for an optional, separately-tested helper that derives it from two
    refs). This keeps the core logic deterministic and offline-testable.

    Each changed path is resolved against ``docs_root`` and:

      * **still on disk** → ingested via the same content-hash idempotent
        path as the full sync, so an *unchanged-but-listed* file (its hash
        already matches the stored chunks) is a no-op (``skipped``);
      * **gone from disk** → its Document is soft-deleted and its chunks
        dropped (``removed``).

    Caller-supplied paths that are not markdown or fall outside the docs
    root are dropped into ``ignored`` (e.g. a changed ``src/*.py`` in the
    same diff). Paths may be repo-relative (``docs/03-guides/x.md``) or
    docs-root-relative (``03-guides/x.md``); both normalise to the same
    relpath, so the same Document id is reached either way.

    Args:
        session: session scoped to ``tenant_id`` (RLS) or a BYPASSRLS admin
            session. The caller owns the transaction.
        project_id: the project whose ``/docs`` we mirror.
        tenant_id: the project's tenant — every row we write is scoped to it.
        docs_root: the repo working tree or its ``docs/`` directory.
        changed_paths: the changed file paths (markdown + anything else; we
            filter). Repo-relative or docs-root-relative.
        embedder: injectable embedder. Defaults to the real
            :class:`OllamaEmbedder`; tests pass a deterministic fake.

    Returns an :class:`IncrementalReindexResult` summarising what changed.
    """
    own_embedder = embedder is None
    # Sobre el cliente httpx COMPARTIDO del proceso (task_prod13_05, perf-9):
    # esto corre en cada sincronización de `docs/`, no una vez por arranque.
    # `aclose()` sobre un cliente inyectado es un no-op, así que el cierre de
    # más abajo ya no puede llevarse por delante el pool de los demás.
    active_embedder: Embedder = embedder or shared_ollama_embedder()
    base_dir = _normalise_docs_root(docs_root)

    try:
        kb_created = await _ensure_internal_docs_kb(
            session, project_id=project_id, tenant_id=tenant_id
        )
        kb_id = internal_docs_kb_id(project_id)

        result = IncrementalReindexResult(
            kb_id=kb_id,
            project_id=project_id,
            tenant_id=tenant_id,
            kb_created=kb_created,
        )

        # De-duplicate while preserving order: a diff can list the same path
        # twice (rename A→B reports both sides) and processing it once is
        # enough — the file is either there or not.
        for relpath in _normalise_changed_relpaths(changed_paths, result):
            abs_path = base_dir / relpath
            if abs_path.is_file():
                corpus = abs_path.read_text(encoding="utf-8")
                chunks_written, skipped = await _ingest_one(
                    session,
                    project_id=project_id,
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    relpath=relpath,
                    corpus=corpus,
                    embedder=active_embedder,
                )
                if skipped:
                    result.skipped.append(relpath)
                else:
                    result.ingested.append(relpath)
                    result.chunks_persisted += chunks_written
            else:
                # Listed but absent on disk → the file was deleted in this
                # change set. Soft-delete its document (no-op if it was never
                # ingested or already removed).
                removed = await _soft_delete_document(
                    session, document_id=internal_doc_id(project_id, relpath)
                )
                if removed:
                    result.removed.append(relpath)

        await session.flush()
    finally:
        if own_embedder:
            await active_embedder.aclose()

    logger.info(
        "docs_kb_sync.reindex_incremental.completed",
        project_id=str(project_id),
        tenant_id=str(tenant_id),
        kb_created=result.kb_created,
        ingested=len(result.ingested),
        skipped=len(result.skipped),
        removed=len(result.removed),
        ignored=len(result.ignored),
        chunks=result.chunks_persisted,
    )
    return result


def _normalise_changed_relpaths(
    changed_paths: list[str], result: IncrementalReindexResult
) -> list[str]:
    """Turn the caller's raw change set into docs-root-relative markdown
    relpaths, de-duplicated and order-preserving.

    Rules:

      * Non-markdown paths are dropped into ``result.ignored``.
      * A leading ``docs/`` (the repo-relative form a git diff emits) is
        stripped so the path is relative to the docs root — matching what
        :func:`_walk_markdown` produces and therefore reaching the same
        deterministic Document id.
      * Backslashes are normalised to forward slashes (a Windows caller may
        hand us ``docs\\x.md``).
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in changed_paths:
        posix = raw.replace("\\", "/").strip().lstrip("/")
        if not posix.lower().endswith(_MARKDOWN_SUFFIX):
            result.ignored.append(raw)
            continue
        relpath = posix
        prefix = f"{DOCS_DIRNAME}/"
        if relpath.startswith(prefix):
            relpath = relpath[len(prefix) :]
        if not relpath or relpath in seen:
            continue
        seen.add(relpath)
        out.append(relpath)
    return out


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------
def _walk_markdown(base_dir: Path) -> list[str]:
    """Return the repo-relative POSIX paths of every ``*.md`` under
    ``base_dir``, sorted for deterministic ordering. Missing dir → []."""
    if not base_dir.is_dir():
        return []
    relpaths: list[str] = []
    for path in base_dir.glob(_MARKDOWN_GLOB):
        if path.is_file():
            relpaths.append(path.relative_to(base_dir).as_posix())
    return sorted(relpaths)


# ---------------------------------------------------------------------------
# KnowledgeBase ensure
# ---------------------------------------------------------------------------
async def _ensure_internal_docs_kb(
    session: AsyncSession, *, project_id: UUID, tenant_id: UUID
) -> bool:
    """Create the per-project internal-docs KB + its project grant if
    missing. Returns True when it had to create the KB row.

    Idempotent: a second call finds the KB and only un-soft-deletes it if a
    prior destructive action had hidden it.
    """
    kb_id = internal_docs_kb_id(project_id)
    existing = (
        await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    ).scalar_one_or_none()

    created = False
    if existing is None:
        session.add(
            KnowledgeBase(
                id=kb_id,
                tenant_id=tenant_id,
                name=INTERNAL_DOCS_KB_NAME,
                description=INTERNAL_DOCS_KB_DESCRIPTION,
                is_builtin=False,
            )
        )
        created = True
    elif existing.deleted_at is not None:
        existing.deleted_at = None

    # Grant the KB to its project (M:N junction) so the viewer surfaces it.
    grant = (
        await session.execute(
            select(KnowledgeBaseProject).where(
                KnowledgeBaseProject.kb_id == kb_id,
                KnowledgeBaseProject.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        session.add(KnowledgeBaseProject(kb_id=kb_id, project_id=project_id, tenant_id=tenant_id))

    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Per-document ingestion
# ---------------------------------------------------------------------------
async def _ingest_one(
    session: AsyncSession,
    *,
    project_id: UUID,
    tenant_id: UUID,
    kb_id: UUID,
    relpath: str,
    corpus: str,
    embedder: Embedder,
) -> tuple[int, bool]:
    """Upsert one document + chunks. Returns ``(chunks_written, skipped)``.

    ``skipped`` is True when the file's content hash matches the chunks
    already stored (idempotent no-op).
    """
    document_id = internal_doc_id(project_id, relpath)
    content_hash = _content_hash(corpus)

    existing_hash = await _existing_content_hash(session, document_id)
    if existing_hash == content_hash:
        # Already up to date — but if a prior run soft-deleted the doc and
        # the file came back unchanged, revive it.
        await _revive_if_deleted(session, document_id=document_id)
        logger.debug("docs_kb_sync.skip_unchanged", relpath=relpath)
        return 0, True

    chunk_texts = chunk_markdown(corpus)
    embeddings = await _embed(embedder, chunk_texts)

    await _upsert_document(
        session,
        document_id=document_id,
        kb_id=kb_id,
        tenant_id=tenant_id,
        project_id=project_id,
        relpath=relpath,
        size_bytes=len(corpus.encode("utf-8")),
    )

    # Replace chunks atomically: drop stale rows, insert the fresh set.
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    for ordinal, (content, embedding) in enumerate(zip(chunk_texts, embeddings, strict=True)):
        session.add(
            Chunk(
                tenant_id=tenant_id,
                document_id=document_id,
                ordinal=ordinal,
                content=content,
                embedding=embedding,
                bbox=None,
                metadata_={
                    "content_hash": content_hash,
                    "source": "internal_docs",
                    "relpath": relpath,
                },
            )
        )
    await session.flush()
    logger.info("docs_kb_sync.indexed", relpath=relpath, chunks=len(chunk_texts))
    return len(chunk_texts), False


async def _embed(embedder: Embedder, texts: list[str]) -> list[list[float] | None]:
    """Embed in one batch. On embedder failure persist NULL embeddings
    (BM25 still surfaces the chunks) — same recipe as the Plan-04 pipeline."""
    if not texts:
        return []
    try:
        vectors = await embedder.embed(texts)
        return list(vectors)
    except EmbeddingError as exc:
        logger.warning("docs_kb_sync.embedder_failed", error=str(exc))
        return [None] * len(texts)


async def _existing_content_hash(session: AsyncSession, document_id: UUID) -> str | None:
    """Content hash stamped on the live document's chunks, or None.

    Returns None if the document has no chunks OR if it is soft-deleted, so
    a revived file is treated as changed and re-chunked from scratch (no
    stale-row risk).
    """
    doc = (
        await session.execute(select(Document.deleted_at).where(Document.id == document_id))
    ).one_or_none()
    if doc is None or doc[0] is not None:
        return None
    row = await session.execute(
        select(Chunk.metadata_).where(Chunk.document_id == document_id).limit(1)
    )
    meta = row.scalar_one_or_none()
    if meta is None:
        return None
    value = meta.get("content_hash")
    return value if isinstance(value, str) else None


async def _revive_if_deleted(session: AsyncSession, *, document_id: UUID) -> None:
    """Clear ``deleted_at`` on a document if it was previously soft-deleted
    (only reachable via the unchanged-hash fast path, which already excludes
    deleted docs — kept defensive for callers that mutate state directly)."""
    doc = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is not None and doc.deleted_at is not None:
        doc.deleted_at = None


async def _upsert_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    kb_id: UUID,
    tenant_id: UUID,
    project_id: UUID,
    relpath: str,
    size_bytes: int,
) -> None:
    """Insert or refresh the single document for one ``.md`` file."""
    storage_key = _INTERNAL_DOCS_STORAGE_KEY_TEMPLATE.format(project_id=project_id, relpath=relpath)
    now = datetime.now(tz=UTC)
    existing = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                kb_id=kb_id,
                title=relpath,
                source_filename=Path(relpath).name,
                source_mime_type=INTERNAL_DOCS_MIME_TYPE,
                source_storage_key=storage_key,
                source_size_bytes=size_bytes,
                status="indexed",
                page_count=0,
                indexed_at=now,
            )
        )
    else:
        existing.title = relpath
        existing.source_size_bytes = size_bytes
        existing.status = "indexed"
        existing.indexed_at = now
        existing.deleted_at = None
        existing.error_message = None
    await session.flush()


async def _remove_absent_documents(
    session: AsyncSession,
    *,
    project_id: UUID,
    kb_id: UUID,
    tenant_id: UUID,
    present_relpaths: list[str],
) -> list[str]:
    """Soft-delete documents whose source ``.md`` no longer exists and drop
    their chunks. Returns the relpaths removed.

    A document is identified by its deterministic id derived from the
    relpath, so we recompute the expected id set from the files on disk and
    soft-delete any live document not in it.
    """
    present_ids = {internal_doc_id(project_id, rp) for rp in present_relpaths}
    live_docs = (
        await session.execute(
            select(Document.id, Document.title).where(
                Document.kb_id == kb_id,
                Document.tenant_id == tenant_id,
                Document.deleted_at.is_(None),
            )
        )
    ).all()

    removed: list[str] = []
    for doc_id, title in live_docs:
        if doc_id in present_ids:
            continue
        await _soft_delete_document(session, document_id=doc_id)
        removed.append(title)
    return removed


async def _soft_delete_document(session: AsyncSession, *, document_id: UUID) -> bool:
    """Drop a document's chunks and soft-delete the document row.

    Chunks are derived data (model contract → hard-deleted); the Document
    keeps a ``deleted_at`` tombstone so the viewer can hide it without
    losing the audit trail. Returns True when a *live* document was found
    and removed, False when there was nothing live to remove (already
    deleted or never existed) — so an incremental caller can distinguish a
    real removal from a no-op.
    """
    doc = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is None or doc.deleted_at is not None:
        return False
    # Source gone: hard-drop chunks (derived data), soft-delete the doc.
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    doc.deleted_at = datetime.now(tz=UTC)
    doc.status = "pending"
    return True


# ---------------------------------------------------------------------------
# Optional thin helper: derive the change set from two git refs
# ---------------------------------------------------------------------------
def changed_markdown_relpaths(repo_path: Path, *, base_ref: str, head_ref: str) -> list[str]:
    """Return the markdown paths that changed between two refs.

    A thin, *optional* convenience for the future webhook/merge hook: it
    shells out to ``git diff --name-only <base>..<head>`` (via the workers'
    audited ``git`` runner) and filters the result to markdown. The core
    :func:`reindex_changed_docs` deliberately does NOT call this — it takes
    the change set as data so it stays pure and offline-testable. This
    helper lives apart precisely so it can be tested separately against a
    real throwaway repo.

    Paths are returned exactly as git emits them (repo-relative POSIX, e.g.
    ``docs/03-guides/x.md``); :func:`reindex_changed_docs` normalises them.
    Deletions are included (``--diff-filter`` is intentionally NOT set) so a
    removed ``.md`` reaches the soft-delete branch.

    Args:
        repo_path: working tree (or bare repo) the diff runs in.
        base_ref: the older ref (e.g. the PR base / previous HEAD).
        head_ref: the newer ref (e.g. the merge commit / new HEAD).
    """
    # Lazy import: keeps the api-server module graph free of the workers
    # package until a caller actually wants the git-backed helper.
    from workers.git_repos import _run_git

    out = _run_git("diff", "--name-only", f"{base_ref}..{head_ref}", cwd=repo_path)
    return [
        line.strip() for line in out.splitlines() if line.strip().lower().endswith(_MARKDOWN_SUFFIX)
    ]


__all__ = [
    "INTERNAL_DOCS_KB_DESCRIPTION",
    "INTERNAL_DOCS_KB_NAME",
    "INTERNAL_DOCS_KB_NAMESPACE",
    "INTERNAL_DOC_NAMESPACE",
    "DocSyncResult",
    "IncrementalReindexResult",
    "changed_markdown_relpaths",
    "internal_doc_id",
    "internal_docs_kb_id",
    "reindex_changed_docs",
    "sync_project_docs",
]
