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
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  ExternalLink,
  Rocket,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, apiFetch } from "@/lib/api";
import { PlanDAG } from "@/lib/plan-dag";
import { PlanGantt } from "@/lib/plan-gantt";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { renderPlanDraft } from "@/lib/plan-draft-md";

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

// Orden por workflow (ver CLAUDE.md §"Estados Válidos del Frontmatter"):
// draft → pending_approval → approved → in_progress → [blocked] →
// pending_human_validation → completed (o rejected / cancelled) → archived.
const STATUS_VARIANT: Record<string, BadgeVariant> = {
  draft: "muted",
  pending_approval: "warning",
  approved: "success",
  in_progress: "default",
  blocked: "danger",
  pending_human_validation: "warning",
  completed: "success",
  rejected: "danger",
  cancelled: "muted",
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
  rejected: "Rechazado",
  cancelled: "Cancelado",
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

      <HumanValidationSection planId={plan.id} status={plan.status} />
      <PlanDeepLinksSection planId={plan.id} status={plan.status} />
      <SummarySection summary={spec.summary} />
      <EstimatesSection estimates={spec.estimates} />
      <CostBreakdownSection planId={plan.id} />
      <SyncToKanbanSection
        planId={plan.id}
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

// --------------------------------------------------------------------------
// Sync to Kanban (task_03_27)
// --------------------------------------------------------------------------
type SyncScope = "total" | "phase" | "selection";

interface SyncResponse {
  created_task_ids: Record<string, string>;
  skipped_task_ids: Record<string, string>;
  dependencies_created: number;
}

function SyncToKanbanSection({
  planId,
  phases,
  taskIds,
}: {
  planId: string;
  phases: PlanPhaseSpec[];
  taskIds: string[];
}) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<SyncScope>("total");
  const [phaseIndex, setPhaseIndex] = useState<number>(0);
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [lastResult, setLastResult] = useState<SyncResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => {
      const body: { scope: SyncScope; phase_index?: number; task_ids?: string[] } = {
        scope,
      };
      if (scope === "phase") body.phase_index = phaseIndex;
      if (scope === "selection") body.task_ids = Array.from(selection);
      return apiFetch<SyncResponse>(`/plans/${planId}/sync-to-kanban`, {
        method: "POST",
        body,
      });
    },
    onSuccess: (data) => {
      setLastResult(data);
      setErrorMsg(null);
      // The Kanban tab caches its tasks query — invalidate so the UI
      // reflects the freshly-materialised cards.
      queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
    },
    onError: (err) => {
      setLastResult(null);
      setErrorMsg(err instanceof ApiError ? err.body : String(err));
    },
  });

  const canSubmit =
    !mutation.isPending &&
    (scope !== "selection" || selection.size > 0) &&
    (scope !== "phase" || (phases.length > 0 && phaseIndex >= 0 && phaseIndex < phases.length));

  return (
    <Card className="mt-6" data-testid="plan-sync-to-kanban">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Sincronizar al Kanban</CardTitle>
        <Button
          onClick={() => {
            setLastResult(null);
            setErrorMsg(null);
            setOpen(true);
          }}
          disabled={taskIds.length === 0}
          data-testid="plan-sync-open"
        >
          Sincronizar al Kanban
        </Button>
      </CardHeader>
      <CardContent>
        {taskIds.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="plan-sync-empty">
            El plan aún no tiene tareas para materializar.
          </p>
        ) : lastResult ? (
          <SyncResultLine result={lastResult} />
        ) : (
          <p className="text-muted-foreground text-sm">
            Materializa las tareas del plan como tarjetas del Kanban. Puedes sincronizar el plan
            completo, una fase concreta o una selección.
          </p>
        )}
      </CardContent>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!mutation.isPending) setOpen(next);
        }}
      >
        <DialogContent data-testid="plan-sync-dialog">
          <DialogHeader>
            <DialogTitle>Sincronizar al Kanban</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <fieldset className="flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2" data-testid="plan-sync-scope-total-row">
                <input
                  type="radio"
                  name="sync-scope"
                  value="total"
                  checked={scope === "total"}
                  onChange={() => setScope("total")}
                  data-testid="plan-sync-scope-total"
                />
                <span>Plan completo ({taskIds.length} tareas)</span>
              </label>
              <label className="flex items-center gap-2" data-testid="plan-sync-scope-phase-row">
                <input
                  type="radio"
                  name="sync-scope"
                  value="phase"
                  checked={scope === "phase"}
                  onChange={() => setScope("phase")}
                  disabled={phases.length === 0}
                  data-testid="plan-sync-scope-phase"
                />
                <span>Una fase</span>
                {scope === "phase" ? (
                  <select
                    value={phaseIndex}
                    onChange={(e) => setPhaseIndex(Number(e.target.value))}
                    data-testid="plan-sync-phase-select"
                    className="bg-background border-muted rounded border px-2 py-1 text-xs"
                  >
                    {phases.map((p, i) => (
                      <option key={i} value={i}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                ) : null}
              </label>
              <label
                className="flex items-center gap-2"
                data-testid="plan-sync-scope-selection-row"
              >
                <input
                  type="radio"
                  name="sync-scope"
                  value="selection"
                  checked={scope === "selection"}
                  onChange={() => setScope("selection")}
                  data-testid="plan-sync-scope-selection"
                />
                <span>Selección custom</span>
              </label>

              {scope === "selection" ? (
                <ul
                  className="border-muted mt-1 max-h-48 overflow-y-auto rounded border px-2 py-1 text-xs"
                  data-testid="plan-sync-selection-list"
                >
                  {taskIds.map((tid) => (
                    <li key={tid} className="flex items-center gap-2 py-0.5">
                      <input
                        type="checkbox"
                        checked={selection.has(tid)}
                        onChange={(e) => {
                          const next = new Set(selection);
                          if (e.target.checked) next.add(tid);
                          else next.delete(tid);
                          setSelection(next);
                        }}
                        data-testid={`plan-sync-selection-${tid}`}
                      />
                      <span className="font-mono">{tid}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </fieldset>

            {errorMsg ? (
              <p className="text-destructive text-xs" data-testid="plan-sync-error">
                {errorMsg}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
              data-testid="plan-sync-cancel"
            >
              Cancelar
            </Button>
            <Button
              onClick={() => mutation.mutate()}
              disabled={!canSubmit}
              data-testid="plan-sync-confirm"
            >
              {mutation.isPending ? "Sincronizando…" : "Sincronizar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function SyncResultLine({ result }: { result: SyncResponse }) {
  const created = Object.keys(result.created_task_ids).length;
  const skipped = Object.keys(result.skipped_task_ids).length;
  return (
    <p className="text-sm" data-testid="plan-sync-result">
      Materializadas <span className="font-semibold">{created}</span> tareas nuevas,{" "}
      <span className="font-semibold">{skipped}</span> ya existían.{" "}
      <span className="text-muted-foreground">
        {result.dependencies_created} dependencias creadas.
      </span>
    </p>
  );
}

// --------------------------------------------------------------------------
// Cost breakdown (task_03_24)
// --------------------------------------------------------------------------
interface CostBreakdownTaskHuman {
  task_id: string;
  title: string;
  hours: string;
  cost: string;
}

interface CostBreakdownTaskAI {
  task_id: string;
  title: string;
  complexity: string;
  model_id: string;
  tokens_in_min: number;
  tokens_in_max: number;
  tokens_out_min: number;
  tokens_out_max: number;
  cost_min: string;
  cost_max: string;
}

interface CostBreakdownResponse {
  human: {
    currency: string;
    hourly_rate: string;
    total_hours: string;
    total_cost: string;
    tasks: CostBreakdownTaskHuman[];
  };
  ai: {
    currency: string;
    default_model_id: string;
    cost_min: string;
    cost_max: string;
    tasks: CostBreakdownTaskAI[];
    missing_models: string[];
  };
}

function CostBreakdownSection({ planId }: { planId: string }) {
  const query = useQuery({
    queryKey: ["plan-cost-breakdown", planId],
    queryFn: () => apiFetch<CostBreakdownResponse>(`/plans/${planId}/cost-breakdown`),
    refetchOnWindowFocus: false,
  });

  if (query.isLoading) {
    return (
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Desglose de coste</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">Calculando…</p>
        </CardContent>
      </Card>
    );
  }
  if (query.isError || !query.data) {
    return null;
  }

  const { human, ai } = query.data;
  const noTasks = human.tasks.length === 0 && ai.tasks.length === 0;

  return (
    <Card className="mt-6" data-testid="plan-cost-breakdown">
      <CardHeader>
        <CardTitle>Desglose de coste</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {noTasks ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid="plan-cost-breakdown-empty"
          >
            El plan aún no tiene tareas para calcular el coste.
          </p>
        ) : (
          <>
            {/* Human cost table */}
            <div data-testid="plan-cost-human">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
                Coste humano · {human.currency} · {human.hourly_rate} {human.currency}/h
              </p>
              <table className="w-full text-xs">
                <thead className="text-left">
                  <tr className="border-muted border-b">
                    <th className="py-1 pr-2 font-semibold">ID</th>
                    <th className="py-1 pr-2 font-semibold">Tarea</th>
                    <th className="py-1 pr-2 font-semibold text-right">Horas</th>
                    <th className="py-1 pr-2 font-semibold text-right">Coste</th>
                  </tr>
                </thead>
                <tbody>
                  {human.tasks.map((t) => (
                    <tr
                      key={t.task_id}
                      data-testid={`plan-cost-human-row-${t.task_id}`}
                      className="border-muted/40 border-b"
                    >
                      <td className="py-1 pr-2 font-mono">{t.task_id}</td>
                      <td className="py-1 pr-2">{t.title}</td>
                      <td className="py-1 pr-2 text-right">{t.hours}</td>
                      <td className="py-1 pr-2 text-right">
                        {t.cost} {human.currency}
                      </td>
                    </tr>
                  ))}
                  <tr className="font-semibold">
                    <td colSpan={2} className="py-1 pr-2 text-right">
                      Total
                    </td>
                    <td className="py-1 pr-2 text-right" data-testid="plan-cost-human-total-hours">
                      {human.total_hours}
                    </td>
                    <td className="py-1 pr-2 text-right" data-testid="plan-cost-human-total">
                      {human.total_cost} {human.currency}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* AI cost table — range (min / max) */}
            <div data-testid="plan-cost-ai">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
                Coste IA · {ai.currency} · modelo por defecto{" "}
                <span className="font-mono">{ai.default_model_id}</span>
              </p>
              <table className="w-full text-xs">
                <thead className="text-left">
                  <tr className="border-muted border-b">
                    <th className="py-1 pr-2 font-semibold">ID</th>
                    <th className="py-1 pr-2 font-semibold">Tarea</th>
                    <th className="py-1 pr-2 font-semibold">Compl.</th>
                    <th className="py-1 pr-2 font-semibold">Modelo</th>
                    <th className="py-1 pr-2 font-semibold text-right">Coste mín</th>
                    <th className="py-1 pr-2 font-semibold text-right">Coste máx</th>
                  </tr>
                </thead>
                <tbody>
                  {ai.tasks.map((t) => (
                    <tr
                      key={t.task_id}
                      data-testid={`plan-cost-ai-row-${t.task_id}`}
                      className="border-muted/40 border-b"
                    >
                      <td className="py-1 pr-2 font-mono">{t.task_id}</td>
                      <td className="py-1 pr-2">{t.title}</td>
                      <td className="py-1 pr-2">{t.complexity}</td>
                      <td className="py-1 pr-2 font-mono">{t.model_id}</td>
                      <td className="py-1 pr-2 text-right">
                        {t.cost_min} {ai.currency}
                      </td>
                      <td className="py-1 pr-2 text-right">
                        {t.cost_max} {ai.currency}
                      </td>
                    </tr>
                  ))}
                  <tr className="font-semibold">
                    <td colSpan={4} className="py-1 pr-2 text-right">
                      Total (rango)
                    </td>
                    <td className="py-1 pr-2 text-right" data-testid="plan-cost-ai-total-min">
                      {ai.cost_min} {ai.currency}
                    </td>
                    <td className="py-1 pr-2 text-right" data-testid="plan-cost-ai-total-max">
                      {ai.cost_max} {ai.currency}
                    </td>
                  </tr>
                </tbody>
              </table>
              {ai.missing_models.length > 0 ? (
                <p
                  className="text-destructive mt-2 text-xs"
                  data-testid="plan-cost-ai-missing-models"
                >
                  Modelos sin precio en el catálogo: {ai.missing_models.join(", ")}
                </p>
              ) : null}
            </div>
          </>
        )}
      </CardContent>
    </Card>
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
              <div>{renderPlanDraft(c.content)}</div>
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
          <MarkdownTextarea
            value={content}
            onChange={setContent}
            placeholder="Escribe tu comentario…"
            rows={4}
            data-testid="plan-comment-content"
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
        {summary.description ? <div>{renderPlanDraft(summary.description)}</div> : null}
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

// --------------------------------------------------------------------------
// Human validation — preview access to the running app (ADR 0062)
// --------------------------------------------------------------------------
interface ReviewSessionInfo {
  session_id: string;
  status: string;
  verdict: string | null;
  expires_at: string | null;
  review_url: string;
  app_url: string;
  verdict_url: string;
}

function HumanValidationSection({ planId, status }: { planId: string; status: string }) {
  const queryClient = useQueryClient();
  const [verdictMsg, setVerdictMsg] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const reviewQuery = useQuery({
    queryKey: ["plan-review-session", planId],
    queryFn: () => apiFetch<ReviewSessionInfo>(`/plans/${planId}/review-session`),
    enabled: status === "pending_human_validation",
    retry: false,
    refetchOnWindowFocus: false,
  });

  if (status !== "pending_human_validation") return null;

  const rs = reviewQuery.data;

  const submitVerdict = async (verdict: "approved" | "rejected") => {
    if (!rs?.verdict_url) return;
    if (verdict === "rejected" && !window.confirm("¿Rechazar el plan? Volverá al equipo.")) return;
    setSubmitting(true);
    setVerdictMsg(null);
    try {
      const body =
        verdict === "rejected"
          ? { verdict, rejection_reason: "Rechazado desde el panel de validación." }
          : { verdict };
      const res = await fetch(rs.verdict_url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setVerdictMsg(
        res.ok
          ? verdict === "approved"
            ? "Plan aprobado ✓"
            : "Plan rechazado"
          : "Error al registrar el veredicto",
      );
      queryClient.invalidateQueries({ queryKey: ["plan", planId] });
      queryClient.invalidateQueries({ queryKey: ["plan-review-session", planId] });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="border-warning/40 mt-6" data-testid="plan-human-validation">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Rocket className="text-primary h-5 w-5" />
          Validación humana — probar la app
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-muted-foreground text-sm">
          El plan está en <code>pending_human_validation</code>: los agentes han terminado y la
          aplicación se ha <b>levantado en un contenedor de revisión</b>. Ábrela para probarla y, si
          todo está bien, aprueba el plan.
        </p>

        {reviewQuery.isLoading && (
          <p className="text-muted-foreground text-sm">Buscando la sesión de revisión…</p>
        )}
        {reviewQuery.isError && (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid="plan-human-validation-none"
          >
            Aún no hay una sesión de revisión levantada para este plan.
          </p>
        )}

        {rs && (
          <>
            <div className="flex flex-wrap gap-3">
              <a
                href={rs.app_url}
                target="_blank"
                rel="noreferrer"
                data-testid="plan-open-app"
                className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-semibold"
              >
                <Rocket className="h-4 w-4" />
                Abrir app para probar
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
              <a
                href={rs.review_url}
                target="_blank"
                rel="noreferrer"
                data-testid="plan-open-review-console"
                className="hover:bg-muted/40 inline-flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-semibold"
              >
                <ClipboardList className="h-4 w-4" />
                Consola de revisión (terminal + logs + checklist)
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>

            <p className="text-muted-foreground text-xs">
              El enlace abre la app servida por el review-runtime a través del proxy firmado del
              api-server (no se publica ningún puerto). La sesión caduca el{" "}
              {rs.expires_at ? new Date(rs.expires_at).toLocaleString("es-ES") : "—"}.
            </p>

            <div className="flex items-center gap-3 border-t pt-4">
              <Button
                onClick={() => void submitVerdict("approved")}
                disabled={submitting || !!rs.verdict}
                data-testid="plan-verdict-approve"
              >
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
                Aprobar plan
              </Button>
              <Button
                variant="outline"
                onClick={() => void submitVerdict("rejected")}
                disabled={submitting || !!rs.verdict}
                data-testid="plan-verdict-reject"
              >
                <XCircle className="mr-1.5 h-4 w-4" />
                Rechazar
              </Button>
              {verdictMsg && (
                <span className="text-sm" data-testid="plan-verdict-msg">
                  {verdictMsg}
                </span>
              )}
              {rs.verdict && (
                <Badge variant={rs.verdict === "approved" ? "success" : "danger"}>
                  {rs.verdict === "approved" ? "Aprobado" : "Rechazado"}
                </Badge>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Deep links to per-plan panels (Plan 06.6 task_06_6_12)
// --------------------------------------------------------------------------

function PlanDeepLinksSection({ planId, status }: { planId: string; status: string }) {
  const inValidation = status === "pending_human_validation";
  return (
    <Card className="mt-6" data-testid="plan-deep-links">
      <CardHeader>
        <CardTitle>Paneles del plan</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Link
            href={`/admin/plans/${planId}/escalated`}
            data-testid="plan-link-escalated"
            className="hover:border-primary/40 hover:bg-muted/30 flex items-start gap-3 rounded-md border p-3 transition-colors"
          >
            <div className="bg-warning-soft text-warning-soft-foreground flex h-10 w-10 shrink-0 items-center justify-center rounded-md">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-1.5 text-sm font-semibold">
                Tareas escaladas
                <ExternalLink className="text-muted-foreground h-3.5 w-3.5" />
              </div>
              <p className="text-muted-foreground mt-0.5 text-xs">
                Tareas del plan en <code>awaiting_human</code> tras agotar reintentos del revisor.
              </p>
            </div>
          </Link>

          {inValidation && (
            <Link
              href={`/admin/review/active?plan=${planId}`}
              data-testid="plan-link-review"
              className="hover:border-primary/40 hover:bg-muted/30 flex items-start gap-3 rounded-md border p-3 transition-colors"
            >
              <div className="bg-info-soft text-info-soft-foreground flex h-10 w-10 shrink-0 items-center justify-center rounded-md">
                <ClipboardList className="h-5 w-5" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-1.5 text-sm font-semibold">
                  Sesión de review
                  <ExternalLink className="text-muted-foreground h-3.5 w-3.5" />
                </div>
                <p className="text-muted-foreground mt-0.5 text-xs">
                  El plan está en validación humana — abre la review-runtime con stack + tests.
                </p>
              </div>
            </Link>
          )}
        </div>

        {!inValidation && (
          <p className="text-muted-foreground text-xs italic">
            La sesión de review aparecerá aquí cuando el plan pase a{" "}
            <code>pending_human_validation</code>.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
