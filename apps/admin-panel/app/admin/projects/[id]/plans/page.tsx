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
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

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

const ALL_STATUSES = [
  "pending_approval",
  "draft",
  "approved",
  "in_progress",
  "blocked",
  "pending_human_validation",
  "completed",
  "cancelled",
  "rejected",
  "archived",
] as const;

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  draft: "muted",
  pending_approval: "warning",
  approved: "success",
  in_progress: "default",
  blocked: "danger",
  pending_human_validation: "warning",
  completed: "success",
  cancelled: "muted",
  rejected: "danger",
  archived: "muted",
};

const STATUS_LABEL: Record<string, string> = {
  draft: "Borrador",
  pending_approval: "Pendiente de aprobación",
  approved: "Aprobado",
  in_progress: "En progreso",
  blocked: "Bloqueado",
  pending_human_validation: "Pendiente validación humana",
  completed: "Completado",
  cancelled: "Cancelado",
  rejected: "Rechazado",
  archived: "Archivado",
};

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectPlansPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const plansQuery = useQuery({
    queryKey: ["plans", projectId],
    queryFn: () => apiFetch<Plan[]>(`/projects/${projectId}/plans`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  const plans = plansQuery.data ?? [];

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
      <PageHeader
        icon={<ClipboardList className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Planes del proyecto"
        description="Cada plan agrupa fases, tareas y dependencias listas para sincronizar al Kanban."
        actions={
          <Link href={`/admin/projects/${projectId}/chat`}>
            <Button variant="outline" data-testid="open-chat-cta">
              <Plus className="mr-2 h-4 w-4" />
              Generar desde chat
            </Button>
          </Link>
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
        aria-label="Filtrar planes por estado"
      >
        <FilterChip
          label="Todos"
          value="all"
          count={plans.length}
          active={statusFilter === "all"}
          onClick={() => setStatusFilter("all")}
        />
        {ALL_STATUSES.map((s) => (
          <FilterChip
            key={s}
            label={STATUS_LABEL[s]}
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
          <p className="text-muted-foreground text-sm">Cargando planes…</p>
        ) : plansQuery.isError ? (
          <Card>
            <CardHeader>
              <CardTitle>Error al cargar los planes</CardTitle>
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
                {plans.length === 0
                  ? "Este proyecto aún no tiene planes. Empieza una conversación en el chat para generar uno."
                  : "Ningún plan en este estado."}
              </p>
            </CardContent>
          </Card>
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

function PlanRow({ projectId, plan }: { projectId: string; plan: Plan }) {
  const variant = STATUS_VARIANT[plan.status] ?? "muted";
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
            {STATUS_LABEL[plan.status] ?? plan.status}
          </Badge>
        </CardHeader>
        <CardContent>
          {plan.description ? (
            <p className="text-muted-foreground text-sm line-clamp-2">{plan.description}</p>
          ) : (
            <p className="text-muted-foreground/60 text-xs italic">Sin descripción</p>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
