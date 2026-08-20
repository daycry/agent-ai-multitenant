"use client";

/**
 * task_03_17 — Pestaña 'Planes' del proyecto.
 *
 * Project-scoped list of plans: state badge, title, descripción, filtros
 * por estado, y link a la vista de detalle (task_03_18). El bus
 * /projects/{id}/plans ya pagina por created_at DESC, así que aquí
 * sólo añadimos UX: filtro cliente sobre el campo `status`.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ClipboardList, Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ViewToggle, type ViewMode } from "@/components/ui/view-toggle";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { MessageKey } from "@/lib/i18n";
import { STATUS_LABEL, STATUS_VARIANT } from "./[planId]/plan-spec-types";

// --------------------------------------------------------------------------
// Types
// --------------------------------------------------------------------------
interface Plan {
  id: string;
  tenant_id: string;
  project_id: string;
  title: string;
  description: string | null;
  status: string;
  conversation_id: string | null;
  specification: Record<string, unknown>;
  created_at: string;
}

// Orden por workflow (ver CLAUDE.md §"Estados Válidos del Frontmatter"):
// draft → pending_approval → approved → in_progress → [blocked] →
// pending_human_validation → completed (o rejected / cancelled) → archived.
const ALL_STATUSES = [
  "draft",
  "pending_approval",
  "approved",
  "in_progress",
  "blocked",
  "pending_human_validation",
  "completed",
  "rejected",
  "cancelled",
  "archived",
] as const;

// `STATUS_VARIANT` y `STATUS_LABEL` viven en `[planId]/plan-spec-types.ts` y se
// importan (prod-16 `task_prod16_03`). Estaban copiados byte a byte en los dos
// ficheros: al traducirlos, dos copias del mismo enum del backend son dos
// oportunidades de divergir, así que se quedó una.

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectPlansPage() {
  const t = useT("plansList");
  const tStatus = useT("planStatus");
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [view, setView] = useState<ViewMode>("list");

  const plansQuery = useQuery({
    queryKey: ["plans", projectId],
    queryFn: () => apiFetch<Plan[]>(`/projects/${projectId}/plans`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  // Referencia estable: `plansQuery.data ?? []` a pelo devuelve un array nuevo en
  // cada render mientras no hay datos, y los dos memos de abajo lo llevan en sus
  // dependencias.
  const plans = useMemo(() => plansQuery.data ?? [], [plansQuery.data]);

  const visible = useMemo(() => {
    if (statusFilter === "all") return plans;
    return plans.filter((p) => p.status === statusFilter);
  }, [plans, statusFilter]);

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const p of plans) {
      map[p.status] = (map[p.status] ?? 0) + 1;
    }
    return map;
  }, [plans]);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <ProjectBreadcrumb projectId={projectId} current={t("breadcrumbCurrent")} />
      <PageHeader
        icon={<ClipboardList className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-2">
            <ViewToggle value={view} onChange={setView} />
            <Link href={`/admin/projects/${projectId}/chat`}>
              <Button variant="outline" data-testid="open-chat-cta">
                <Plus className="mr-2 h-4 w-4" />
                {t("generateFromChat")}
              </Button>
            </Link>
          </div>
        }
        data-testid="plans-tab-header"
      />

      {/* ----------------------------------------------------------------
          Status filter chips
         ---------------------------------------------------------------- */}
      <div
        className="bg-muted mt-6 inline-flex flex-wrap gap-1 rounded-md p-1"
        data-testid="plans-status-filter"
        role="tablist"
        aria-label={t("filterAriaLabel")}
      >
        <FilterChip
          label={t("filterAll")}
          value="all"
          count={plans.length}
          active={statusFilter === "all"}
          onClick={() => setStatusFilter("all")}
        />
        {ALL_STATUSES.map((s) => (
          <FilterChip
            key={s}
            label={tStatus(STATUS_LABEL[s])}
            value={s}
            count={counts[s] ?? 0}
            active={statusFilter === s}
            onClick={() => setStatusFilter(s)}
          />
        ))}
      </div>

      {/* ----------------------------------------------------------------
          List
         ---------------------------------------------------------------- */}
      <div className="mt-6">
        {plansQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">{t("loading")}</p>
        ) : plansQuery.isError ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("errorTitle")}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-destructive text-sm" data-testid="plans-error">
                {plansQuery.error instanceof ApiError
                  ? plansQuery.error.body
                  : String(plansQuery.error)}
              </p>
            </CardContent>
          </Card>
        ) : visible.length === 0 ? (
          <Card>
            <CardContent className="py-8">
              <p className="text-muted-foreground text-sm" data-testid="plans-empty">
                {plans.length === 0 ? t("emptyNoPlans") : t("emptyFiltered")}
              </p>
            </CardContent>
          </Card>
        ) : view === "kanban" ? (
          <div
            className="grid grid-cols-2 gap-3 overflow-x-auto pb-2 sm:grid-cols-3 lg:grid-cols-5"
            data-testid="plans-kanban-columns"
          >
            {ALL_STATUSES.map((s) => (
              <PlanKanbanColumn
                key={s}
                status={s}
                label={tStatus(STATUS_LABEL[s])}
                variant={STATUS_VARIANT[s] ?? "muted"}
                plans={visible.filter((p) => p.status === s)}
                projectId={projectId}
              />
            ))}
          </div>
        ) : (
          <ul className="space-y-3" data-testid="plans-list">
            {visible.map((plan) => (
              <li key={plan.id}>
                <PlanRow projectId={projectId} plan={plan} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Subcomponents
// --------------------------------------------------------------------------
interface FilterChipProps {
  label: string;
  value: string;
  count: number;
  active: boolean;
  onClick: () => void;
}

function FilterChip({ label, value, count, active, onClick }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      role="tab"
      aria-selected={active}
      data-testid={`plans-filter-${value}`}
      data-active={active ? "true" : "false"}
      className={
        active
          ? "bg-background text-foreground rounded px-3 py-1 text-xs font-medium shadow"
          : "text-muted-foreground hover:text-foreground rounded px-3 py-1 text-xs font-medium"
      }
    >
      {label}
      <span className="ml-1 opacity-60" data-testid={`plans-filter-count-${value}`}>
        ({count})
      </span>
    </button>
  );
}

function PlanKanbanColumn({
  status,
  label,
  variant,
  plans,
  projectId,
}: {
  status: string;
  label: string;
  variant: BadgeVariant;
  plans: Plan[];
  projectId: string;
}) {
  return (
    <div
      data-testid={`plans-col-${status}`}
      data-status={status}
      className="bg-muted/40 flex min-h-[10rem] flex-col gap-2 rounded-lg p-2"
    >
      <div className="flex items-center justify-between px-1">
        <Badge variant={variant}>{label}</Badge>
        <span
          className="text-muted-foreground text-xs tabular-nums"
          data-testid={`plans-col-count-${status}`}
        >
          {plans.length}
        </span>
      </div>
      {plans.length === 0 ? (
        <p
          className="text-muted-foreground p-2 text-xs italic"
          data-testid={`plans-col-empty-${status}`}
        >
          —
        </p>
      ) : (
        plans.map((p) => (
          <Link
            key={p.id}
            href={`/admin/projects/${projectId}/plans/${p.id}`}
            data-testid={`plans-card-${p.id}`}
            className="bg-card hover:border-primary/40 rounded-md border p-2 text-sm shadow-sm transition hover:shadow-md"
          >
            <p className="line-clamp-2 font-medium leading-tight">{p.title}</p>
            {p.description && (
              <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">{p.description}</p>
            )}
          </Link>
        ))
      )}
    </div>
  );
}

function PlanRow({ projectId, plan }: { projectId: string; plan: Plan }) {
  const t = useT("plansList");
  const tStatus = useT("planStatus");
  const variant = STATUS_VARIANT[plan.status] ?? "muted";
  const statusKey: MessageKey<"planStatus"> | undefined = STATUS_LABEL[plan.status];
  return (
    <Link
      href={`/admin/projects/${projectId}/plans/${plan.id}`}
      data-testid={`plan-row-${plan.id}`}
      className="block"
    >
      <Card className="transition-colors hover:bg-muted/30">
        <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
          <CardTitle className="flex-1 text-base">{plan.title}</CardTitle>
          <Badge
            variant={variant}
            data-testid={`plan-row-${plan.id}-badge`}
            data-status={plan.status}
          >
            {statusKey ? tStatus(statusKey) : plan.status}
          </Badge>
        </CardHeader>
        <CardContent>
          {plan.description ? (
            <p className="text-muted-foreground text-sm line-clamp-2">{plan.description}</p>
          ) : (
            <p className="text-muted-foreground/60 text-xs italic">{t("noDescription")}</p>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
