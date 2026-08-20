"use client";

/**
 * DocsBookmarksView — the starred-docs list (Plan 07 task_07_15).
 *
 * Reads the tenant-scoped bookmark store ({@link getBookmarks}) and lists
 * starred docs newest-first. Each row opens the doc in the render pane (via
 * `onOpenDoc`, which the page reflects in `?project=&path=`) and can be
 * un-starred in place. A small recency filter narrows the list to docs starred
 * within a window — recency is meaningful here because bookmarks carry an
 * `addedAt` timestamp (the tree/search API exposes no per-doc mtime).
 *
 * Bookmarks live in `localStorage`, so the parent passes them in as controlled
 * state (the page is the single owner that re-reads after a toggle) — this
 * component never writes directly, it just calls `onOpenDoc` / `onRemove`.
 */

import { useMemo, useState } from "react";
import { BookmarkX, FileText, Star } from "lucide-react";

import { useT, type Translator } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { categoryOf, DOC_CATEGORY_KEYS } from "@/lib/docs-filters";
import type { DocBookmark } from "@/lib/docs-bookmarks";

/**
 * Ventanas de recencia del filtro de marcadores (dias, o null = siempre).
 *
 * Llevan la CLAVE del diccionario, no la etiqueta: eran cuatro literales
 * castellanos en una constante de modulo, que es la forma que ninguna de las
 * dos guardas ve.
 */
const RECENCY_OPTIONS: {
  value: string;
  labelKey: Parameters<Translator<"docs">>[0];
  days: number | null;
}[] = [
  { value: "all", labelKey: "recencyAll", days: null },
  { value: "1", labelKey: "recencyToday", days: 1 },
  { value: "7", labelKey: "recency7", days: 7 },
  { value: "30", labelKey: "recency30", days: 30 },
];

const DAY_MS = 24 * 60 * 60 * 1000;

interface DocsBookmarksViewProps {
  bookmarks: DocBookmark[];
  selectedProjectId: string | null;
  selectedPath: string | null;
  onOpenDoc: (projectId: string, relpath: string) => void;
  onRemove: (projectId: string, relpath: string) => void;
}

export function DocsBookmarksView({
  bookmarks,
  selectedProjectId,
  selectedPath,
  onOpenDoc,
  onRemove,
}: DocsBookmarksViewProps) {
  const t = useT("docs");
  const [recency, setRecency] = useState("all");

  const days = RECENCY_OPTIONS.find((o) => o.value === recency)?.days ?? null;

  const visible = useMemo(() => {
    if (days === null) return bookmarks;
    const cutoff = Date.now() - days * DAY_MS;
    return bookmarks.filter((b) => b.addedAt >= cutoff);
  }, [bookmarks, days]);

  return (
    <div className="flex flex-col gap-3" data-testid="docs-bookmarks-view">
      <div className="flex flex-wrap items-center gap-1.5" data-testid="docs-bookmarks-recency">
        {RECENCY_OPTIONS.map((opt) => {
          const on = opt.value === recency;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => setRecency(opt.value)}
              aria-pressed={on}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs transition-colors",
                on
                  ? "border-primary/50 bg-primary/10 text-primary font-medium"
                  : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
              )}
              data-testid={`docs-bookmarks-recency-${opt.value}`}
            >
              {t(opt.labelKey)}
            </button>
          );
        })}
      </div>

      {bookmarks.length === 0 ? (
        <p
          className="text-muted-foreground px-1 py-6 text-center text-xs italic"
          data-testid="docs-bookmarks-empty"
        >
          {t("bookmarksEmpty")}
        </p>
      ) : visible.length === 0 ? (
        <p
          className="text-muted-foreground px-1 py-6 text-center text-xs italic"
          data-testid="docs-bookmarks-empty-window"
        >
          {t("bookmarksEmptyWindow")}
        </p>
      ) : (
        <ul className="flex flex-col gap-2" data-testid="docs-bookmarks-list">
          {visible.map((bookmark) => (
            <li key={`${bookmark.projectId}:${bookmark.relpath}`}>
              <BookmarkRow
                bookmark={bookmark}
                active={
                  bookmark.projectId === selectedProjectId && bookmark.relpath === selectedPath
                }
                onOpen={() => onOpenDoc(bookmark.projectId, bookmark.relpath)}
                onRemove={() => onRemove(bookmark.projectId, bookmark.relpath)}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BookmarkRow({
  bookmark,
  active,
  onOpen,
  onRemove,
}: {
  bookmark: DocBookmark;
  active: boolean;
  onOpen: () => void;
  onRemove: () => void;
}) {
  const t = useT("docs");
  const tFacets = useT("docFacets");
  const filename = bookmark.relpath.split("/").filter(Boolean).pop() ?? bookmark.relpath;
  const category = tFacets(DOC_CATEGORY_KEYS[categoryOf(bookmark.relpath)]);

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-2 transition-colors",
        active ? "border-primary/50 bg-primary/5" : "border-border hover:border-primary/40",
      )}
      data-testid={`docs-bookmark-${bookmark.projectId}-${bookmark.relpath}`}
    >
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left"
        data-testid="docs-bookmark-open"
      >
        <span className="flex w-full min-w-0 items-center gap-1.5">
          <FileText className="text-muted-foreground h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="text-foreground truncate text-sm font-medium">{filename}</span>
        </span>
        <span className="text-muted-foreground flex w-full min-w-0 items-center gap-1.5 text-[11px]">
          <span className="truncate">{bookmark.projectName}</span>
          <span aria-hidden="true">·</span>
          <span className="shrink-0">{category}</span>
        </span>
      </button>
      <button
        type="button"
        onClick={onRemove}
        className="text-muted-foreground hover:text-destructive shrink-0 rounded p-1 transition-colors"
        aria-label={t("bookmarkRemove")}
        title={t("bookmarkRemove")}
        data-testid="docs-bookmark-remove"
      >
        <BookmarkX className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );
}

/** A solid star button used elsewhere (tree row / viewer header) to toggle. */
export function BookmarkStar({
  bookmarked,
  onToggle,
  className,
  testid,
}: {
  bookmarked: boolean;
  onToggle: () => void;
  className?: string;
  testid?: string;
}) {
  const t = useT("docs");
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      aria-pressed={bookmarked}
      aria-label={bookmarked ? t("bookmarkRemove") : t("bookmarkAdd")}
      title={bookmarked ? t("bookmarkRemove") : t("bookmarkAdd")}
      className={cn(
        "shrink-0 rounded p-1 transition-colors",
        bookmarked ? "text-warning-soft-foreground" : "text-muted-foreground hover:text-foreground",
        className,
      )}
      data-testid={testid ?? "docs-bookmark-star"}
    >
      <Star className={cn("h-4 w-4", bookmarked && "fill-current")} aria-hidden="true" />
    </button>
  );
}
