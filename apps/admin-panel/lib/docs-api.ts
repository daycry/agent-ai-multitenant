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

import GithubSlugger from "github-slugger";

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
// Table of contents (derived client-side from the rendered headings)
// ---------------------------------------------------------------------------

/** One heading in a doc's auto-generated table of contents. */
export interface TocEntry {
  /** Heading depth, 1..6 (`#` … `######`). */
  level: number;
  /** Visible heading text (markdown markers stripped). */
  text: string;
  /**
   * URL-fragment id. Matches the `id` `rehype-slug` assigns to the rendered
   * heading (same `github-slugger` algorithm, one slugger per document) so a
   * TOC link's `#id` lands on the heading.
   */
  id: string;
}

// Code fences (``` … ``` / ~~~ … ~~~). We skip their bodies so a commented
// `# foo` inside a snippet never leaks into the TOC.
const FENCE_RE = /^\s*(`{3,}|~{3,})/;
// ATX headings: 1–6 leading `#`, then at least one space, then the text.
// Setext headings (underlined with === / ---) are intentionally ignored —
// the canonical docs use ATX exclusively.
const HEADING_RE = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
// Strip the inline markdown markers we don't want in plain TOC text: links
// (keep the label), emphasis/strong, inline code backticks.
const LINK_RE = /\[([^\]]+)\]\([^)]*\)/g;
const EMPHASIS_RE = /(\*\*|__|\*|_|~~|`)/g;

function stripInlineMarkdown(text: string): string {
  return text.replace(LINK_RE, "$1").replace(EMPHASIS_RE, "").trim();
}

/**
 * Build a doc's table of contents from its raw markdown.
 *
 * Parses ATX headings outside code fences and slugs each with
 * `github-slugger` — the very library `rehype-slug` uses — so the generated
 * ids line up with the rendered heading anchors. A leading H1 (the doc title)
 * is kept; callers decide whether to show it.
 */
export function extractToc(markdown: string): TocEntry[] {
  const slugger = new GithubSlugger();
  const entries: TocEntry[] = [];
  let inFence = false;
  let fenceMarker = "";

  for (const rawLine of markdown.split("\n")) {
    const fenceMatch = rawLine.match(FENCE_RE);
    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      if (!inFence) {
        inFence = true;
        fenceMarker = marker;
      } else if (marker === fenceMarker) {
        inFence = false;
        fenceMarker = "";
      }
      continue;
    }
    if (inFence) {
      continue;
    }

    const headingMatch = rawLine.match(HEADING_RE);
    if (!headingMatch) {
      continue;
    }
    const level = headingMatch[1].length;
    const text = stripInlineMarkdown(headingMatch[2]);
    if (text.length === 0) {
      continue;
    }
    entries.push({ level, text, id: slugger.slug(text) });
  }

  return entries;
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
