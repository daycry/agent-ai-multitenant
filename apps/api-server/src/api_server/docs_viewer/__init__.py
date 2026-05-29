"""Docs-viewer backend (Plan 07 Fase D).

Read-only viewer over a project's canonical ``/docs`` tree:

  * the **doc tree** (canonical folders → ``.md`` files) and the **raw
    markdown content** are served from the project's ``docs/`` directory on
    the persistent filesystem under ``settings.data_root`` (the worktree
    convention from :mod:`workers.git_repos`). The root is *injectable* so
    tests run against a tmp dir with no real worktree;
  * **full-text search** runs over the per-project internal-docs KB chunks
    built in Fase C (:mod:`api_server.docs_structure.kb_sync`), reusing
    :func:`api_server.rag.search.bm25_chunks`.

RBAC: a caller only sees / searches projects they are a member of — the
router gates on :func:`api_server.auth.deps.require_tenant_member` and the
project must be visible under the request's RLS scope (cross-tenant /
inaccessible → 404).
"""

from __future__ import annotations

from api_server.docs_viewer.service import (
    DocContent,
    DocSearchHit,
    DocsViewerError,
    DocTree,
    DocTreeFile,
    DocTreeFolder,
    PathTraversalError,
    project_docs_root,
    read_doc_content,
    read_doc_tree,
)

__all__ = [
    "DocContent",
    "DocSearchHit",
    "DocTree",
    "DocTreeFile",
    "DocTreeFolder",
    "DocsViewerError",
    "PathTraversalError",
    "project_docs_root",
    "read_doc_content",
    "read_doc_tree",
]
