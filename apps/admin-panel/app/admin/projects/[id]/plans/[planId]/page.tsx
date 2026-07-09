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
import { Badge } from "@/components/ui/badge";
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
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import {
  type PlanPhaseSpec,
  type PlanResponse,
  type PlanSpecification,
  type PlanTaskSpec,
  STATUS_LABEL,
  STATUS_VARIANT,
} from "./plan-spec-types";
import {
  DAGSection,
  EstimatesSection,
  GanttSection,
  PhasesSection,
  SummarySection,
  TasksSection,
} from "./plan-spec-sections";

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

      <PlanLifecycleSection planId={plan.id} status={plan.status} />
      <HumanValidationSection planId={plan.id} status={plan.status} />
      <CorrectionsSection planId={plan.id} status={plan.status} spec={spec} />
      <PlanDeepLinksSection planId={plan.id} status={plan.status} />
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

// --------------------------------------------------------------------------
// Plan lifecycle — explicit state transitions (draft → approval → in_progress)
//
// The lifecycle was missing its operator-facing controls: a draft could already
// sync to the Kanban (now blocked server-side) and there was no button to move a
// plan through approval or to start its execution. This action bar surfaces only
// the transition that's legal for the current status.
// --------------------------------------------------------------------------
function PlanLifecycleSection({ planId, status }: { planId: string; status: string }) {
  const queryClient = useQueryClient();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["plan", planId] });
    queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
  };
  const onErr = (e: unknown) => setErrorMsg(e instanceof ApiError ? e.body : String(e));

  const sendToApproval = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/plans/${planId}`, {
        method: "PUT",
        body: { status: "pending_approval" },
      }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });
  const approve = useMutation({
    mutationFn: () => apiFetch<{ status: string }>(`/plans/${planId}/approve`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });
  const startExecution = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/plans/${planId}/start-execution`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });
  // hallazgo #3 (QA 2026-07-07): el desbloqueo solo existía en /plans/{id}/escalated
  // y el operador no lo encontró desde el detalle. Misma mutación que allí.
  const unblock = useMutation({
    mutationFn: () => apiFetch<{ status: string }>(`/plans/${planId}/unblock`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });

  const canSendToApproval = status === "draft";
  const canApprove = status === "pending_approval" || status === "pending_second_approval";
  const canStart = status === "approved";
  const canUnblock = status === "blocked";
  // Action bar, not a status display: render nothing when no transition is offered.
  if (!canSendToApproval && !canApprove && !canStart && !canUnblock) return null;

  const pending =
    sendToApproval.isPending || approve.isPending || startExecution.isPending || unblock.isPending;

  return (
    <Card className="mt-6" data-testid="plan-lifecycle">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Ciclo de vida del plan</CardTitle>
        <div className="flex flex-wrap gap-2">
          {canSendToApproval ? (
            <Button
              onClick={() => sendToApproval.mutate()}
              disabled={pending}
              data-testid="plan-send-to-approval"
            >
              Enviar a aprobación
            </Button>
          ) : null}
          {canApprove ? (
            <Button
              onClick={() => approve.mutate()}
              disabled={pending}
              data-testid="plan-lifecycle-approve"
            >
              Aprobar plan
            </Button>
          ) : null}
          {canStart ? (
            <Button
              onClick={() => startExecution.mutate()}
              disabled={pending}
              data-testid="plan-start-execution"
            >
              Empezar ejecución
            </Button>
          ) : null}
          {canUnblock ? (
            <Button
              onClick={() => unblock.mutate()}
              disabled={pending}
              data-testid="plan-detail-unblock"
            >
              Desbloquear plan
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">
          {canSendToApproval
            ? "El plan está en borrador. Envíalo a aprobación para revisarlo y aprobarlo."
            : canApprove
              ? "El plan espera aprobación. Al aprobarlo podrás sincronizar sus tareas al Kanban."
              : canUnblock
                ? "El plan está bloqueado: ninguna tarea abierta puede avanzar. «Desbloquear plan» lo reactiva y re-encola todas sus tareas bloqueadas (reinicia sus reintentos)."
                : "El plan está aprobado. «Empezar ejecución» lo marca en curso y crea las tareas en el Kanban."}
        </p>
        {errorMsg ? (
          <p className="text-destructive mt-2 text-xs" data-testid="plan-lifecycle-error">
            {errorMsg}
          </p>
        ) : null}
      </CardContent>
    </Card>
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
  status,
  phases,
  taskIds,
}: {
  status: string;
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
  // Materialising tasks is only legal once the plan is signed off (mirrors the
  // backend guard). A draft must not seed the Kanban.
  const syncable = status === "approved" || status === "in_progress";

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
          disabled={taskIds.length === 0 || !syncable}
          data-testid="plan-sync-open"
        >
          Sincronizar al Kanban
        </Button>
      </CardHeader>
      <CardContent>
        {!syncable ? (
          <p className="text-muted-foreground text-sm italic" data-testid="plan-sync-not-approved">
            Solo se pueden materializar tareas de un plan <strong>aprobado</strong> o en curso.
            Aprueba el plan primero.
          </p>
        ) : taskIds.length === 0 ? (
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
              <div className="overflow-x-auto">
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
                      <td
                        className="py-1 pr-2 text-right"
                        data-testid="plan-cost-human-total-hours"
                      >
                        {human.total_hours}
                      </td>
                      <td className="py-1 pr-2 text-right" data-testid="plan-cost-human-total">
                        {human.total_cost} {human.currency}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            {/* AI cost table — range (min / max) */}
            <div data-testid="plan-cost-ai">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
                Coste IA · {ai.currency} · modelo por defecto{" "}
                <span className="font-mono">{ai.default_model_id}</span>
              </p>
              <div className="overflow-x-auto">
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
              </div>
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
// Human validation — preview access to the running app (ADR 0062)
// --------------------------------------------------------------------------
interface ReviewSessionInfo {
  session_id: string;
  status: string;
  verdict: string | null;
  rejection_reason: string | null;
  expires_at: string | null;
  review_url: string;
  app_url: string;
  verdict_url: string;
}

function HumanValidationSection({ planId, status }: { planId: string; status: string }) {
  const queryClient = useQueryClient();
  const [verdictMsg, setVerdictMsg] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
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

  // El motivo del rechazo ES el feedback que reciben los agentes en el rework
  // — antes iba un texto fijo y la intención del validador se perdía. La modal
  // usa MarkdownTextarea (preferencia del operador: todo textarea con preview).
  const submitVerdict = async (verdict: "approved" | "rejected", reason = "") => {
    if (!rs?.verdict_url) return;
    const rejectionReason = reason.trim() || "Rechazado desde el panel de validación (sin motivo).";
    setSubmitting(true);
    setVerdictMsg(null);
    try {
      const body =
        verdict === "rejected" ? { verdict, rejection_reason: rejectionReason } : { verdict };
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
                onClick={() => setRejectOpen(true)}
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

            <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
              <DialogContent data-testid="plan-reject-dialog">
                <DialogHeader>
                  <DialogTitle>Rechazar plan</DialogTitle>
                </DialogHeader>
                <DialogBody className="space-y-3">
                  <p className="text-muted-foreground text-sm">
                    El motivo llega a los agentes como feedback del rework — cuanto más concreto
                    (qué está mal, dónde y qué se espera), mejor corrige el equipo. Tras rechazar
                    podrás generar tareas correctivas desde el motivo y aceptarlas en este mismo
                    plan.
                  </p>
                  <MarkdownTextarea
                    value={rejectReason}
                    onChange={setRejectReason}
                    placeholder="P. ej.: El filtro de Content-Type application/json es global; debe acotarse al grupo api/v1…"
                    rows={6}
                    data-testid="plan-reject-reason"
                  />
                </DialogBody>
                <DialogFooter>
                  <Button
                    variant="outline"
                    onClick={() => setRejectOpen(false)}
                    disabled={submitting}
                  >
                    Cancelar
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={submitting}
                    data-testid="plan-reject-confirm"
                    onClick={() => {
                      setRejectOpen(false);
                      void submitVerdict("rejected", rejectReason);
                    }}
                  >
                    <XCircle className="mr-1.5 h-4 w-4" />
                    Rechazar plan
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Correcciones del rechazo (ADR 0107) — el motivo del veredicto rechazado se
// convierte en tareas correctivas del MISMO plan; aceptarlas las sincroniza
// al Kanban y reactiva el plan (rejected → in_progress).
// --------------------------------------------------------------------------
interface GenerateCorrectionsResponse {
  session_id: string;
  reason: string;
  task_ids: string[];
  tasks: PlanTaskSpec[];
  already_generated: boolean;
}

function CorrectionsSection({
  planId,
  status,
  spec,
}: {
  planId: string;
  status: string;
  spec: PlanSpecification;
}) {
  const queryClient = useQueryClient();
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [emptyGeneration, setEmptyGeneration] = useState(false);

  const isRejected = status === "rejected";
  const corrections = spec.corrections ?? [];
  const proposed = corrections.filter((c) => c.status === "proposed");
  const accepted = corrections.filter((c) => c.status === "accepted");

  // El motivo vive en la sesión de review rechazada; una vez generada la
  // tanda también queda copiado en la entrada de corrections del spec.
  const sessionQuery = useQuery({
    queryKey: ["plan-review-session", planId],
    queryFn: () => apiFetch<ReviewSessionInfo>(`/plans/${planId}/review-session`),
    enabled: isRejected,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["plan", planId] });
    queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
  };
  const onErr = (e: unknown) => setErrorMsg(e instanceof ApiError ? e.body : String(e));

  const generate = useMutation({
    mutationFn: () =>
      apiFetch<GenerateCorrectionsResponse>(`/plans/${planId}/generate-corrections`, {
        method: "POST",
      }),
    onSuccess: (res) => {
      setErrorMsg(null);
      setEmptyGeneration(res.task_ids.length === 0);
      invalidate();
    },
    onError: onErr,
  });

  const tasksById = new Map((spec.tasks ?? []).map((t) => [t.id, t]));
  const proposedIds = proposed.flatMap((c) => c.task_ids ?? []);
  const selectedIds = proposedIds.filter((id) => !unchecked.has(id));

  const accept = useMutation({
    mutationFn: () =>
      apiFetch<PlanResponse>(`/plans/${planId}/accept-corrections`, {
        method: "POST",
        body: { task_ids: selectedIds },
      }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });

  // Solo aparece en un plan rechazado (flujo vivo) o con historial de
  // correcciones (lectura tras la aceptación).
  if (!isRejected && corrections.length === 0) return null;

  const reason =
    sessionQuery.data?.rejection_reason ??
    proposed[0]?.reason ??
    accepted[accepted.length - 1]?.reason ??
    null;

  const toggle = (id: string) => {
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Card className="border-destructive/40 mt-6" data-testid="plan-corrections">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <XCircle className="text-destructive h-5 w-5" />
          Correcciones del rechazo
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {reason ? (
          <div
            className="bg-muted/30 rounded-md border p-3 text-sm"
            data-testid="plan-corrections-reason"
          >
            <p className="text-muted-foreground mb-1 text-xs font-semibold uppercase">
              Motivo del validador
            </p>
            {renderPlanDraft(reason)}
          </div>
        ) : isRejected && sessionQuery.isError ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid="plan-corrections-no-reason"
          >
            El plan fue rechazado sin sesión de review con motivo: no hay nada desde lo que generar
            correcciones automáticas.
          </p>
        ) : null}

        {isRejected && proposed.length === 0 && !sessionQuery.isError ? (
          <div className="space-y-2">
            <p className="text-muted-foreground text-sm">
              Genera tareas correctivas a partir del motivo: se añaden al plan como propuestas y
              podrás revisarlas antes de aceptarlas. Al aceptar, se crean en el Kanban y el plan
              vuelve a estar en curso — mismo plan, misma rama git.
            </p>
            <Button
              onClick={() => generate.mutate()}
              disabled={generate.isPending || !reason}
              data-testid="plan-corrections-generate"
            >
              {generate.isPending ? "Generando tareas correctivas…" : "Generar tareas correctivas"}
            </Button>
            {emptyGeneration ? (
              <p className="text-destructive text-xs" data-testid="plan-corrections-empty">
                El modelo no propuso tareas usables. Reintenta o crea las tareas a mano.
              </p>
            ) : null}
          </div>
        ) : null}

        {proposed.length > 0 ? (
          <div className="space-y-3">
            <p className="text-muted-foreground text-sm">
              Tareas correctivas propuestas — desmarca las que no quieras materializar:
            </p>
            <ul className="space-y-2">
              {proposedIds.map((id) => {
                const task = tasksById.get(id);
                if (!task) return null;
                return (
                  <li
                    key={id}
                    className="flex items-start gap-3 rounded-md border p-3"
                    data-testid={`plan-correction-task-${id}`}
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={!unchecked.has(id)}
                      onChange={() => toggle(id)}
                      data-testid={`plan-correction-check-${id}`}
                    />
                    <div className="flex-1 text-sm">
                      <p className="font-medium">
                        <span className="text-muted-foreground mr-1.5 font-mono text-xs">{id}</span>
                        {task.title}
                      </p>
                      {task.description ? (
                        <p className="text-muted-foreground mt-0.5 text-xs">{task.description}</p>
                      ) : null}
                      <p className="text-muted-foreground mt-1 text-xs">
                        {task.role ? <>rol: {task.role} · </> : null}
                        complejidad: {task.complexity ?? "m"}
                        {task.depends_on && task.depends_on.length > 0 ? (
                          <> · depende de: {task.depends_on.join(", ")}</>
                        ) : null}
                      </p>
                      {task.acceptance_criteria && task.acceptance_criteria.length > 0 ? (
                        <ul className="mt-1 list-disc pl-5 text-xs">
                          {task.acceptance_criteria.map((c, i) => (
                            <li key={i}>{c}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
            {isRejected ? (
              <Button
                onClick={() => accept.mutate()}
                disabled={accept.isPending || selectedIds.length === 0}
                data-testid="plan-corrections-accept"
              >
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
                {accept.isPending ? "Aceptando…" : `Aceptar correcciones (${selectedIds.length})`}
              </Button>
            ) : null}
          </div>
        ) : null}

        {accepted.length > 0 ? (
          <div className="space-y-1" data-testid="plan-corrections-accepted">
            {accepted.map((entry, i) => (
              <p key={i} className="text-muted-foreground flex items-center gap-2 text-xs">
                <Badge variant="success">aceptada</Badge>
                {(entry.accepted_task_ids ?? entry.task_ids ?? []).join(", ")} — las tareas están en
                el Kanban y el plan sigue su ciclo.
              </p>
            ))}
          </div>
        ) : null}

        {errorMsg ? (
          <p className="text-destructive text-xs" data-testid="plan-corrections-error">
            {errorMsg}
          </p>
        ) : null}
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
                Tareas escaladas y bloqueadas
                <ExternalLink className="text-muted-foreground h-3.5 w-3.5" />
              </div>
              <p className="text-muted-foreground mt-0.5 text-xs">
                Tareas esperando una acción humana (aprobar, reintentar, desbloquear) — incluye las
                bloqueadas por reintentos agotados y el desbloqueo del plan.
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
