"use client";

/**
 * DocsSearchPanel — instant search over one project's docs (Plan 07 task_07_13).
 *
 * A single debounced search box with two modes, switched by a tab:
 *
 *   * **Texto** — full-text search via `GET /projects/{id}/docs/search?q=`,
 *     ranked hits with the source doc path + a snippet.
 *   * **Semántica** — vector search via `GET .../semantic-search?q=`, same
 *     shape plus a cosine `score` shown as a relevance badge.
 *
 * Search is only enabled once a project is selected (the visor needs a
 * `?project=` to scope to). Both endpoints are RBAC/RLS-gated server-side, so
 * we never have to filter results here. Clicking a hit calls `onOpenDoc` so the
 * parent opens that doc in the render pane (and reflects it in the URL).
 *
 * States: idle (no query / no project), loading, error (API message), empty
 * (query ran, no hits), and results. Input is debounced 300ms; queries are
 * cached by (mode, project, query) and the in-flight request is aborted when
 * the query changes so stale responses never overwrite fresher ones.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  fetchDocSearch,
  fetchDocSemanticSearch,
  type DocSearchHit,
  type DocSemanticHit,
} from "@/lib/docs-api";
import { matchesFilter, type DocsFilter } from "@/lib/docs-filters";

import { BookmarkStar } from "./docs-bookmarks-view";

const DEBOUNCE_MS = 300;
/** Min query length before we hit the API — single chars are noise. */
const MIN_QUERY_LEN = 2;

type SearchMode = "fulltext" | "semantic";

/** Bookmark wiring for a search hit (project name bound by the parent). */
export interface SearchBookmarkControls {
  isBookmarked: (projectId: string, relpath: string) => boolean;
  onToggleBookmark: (projectId: string, relpath: string) => void;
}

interface DocsSearchPanelProps {
  /** The project to search within, or null when none is selected. */
  projectId: string | null;
  /** The currently-open doc path, so the matching hit can be marked active. */
  selectedPath: string | null;
  /** Open a hit's doc in the render pane (parent reflects it in the URL). */
  onOpenDoc: (projectId: string, relpath: string) => void;
  /** Active facet filter, applied client-side to the returned hits. */
  filter: DocsFilter;
  bookmarks: SearchBookmarkControls;
}

export function DocsSearchPanel({
  projectId,
  selectedPath,
  onOpenDoc,
  filter,
  bookmarks,
}: DocsSearchPanelProps) {
  const [mode, setMode] = useState<SearchMode>("fulltext");
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const handle = setTimeout(() => setDebounced(query.trim()), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  const enabled = Boolean(projectId) && debounced.length >= MIN_QUERY_LEN;

  const searchQuery = useQuery<DocSearchHit[] | DocSemanticHit[]>({
    queryKey: ["docs-search", mode, projectId, debounced],
    queryFn: async ({ signal }) => {
      // projectId is non-null whenever `enabled`.
      const pid = projectId as string;
      if (mode === "semantic") {
        const res = await fetchDocSemanticSearch(pid, debounced, signal);
        return res.hits;
      }
      const res = await fetchDocSearch(pid, debounced, signal);
      return res.hits;
    },
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  const terms = useMemo(() => splitTerms(debounced), [debounced]);
  // Hits come back already RBAC-scoped; the facet filter narrows them
  // client-side so search obeys the same constraint as the tree.
  const hits = useMemo(
    () => (searchQuery.data ?? []).filter((hit) => matchesFilter(hit.relpath, filter)),
    [searchQuery.data, filter],
  );

  return (
    <div className="flex flex-col gap-3" data-testid="docs-search-panel">
      <Tabs defaultValue="fulltext" value={mode} onValueChange={(v) => setMode(v as SearchMode)}>
        <TabsList data-testid="docs-search-tabs">
          <TabsTrigger value="fulltext" data-testid="docs-search-tab-fulltext">
            Texto
          </TabsTrigger>
          <TabsTrigger value="semantic" data-testid="docs-search-tab-semantic">
            Semántica
          </TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="relative">
        <Search
          className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
          aria-hidden="true"
        />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={
            projectId
              ? mode === "semantic"
                ? "Búsqueda semántica…"
                : "Buscar en la documentación…"
              : "Selecciona un proyecto para buscar"
          }
          disabled={!projectId}
          className="pl-9"
          aria-label="Buscar en la documentación"
          data-testid="docs-search-input"
        />
      </div>

      <SearchResults
        projectId={projectId}
        enabled={enabled}
        debounced={debounced}
        mode={mode}
        terms={terms}
        hits={hits}
        isFetching={searchQuery.isFetching}
        isError={searchQuery.isError}
        error={searchQuery.error}
        selectedPath={selectedPath}
        onOpenDoc={onOpenDoc}
        bookmarks={bookmarks}
      />
    </div>
  );
}

function SearchResults({
  projectId,
  enabled,
  debounced,
  mode,
  terms,
  hits,
  isFetching,
  isError,
  error,
  selectedPath,
  onOpenDoc,
  bookmarks,
}: {
  projectId: string | null;
  enabled: boolean;
  debounced: string;
  mode: SearchMode;
  terms: string[];
  hits: DocSearchHit[] | DocSemanticHit[];
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  selectedPath: string | null;
  onOpenDoc: (projectId: string, relpath: string) => void;
  bookmarks: SearchBookmarkControls;
}) {
  if (!projectId) {
    return (
      <p className="text-muted-foreground px-1 py-2 text-xs italic" data-testid="docs-search-idle">
        Selecciona un proyecto en el árbol para buscar en su documentación.
      </p>
    );
  }

  if (!enabled) {
    return (
      <p className="text-muted-foreground px-1 py-2 text-xs italic" data-testid="docs-search-hint">
        Escribe al menos {MIN_QUERY_LEN} caracteres para buscar.
      </p>
    );
  }

  if (isFetching && hits.length === 0) {
    return (
      <p
        className="text-muted-foreground flex items-center gap-2 px-1 py-2 text-xs"
        data-testid="docs-search-loading"
      >
        <Spinner className="h-3.5 w-3.5" />
        Buscando…
      </p>
    );
  }

  if (isError) {
    return (
      <p className="text-destructive px-1 py-2 text-xs" data-testid="docs-search-error">
        {error instanceof ApiError ? error.body : "No se pudo completar la búsqueda."}
      </p>
    );
  }

  if (hits.length === 0) {
    return (
      <p className="text-muted-foreground px-1 py-2 text-xs italic" data-testid="docs-search-empty">
        Nada coincide con &quot;{debounced}&quot;.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2" data-testid="docs-search-results">
      {hits.map((hit) => (
        <li key={`${hit.document_id}:${hit.chunk_id}`}>
          <SearchHitRow
            hit={hit}
            mode={mode}
            terms={terms}
            active={hit.relpath === selectedPath}
            onOpen={() => onOpenDoc(projectId, hit.relpath)}
            bookmarked={bookmarks.isBookmarked(projectId, hit.relpath)}
            onToggleBookmark={() => bookmarks.onToggleBookmark(projectId, hit.relpath)}
          />
        </li>
      ))}
    </ul>
  );
}

function SearchHitRow({
  hit,
  mode,
  terms,
  active,
  onOpen,
  bookmarked,
  onToggleBookmark,
}: {
  hit: DocSearchHit | DocSemanticHit;
  mode: SearchMode;
  terms: string[];
  active: boolean;
  onOpen: () => void;
  bookmarked: boolean;
  onToggleBookmark: () => void;
}) {
  const score = mode === "semantic" && "score" in hit ? hit.score : null;

  return (
    <div
      className={cn(
        "group relative rounded-lg border transition-colors",
        active
          ? "border-primary/50 bg-primary/5"
          : "border-border hover:border-primary/40 hover:bg-muted/50",
      )}
      data-testid={`docs-search-hit-row-${hit.chunk_id}`}
    >
      <button
        type="button"
        onClick={onOpen}
        className="w-full px-3 py-2 pr-9 text-left"
        data-testid={`docs-search-hit-${hit.chunk_id}`}
      >
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5">
            <FileText className="text-muted-foreground h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span
              className="text-foreground truncate font-mono text-xs"
              data-testid="docs-search-hit-path"
            >
              {hit.relpath}
            </span>
          </span>
          {score !== null && (
            <span
              className="bg-muted text-muted-foreground shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium tabular-nums"
              data-testid="docs-search-hit-score"
              title="Similitud coseno"
            >
              {(score * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <p className="text-muted-foreground line-clamp-3 text-xs leading-relaxed">
          {highlightSnippet(hit.snippet, terms)}
        </p>
      </button>
      <BookmarkStar
        bookmarked={bookmarked}
        onToggle={onToggleBookmark}
        className={cn(
          "absolute right-1.5 top-1.5",
          !bookmarked && "opacity-0 focus-within:opacity-100 group-hover:opacity-100",
        )}
        testid={`docs-search-hit-star-${hit.chunk_id}`}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Highlighting (client-side; the backend snippet is plain text)
// ---------------------------------------------------------------------------

/** Split a query into distinct, lowercased terms for highlighting. */
function splitTerms(query: string): string[] {
  const seen = new Set<string>();
  for (const raw of query.split(/\s+/)) {
    const term = raw.trim().toLowerCase();
    if (term.length >= 2) seen.add(term);
  }
  return Array.from(seen);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Wrap occurrences of any query term in `<mark>` within the plain-text snippet.
 *
 * The backend returns the snippet as plain text (no markup), so we highlight
 * client-side by splitting on a case-insensitive term regex — never injecting
 * HTML. Returns the original string when there is nothing to highlight.
 */
function highlightSnippet(snippet: string, terms: string[]): ReactNode {
  if (terms.length === 0) return snippet;

  const pattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  const parts = snippet.split(pattern);
  if (parts.length <= 1) return snippet;

  return parts.map((part, i) => {
    if (part.length > 0 && terms.includes(part.toLowerCase())) {
      return (
        <mark key={i} className="bg-warning-soft text-warning-soft-foreground rounded px-0.5">
          {part}
        </mark>
      );
    }
    return <span key={i}>{part}</span>;
  });
}
