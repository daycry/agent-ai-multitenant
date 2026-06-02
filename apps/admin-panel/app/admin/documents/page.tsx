"use client";

/**
 * Documents index (Plan 06.6 task_06_6_11).
 *
 * El backend no expone un listing global de documentos — cada
 * documento vive bajo una Knowledge Base de un Project. Esta página
 * lista los proyectos y enlaza a su sección KBs, desde donde se
 * accede a los documentos.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { FileText, FolderKanban } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
}

export default function DocumentsIndexPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["projects", "for-documents"],
    queryFn: () => apiFetch<Project[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  return (
    <div
      className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="documents-index"
    >
      <PageHeader
        icon={<FileText className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Documentos"
        description="Los documentos viven dentro de Knowledge Bases de cada proyecto. Selecciona un proyecto para ver sus KBs."
      />

      <StateBlock
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={Boolean(data && data.length === 0)}
        loadingLabel="Cargando proyectos…"
        loadingTestId="documents-loading"
        errorTitle="No se pudieron cargar los proyectos"
        errorTestId="documents-error"
        emptyTestId="documents-empty"
        empty={
          <Card className="p-8 text-center">
            <p className="text-muted-foreground text-sm">
              Este tenant aún no tiene proyectos. Crea uno desde{" "}
              <Link href="/admin/projects/new" className="text-primary underline">
                /admin/projects/new
              </Link>{" "}
              para empezar.
            </p>
          </Card>
        }
      >
        <div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
          data-testid="documents-projects-grid"
        >
          {(data ?? []).map((project) => (
            <Link
              key={project.id}
              href={`/admin/projects/${project.id}/knowledge-bases`}
              data-testid={`documents-project-link-${project.id}`}
              className="block"
            >
              <Card className="hover:border-primary/40 h-full cursor-pointer transition-colors">
                <CardHeader className="flex flex-row items-center gap-2 space-y-0">
                  <FolderKanban className="text-muted-foreground h-5 w-5" />
                  <CardTitle className="text-base">{project.name}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-muted-foreground text-sm">
                    {project.description ?? "Sin descripción."}
                  </p>
                  <p className="text-muted-foreground mt-2 text-xs italic">
                    Click para ver Knowledge Bases / documentos →
                  </p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </StateBlock>
    </div>
  );
}
