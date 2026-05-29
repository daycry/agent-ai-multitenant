"use client";

/**
 * Docs visor (Plan 07 Fase D, task_07_11).
 *
 * Cross-project documentation browser. The left rail ({@link DocsSidebar})
 * shows project → canonical folders → `.md` files for every project the user
 * can access; selecting a file records the choice in the URL
 * (`?project=<id>&path=<relpath>`) so it is deep-linkable and survives reload.
 *
 * task_07_11 ships the route + navigable tree only. The main pane shows the
 * selected file's identity plus a placeholder; the Markdown/Mermaid renderer
 * lands in task_07_12 and will read the same `?project`/`?path` params.
 */

import { Suspense, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BookOpen, FileText } from "lucide-react";

import { Breadcrumb } from "@/components/layout/breadcrumb";
import { PageHeader } from "@/components/layout/page-header";

import { DocsSidebar } from "./docs-sidebar";

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
        {/* Sidebar tree */}
        <aside className="bg-sidebar text-sidebar-foreground border-sidebar-border h-[70vh] rounded-xl border">
          <DocsSidebar
            selectedProjectId={selectedProjectId}
            selectedPath={selectedPath}
            onSelect={handleSelect}
          />
        </aside>

        {/* Content pane */}
        <section
          className="bg-card text-card-foreground min-h-[70vh] rounded-xl border p-6"
          data-testid="docs-content-pane"
        >
          {selectedProjectId && selectedPath ? (
            <div data-testid="docs-selected-doc">
              <div className="text-muted-foreground mb-4 flex items-center gap-2 text-sm">
                <FileText className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="break-all font-mono text-xs" data-testid="docs-selected-path">
                  {selectedPath}
                </span>
              </div>
              {/* The Markdown/Mermaid renderer arrives in task_07_12 and will
                  fetch /content for this project+path. Until then we confirm
                  the selection so the route is navigable end-to-end. */}
              <p className="text-muted-foreground text-sm">
                Documento seleccionado. El visor de Markdown se habilita en la siguiente entrega.
              </p>
            </div>
          ) : (
            <div
              className="flex h-full flex-col items-center justify-center text-center"
              data-testid="docs-content-empty"
            >
              <BookOpen className="text-muted-foreground/50 mb-3 h-10 w-10" aria-hidden="true" />
              <p className="text-muted-foreground text-sm">
                Selecciona un documento en el árbol de la izquierda para empezar.
              </p>
            </div>
          )}
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
