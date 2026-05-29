/**
 * Tenant-scoped localStorage for docs-visor bookmarks (Plan 07 task_07_15).
 *
 * Bookmarks are a purely client-side convenience: a starred doc is a
 * `(projectId, relpath)` pair the user wants to find again, persisted in
 * `localStorage` under a key namespaced by the active tenant — the same
 * source-of-truth-in-localStorage approach as {@link getTenantId} in
 * `lib/tenant-storage.ts`, and for the same reason: query functions and
 * non-React module code must read it without prop-drilling React context.
 *
 * The key embeds the tenant id (`admin-panel.docs-bookmarks.<tenant>`) so a
 * superadmin switching tenants never sees another tenant's stars, and the
 * portfolio / "all tenants" view (no tenant picked) gets its own bucket. The
 * stored value is just a list of pairs + a timestamp; nothing sensitive, and
 * RBAC is still enforced server-side when a bookmark is opened (the
 * `/content` fetch 404s if the doc is no longer visible).
 */

import { getTenantId } from "@/lib/tenant-storage";

const KEY_PREFIX = "admin-panel.docs-bookmarks";
/** Bucket suffix when no tenant is selected (portfolio / superadmin view). */
const NO_TENANT_BUCKET = "_all";

/** One starred doc. `addedAt` is epoch ms, used for the recency filter/sort. */
export interface DocBookmark {
  projectId: string;
  /** Human label for the project, captured at star time for the list view. */
  projectName: string;
  /** Repo-relative path of the `.md` (same value the tree/search use). */
  relpath: string;
  /** Epoch milliseconds the bookmark was created. */
  addedAt: number;
}

function storageKey(): string {
  const tenantId = getTenantId();
  return `${KEY_PREFIX}.${tenantId ?? NO_TENANT_BUCKET}`;
}

/** Stable identity for a bookmark — a doc is unique per (project, path). */
export function bookmarkId(projectId: string, relpath: string): string {
  return `${projectId}::${relpath}`;
}

function isBookmark(value: unknown): value is DocBookmark {
  if (typeof value !== "object" || value === null) return false;
  const b = value as Record<string, unknown>;
  return (
    typeof b.projectId === "string" &&
    typeof b.projectName === "string" &&
    typeof b.relpath === "string" &&
    typeof b.addedAt === "number"
  );
}

/**
 * Read all bookmarks for the active tenant, newest first.
 *
 * Returns `[]` on SSR, a missing/blank key, or malformed JSON — a corrupt
 * value never throws into the render tree, it just reads as "no bookmarks".
 */
export function getBookmarks(): DocBookmark[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(storageKey());
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const items = parsed.filter(isBookmark);
    return items.sort((a, b) => b.addedAt - a.addedAt);
  } catch {
    return [];
  }
}

function writeBookmarks(items: DocBookmark[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey(), JSON.stringify(items));
}

export function isBookmarked(projectId: string, relpath: string): boolean {
  const id = bookmarkId(projectId, relpath);
  return getBookmarks().some((b) => bookmarkId(b.projectId, b.relpath) === id);
}

/**
 * Add a bookmark (no-op if the same doc is already starred). Returns the new
 * full list so a caller can update React state without a second read.
 */
export function addBookmark(
  projectId: string,
  projectName: string,
  relpath: string,
): DocBookmark[] {
  const id = bookmarkId(projectId, relpath);
  const existing = getBookmarks();
  if (existing.some((b) => bookmarkId(b.projectId, b.relpath) === id)) {
    return existing;
  }
  const next = [{ projectId, projectName, relpath, addedAt: Date.now() }, ...existing];
  writeBookmarks(next);
  return next;
}

/** Remove a bookmark (no-op if absent). Returns the new full list. */
export function removeBookmark(projectId: string, relpath: string): DocBookmark[] {
  const id = bookmarkId(projectId, relpath);
  const next = getBookmarks().filter((b) => bookmarkId(b.projectId, b.relpath) !== id);
  writeBookmarks(next);
  return next;
}

/**
 * Toggle a bookmark on/off. Returns the new full list so the caller can keep
 * its starred-state in sync in one round-trip.
 */
export function toggleBookmark(
  projectId: string,
  projectName: string,
  relpath: string,
): DocBookmark[] {
  return isBookmarked(projectId, relpath)
    ? removeBookmark(projectId, relpath)
    : addBookmark(projectId, projectName, relpath);
}
