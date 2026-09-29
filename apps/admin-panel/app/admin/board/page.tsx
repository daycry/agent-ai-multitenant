"use client";

/**
 * task_ui_11 — `/admin/board` es el PORTFOLIO del tenant: los planes de todos los
 * proyectos en una lista, con la columna Proyecto siempre visible, filtros por
 * estado, proyecto y responsable, y el enlace al tablero de cada proyecto.
 *
 * Deliberadamente NO pinta tareas: el doble Kanban (planes → tareas, CLAUDE.md
 * principio 6) vive dentro del proyecto (`/admin/projects/{id}/board`); un
 * tablero de tareas aquí sería el Kanban plano que el principio prohíbe.
 *
 * Datos: `GET /plans` (tenant entero, paginado exhaustivo) + `GET /projects`
 * para rotular la columna Proyecto. El filtrado es local sobre lo ya cargado:
 * el endpoint admite `?project_id=`/`?status=` pero no un filtro por responsable,
 * y filtrar en tres sitios distintos daría tres semánticas.
 *
 * «Responsable» es `created_by` del plan (el único responsable que el plan
 * expone); llega como id, sin nombre, porque el panel no tiene un directorio de
 * usuarios legible para todos los roles. Se muestra abreviado.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { LayoutGrid } from "lucide-react";

import { STATUS_LABEL as PLAN_STATUS_KEY } from "@/app/admin/projects/[id]/plans/[planId]/plan-spec-types";
import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useT } from "@/lib/i18n";
import { fetchAllPages } from "@/lib/paginate";
import { useErrorText } from "@/lib/use-error-text";

interface Project {
  id: string;
  name: string;
}

interface Plan {
  id: string;
  project_id: string;
  title: string;
  status: string;
  created_by?: string | null;
}

const PLAN_STATUS_VARIANT: Record<string, BadgeVariant> = {
  pending_approval: "muted",
  approved: "info",
  in_progress: "primary",
  blocked: "danger",
  pending_human_validation: "warning",
  completed: "success",
  cancelled: "muted",
  rejected: "danger",
  archived: "muted",
};

const shortId = (id: string) => id.slice(0, 8);

export default function BoardPortfolioPage() {
  const errorText = useErrorText();
  const t = useT("portfolio");
  const tPlan = useT("planStatus");
  const [status, setStatus] = useState("");
  const [projectId, setProjectId] = useState("");
  const [owner, setOwner] = useState("");

  const projectsQuery = useQuery({
    queryKey: ["projects", "tenant"],
    queryFn: () => fetchAllPages<Project>("/projects"),
    refetchOnWindowFocus: false,
  });
  const plansQuery = useQuery({
    queryKey: ["plans", "tenant"],
    queryFn: () => fetchAllPages<Plan>("/plans"),
    refetchOnWindowFocus: false,
  });

  const plans = useMemo(() => plansQuery.data?.items ?? [], [plansQuery.data]);
  const projects = useMemo(() => projectsQuery.data?.items ?? [], [projectsQuery.data]);
  const projectsById = useMemo(() => new Map(projects.map((p) => [p.id, p] as const)), [projects]);
  const owners = useMemo(
    () => Array.from(new Set(plans.map((p) => p.created_by).filter((o): o is string => !!o))),
    [plans],
  );
  // El ciclo de vida completo, no sólo los estados presentes: el filtro es un
  // catálogo estable, y «archivado» debe poder elegirse aunque hoy no haya ninguno.
  const statuses = Object.keys(PLAN_STATUS_KEY);

  const visible = plans.filter(
    (p) =>
      (!status || p.status === status) &&
      (!projectId || p.project_id === projectId) &&
      (!owner || p.created_by === owner),
  );

  const statusLabel = (s: string) => (PLAN_STATUS_KEY[s] ? tPlan(PLAN_STATUS_KEY[s]) : s);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<LayoutGrid className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
      />

      {plansQuery.data?.truncated && (
        <Card className="mb-4 border-amber-500 p-3" data-testid="portfolio-truncated-warning">
          <p className="text-sm text-amber-600 dark:text-amber-400">{t("truncated")}</p>
        </Card>
      )}

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs font-medium">
          {t("filterStatus")}
          <Select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            data-testid="portfolio-filter-status"
          >
            <option value="">{t("allStatuses")}</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium">
          {t("filterProject")}
          <Select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            data-testid="portfolio-filter-project"
          >
            <option value="">{t("allProjects")}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium">
          {t("filterOwner")}
          <Select
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            data-testid="portfolio-filter-owner"
          >
            <option value="">{t("allOwners")}</option>
            {owners.map((o) => (
              <option key={o} value={o}>
                {shortId(o)}
              </option>
            ))}
          </Select>
        </label>
      </div>

      {plansQuery.isLoading && <p className="text-muted-foreground text-sm">{t("loading")}</p>}

      {plansQuery.isError && (
        <Card className="border-destructive p-4">
          <p className="text-destructive text-sm">
            {t("error")} {errorText(plansQuery.error)}
          </p>
        </Card>
      )}

      {plansQuery.data && plans.length === 0 && (
        <Card className="p-8 text-center" data-testid="portfolio-empty-tenant">
          <p className="text-muted-foreground text-sm">{t("empty")}</p>
        </Card>
      )}

      {plans.length > 0 && visible.length === 0 && (
        <Card className="p-8 text-center" data-testid="portfolio-empty">
          <p className="text-muted-foreground text-sm">{t("emptyFiltered")}</p>
        </Card>
      )}

      {visible.length > 0 && (
        <>
          <p className="text-muted-foreground mb-2 text-xs" data-testid="portfolio-count">
            {t(visible.length === 1 ? "countOne" : "countMany", { count: visible.length })}
          </p>
          <Table data-testid="portfolio-table">
            <TableHeader>
              <TableRow>
                <TableHead>{t("colPlan")}</TableHead>
                <TableHead data-testid="portfolio-col-project">{t("colProject")}</TableHead>
                <TableHead>{t("colStatus")}</TableHead>
                <TableHead>{t("colOwner")}</TableHead>
                <TableHead>{t("colActions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((p) => (
                <TableRow key={p.id} data-testid={`portfolio-row-${p.id}`}>
                  <TableCell className="font-medium">
                    <Link
                      href={`/admin/projects/${p.project_id}/plans/${p.id}`}
                      className="hover:underline"
                    >
                      {p.title}
                    </Link>
                  </TableCell>
                  <TableCell data-testid={`portfolio-project-${p.id}`}>
                    {projectsById.get(p.project_id)?.name ?? shortId(p.project_id)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={PLAN_STATUS_VARIANT[p.status] ?? "muted"}>
                      {statusLabel(p.status)}
                    </Badge>
                  </TableCell>
                  <TableCell title={p.created_by ?? undefined}>
                    {p.created_by ? shortId(p.created_by) : t("ownerNone")}
                  </TableCell>
                  <TableCell>
                    <Link
                      href={`/admin/projects/${p.project_id}/board`}
                      className="text-primary text-sm hover:underline"
                      data-testid={`portfolio-board-link-${p.id}`}
                    >
                      {t("openBoard")}
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  );
}
