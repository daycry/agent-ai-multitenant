"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban, Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";

interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  team_id: string | null;
  is_template: boolean;
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "muted"> = {
  active: "success",
  paused: "warning",
  archived: "muted",
};

export default function ProjectsListPage() {
  // Default filter: tenant's own projects (no templates -- those live
  // in the wizard at /admin/projects/new).
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["projects", "tenant"],
    queryFn: () => apiFetch<Project[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<FolderKanban className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Proyectos"
        description="Proyectos activos del tenant. Las plantillas se eligen al crear."
        actions={
          <RoleGuard min="tenant_admin">
            <Button asChild>
              <Link href="/admin/projects/new" data-testid="new-project-button">
                <Plus className="mr-1 h-4 w-4" /> Crear proyecto
              </Link>
            </Button>
          </RoleGuard>
        }
      />

      <StateBlock
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={Boolean(data && data.length === 0)}
        loadingLabel="Cargando proyectos…"
        errorTitle="Could not load projects"
        emptyIcon={FolderKanban}
        empty={
          <Card className="p-8 text-center" data-testid="projects-empty">
            <p className="text-muted-foreground mb-4 text-sm">
              Este tenant aún no tiene proyectos. Empieza desde una plantilla.
            </p>
            <RoleGuard min="tenant_admin">
              <Button asChild>
                <Link href="/admin/projects/new">Crear el primero</Link>
              </Button>
            </RoleGuard>
          </Card>
        }
      >
        {data && data.length > 0 && (
          <div
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
            data-testid="projects-grid"
          >
            {data.map((project) => (
              <Link
                key={project.id}
                href={`/admin/projects/${project.id}`}
                data-testid={`project-link-${project.id}`}
                className="block"
              >
                <Card
                  data-testid={`project-${project.id}`}
                  className="hover:border-primary/40 cursor-pointer transition-colors"
                >
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                    <CardTitle className="text-base">{project.name}</CardTitle>
                    <Badge variant={STATUS_VARIANT[project.status] ?? "muted"}>
                      {project.status}
                    </Badge>
                  </CardHeader>
                  <CardContent>
                    {project.description ? (
                      <p className="text-muted-foreground text-sm">{project.description}</p>
                    ) : (
                      <p className="text-muted-foreground text-xs italic">Sin descripción.</p>
                    )}
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </StateBlock>
    </div>
  );
}
