"use client";

/**
 * DocsSidebar — left rail of the docs visor (Plan 07 task_07_11).
 *
 * Lists the projects the current user can see (`GET /projects`, already
 * RBAC/RLS-scoped server-side — we never render a project the API hid) and,
 * per project, lazily fetches its canonical doc tree from
 * `GET /projects/{id}/docs/tree` the first time the project section is
 * expanded. Selecting a file bubbles up via `onSelect(projectId, relpath)`;
 * the parent reflects it in the URL so the selection is deep-linkable.
 *
 * Handles loading / error / empty at both levels: the project list and each
 * project's tree.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FolderKanban } from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";
import { cn } from "@/lib/utils";
import { fetchDocsTree } from "@/lib/docs-api";
import { filterTree, isFilterActive, type DocsFilter } from "@/lib/docs-filters";

import { DocsTree } from "./docs-tree";

interface ProjectSummary {
  id: string;
  name: string;
  status: string;
}

/**
 * Bookmark wiring the sidebar threads down to each file row. `onToggleBookmark`
 * here doesn't yet know the project *name* (only its id) — each
 * {@link ProjectSection} closes over its own name before calling up, so the
 * page records a human label with the star.
 */
export interface SidebarBookmarkControls {
  isBookmarked: (projectId: string, relpath: string) => boolean;
  onToggleBookmark: (projectId: string, projectName: string, relpath: string) => void;
}

interface DocsSidebarProps {
  selectedProjectId: string | null;
  selectedPath: string | null;
  onSelect: (projectId: string, relpath: string) => void;
  /** Active facet filter pruning each project's tree. */
  filter: DocsFilter;
  bookmarks: SidebarBookmarkControls;
}

export function DocsSidebar({
  selectedProjectId,
  selectedPath,
  onSelect,
  filter,
  bookmarks,
}: DocsSidebarProps) {
  const t = useT("docs");
  const errorText = useErrorText();
  const projectsQuery = useQuery({
    queryKey: ["projects", "for-docs"],
    queryFn: () => apiFetch<ProjectSummary[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  return (
    <nav
      className="flex h-full flex-col overflow-y-auto p-3"
      aria-label={t("sidebarAria")}
      data-testid="docs-sidebar"
    >
      <p className="text-sidebar-muted-foreground mb-2 px-2 text-xs font-semibold uppercase tracking-wider">
        {t("projectsHeading")}
      </p>

      {projectsQuery.isLoading && (
        <p
          className="text-sidebar-muted-foreground flex items-center gap-2 px-2 py-1 text-xs"
          data-testid="docs-projects-loading"
        >
          <Spinner className="h-3.5 w-3.5" />
          {t("projectsLoading")}
        </p>
      )}

      {projectsQuery.isError && (
        <p className="text-destructive px-2 py-1 text-xs" data-testid="docs-projects-error">
          {errorText(projectsQuery.error)}
        </p>
      )}

      {projectsQuery.data && projectsQuery.data.length === 0 && (
        <p
          className="text-sidebar-muted-foreground px-2 py-1 text-xs italic"
          data-testid="docs-projects-empty"
        >
          {t("projectsEmpty")}
        </p>
      )}

      {projectsQuery.data && projectsQuery.data.length > 0 && (
        <ul className="space-y-1" data-testid="docs-projects-list">
          {projectsQuery.data.map((project) => (
            <li key={project.id}>
              <ProjectSection
                project={project}
                forceOpen={selectedProjectId === project.id}
                selectedProjectId={selectedProjectId}
                selectedPath={selectedPath}
                onSelect={onSelect}
                filter={filter}
                bookmarks={bookmarks}
              />
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}

function ProjectSection({
  project,
  forceOpen,
  selectedProjectId,
  selectedPath,
  onSelect,
  filter,
  bookmarks,
}: {
  project: ProjectSummary;
  forceOpen: boolean;
  selectedProjectId: string | null;
  selectedPath: string | null;
  onSelect: (projectId: string, relpath: string) => void;
  filter: DocsFilter;
  bookmarks: SidebarBookmarkControls;
}) {
  const t = useT("docs");
  const errorText = useErrorText();
  // A project section opens lazily; once opened we fetch its tree. A deep-link
  // (selectedProjectId === this) starts open so the file is reachable.
  const [open, setOpen] = useState(forceOpen);

  const treeQuery = useQuery({
    queryKey: ["docs-tree", project.id],
    queryFn: () => fetchDocsTree(project.id),
    enabled: open,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  // The full tree had docs but the active filter pruned them all → show a hint
  // rather than a blank section (distinct from a genuinely empty project).
  const filtered = treeQuery.data ? filterTree(treeQuery.data, filter) : null;
  const prunedToEmpty =
    filtered !== null &&
    isFilterActive(filter) &&
    filtered.folders.length === 0 &&
    filtered.files.length === 0 &&
    treeQuery.data !== undefined &&
    (treeQuery.data.folders.length > 0 || treeQuery.data.files.length > 0);

  // Bind this project's name into the toggle so the page records a label.
  const treeBookmarks = {
    isBookmarked: bookmarks.isBookmarked,
    onToggleBookmark: (projectId: string, relpath: string) =>
      bookmarks.onToggleBookmark(projectId, project.name, relpath),
  };

  return (
    <div data-testid={`docs-project-${project.id}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "text-sidebar-foreground hover:bg-sidebar-border",
          "flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-sm font-medium transition-colors",
        )}
        aria-expanded={open}
        data-testid={`docs-project-toggle-${project.id}`}
      >
        <ChevronRight
          className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden="true"
        />
        <FolderKanban className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="truncate">{project.name}</span>
      </button>

      {open && (
        <div className="mt-0.5 pl-2">
          {treeQuery.isLoading && (
            <p
              className="text-sidebar-muted-foreground flex items-center gap-2 px-2 py-1 text-xs"
              data-testid={`docs-tree-loading-${project.id}`}
            >
              <Spinner className="h-3.5 w-3.5" />
              {t("treeLoading")}
            </p>
          )}
          {treeQuery.isError && (
            <p
              className="text-destructive px-2 py-1 text-xs"
              data-testid={`docs-tree-error-${project.id}`}
            >
              {errorText(treeQuery.error)}
            </p>
          )}
          {prunedToEmpty && (
            <p
              className="text-sidebar-muted-foreground px-2 py-1 text-xs italic"
              data-testid={`docs-tree-filtered-empty-${project.id}`}
            >
              {t("treeFilteredEmpty")}
            </p>
          )}
          {filtered && !prunedToEmpty && (
            <DocsTree
              projectId={project.id}
              tree={filtered}
              selectedProjectId={selectedProjectId}
              selectedPath={selectedPath}
              onSelect={onSelect}
              bookmarks={treeBookmarks}
            />
          )}
        </div>
      )}
    </div>
  );
}
