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
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, FileText } from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import { extractToc, fetchDocContent, type DocContent } from "@/lib/docs-api";

import { DocToc } from "./doc-toc";
import { MarkdownRenderer } from "./markdown-renderer";

interface DocViewerPaneProps {
  projectId: string | null;
  path: string | null;
}

export function DocViewerPane({ projectId, path }: DocViewerPaneProps) {
  const enabled = Boolean(projectId && path);

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
      </div>

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
    </div>
  );
}
