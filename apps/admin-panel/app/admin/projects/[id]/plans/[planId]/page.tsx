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

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ClipboardList } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { PlanDAG } from "@/lib/plan-dag";
import { PlanGantt } from "@/lib/plan-gantt";

// --------------------------------------------------------------------------
// Types — mirror the backend's PlanResponse + PlanSpecification shape.
// --------------------------------------------------------------------------
interface PlanTaskSpec {
  id: string;
  title: string;
  description?: string;
  complexity?: string;
  role?: string;
  depends_on?: string[];
  estimated_hours?: number;
}

interface PlanPhaseSpec {
  name: string;
  description?: string;
  tasks?: string[];
}

interface PlanSpecification {
  summary?: {
    title?: string;
    description?: string;
    scope_in?: string[];
    scope_out?: string[];
    decisions?: string[];
    risks?: Array<{ name: string; mitigation?: string } | string>;
  };
  phases?: PlanPhaseSpec[];
  tasks?: PlanTaskSpec[];
  estimates?: {
    duration_calendar?: string;
    effort_person_days?: number;
    cost_human_eur?: number | [number, number];
    cost_ai_eur?: number | [number, number];
  };
  metadata?: Record<string, unknown>;
}

interface PlanResponse {
  id: string;
  title: string;
  description: string | null;
  status: string;
  conversation_id: string | null;
  specification: PlanSpecification;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

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

function formatCostRange(value: number | [number, number] | undefined): string | null {
  if (value === undefined) return null;
  if (typeof value === "number") return `${value.toLocaleString("es-ES")} €`;
  const [min, max] = value;
  return `${min.toLocaleString("es-ES")} – ${max.toLocaleString("es-ES")} €`;
}

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
      <Link
        href={`/admin/projects/${projectId}/plans`}
        className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1 text-xs"
        data-testid="plan-detail-back"
      >
        <ChevronLeft className="h-3 w-3" />
        Volver a planes
      </Link>

      <PageHeader
        icon={<ClipboardList className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={plan.title}
        description={plan.description ?? undefined}
        actions={
          <Badge variant={variant} data-testid="plan-detail-status-badge" data-status={plan.status}>
            {STATUS_LABEL[plan.status] ?? plan.status}
          </Badge>
        }
        data-testid="plan-detail-header"
      />

      <SummarySection summary={spec.summary} />
      <EstimatesSection estimates={spec.estimates} />
      <PhasesSection phases={spec.phases} tasks={spec.tasks} />
      <DAGSection tasks={spec.tasks} />
      <GanttSection tasks={spec.tasks} />
      <TasksSection tasks={spec.tasks} />
      <CommentsSection planId={plan.id} taskIds={(spec.tasks ?? []).map((t) => t.id)} />
    </div>
  );
}

// --------------------------------------------------------------------------
// Inline comments (task_03_21)
// --------------------------------------------------------------------------
interface PlanCommentResponse {
  id: string;
  plan_id: string;
  target_kind: string;
  target_ref: string | null;
  author_user_id: string | null;
  content: string;
  created_at: string;
}

function CommentsSection({ planId, taskIds }: { planId: string; taskIds: string[] }) {
  const queryClient = useQueryClient();
  const [targetKind, setTargetKind] = useState<"plan" | "task">("plan");
  const [targetRef, setTargetRef] = useState<string>("");
  const [content, setContent] = useState("");

  const commentsQuery = useQuery({
    queryKey: ["plan-comments", planId],
    queryFn: () => apiFetch<PlanCommentResponse[]>(`/plans/${planId}/comments`),
    refetchOnWindowFocus: false,
  });

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<PlanCommentResponse>(`/plans/${planId}/comments`, {
        method: "POST",
        body:
          targetKind === "task" && targetRef
            ? { target_kind: "task", target_ref: targetRef, content }
            : { target_kind: "plan", content },
      }),
    onSuccess: (created) => {
      queryClient.setQueryData<PlanCommentResponse[]>(["plan-comments", planId], (prev) =>
        prev ? [...prev, created] : [created],
      );
      setContent("");
    },
  });

  const canSubmit =
    content.trim().length > 0 &&
    !mutation.isPending &&
    (targetKind !== "task" || taskIds.includes(targetRef));

  return (
    <Card className="mt-6" data-testid="plan-comments">
      <CardHeader>
        <CardTitle>Comentarios</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="mb-4 space-y-2" data-testid="plan-comments-list">
          {(commentsQuery.data ?? []).map((c) => (
            <li
              key={c.id}
              data-testid={`plan-comment-${c.id}`}
              data-target-kind={c.target_kind}
              data-target-ref={c.target_ref ?? ""}
              className="border-muted rounded border px-3 py-2 text-sm"
            >
              <p className="text-muted-foreground mb-1 text-[10px] uppercase tracking-wide">
                {c.target_kind === "task" ? (
                  <>
                    Sobre tarea <span className="font-mono">{c.target_ref}</span>
                  </>
                ) : c.target_kind === "phase" ? (
                  <>Sobre fase {c.target_ref}</>
                ) : (
                  <>Sobre el plan</>
                )}
              </p>
              <p className="whitespace-pre-wrap">{c.content}</p>
            </li>
          ))}
          {(commentsQuery.data ?? []).length === 0 ? (
            <p className="text-muted-foreground text-xs italic" data-testid="plan-comments-empty">
              Aún no hay comentarios.
            </p>
          ) : null}
        </ul>

        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!canSubmit) return;
            mutation.mutate();
          }}
          data-testid="plan-comment-form"
        >
          <div className="flex gap-2">
            <select
              value={targetKind}
              onChange={(e) => {
                const next = e.target.value as "plan" | "task";
                setTargetKind(next);
                if (next === "task" && taskIds.length > 0) setTargetRef(taskIds[0]);
                else setTargetRef("");
              }}
              data-testid="plan-comment-target-kind"
              className="bg-background border-muted rounded border px-2 py-1 text-sm"
            >
              <option value="plan">Sobre el plan</option>
              <option value="task" disabled={taskIds.length === 0}>
                Sobre una tarea
              </option>
            </select>
            {targetKind === "task" ? (
              <select
                value={targetRef}
                onChange={(e) => setTargetRef(e.target.value)}
                data-testid="plan-comment-target-ref"
                className="bg-background border-muted rounded border px-2 py-1 text-sm font-mono"
              >
                {taskIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Escribe tu comentario…"
            rows={2}
            data-testid="plan-comment-content"
            className="bg-background border-muted w-full resize-none rounded border px-2 py-1 text-sm"
          />
          <div className="flex justify-end">
            <Button type="submit" disabled={!canSubmit} data-testid="plan-comment-submit">
              Comentar
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Gantt visualisation with critical path (task_03_20)
// --------------------------------------------------------------------------
function GanttSection({ tasks }: { tasks: PlanSpecification["tasks"] | undefined }) {
  if (!tasks || tasks.length === 0) return null;
  return (
    <Card className="mt-6" data-testid="plan-gantt">
      <CardHeader>
        <CardTitle>Gantt</CardTitle>
      </CardHeader>
      <CardContent>
        <PlanGantt tasks={tasks} />
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// DAG visualisation (task_03_19)
// --------------------------------------------------------------------------
function DAGSection({ tasks }: { tasks: PlanSpecification["tasks"] | undefined }) {
  if (!tasks || tasks.length === 0) return null;
  return (
    <Card className="mt-6" data-testid="plan-dag">
      <CardHeader>
        <CardTitle>Grafo de dependencias</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <PlanDAG tasks={tasks} />
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Sections
// --------------------------------------------------------------------------
function SummarySection({ summary }: { summary: PlanSpecification["summary"] | undefined }) {
  if (!summary || Object.keys(summary).length === 0) {
    return (
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Resumen</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm italic" data-testid="plan-summary-empty">
            Este plan aún no tiene resumen. La sección se rellenará cuando el equipo termine la
            conversación de planning.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-6" data-testid="plan-summary">
      <CardHeader>
        <CardTitle>Resumen</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {summary.description ? <p className="whitespace-pre-wrap">{summary.description}</p> : null}
        {summary.scope_in && summary.scope_in.length > 0 ? (
          <ScopeList label="En alcance" items={summary.scope_in} testId="plan-scope-in" />
        ) : null}
        {summary.scope_out && summary.scope_out.length > 0 ? (
          <ScopeList label="Fuera de alcance" items={summary.scope_out} testId="plan-scope-out" />
        ) : null}
        {summary.decisions && summary.decisions.length > 0 ? (
          <ScopeList label="Decisiones" items={summary.decisions} testId="plan-decisions" />
        ) : null}
        {summary.risks && summary.risks.length > 0 ? (
          <div data-testid="plan-risks">
            <p className="font-semibold">Riesgos</p>
            <ul className="list-disc pl-5">
              {summary.risks.map((risk, i) => {
                if (typeof risk === "string") return <li key={i}>{risk}</li>;
                return (
                  <li key={i}>
                    <span className="font-medium">{risk.name}</span>
                    {risk.mitigation ? (
                      <span className="text-muted-foreground"> — {risk.mitigation}</span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ScopeList({ label, items, testId }: { label: string; items: string[]; testId: string }) {
  return (
    <div data-testid={testId}>
      <p className="font-semibold">{label}</p>
      <ul className="list-disc pl-5">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function EstimatesSection({
  estimates,
}: {
  estimates: PlanSpecification["estimates"] | undefined;
}) {
  if (!estimates || Object.keys(estimates).length === 0) return null;
  const humanCost = formatCostRange(estimates.cost_human_eur);
  const aiCost = formatCostRange(estimates.cost_ai_eur);

  return (
    <Card className="mt-6" data-testid="plan-estimates">
      <CardHeader>
        <CardTitle>Estimaciones</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <EstimateCell
            label="Duración"
            value={estimates.duration_calendar}
            testId="estimate-duration"
          />
          <EstimateCell
            label="Esfuerzo (persona-días)"
            value={
              estimates.effort_person_days !== undefined
                ? String(estimates.effort_person_days)
                : null
            }
            testId="estimate-effort"
          />
          <EstimateCell label="Coste humano" value={humanCost} testId="estimate-cost-human" />
          <EstimateCell label="Coste IA" value={aiCost} testId="estimate-cost-ai" />
        </dl>
      </CardContent>
    </Card>
  );
}

function EstimateCell({
  label,
  value,
  testId,
}: {
  label: string;
  value: string | null | undefined;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">{label}</dt>
      <dd className="font-medium">{value ?? "—"}</dd>
    </div>
  );
}

function PhasesSection({
  phases,
  tasks,
}: {
  phases: PlanSpecification["phases"] | undefined;
  tasks: PlanSpecification["tasks"] | undefined;
}) {
  if (!phases || phases.length === 0) return null;
  const titleById = new Map<string, string>((tasks ?? []).map((t) => [t.id, t.title]));
  return (
    <Card className="mt-6" data-testid="plan-phases">
      <CardHeader>
        <CardTitle>Fases</CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="space-y-3 list-decimal pl-5">
          {phases.map((phase, i) => (
            <li key={i} data-testid={`plan-phase-${i}`}>
              <p className="font-medium">{phase.name}</p>
              {phase.description ? (
                <p className="text-muted-foreground text-xs">{phase.description}</p>
              ) : null}
              {phase.tasks && phase.tasks.length > 0 ? (
                <ul className="mt-1 text-xs list-disc pl-5">
                  {phase.tasks.map((tid) => (
                    <li key={tid}>
                      <span className="font-mono">{tid}</span>
                      {titleById.has(tid) ? (
                        <span className="text-muted-foreground"> · {titleById.get(tid)}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function TasksSection({ tasks }: { tasks: PlanSpecification["tasks"] | undefined }) {
  if (!tasks || tasks.length === 0) return null;
  return (
    <Card className="mt-6" data-testid="plan-tasks">
      <CardHeader>
        <CardTitle>Tareas ({tasks.length})</CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-xs">
          <thead className="text-left">
            <tr className="border-muted border-b">
              <th className="py-1 pr-2 font-semibold">ID</th>
              <th className="py-1 pr-2 font-semibold">Título</th>
              <th className="py-1 pr-2 font-semibold">Rol</th>
              <th className="py-1 pr-2 font-semibold">Compl.</th>
              <th className="py-1 pr-2 font-semibold">Depende de</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr
                key={task.id}
                className="border-muted/40 border-b align-top"
                data-testid={`plan-task-${task.id}`}
              >
                <td className="py-1 pr-2 font-mono">{task.id}</td>
                <td className="py-1 pr-2">{task.title}</td>
                <td className="py-1 pr-2">{task.role ?? "—"}</td>
                <td className="py-1 pr-2">{task.complexity ?? "—"}</td>
                <td className="py-1 pr-2 font-mono">
                  {task.depends_on && task.depends_on.length > 0 ? task.depends_on.join(", ") : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
