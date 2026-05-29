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
import { ApiError, apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import { fetchDocsTree } from "@/lib/docs-api";

import { DocsTree } from "./docs-tree";

interface ProjectSummary {
  id: string;
  name: string;
  status: string;
}

interface DocsSidebarProps {
  selectedProjectId: string | null;
  selectedPath: string | null;
  onSelect: (projectId: string, relpath: string) => void;
}

export function DocsSidebar({ selectedProjectId, selectedPath, onSelect }: DocsSidebarProps) {
  const projectsQuery = useQuery({
    queryKey: ["projects", "for-docs"],
    queryFn: () => apiFetch<ProjectSummary[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  return (
    <nav
      className="flex h-full flex-col overflow-y-auto p-3"
      aria-label="Árbol de documentación"
      data-testid="docs-sidebar"
    >
      <p className="text-sidebar-muted-foreground mb-2 px-2 text-xs font-semibold uppercase tracking-wider">
        Proyectos
      </p>

      {projectsQuery.isLoading && (
        <p
          className="text-sidebar-muted-foreground flex items-center gap-2 px-2 py-1 text-xs"
          data-testid="docs-projects-loading"
        >
          <Spinner className="h-3.5 w-3.5" />
          Cargando proyectos…
        </p>
      )}

      {projectsQuery.isError && (
        <p className="text-destructive px-2 py-1 text-xs" data-testid="docs-projects-error">
          {projectsQuery.error instanceof ApiError
            ? projectsQuery.error.body
            : "No se pudieron cargar los proyectos."}
        </p>
      )}

      {projectsQuery.data && projectsQuery.data.length === 0 && (
        <p
          className="text-sidebar-muted-foreground px-2 py-1 text-xs italic"
          data-testid="docs-projects-empty"
        >
          No tienes proyectos accesibles.
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
}: {
  project: ProjectSummary;
  forceOpen: boolean;
  selectedProjectId: string | null;
  selectedPath: string | null;
  onSelect: (projectId: string, relpath: string) => void;
}) {
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
              Cargando árbol…
            </p>
          )}
          {treeQuery.isError && (
            <p
              className="text-destructive px-2 py-1 text-xs"
              data-testid={`docs-tree-error-${project.id}`}
            >
              {treeQuery.error instanceof ApiError
                ? treeQuery.error.body
                : "No se pudo cargar el árbol."}
            </p>
          )}
          {treeQuery.data && (
            <DocsTree
              projectId={project.id}
              tree={treeQuery.data}
              selectedProjectId={selectedProjectId}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          )}
        </div>
      )}
    </div>
  );
}
