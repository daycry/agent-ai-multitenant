"use client";

/**
 * task_03_18 — Vista de detalle del plan con renderizado de la plantilla
 * canónica.
 *
 * Una sola página densa que renderiza, sobre el JSONB `plan.specification`
 * que persiste task_03_14:
 *
 *   - cabecera (título, estado, descripción, cabecera de coste IA vs humano)
 *   - sumario: alcance, decisiones, riesgos
 *   - fases: la cadena ordenada de fases con sus tareas
 *   - tareas: tabla de la lista plana con role / complejidad / deps
 *   - estimates: calendar / persona-días / costes
 *
 * Las vistas avanzadas (DAG visual task_03_19, Gantt task_03_20, comentarios
 * task_03_21) viven en sus propios módulos y se montan como tabs sobre
 * esta misma página.
 */

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ClipboardList } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { PreviewLauncher } from "@/components/projects/preview-launcher";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { type PlanResponse, STATUS_LABEL, STATUS_VARIANT } from "./plan-spec-types";
import {
  DAGSection,
  EstimatesSection,
  GanttSection,
  PhasesSection,
  SummarySection,
  TasksSection,
} from "./plan-spec-sections";
import { CommentsSection } from "./plan-comments-section";
import { CorrectionsSection } from "./plan-corrections-section";
import { CostBreakdownSection } from "./plan-cost-section";
import { PlanCodeDiffSection } from "./plan-code-diff-section";
import { PlanDeepLinksSection } from "./plan-deep-links-section";
import { PlanLifecycleSection } from "./plan-lifecycle-section";
import { PlanStatusHeader } from "./plan-status-header";
import { SyncToKanbanSection } from "./plan-sync-section";
import { HumanValidationSection } from "./plan-validation-section";

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function PlanDetailPage() {
  const params = useParams<{ id: string; planId: string }>();
  const projectId = params.id;
  const planId = params.planId;

  const planQuery = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => apiFetch<PlanResponse>(`/plans/${planId}`),
    refetchOnWindowFocus: false,
    enabled: Boolean(planId),
  });

  if (planQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <p className="text-muted-foreground text-sm">Cargando plan…</p>
      </div>
    );
  }
  if (planQuery.isError) {
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <Card>
          <CardHeader>
            <CardTitle>Error cargando el plan</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-destructive text-sm" data-testid="plan-detail-error">
              {planQuery.error instanceof ApiError ? planQuery.error.body : String(planQuery.error)}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const plan = planQuery.data!;
  const spec = plan.specification ?? {};
  const variant = STATUS_VARIANT[plan.status] ?? "muted";

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8" data-testid="plan-detail">
      <ProjectBreadcrumb projectId={projectId} current={plan.title} />

      <PageHeader
        icon={<ClipboardList className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={plan.title}
        actions={
          <Badge variant={variant} data-testid="plan-detail-status-badge" data-status={plan.status}>
            {STATUS_LABEL[plan.status] ?? plan.status}
          </Badge>
        }
        data-testid="plan-detail-header"
      />

      {plan.description && (
        <Card className="mt-2" data-testid="plan-description">
          <CardContent className="pt-6 text-sm">{renderPlanDraft(plan.description)}</CardContent>
        </Card>
      )}

      {/* task_wf_30: progreso, PR y coste real ARRIBA — el estado del plan se
          lee sin desplazarse, en vez de estar repartido por cuatro secciones. */}
      <PlanStatusHeader planId={plan.id} />
      <PlanLifecycleSection planId={plan.id} status={plan.status} />
      <HumanValidationSection planId={plan.id} status={plan.status} />
      {/* ADR 0130: preview on-demand de la rama del plan (24h, sin veredicto) —
          útil para re-inspeccionar un plan cuya validación humana ya caducó. */}
      <div className="mt-2">
        <PreviewLauncher scope="plans" id={plan.id} title="Preview de la app (este plan)" />
      </div>
      <CorrectionsSection planId={plan.id} status={plan.status} spec={spec} />
      <PlanDeepLinksSection planId={plan.id} status={plan.status} />
      <PlanCodeDiffSection projectId={projectId} planId={plan.id} />
      <SummarySection summary={spec.summary} />
      <EstimatesSection estimates={spec.estimates} />
      <CostBreakdownSection planId={plan.id} />
      <SyncToKanbanSection
        planId={plan.id}
        status={plan.status}
        phases={spec.phases ?? []}
        taskIds={(spec.tasks ?? []).map((t) => t.id)}
      />
      <PhasesSection phases={spec.phases} tasks={spec.tasks} />
      <DAGSection tasks={spec.tasks} />
      <GanttSection tasks={spec.tasks} />
      <TasksSection tasks={spec.tasks} />
      <CommentsSection planId={plan.id} taskIds={(spec.tasks ?? []).map((t) => t.id)} />
    </div>
  );
}
