/**
 * Pure filter helpers for the docs visor (Plan 07 task_07_15).
 *
 * Two facets are derived entirely from a doc's repo-relative path — no extra
 * API call:
 *
 *   * **Category** — the canonical docs folder a `.md` lives under
 *     (`01-overview`, `05-architecture-decisions`, …). The product mandates the
 *     7-folder canonical layout under `docs/`, so the first path segment after
 *     `docs/` is the category; anything else (a loose root file, a
 *     non-canonical folder) maps to `other`.
 *   * **Type** — coarse doc kind inferred from the filename: an ADR
 *     (`05-architecture-decisions/*` or `NNNN-*.md`), a changelog entry, a
 *     runbook, a README/index, or a generic doc.
 *
 * The active {@link DocsFilter} is applied to the sidebar tree (pruning files /
 * empty folders) and to search hits (which only carry a `relpath`), so the same
 * predicate scopes both surfaces consistently. Everything here is a pure
 * function of the input — trivially unit-testable and safe to run in render.
 */

import type { Translator } from "@/lib/i18n";

import type { DocTree, DocTreeFile, DocTreeFolder } from "@/lib/docs-api";

// ---------------------------------------------------------------------------
// Category (canonical docs folder)
// ---------------------------------------------------------------------------

/** The 7 canonical docs folders + an `other` catch-all for everything else. */
export const DOC_CATEGORIES = [
  "01-overview",
  "02-getting-started",
  "03-guides",
  "04-reference",
  "05-architecture-decisions",
  "06-runbooks",
  "07-changelog",
  "other",
] as const;

export type DocCategory = (typeof DOC_CATEGORIES)[number];

const CANONICAL_SET = new Set<string>(DOC_CATEGORIES);

/** Human labels for the category facet (ES). */
/** Las claves del namespace `docFacets`, para tipar los dos mapas. */
export type DocFacetKey = Parameters<Translator<"docFacets">>[0];

/**
 * Clave del diccionario por categoria, no el texto (prod-16 `task_prod16_04`).
 *
 * El mapa se conserva para que TypeScript siga exigiendo una entrada por cada
 * carpeta canonica: es la misma garantia que daba el `Record<DocCategory,
 * string>` de antes, sin el castellano cableado en un modulo puro.
 */
export const DOC_CATEGORY_KEYS: Record<DocCategory, DocFacetKey> = {
  "01-overview": "categoryOverview",
  "02-getting-started": "categoryGettingStarted",
  "03-guides": "categoryGuides",
  "04-reference": "categoryReference",
  "05-architecture-decisions": "categoryAdr",
  "06-runbooks": "categoryRunbooks",
  "07-changelog": "categoryChangelog",
  other: "categoryOther",
};

/**
 * Map a doc's repo-relative path to its canonical category.
 *
 * Paths look like `docs/05-architecture-decisions/0021-llm.md`; the segment
 * right after a leading `docs/` is the category. A non-canonical or missing
 * segment (root README, ad-hoc folder) is `other`.
 */
export function categoryOf(relpath: string): DocCategory {
  const segments = relpath.split("/").filter(Boolean);
  // Skip a leading `docs/` so both `docs/03-guides/x.md` and `03-guides/x.md`
  // resolve the same way.
  const start = segments[0] === "docs" ? 1 : 0;
  const candidate = segments[start];
  if (candidate && CANONICAL_SET.has(candidate)) {
    return candidate as DocCategory;
  }
  return "other";
}

// ---------------------------------------------------------------------------
// Type (coarse doc kind from the filename)
// ---------------------------------------------------------------------------

export const DOC_TYPES = ["adr", "changelog", "runbook", "readme", "doc"] as const;

export type DocType = (typeof DOC_TYPES)[number];

export const DOC_TYPE_KEYS: Record<DocType, DocFacetKey> = {
  adr: "typeAdr",
  changelog: "typeChangelog",
  runbook: "typeRunbook",
  readme: "typeReadme",
  doc: "typeDoc",
};

const ADR_FILENAME_RE = /^\d{3,4}-/; // e.g. 0021-llm-providers.md
const README_RE = /^(readme|index)\.md$/i;

/** Infer a doc's coarse type from its path + filename. */
export function typeOf(relpath: string): DocType {
  const category = categoryOf(relpath);
  const filename = relpath.split("/").filter(Boolean).pop() ?? "";

  if (README_RE.test(filename)) return "readme";
  if (category === "05-architecture-decisions" || ADR_FILENAME_RE.test(filename)) return "adr";
  if (category === "07-changelog") return "changelog";
  if (category === "06-runbooks") return "runbook";
  return "doc";
}

// ---------------------------------------------------------------------------
// The active filter + the predicate it produces
// ---------------------------------------------------------------------------

/**
 * The active filter selection. An empty set in a facet means "no constraint"
 * (everything passes); a non-empty set keeps only matching docs.
 */
export interface DocsFilter {
  categories: ReadonlySet<DocCategory>;
  types: ReadonlySet<DocType>;
}

export const EMPTY_FILTER: DocsFilter = {
  categories: new Set<DocCategory>(),
  types: new Set<DocType>(),
};

export function isFilterActive(filter: DocsFilter): boolean {
  return filter.categories.size > 0 || filter.types.size > 0;
}

/** Does a doc at `relpath` pass the active filter? */
export function matchesFilter(relpath: string, filter: DocsFilter): boolean {
  if (filter.categories.size > 0 && !filter.categories.has(categoryOf(relpath))) {
    return false;
  }
  if (filter.types.size > 0 && !filter.types.has(typeOf(relpath))) {
    return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Applying the filter to the tree (prune files + now-empty folders)
// ---------------------------------------------------------------------------

function filterFolder(folder: DocTreeFolder, filter: DocsFilter): DocTreeFolder | null {
  const files = folder.files.filter((f) => matchesFilter(f.relpath, filter));
  const folders = folder.folders
    .map((child) => filterFolder(child, filter))
    .filter((child): child is DocTreeFolder => child !== null);

  // Drop a folder that, after filtering, holds nothing — keeps the tree tidy.
  if (files.length === 0 && folders.length === 0) {
    return null;
  }
  return { ...folder, files, folders };
}

/**
 * Return a copy of `tree` with files / folders not matching `filter` pruned.
 *
 * When the filter is inactive the original tree is returned unchanged (cheap
 * identity — callers can skip re-rendering). Folders left empty by the prune
 * are removed so the tree never shows a dead branch.
 */
export function filterTree(tree: DocTree, filter: DocsFilter): DocTree {
  if (!isFilterActive(filter)) return tree;
  const files: DocTreeFile[] = tree.files.filter((f) => matchesFilter(f.relpath, filter));
  const folders = tree.folders
    .map((folder) => filterFolder(folder, filter))
    .filter((folder): folder is DocTreeFolder => folder !== null);
  return { ...tree, files, folders };
}
