"""Filesystem + search primitives for the docs viewer (Plan 07 task_07_D_api).

Two concerns, kept apart from the router so they stay pure and unit-testable:

  1. **Filesystem** — resolve a project's ``docs/`` directory, walk it into a
     tree of canonical folders → ``.md`` files, and read one file's raw
     markdown by repo-relative path. Every path the caller supplies is
     validated against traversal (``..``, absolute paths, drive letters) so a
     malicious ``path=../../secrets`` can never escape the project's docs
     root.

  2. **Search** — turn the chunk ids :func:`api_server.rag.search.bm25_chunks`
     returns (ranked, KB-visibility-filtered) into ranked hits carrying a
     snippet + the source doc's repo-relative path. The chunks come from the
     project's internal-docs KB (Fase C), so search and the tree/content
     surfaces agree on the same ``relpath``.

The **docs root is injectable**. Production derives it from
``settings.data_root`` via :func:`project_docs_root` (the worktree-tree
convention lives in :mod:`workers.git_repos`); tests pass a tmp dir directly.
Nothing here touches the network or a real git worktree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.docs_structure.constants import CANONICAL_DOC_FOLDER_NAMES, DOCS_DIRNAME
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.rag.search import bm25_chunks, vector_chunks

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Named constants (no magic numbers — project config principle)
# ---------------------------------------------------------------------------
# Suffix of the files the viewer surfaces. The canonical /docs tree is
# markdown-only; anything else (images, .py) is intentionally hidden.
MARKDOWN_SUFFIX = ".md"

# How many ranked chunks full-text search returns by default, and the hard
# cap a caller may request via ``?limit=``. Kept small: the viewer's search
# box wants a tight, relevant list, not a full dump.
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100

# Max characters of a chunk's content surfaced as a result snippet. A chunk
# can be a whole section; the snippet is a preview, the viewer opens the doc
# for the full text.
SNIPPET_MAX_CHARS = 280


class DocsViewerError(Exception):
    """Base class for docs-viewer service errors (translated to HTTP by the
    router; never leaked raw to the client)."""


class PathTraversalError(DocsViewerError):
    """Raised when a caller-supplied doc path tries to escape the docs root
    (``..`` segment, absolute path, drive letter, or NUL byte)."""


class DocNotFoundError(DocsViewerError):
    """Raised when a requested ``.md`` does not exist under the docs root."""


# ---------------------------------------------------------------------------
# Tree data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DocTreeFile:
    """One ``.md`` file in the tree."""

    name: str
    relpath: str
    size_bytes: int


@dataclass(frozen=True)
class DocTreeFolder:
    """One folder of the canonical structure (or a nested sub-folder)."""

    name: str
    relpath: str
    folders: list[DocTreeFolder] = field(default_factory=list)
    files: list[DocTreeFile] = field(default_factory=list)


@dataclass(frozen=True)
class DocTree:
    """The whole doc tree for one project."""

    project_id: UUID
    folders: list[DocTreeFolder] = field(default_factory=list)
    files: list[DocTreeFile] = field(default_factory=list)


@dataclass(frozen=True)
class DocContent:
    """One doc's raw markdown content + its location."""

    project_id: UUID
    relpath: str
    content: str
    size_bytes: int


@dataclass(frozen=True)
class DocSearchHit:
    """One ranked full-text search hit."""

    chunk_id: UUID
    document_id: UUID
    relpath: str | None
    ordinal: int
    rank: int
    snippet: str


@dataclass(frozen=True)
class DocSemanticHit:
    """One ranked semantic (vector) search hit.

    Mirrors :class:`DocSearchHit` but carries a cosine ``score`` in
    ``[0.0, 1.0]`` (higher = more similar) instead of a BM25 ``rank``-only
    ordering. ``rank`` is still surfaced (1-based vector order) so the viewer
    can show position; ``score`` is the cosine similarity for relevance bars.
    """

    chunk_id: UUID
    document_id: UUID
    relpath: str | None
    ordinal: int
    rank: int
    score: float
    snippet: str


# ---------------------------------------------------------------------------
# Docs root resolution (injectable; production default derives from settings)
# ---------------------------------------------------------------------------
def project_docs_root(data_root: Path | str, *, tenant_id: UUID, project_id: UUID) -> Path:
    """Resolve the on-disk ``docs/`` directory for one project.

    Production default under ``settings.data_root`` (the persistent filesystem
    the worktrees live on, per :mod:`workers.git_repos`). UUIDs key the path
    rather than slugs because the ``Project`` row carries no stable slug and
    UUIDs are always on hand; the leaf ``docs/`` matches the canonical folder
    name :data:`api_server.docs_structure.constants.DOCS_DIRNAME`.

    Tests bypass this entirely by passing their own tmp ``docs_root`` to
    :func:`read_doc_tree` / :func:`read_doc_content`.
    """
    return (
        Path(data_root)
        / "projects"
        / str(tenant_id)
        / str(project_id)
        / "docs-mirror"
        / DOCS_DIRNAME
    )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------
def _safe_relpath(relpath: str) -> PurePosixPath:
    """Validate + normalise a caller-supplied repo-relative doc path.

    Rejects anything that could escape the docs root:
      * empty / whitespace-only,
      * a NUL byte,
      * an absolute path or a Windows drive/UNC prefix,
      * any ``..`` segment (checked on the raw input *before* normalisation,
        so ``a/../../b`` can never collapse into an escape),
      * a non-``.md`` suffix.

    Returns the normalised POSIX path on success; raises
    :class:`PathTraversalError` (or :class:`DocNotFoundError` for a bad
    suffix) otherwise.
    """
    if not relpath or not relpath.strip():
        raise PathTraversalError("empty doc path")
    if "\x00" in relpath:
        raise PathTraversalError("doc path contains NUL byte")

    # Normalise separators so a Windows-style ``a\b.md`` is handled uniformly.
    candidate = relpath.replace("\\", "/").strip()

    # Absolute paths and drive letters (``/x``, ``C:/x``) are rejected up
    # front — they would escape the root regardless of segments.
    if candidate.startswith("/") or (len(candidate) >= 2 and candidate[1] == ":"):
        raise PathTraversalError("absolute doc path is not allowed")

    parts = [p for p in candidate.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathTraversalError("doc path may not contain '..'")
    if not parts:
        raise PathTraversalError("empty doc path")

    posix = PurePosixPath(*parts)
    if posix.suffix.lower() != MARKDOWN_SUFFIX:
        raise DocNotFoundError("only .md documents are viewable")
    return posix


def _resolve_within(docs_root: Path, posix: PurePosixPath) -> Path:
    """Join ``posix`` onto ``docs_root`` and confirm the result stays inside.

    Defence in depth on top of :func:`_safe_relpath`: even after the segment
    checks, we resolve the absolute path and re-assert containment so a
    symlink or odd filesystem behaviour cannot smuggle the read elsewhere.
    """
    root = docs_root.resolve()
    target = (root / Path(*posix.parts)).resolve()
    if root != target and root not in target.parents:
        raise PathTraversalError("doc path escapes the docs root")
    return target


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------
def read_doc_tree(docs_root: Path, *, project_id: UUID) -> DocTree:
    """Walk ``docs_root`` into a :class:`DocTree` of folders → ``.md`` files.

    Only canonical top-level folders (and their descendants) plus top-level
    ``.md`` files are surfaced; non-markdown files are skipped. A missing
    ``docs_root`` yields an empty tree (a freshly-bootstrapped project whose
    worktree has not been materialised yet). Deterministic ordering: folders
    and files sorted by name.
    """
    root = docs_root
    if not root.is_dir():
        return DocTree(project_id=project_id, folders=[], files=[])

    folders: list[DocTreeFolder] = []
    files: list[DocTreeFile] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            # Only the canonical numbered folders are part of the contract;
            # ignore stray top-level dirs (e.g. an accidental ``assets/``).
            if entry.name not in CANONICAL_DOC_FOLDER_NAMES:
                continue
            folders.append(_build_folder(entry, base=root))
        elif entry.is_file() and entry.suffix.lower() == MARKDOWN_SUFFIX:
            files.append(_build_file(entry, base=root))
    return DocTree(project_id=project_id, folders=folders, files=files)


def _build_folder(folder: Path, *, base: Path) -> DocTreeFolder:
    """Recurse into a folder, collecting sub-folders + ``.md`` files."""
    sub_folders: list[DocTreeFolder] = []
    files: list[DocTreeFile] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            sub_folders.append(_build_folder(entry, base=base))
        elif entry.is_file() and entry.suffix.lower() == MARKDOWN_SUFFIX:
            files.append(_build_file(entry, base=base))
    return DocTreeFolder(
        name=folder.name,
        relpath=folder.relative_to(base).as_posix(),
        folders=sub_folders,
        files=files,
    )


def _build_file(file: Path, *, base: Path) -> DocTreeFile:
    try:
        size = file.stat().st_size
    except OSError:
        size = 0
    return DocTreeFile(
        name=file.name,
        relpath=file.relative_to(base).as_posix(),
        size_bytes=size,
    )


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------
def read_doc_content(docs_root: Path, *, project_id: UUID, relpath: str) -> DocContent:
    """Read one doc's raw markdown by repo-relative path (path-traversal-safe).

    Raises :class:`PathTraversalError` on an escape attempt and
    :class:`DocNotFoundError` when no such ``.md`` exists under the root.
    """
    posix = _safe_relpath(relpath)
    target = _resolve_within(docs_root, posix)
    if not target.is_file():
        raise DocNotFoundError(f"doc not found: {posix.as_posix()}")
    content = target.read_text(encoding="utf-8")
    return DocContent(
        project_id=project_id,
        relpath=posix.as_posix(),
        content=content,
        size_bytes=len(content.encode("utf-8")),
    )


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------
def _snippet(content: str) -> str:
    """A single-line preview of a chunk, capped at :data:`SNIPPET_MAX_CHARS`."""
    collapsed = " ".join(content.split())
    if len(collapsed) <= SNIPPET_MAX_CHARS:
        return collapsed
    return collapsed[:SNIPPET_MAX_CHARS].rstrip() + "…"


async def search_docs(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    project_id: UUID,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[DocSearchHit]:
    """Full-text search the project's internal-docs KB chunks.

    Reuses :func:`api_server.rag.search.bm25_chunks` for the ranked,
    KB-visibility-filtered chunk ids (so a project only ever searches KBs it
    can read — and the internal-docs KB is granted to its own project by Fase
    C). Each chunk is hydrated into a :class:`DocSearchHit` carrying a snippet
    and the source doc's ``relpath`` (stamped on the chunk metadata at sync
    time). Hits preserve BM25 rank order; ``rank`` is the 1-based position.

    A blank query returns ``[]`` (``bm25_chunks`` already short-circuits).
    """
    bounded = max(1, min(limit, MAX_SEARCH_LIMIT))
    chunk_ids = await bm25_chunks(
        session,
        query=query,
        tenant_id=tenant_id,
        project_id=project_id,
        limit=bounded,
    )
    if not chunk_ids:
        return []

    rows = await session.execute(
        text(
            "SELECT chunks.id, chunks.document_id, chunks.ordinal,"
            "       chunks.content, chunks.metadata AS meta"
            " FROM chunks"
            " WHERE chunks.id = ANY(:ids)"
        ),
        {"ids": chunk_ids},
    )
    by_id = {row[0]: row for row in rows.all()}

    # Resolve relpath: the chunk metadata carries it (Fase C stamps
    # ``relpath`` + ``source='internal_docs'``). Fall back to the document's
    # title (which the sync sets to the relpath) when a chunk predates the
    # metadata convention.
    missing_relpath_doc_ids = {
        row[1] for row in by_id.values() if not isinstance((row[4] or {}).get("relpath"), str)
    }
    title_by_doc: dict[UUID, str] = {}
    if missing_relpath_doc_ids:
        from api_server.db.knowledge import Document

        doc_rows = await session.execute(
            select(Document.id, Document.title).where(Document.id.in_(missing_relpath_doc_ids))
        )
        title_by_doc = dict(doc_rows.tuples().all())

    hits: list[DocSearchHit] = []
    for rank, chunk_id in enumerate(chunk_ids, start=1):
        row = by_id.get(chunk_id)
        if row is None:
            continue
        document_id, ordinal, content, meta = row[1], row[2], row[3], row[4] or {}
        relpath = meta.get("relpath")
        if not isinstance(relpath, str):
            relpath = title_by_doc.get(document_id)
        hits.append(
            DocSearchHit(
                chunk_id=chunk_id,
                document_id=document_id,
                relpath=relpath,
                ordinal=ordinal,
                rank=rank,
                snippet=_snippet(content),
            )
        )
    return hits


# ---------------------------------------------------------------------------
# Semantic (vector) search
# ---------------------------------------------------------------------------
def _relpath_from_meta(meta: dict[str, object] | None, fallback_title: str | None) -> str | None:
    """The source doc's repo-relative path for a chunk.

    Fase C stamps ``relpath`` on the chunk metadata; fall back to the
    document's title (which the sync also sets to the relpath) for any chunk
    that predates the metadata convention. Shared by full-text and semantic
    hydration so both surfaces agree on the same path.
    """
    relpath = (meta or {}).get("relpath")
    if isinstance(relpath, str):
        return relpath
    return fallback_title


async def semantic_search_docs(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    project_id: UUID,
    embedder: Embedder,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[DocSemanticHit]:
    """Semantic (pgvector cosine) search over the project's internal-docs KB.

    Embeds ``query`` with the injectable ``embedder`` (production: the real
    :class:`~api_server.ingestion.embeddings.OllamaEmbedder`; tests: the
    deterministic :class:`~api_server.ingestion.embeddings.HashEmbedder` — no
    network), then reuses :func:`api_server.rag.search.vector_chunks` for the
    ranked, KB-visibility-filtered chunk ids. Because ``vector_chunks`` carries
    the same KB-visibility filter as the full-text path, a member of one tenant
    can never get a hit from another tenant's docs.

    Each hit is hydrated into a :class:`DocSemanticHit` carrying a snippet, the
    source doc's ``relpath``, and a cosine ``score`` in ``[0.0, 1.0]`` (computed
    from the same query vector). Hits preserve the vector-distance order;
    ``rank`` is the 1-based position.

    Returns ``[]`` when:
      * ``query`` is blank,
      * the embedder yields no vector (an embedding failure is non-fatal —
        semantic search simply has nothing to rank by, same recipe as the
        Plan-04 RAG tool), or
      * no chunk has an embedding to compare against (``vector_chunks`` skips
        NULL-embedding rows).
    """
    if not query.strip():
        return []

    query_embedding = await _embed_query(embedder, query)
    if query_embedding is None:
        return []

    bounded = max(1, min(limit, MAX_SEARCH_LIMIT))
    chunk_ids = await vector_chunks(
        session,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
        project_id=project_id,
        limit=bounded,
    )
    if not chunk_ids:
        return []

    # Hydrate the ranked ids: content + metadata for the snippet/relpath, plus
    # the cosine similarity against the same query vector. ``1 - cosine
    # distance`` gives a [0, 1]-ish similarity (clamped) for a relevance score.
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"
    rows = await session.execute(
        text(
            "SELECT chunks.id, chunks.document_id, chunks.ordinal,"
            "       chunks.content, chunks.metadata AS meta,"
            "       1 - (chunks.embedding <=> CAST(:qvec AS vector)) AS similarity"
            " FROM chunks"
            " WHERE chunks.id = ANY(:ids)"
        ),
        {"ids": chunk_ids, "qvec": qvec_str},
    )
    by_id = {row[0]: row for row in rows.all()}

    missing_relpath_doc_ids = {
        row[1] for row in by_id.values() if not isinstance((row[4] or {}).get("relpath"), str)
    }
    title_by_doc: dict[UUID, str] = {}
    if missing_relpath_doc_ids:
        from api_server.db.knowledge import Document

        doc_rows = await session.execute(
            select(Document.id, Document.title).where(Document.id.in_(missing_relpath_doc_ids))
        )
        title_by_doc = dict(doc_rows.tuples().all())

    hits: list[DocSemanticHit] = []
    for rank, chunk_id in enumerate(chunk_ids, start=1):
        row = by_id.get(chunk_id)
        if row is None:
            continue
        document_id, ordinal, content, meta, similarity = (
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
        )
        hits.append(
            DocSemanticHit(
                chunk_id=chunk_id,
                document_id=document_id,
                relpath=_relpath_from_meta(meta, title_by_doc.get(document_id)),
                ordinal=ordinal,
                rank=rank,
                score=_clamp_unit(float(similarity)),
                snippet=_snippet(content),
            )
        )
    return hits


def _clamp_unit(value: float) -> float:
    """Clamp a cosine similarity to ``[0.0, 1.0]``.

    Cosine similarity is mathematically in ``[-1, 1]``; for the viewer's
    relevance bar we clamp to ``[0, 1]`` (a negative similarity means
    'unrelated', which we surface as ``0.0``).
    """
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


async def _embed_query(embedder: Embedder, query: str) -> list[float] | None:
    """Embed the query for the vector path; ``None`` if the embedder fails.

    An embedding failure is non-fatal — semantic search simply has nothing to
    rank by (the same non-blocking recipe the Plan-04 RAG tool uses), so the
    caller returns an empty hit list rather than a 5xx.
    """
    try:
        vectors = await embedder.embed([query])
    except EmbeddingError as exc:
        logger.warning("docs_viewer.semantic_search.embedder_failed", error=str(exc))
        return None
    if not vectors:
        return None
    return list(vectors[0])


__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MARKDOWN_SUFFIX",
    "MAX_SEARCH_LIMIT",
    "SNIPPET_MAX_CHARS",
    "DocContent",
    "DocNotFoundError",
    "DocSearchHit",
    "DocSemanticHit",
    "DocTree",
    "DocTreeFile",
    "DocTreeFolder",
    "DocsViewerError",
    "PathTraversalError",
    "project_docs_root",
    "read_doc_content",
    "read_doc_tree",
    "search_docs",
    "semantic_search_docs",
]
