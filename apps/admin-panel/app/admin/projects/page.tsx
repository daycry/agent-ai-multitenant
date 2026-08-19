"use client";

/**
 * Listado de proyectos del tenant (sin plantillas: ésas se eligen en el wizard
 * de `/admin/projects/new`).
 *
 * i18n (prod-16 `task_prod16_03`): todo el texto sale del diccionario
 * (`projectsList`). El `status` del proyecto se sigue pintando crudo en el badge
 * a propósito — es el valor del enum del backend, y es lo que el operador filtra
 * en la API y busca en los logs.
 */

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
import { useT } from "@/lib/i18n";

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
  const t = useT("projectsList");
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
        title={t("title")}
        description={t("description")}
        actions={
          <RoleGuard min="tenant_admin">
            <Button asChild>
              <Link href="/admin/projects/new" data-testid="new-project-button">
                <Plus className="mr-1 h-4 w-4" /> {t("newProject")}
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
        loadingLabel={t("loading")}
        errorTitle={t("errorTitle")}
        emptyIcon={FolderKanban}
        empty={
          <Card className="p-8 text-center" data-testid="projects-empty">
            <p className="text-muted-foreground mb-4 text-sm">{t("emptyBody")}</p>
            <RoleGuard min="tenant_admin">
              <Button asChild>
                <Link href="/admin/projects/new">{t("emptyCta")}</Link>
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
                      <p className="text-muted-foreground text-xs italic">{t("noDescription")}</p>
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
