"use client";

/**
 * Docs visor (Plan 07 Fase D).
 *
 * Cross-project documentation browser. The left rail has two tabs:
 *
 *   * **Explorar** — instant {@link DocsSearchPanel} + facet
 *     {@link DocsFiltersPanel} + the navigable {@link DocsSidebar} tree.
 *   * **Marcadores** — the {@link DocsBookmarksView} of starred docs.
 *
 * Selecting a file records the choice in the URL (`?project=<id>&path=<relpath>`)
 * so it is deep-linkable and survives reload. The main pane
 * ({@link DocViewerPane}) renders the selected doc's markdown with a TOC.
 *
 * task_07_11 shipped the route + tree; 07_12 the render pane; 07_13 the search
 * panel. task_07_15 adds: facet filters (category + type) that prune the tree
 * and search hits, and a tenant-scoped client-side bookmarks feature — star a
 * doc from the tree / a search hit / the viewer header; starred docs are listed
 * in the Marcadores tab and persisted in `localStorage`
 * ({@link lib/docs-bookmarks}). Bookmarks live in `localStorage`, so the page is
 * the single owner of that state: it re-reads after every toggle and passes the
 * list + controls down.
 */

import { Suspense, useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Bookmark, FolderTree } from "lucide-react";

import { Breadcrumb } from "@/components/layout/breadcrumb";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiFetch } from "@/lib/api";
import { EMPTY_FILTER, type DocsFilter } from "@/lib/docs-filters";
import {
  getBookmarks,
  isBookmarked as readIsBookmarked,
  toggleBookmark,
  type DocBookmark,
} from "@/lib/docs-bookmarks";
import { useRouter, useSearchParams } from "next/navigation";

import { DocsSidebar } from "./docs-sidebar";
import { DocsSearchPanel } from "./docs-search-panel";
import { DocsFiltersPanel } from "./docs-filters-panel";
import { DocsBookmarksView } from "./docs-bookmarks-view";
import { DocViewerPane } from "./doc-viewer-pane";

interface ProjectSummary {
  id: string;
  name: string;
  status: string;
}

function DocsVisor() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const selectedProjectId = searchParams.get("project");
  const selectedPath = searchParams.get("path");

  const [filter, setFilter] = useState<DocsFilter>(EMPTY_FILTER);

  // localStorage is the source of truth; mirror it in state so the UI reflects
  // a toggle immediately. `version` bumps to force a re-read after each write.
  const [bookmarks, setBookmarks] = useState<DocBookmark[]>(() => getBookmarks());
  const refreshBookmarks = useCallback(() => setBookmarks(getBookmarks()), []);

  // Project names resolve a bookmark's human label (cached by the sidebar too).
  const projectsQuery = useQuery({
    queryKey: ["projects", "for-docs"],
    queryFn: () => apiFetch<ProjectSummary[]>("/projects"),
    refetchOnWindowFocus: false,
  });
  const projectName = useCallback(
    (projectId: string): string => {
      const fromApi = projectsQuery.data?.find((p) => p.id === projectId)?.name;
      if (fromApi) return fromApi;
      const fromBookmark = bookmarks.find((b) => b.projectId === projectId)?.projectName;
      return fromBookmark ?? projectId;
    },
    [projectsQuery.data, bookmarks],
  );

  const handleSelect = useCallback(
    (projectId: string, relpath: string) => {
      const params = new URLSearchParams();
      params.set("project", projectId);
      params.set("path", relpath);
      router.replace(`/admin/docs?${params.toString()}`, { scroll: false });
    },
    [router],
  );

  // Bookmark controls. Reading uses the live state (so the UI re-renders on a
  // toggle); writing goes through the tenant-scoped store + re-reads.
  const isBookmarked = useCallback(
    (projectId: string, relpath: string): boolean =>
      bookmarks.some((b) => b.projectId === projectId && b.relpath === relpath),
    [bookmarks],
  );
  const toggle = useCallback(
    (projectId: string, name: string, relpath: string) => {
      toggleBookmark(projectId, name, relpath);
      refreshBookmarks();
    },
    [refreshBookmarks],
  );
  const removeFromView = useCallback(
    (projectId: string, relpath: string) => {
      // `toggleBookmark` removes when present; the view only shows starred docs.
      if (readIsBookmarked(projectId, relpath)) {
        toggleBookmark(projectId, projectName(projectId), relpath);
        refreshBookmarks();
      }
    },
    [projectName, refreshBookmarks],
  );

  const sidebarBookmarks = useMemo(
    () => ({ isBookmarked, onToggleBookmark: toggle }),
    [isBookmarked, toggle],
  );
  const searchBookmarks = useMemo(
    () => ({
      isBookmarked,
      onToggleBookmark: (projectId: string, relpath: string) =>
        toggle(projectId, projectName(projectId), relpath),
    }),
    [isBookmarked, toggle, projectName],
  );

  const selectedBookmarked =
    selectedProjectId !== null && selectedPath !== null
      ? isBookmarked(selectedProjectId, selectedPath)
      : false;
  const toggleSelected = useMemo(() => {
    if (selectedProjectId === null || selectedPath === null) return undefined;
    return () => toggle(selectedProjectId, projectName(selectedProjectId), selectedPath);
  }, [selectedProjectId, selectedPath, toggle, projectName]);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8" data-testid="docs-visor">
      <Breadcrumb items={[{ label: "Inicio", href: "/admin" }, { label: "Documentación" }]} />
      <PageHeader
        icon={<BookOpen className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Documentación"
        description="Explora la documentación de cada proyecto. Filtra por categoría o tipo, busca en el texto y marca documentos para encontrarlos rápido."
      />

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[20rem_1fr]">
        {/* Left column: tabbed Explorar / Marcadores */}
        <div className="flex flex-col gap-4">
          <Tabs defaultValue="explore">
            <TabsList className="w-full" data-testid="docs-rail-tabs">
              <TabsTrigger value="explore" className="flex-1" data-testid="docs-rail-tab-explore">
                <FolderTree className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Explorar
              </TabsTrigger>
              <TabsTrigger
                value="bookmarks"
                className="flex-1"
                data-testid="docs-rail-tab-bookmarks"
              >
                <Bookmark className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Marcadores
                {bookmarks.length > 0 && (
                  <span
                    className="bg-primary/10 text-primary ml-1.5 rounded-full px-1.5 text-[10px] font-medium tabular-nums"
                    data-testid="docs-bookmarks-count"
                  >
                    {bookmarks.length}
                  </span>
                )}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="explore" className="flex flex-col gap-4">
              <div
                className="bg-card text-card-foreground rounded-xl border p-3"
                data-testid="docs-search"
              >
                <DocsSearchPanel
                  projectId={selectedProjectId}
                  selectedPath={selectedPath}
                  onOpenDoc={handleSelect}
                  filter={filter}
                  bookmarks={searchBookmarks}
                />
              </div>
              <div className="bg-card text-card-foreground rounded-xl border p-3">
                <DocsFiltersPanel
                  filter={filter}
                  onChange={setFilter}
                  disabled={!selectedProjectId}
                />
              </div>
              <aside className="bg-sidebar text-sidebar-foreground border-sidebar-border h-[60vh] rounded-xl border">
                <DocsSidebar
                  selectedProjectId={selectedProjectId}
                  selectedPath={selectedPath}
                  onSelect={handleSelect}
                  filter={filter}
                  bookmarks={sidebarBookmarks}
                />
              </aside>
            </TabsContent>

            <TabsContent value="bookmarks">
              <div
                className="bg-card text-card-foreground rounded-xl border p-3"
                data-testid="docs-bookmarks"
              >
                <DocsBookmarksView
                  bookmarks={bookmarks}
                  selectedProjectId={selectedProjectId}
                  selectedPath={selectedPath}
                  onOpenDoc={handleSelect}
                  onRemove={removeFromView}
                />
              </div>
            </TabsContent>
          </Tabs>
        </div>

        {/* Content pane */}
        <section
          className="bg-card text-card-foreground min-h-[70vh] rounded-xl border p-6"
          data-testid="docs-content-pane"
        >
          <DocViewerPane
            projectId={selectedProjectId}
            path={selectedPath}
            bookmarked={selectedBookmarked}
            onToggleBookmark={toggleSelected}
          />
        </section>
      </div>
    </div>
  );
}

export default function DocsPage() {
  // useSearchParams needs a Suspense boundary in the App Router.
  return (
    <Suspense fallback={null}>
      <DocsVisor />
    </Suspense>
  );
}
