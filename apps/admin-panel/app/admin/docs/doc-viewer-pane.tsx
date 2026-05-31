"use client";

/**
 * DocViewerPane — the docs visor's main reading pane (Plan 07 task_07_12).
 *
 * Given the selected `projectId` + `path`, fetches the doc's raw markdown from
 * `GET /projects/{id}/docs/content?path=` (RBAC-gated server-side: a path in a
 * project the user can't see is a 404/403 from the API, surfaced here as an
 * error state) and renders it with {@link MarkdownRenderer} plus an
 * auto-generated {@link DocToc}.
 *
 * States handled: nothing selected (empty), loading, error (with the API
 * message), and rendered. The TOC rail only shows once there's content with
 * at least one heading.
 *
 * task_07_16 adds a per-pane mode toggle (Documento / Comparar): "Comparar"
 * swaps the markdown render for {@link DocDiffView}, which compares two git
 * refs of the same `.md`. The toggle resets to "Documento" whenever the open
 * doc changes.
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { BookOpen, FileText, GitCompare } from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/lib/api";
import { extractToc, fetchDocContent, type DocContent, type TocEntry } from "@/lib/docs-api";

import { BookmarkStar } from "./docs-bookmarks-view";
import { DocDiffView } from "./doc-diff-view";
import { DocToc } from "./doc-toc";
import { MarkdownRenderer } from "./markdown-renderer";

type ViewerMode = "read" | "diff";

interface DocViewerPaneProps {
  projectId: string | null;
  path: string | null;
  /** Whether the open doc is starred (controlled by the page). */
  bookmarked?: boolean;
  /** Toggle the open doc's bookmark; absent → no star is shown. */
  onToggleBookmark?: () => void;
}

export function DocViewerPane({
  projectId,
  path,
  bookmarked = false,
  onToggleBookmark,
}: DocViewerPaneProps) {
  const enabled = Boolean(projectId && path);
  const [mode, setMode] = useState<ViewerMode>("read");

  // A new doc selection always returns to reading mode — a comparison set up
  // for the previous file would be meaningless for a different one.
  useEffect(() => {
    setMode("read");
  }, [projectId, path]);

  const contentQuery = useQuery<DocContent>({
    // projectId/path are non-null whenever `enabled`; assert for the key.
    queryKey: ["docs-content", projectId, path],
    queryFn: () => fetchDocContent(projectId as string, path as string),
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const toc = useMemo(
    () => (contentQuery.data ? extractToc(contentQuery.data.content) : []),
    [contentQuery.data],
  );

  if (!enabled) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center text-center"
        data-testid="docs-content-empty"
      >
        <BookOpen className="text-muted-foreground/50 mb-3 h-10 w-10" aria-hidden="true" />
        <p className="text-muted-foreground text-sm">
          Selecciona un documento en el árbol de la izquierda para empezar.
        </p>
      </div>
    );
  }

  return (
    <div data-testid="docs-selected-doc">
      <div className="text-muted-foreground mb-4 flex items-center gap-2 text-sm">
        <FileText className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="break-all font-mono text-xs" data-testid="docs-selected-path">
          {path}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Tabs
            defaultValue="read"
            value={mode}
            onValueChange={(value) => setMode(value as ViewerMode)}
          >
            <TabsList data-testid="docs-viewer-mode-tabs">
              <TabsTrigger value="read" data-testid="docs-viewer-mode-read">
                <FileText className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Documento
              </TabsTrigger>
              <TabsTrigger value="diff" data-testid="docs-viewer-mode-diff">
                <GitCompare className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Comparar
              </TabsTrigger>
            </TabsList>
          </Tabs>
          {onToggleBookmark && (
            <BookmarkStar
              bookmarked={bookmarked}
              onToggle={onToggleBookmark}
              testid="docs-viewer-star"
            />
          )}
        </div>
      </div>

      {mode === "diff" ? (
        <DocDiffView projectId={projectId} path={path} />
      ) : (
        <ReadMode contentQuery={contentQuery} toc={toc} />
      )}
    </div>
  );
}

function ReadMode({
  contentQuery,
  toc,
}: {
  contentQuery: UseQueryResult<DocContent>;
  toc: TocEntry[];
}) {
  return (
    <>
      {contentQuery.isLoading && (
        <div
          className="text-muted-foreground flex items-center gap-2 py-8 text-sm"
          data-testid="docs-content-loading"
        >
          <Spinner className="h-4 w-4" />
          Cargando documento…
        </div>
      )}

      {contentQuery.isError && (
        <div
          className="border-destructive/40 bg-destructive/5 text-destructive rounded-lg border p-4 text-sm"
          data-testid="docs-content-error"
        >
          {contentQuery.error instanceof ApiError && contentQuery.error.status === 404
            ? "El documento no existe o no es accesible."
            : contentQuery.error instanceof ApiError
              ? contentQuery.error.body
              : "No se pudo cargar el documento."}
        </div>
      )}

      {contentQuery.data && (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_14rem]">
          <article className="min-w-0">
            <MarkdownRenderer content={contentQuery.data.content} />
          </article>
          {toc.length > 0 && (
            <aside className="hidden xl:block">
              <div className="sticky top-6">
                <DocToc entries={toc} />
              </div>
            </aside>
          )}
        </div>
      )}
    </>
  );
}
