"""Docs-viewer read-only API (Plan 07 Fase D — task_07_D_api / task_07_18).

Three project-scoped endpoints, all gated to active members of the
project's tenant:

  * ``GET /projects/{project_id}/docs/tree``    — the canonical doc tree
    (folders → ``.md`` files), read from the project's ``docs/`` directory on
    the persistent filesystem under ``settings.data_root``.
  * ``GET /projects/{project_id}/docs/content`` — one doc's RAW markdown by
    repo-relative ``?path=``, path-traversal-safe.
  * ``GET /projects/{project_id}/docs/diff``    — the unified diff of one doc
    ``.md`` between two git ``?base=``/``?head=`` refs of the project repo
    (task_07_16 backend half), path-traversal-safe + git-ref-injection-safe.
  * ``GET /projects/{project_id}/docs/search``  — full-text search over the
    project's internal-docs KB chunks (Fase C), ranked, with snippets +
    source doc paths.
  * ``GET /projects/{project_id}/docs/export/zip`` — download the whole ``docs/``
    tree as a deterministic, path-safe ZIP bundle (task_07_17).
  * ``GET /projects/{project_id}/docs/export/pdf`` — render one doc to PDF
    (task_07_17). No offline renderer ships in the runtime, so this is a
    documented ``501 Not Implemented`` deferral pointing callers at the ZIP
    export; we do not add a heavy / native dependency just for it.

RBAC (task_07_18): the caller must be a member of the active tenant
(:func:`require_tenant_member`) AND the project must be visible under the
request's RLS scope. A cross-tenant or otherwise inaccessible project is a
**404** (RLS hides the row — we never confirm whether it exists elsewhere),
mirroring the established KB-router contract. Search reuses
:func:`api_server.rag.search.bm25_chunks`, whose KB-visibility filter already
restricts results to KBs the project can read — so a member of tenant A can
never get a hit from tenant B's docs.

The on-disk docs root is resolved by an injectable dependency
(:func:`get_docs_root_resolver`) so tests point it at a tmp dir; production
derives it from ``settings.data_root`` via
:func:`api_server.docs_viewer.service.project_docs_root`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_member,
)
from api_server.config import get_settings
from api_server.db.domain import Project
from api_server.docs_viewer.service import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    ZIP_MEDIA_TYPE,
    DocDiffError,
    DocNotFoundError,
    DocsViewerError,
    InvalidGitRefError,
    PathTraversalError,
    PdfExportNotConfiguredError,
    diff_doc,
    export_doc_pdf,
    export_docs_zip,
    project_docs_root,
    project_repo_root,
    read_doc_content,
    read_doc_tree,
    search_docs,
    semantic_search_docs,
)
from api_server.ingestion.embeddings import Embedder, OllamaEmbedder
from api_server.routers._helpers import require_tenant_id

router = APIRouter(prefix="/projects/{project_id}/docs", tags=["docs-viewer"])

# Type of the callable a request gets to resolve a project's on-disk docs
# root: ``(tenant_id, project_id) -> Path``. Production builds it from
# ``settings.data_root``; tests override the dependency to point at a tmp dir.
DocsRootResolver = Callable[[UUID, UUID], Path]

# Same shape, but resolving the project's git *repo root* (the working tree
# holding ``.git`` + the ``docs/`` subtree) — what the diff endpoint shells
# ``git`` in. Distinct from the docs-root resolver because git runs at the
# repo, not inside ``docs/``.
DocsRepoResolver = Callable[[UUID, UUID], Path]


def get_docs_root_resolver() -> DocsRootResolver:
    """Provide the production docs-root resolver (overridable in tests).

    Bound once per request from ``settings.data_root``. Tests register an
    override via ``app.dependency_overrides[get_docs_root_resolver]`` so the
    filesystem reads hit a tmp dir instead of a real worktree.
    """
    data_root = get_settings().data_root

    def _resolve(tenant_id: UUID, project_id: UUID) -> Path:
        return project_docs_root(data_root, tenant_id=tenant_id, project_id=project_id)

    return _resolve


def get_docs_repo_resolver() -> DocsRepoResolver:
    """Provide the production docs *repo-root* resolver (overridable in tests).

    The diff endpoint needs the git working tree (where ``.git`` lives), not
    the ``docs/`` directory. Bound once per request from ``settings.data_root``
    via :func:`api_server.docs_viewer.service.project_repo_root`. Tests
    register an override via ``app.dependency_overrides[get_docs_repo_resolver]``
    pointing at a throwaway git repo so no real worktree is needed.
    """
    data_root = get_settings().data_root

    def _resolve(tenant_id: UUID, project_id: UUID) -> Path:
        return project_repo_root(data_root, tenant_id=tenant_id, project_id=project_id)

    return _resolve


async def get_query_embedder() -> AsyncIterator[Embedder]:
    """Provide the query embedder for semantic search (overridable in tests).

    Production yields the real :class:`OllamaEmbedder` (owns an httpx client we
    close after the request). Tests register an override via
    ``app.dependency_overrides[get_query_embedder]`` returning the deterministic
    :class:`~api_server.ingestion.embeddings.HashEmbedder` so no network /
    running Ollama is required (it is down in CI).
    """
    embedder = OllamaEmbedder()
    try:
        yield embedder
    finally:
        await embedder.aclose()


async def _require_visible_project(session: AsyncSession, project_id: UUID) -> None:
    """404 unless the project is visible under the current RLS scope.

    Cross-tenant / inaccessible projects are hidden by RLS, so the SELECT
    returns nothing → 404. We deliberately return 404 (not 403) so a caller
    cannot probe which project ids exist in other tenants.
    """
    result = await session.execute(
        select(Project.id).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")


def _tree_file_payload(name: str, relpath: str, size_bytes: int) -> dict[str, object]:
    return {"type": "file", "name": name, "relpath": relpath, "size_bytes": size_bytes}


def _tree_folder_payload(folder: object) -> dict[str, object]:
    # ``folder`` is a DocTreeFolder; typed loosely to keep the recursion flat.
    from api_server.docs_viewer.service import DocTreeFolder

    assert isinstance(folder, DocTreeFolder)
    return {
        "type": "folder",
        "name": folder.name,
        "relpath": folder.relpath,
        "folders": [_tree_folder_payload(f) for f in folder.folders],
        "files": [_tree_file_payload(f.name, f.relpath, f.size_bytes) for f in folder.files],
    }


# ===========================================================================
# Tree
# ===========================================================================
@router.get("/tree")
async def get_docs_tree(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: DocsRootResolver = Depends(get_docs_root_resolver),
) -> dict[str, object]:
    """Return the project's canonical doc tree (folders → ``.md`` files)."""
    tenant_id = require_tenant_id(principal)
    await _require_visible_project(session, project_id)

    docs_root = resolver(tenant_id, project_id)
    tree = read_doc_tree(docs_root, project_id=project_id)
    return {
        "project_id": str(project_id),
        "folders": [_tree_folder_payload(f) for f in tree.folders],
        "files": [_tree_file_payload(f.name, f.relpath, f.size_bytes) for f in tree.files],
    }


# ===========================================================================
# Content
# ===========================================================================
@router.get("/content")
async def get_doc_content(
    project_id: UUID,
    path: str = Query(..., description="Repo-relative path of the .md to read"),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: DocsRootResolver = Depends(get_docs_root_resolver),
) -> dict[str, object]:
    """Return one doc's RAW markdown content (path-traversal-safe)."""
    tenant_id = require_tenant_id(principal)
    await _require_visible_project(session, project_id)

    docs_root = resolver(tenant_id, project_id)
    try:
        doc = read_doc_content(docs_root, project_id=project_id, relpath=path)
    except PathTraversalError as exc:
        # A traversal attempt is a client error — 400, not a 404 that might
        # confirm a path layout. The message is generic.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid doc path"
        ) from exc
    except DocNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="doc not found") from exc
    except DocsViewerError as exc:  # defensive — base class, unexpected subtype
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid doc request"
        ) from exc

    return {
        "project_id": str(project_id),
        "relpath": doc.relpath,
        "content": doc.content,
        "size_bytes": doc.size_bytes,
    }


# ===========================================================================
# Export — ZIP bundle (task_07_17)
# ===========================================================================
@router.get("/export/zip")
async def export_project_docs_zip(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: DocsRootResolver = Depends(get_docs_root_resolver),
) -> Response:
    """Download a project's ``/docs`` markdown as a deterministic ZIP bundle.

    RBAC-scoped (active tenant member + RLS-visible project) and path-safe: the
    bundle contains exactly the canonical tree's ``.md`` files, each re-validated
    against traversal before it is read. Returns ``application/zip`` with a
    ``Content-Disposition: attachment`` header so the browser downloads it.
    """
    tenant_id = require_tenant_id(principal)
    await _require_visible_project(session, project_id)

    docs_root = resolver(tenant_id, project_id)
    bundle = export_docs_zip(docs_root, project_id=project_id)
    return Response(
        content=bundle.content,
        media_type=ZIP_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{bundle.filename}"'},
    )


# ===========================================================================
# Export — PDF (task_07_17, renderer deferred → 501)
# ===========================================================================
@router.get("/export/pdf")
async def export_project_doc_pdf(
    project_id: UUID,
    path: str = Query(..., description="Repo-relative path of the .md to render"),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    resolver: DocsRootResolver = Depends(get_docs_root_resolver),
) -> Response:
    """Render one doc to PDF — currently **not configured** in this runtime.

    No offline markdown→PDF renderer ships in the api-server image (we do not
    pull a heavy / native dependency just for this), so this endpoint returns a
    clear ``501 Not Implemented`` directing callers to the ZIP export. A
    traversal attempt in ``path`` is still a ``400`` (validated before the 501),
    so the deferral never weakens path safety. RBAC-scoped like every other
    docs-viewer surface.
    """
    tenant_id = require_tenant_id(principal)
    await _require_visible_project(session, project_id)

    docs_root = resolver(tenant_id, project_id)
    try:
        pdf_bytes = export_doc_pdf(docs_root, project_id=project_id, relpath=path)
    except PathTraversalError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid doc path"
        ) from exc
    except DocNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="doc not found") from exc
    except PdfExportNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="PDF export is not configured; use /export/zip instead",
        ) from exc
    except DocsViewerError as exc:  # defensive — unexpected subtype
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid export request"
        ) from exc

    return Response(content=pdf_bytes, media_type="application/pdf")


# ===========================================================================
# Diff between two git refs (task_07_16, backend half)
# ===========================================================================
@router.get("/diff")
async def get_doc_diff(
    project_id: UUID,
    path: str = Query(..., description="Repo-relative path of the .md to diff"),
    base: str = Query(..., min_length=1, description="Base git ref / commit-ish"),
    head: str = Query(..., min_length=1, description="Head git ref / commit-ish"),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    repo_resolver: DocsRepoResolver = Depends(get_docs_repo_resolver),
) -> dict[str, object]:
    """Return the diff of one doc ``.md`` between two git refs of the project.

    Path-traversal-safe (``..``/absolute rejected) and ref-injection-safe
    (option-like / whitespace refs rejected). The body is returned both raw
    (verbatim ``git diff``) and parsed into classified lines + add/remove
    counts the frontend diff viewer can render.
    """
    tenant_id = require_tenant_id(principal)
    await _require_visible_project(session, project_id)

    repo_root = repo_resolver(tenant_id, project_id)
    try:
        diff = diff_doc(
            repo_root,
            project_id=project_id,
            relpath=path,
            base_ref=base,
            head_ref=head,
        )
    except PathTraversalError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid doc path"
        ) from exc
    except DocNotFoundError as exc:
        # A non-``.md`` path is the only DocNotFoundError diff_doc raises.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="only .md documents are diffable"
        ) from exc
    except InvalidGitRefError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid git ref"
        ) from exc
    except DocDiffError as exc:
        # Bad ref / not-a-repo: a client error (the refs they asked for don't
        # resolve), never the raw git stderr.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="could not diff the given refs"
        ) from exc
    except DocsViewerError as exc:  # defensive — unexpected subtype
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid diff request"
        ) from exc

    return {
        "project_id": str(project_id),
        "relpath": diff.relpath,
        "base_ref": diff.base_ref,
        "head_ref": diff.head_ref,
        "unchanged": diff.unchanged,
        "added": diff.added,
        "removed": diff.removed,
        "raw": diff.raw,
        "lines": [{"kind": line.kind, "content": line.content} for line in diff.lines],
    }


# ===========================================================================
# Full-text search
# ===========================================================================
@router.get("/search")
async def search_project_docs(
    project_id: UUID,
    q: str = Query(..., min_length=1, description="Full-text query"),
    limit: int = Query(
        default=DEFAULT_SEARCH_LIMIT,
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description=f"Max ranked hits (1..{MAX_SEARCH_LIMIT}).",
    ),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Full-text search the project's internal-docs KB, ranked + snippeted."""
    tenant_id = require_tenant_id(principal)
    await _require_visible_project(session, project_id)

    hits = await search_docs(
        session,
        query=q,
        tenant_id=tenant_id,
        project_id=project_id,
        limit=limit,
    )
    return {
        "project_id": str(project_id),
        "query": q,
        "hits": [
            {
                "chunk_id": str(hit.chunk_id),
                "document_id": str(hit.document_id),
                "relpath": hit.relpath,
                "ordinal": hit.ordinal,
                "rank": hit.rank,
                "snippet": hit.snippet,
            }
            for hit in hits
        ],
    }


# ===========================================================================
# Semantic (vector) search
# ===========================================================================
@router.get("/semantic-search")
async def semantic_search_project_docs(
    project_id: UUID,
    q: str = Query(..., min_length=1, description="Semantic query"),
    limit: int = Query(
        default=DEFAULT_SEARCH_LIMIT,
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description=f"Max ranked hits (1..{MAX_SEARCH_LIMIT}).",
    ),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    embedder: Embedder = Depends(get_query_embedder),
) -> dict[str, object]:
    """Semantic search the project's internal-docs KB, ranked + snippeted.

    Embeds ``q`` and ranks the project's internal-docs chunks by pgvector
    cosine similarity. Returns ``hits: []`` when the query embeds to nothing,
    no chunk has an embedding, or nothing matches — never a 5xx for an
    embedder hiccup (semantic search degrades to "no results").
    """
    tenant_id = require_tenant_id(principal)
    await _require_visible_project(session, project_id)

    hits = await semantic_search_docs(
        session,
        query=q,
        tenant_id=tenant_id,
        project_id=project_id,
        embedder=embedder,
        limit=limit,
    )
    return {
        "project_id": str(project_id),
        "query": q,
        "hits": [
            {
                "chunk_id": str(hit.chunk_id),
                "document_id": str(hit.document_id),
                "relpath": hit.relpath,
                "ordinal": hit.ordinal,
                "rank": hit.rank,
                "score": hit.score,
                "snippet": hit.snippet,
            }
            for hit in hits
        ],
    }


__all__ = [
    "get_docs_repo_resolver",
    "get_docs_root_resolver",
    "get_query_embedder",
    "router",
]
