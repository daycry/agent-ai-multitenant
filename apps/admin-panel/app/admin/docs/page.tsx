"use client";

/**
 * Docs visor (Plan 07 Fase D, task_07_11).
 *
 * Cross-project documentation browser. The left rail ({@link DocsSidebar})
 * shows project → canonical folders → `.md` files for every project the user
 * can access; selecting a file records the choice in the URL
 * (`?project=<id>&path=<relpath>`) so it is deep-linkable and survives reload.
 *
 * task_07_11 shipped the route + navigable tree; task_07_12 adds the main
 * reading pane ({@link DocViewerPane}): it fetches `/content` for the selected
 * `?project`/`?path` and renders the markdown (GFM + syntax highlight +
 * Mermaid) with an auto-generated table of contents. task_07_13 adds the
 * instant {@link DocsSearchPanel} above the tree: a debounced search box with a
 * full-text / semantic tab; clicking a hit opens that doc in the render pane.
 */

import { Suspense, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BookOpen } from "lucide-react";

import { Breadcrumb } from "@/components/layout/breadcrumb";
import { PageHeader } from "@/components/layout/page-header";

import { DocsSidebar } from "./docs-sidebar";
import { DocsSearchPanel } from "./docs-search-panel";
import { DocViewerPane } from "./doc-viewer-pane";

function DocsVisor() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const selectedProjectId = searchParams.get("project");
  const selectedPath = searchParams.get("path");

  const handleSelect = useCallback(
    (projectId: string, relpath: string) => {
      const params = new URLSearchParams();
      params.set("project", projectId);
      params.set("path", relpath);
      router.replace(`/admin/docs?${params.toString()}`, { scroll: false });
    },
    [router],
  );

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8" data-testid="docs-visor">
      <Breadcrumb items={[{ label: "Inicio", href: "/admin" }, { label: "Documentación" }]} />
      <PageHeader
        icon={<BookOpen className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Documentación"
        description="Explora la documentación de cada proyecto. Selecciona un proyecto en el árbol para ver sus carpetas canónicas y abrir un documento."
      />

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[18rem_1fr]">
        {/* Left column: search + tree */}
        <div className="flex flex-col gap-4">
          <div
            className="bg-card text-card-foreground rounded-xl border p-3"
            data-testid="docs-search"
          >
            <DocsSearchPanel
              projectId={selectedProjectId}
              selectedPath={selectedPath}
              onOpenDoc={handleSelect}
            />
          </div>
          <aside className="bg-sidebar text-sidebar-foreground border-sidebar-border h-[60vh] rounded-xl border">
            <DocsSidebar
              selectedProjectId={selectedProjectId}
              selectedPath={selectedPath}
              onSelect={handleSelect}
            />
          </aside>
        </div>

        {/* Content pane */}
        <section
          className="bg-card text-card-foreground min-h-[70vh] rounded-xl border p-6"
          data-testid="docs-content-pane"
        >
          <DocViewerPane projectId={selectedProjectId} path={selectedPath} />
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
