/**
 * Typed client + shapes for the docs-viewer API (Plan 07 Fase D).
 *
 * The backend exposes project-scoped, RBAC-gated GET endpoints under
 * `/projects/{project_id}/docs/*` (see
 * apps/api-server/src/api_server/routers/docs_viewer.py). This module
 * mirrors the JSON contracts as TypeScript types and offers thin
 * fetchers on top of `apiFetch` so every docs-viewer slice (sidebar,
 * renderer, search, diff, export) consumes the same source of truth.
 *
 * task_07_11 only needs the tree fetcher + tree types; the rest of the
 * shapes are declared here so later tasks (07_12+) extend this file
 * instead of redefining the contract per page.
 */

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Tree (GET /projects/{id}/docs/tree)
// ---------------------------------------------------------------------------

export interface DocTreeFile {
  type: "file";
  name: string;
  /** Repo-relative path used as the `?path=` query for /content + /diff. */
  relpath: string;
  size_bytes: number;
}

export interface DocTreeFolder {
  type: "folder";
  name: string;
  relpath: string;
  folders: DocTreeFolder[];
  files: DocTreeFile[];
}

export interface DocTree {
  project_id: string;
  folders: DocTreeFolder[];
  files: DocTreeFile[];
}

export function fetchDocsTree(projectId: string): Promise<DocTree> {
  return apiFetch<DocTree>(`/projects/${projectId}/docs/tree`);
}

// ---------------------------------------------------------------------------
// Content (GET /projects/{id}/docs/content?path=)
// ---------------------------------------------------------------------------

export interface DocContent {
  project_id: string;
  relpath: string;
  content: string;
  size_bytes: number;
}

export function fetchDocContent(projectId: string, path: string): Promise<DocContent> {
  const qs = new URLSearchParams({ path });
  return apiFetch<DocContent>(`/projects/${projectId}/docs/content?${qs.toString()}`);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Total number of `.md` files anywhere in the tree (folders + root). */
export function countDocFiles(tree: Pick<DocTree, "folders" | "files">): number {
  let total = tree.files.length;
  for (const folder of tree.folders) {
    total += countDocFiles(folder);
  }
  return total;
}
