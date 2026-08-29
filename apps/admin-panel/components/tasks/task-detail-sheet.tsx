"use client";

/**
 * TaskDetailSheet — a modal with one task's detail + its runs + its comments.
 *
 * Opened from a Kanban card. Fetches the full task (`GET /projects/{pid}/tasks/{tid}`)
 * for description / acceptance criteria / dependencies, its runs (`GET /runs?task_id=`),
 * and its comments (reusing PlanComment with `target_kind=task`, `target_ref=<spec id>`).
 * Comments added here are threaded into the agent's prompt (Feature C).
 *
 * `task_wf_40`: desde aquí también se actúa sobre la tarea cuando está parada
 * esperando a un humano. Hasta ahora esas acciones solo existían en el panel de
 * tareas escaladas del plan, así que una tarea `blocked` por un run que falló
 * de forma ordinaria —que no escala— se veía pero no se podía desatascar.
 *
 * Los criterios de aceptación viven en `task-criteria-section.tsx` desde el
 * ADR 0162: con el editor de la declaración («cómo se comprueba esto») dentro,
 * esta ficha pasaba de 662 a ~850 líneas, que es justo el crecimiento por
 * agregación que la guarda de tamaño del panel existe para frenar.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CriteriaSection } from "@/components/tasks/task-criteria-section";
import { TaskEditDialog } from "@/components/tasks/task-edit-dialog";
import { acceptsHumanAction, TaskHumanActions } from "@/components/tasks/task-human-actions";
import { TaskReviewCriteria } from "@/components/tasks/task-review-criteria";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { fetchAllPages } from "@/lib/paginate";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { fmtRunDuration, fmtRunMoney, fmtRunTokens, fmtRunWhen, listRuns } from "@/lib/runs";
import { useErrorText } from "@/lib/use-error-text";

const PLAN_TASK_SPEC_ID_KEY = "plan_task_spec_id";

const VERDICT_VARIANT: Record<string, BadgeVariant> = {
  running: "info",
  done: "success",
  awaiting_human_approval: "warning",
  aborted: "warning",
  cancelled: "muted",
  failed: "danger",
};

interface TaskDetail {
  id: string;
  project_id: string;
  plan_id: string | null;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  acceptance_criteria: unknown[];
  depends_on: string[];
  inputs: Record<string, unknown>;
}

interface PlanCommentResponse {
  id: string;
  plan_id: string;
  target_kind: string;
  target_ref: string | null;
  author_user_id: string | null;
  content: string;
  created_at: string;
}

export function TaskDetailSheet({
  task,
  open,
  onOpenChange,
}: {
  task: { id: string; project_id: string; title: string } | null;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const t = useT("taskDetail");
  const tCommon = useT("common");
  const tEdit = useT("taskEdit");
  const router = useRouter();
  const queryClient = useQueryClient();
  const taskId = task?.id ?? null;
  const projectId = task?.project_id ?? null;
  const [editOpen, setEditOpen] = useState(false);

  const detailQuery = useQuery({
    queryKey: ["task-detail", taskId],
    queryFn: () => apiFetch<TaskDetail>(`/projects/${projectId}/tasks/${taskId}`),
    enabled: open && !!taskId && !!projectId,
    refetchOnWindowFocus: false,
  });

  const runsQuery = useQuery({
    queryKey: ["task-runs", taskId],
    queryFn: () => listRuns({ task_id: taskId as string, limit: 50 }),
    enabled: open && !!taskId,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((r) => r.verdict === "running") ? 5000 : false,
  });

  const detail = detailQuery.data;
  const specId =
    typeof detail?.inputs?.[PLAN_TASK_SPEC_ID_KEY] === "string"
      ? (detail.inputs[PLAN_TASK_SPEC_ID_KEY] as string)
      : undefined;
  const planId = detail?.plan_id ?? null;
  const criteria = (detail?.acceptance_criteria ?? []) as unknown[];
  const rows = runsQuery.data ?? [];

  function openRun(id: string) {
    onOpenChange(false);
    router.push(`/admin/executions/${id}`);
  }

  // Una acción humana cambia el estado de la tarea y puede reactivar su plan,
  // así que además del detalle se invalidan las DOS listas que montan esta
  // ficha (el tablero por plan y el Kanban del proyecto). Invalidar una clave
  // que no está montada no cuesta nada; dejarse la que sí lo está deja al
  // operador mirando una tarjeta que ya no dice la verdad.
  function onActionApplied() {
    void queryClient.invalidateQueries({ queryKey: ["task-detail", taskId] });
    void queryClient.invalidateQueries({ queryKey: ["task-runs", taskId] });
    void queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
    void queryClient.invalidateQueries({ queryKey: ["tasks", "by-plan"] });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="xl">
      <DialogContent data-testid="task-detail-sheet">
        <DialogHeader>
          {/* El título sale del DETALLE cuando ya está cargado, no del que
              traía la tarjeta: tras renombrar la tarea desde el formulario de
              edición, la cabecera seguía enseñando el nombre viejo. */}
          <DialogTitle>{detail?.title ?? task?.title ?? t("fallbackTitle")}</DialogTitle>
          {detail && (
            <p className="text-muted-foreground text-sm">
              <Badge variant="muted">{detail.status}</Badge>
            </p>
          )}
        </DialogHeader>
        <DialogBody>
          {/* Descripción */}
          {detail?.description ? (
            <div className="mb-4 text-sm" data-testid="task-detail-description">
              {renderPlanDraft(detail.description)}
            </div>
          ) : null}

          {/* Criterios de aceptación (editables) */}
          {detail && projectId && taskId ? (
            <CriteriaSection projectId={projectId} taskId={taskId} criteria={criteria} />
          ) : null}

          {/* task_wf_61: el veredicto del reviewer, criterio a criterio. Va
              justo DEBAJO de los criterios porque es su respuesta: qué se
              comprobó de cada uno y con qué evidencia. */}
          {taskId ? <TaskReviewCriteria taskId={taskId} /> : null}

          {/* Dependencias (resueltas a título, no UUID) */}
          {detail?.depends_on?.length && projectId ? (
            <DependsOnSection projectId={projectId} dependsOn={detail.depends_on} />
          ) : null}

          {/* Runs */}
          <section className="mb-4" data-testid="task-detail-runs">
            <h4 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
              {t("runsHeading")}
            </h4>
            {runsQuery.isLoading ? (
              <p className="text-muted-foreground text-sm">{t("runsLoading")}</p>
            ) : null}
            {!runsQuery.isLoading && rows.length === 0 ? (
              <p
                className="text-muted-foreground text-sm italic"
                data-testid="task-detail-runs-empty"
              >
                {t("runsEmpty")}
              </p>
            ) : null}
            <div className="space-y-1">
              {rows.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => openRun(r.id)}
                  data-testid={`task-run-row-${r.id}`}
                  className="border-border hover:bg-muted/40 flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors"
                >
                  <span className="flex flex-col">
                    <span className="text-sm tabular-nums">{fmtRunWhen(r.created_at)}</span>
                    <span className="text-muted-foreground text-xs">
                      {r.agent_name ?? "—"} · {r.model ?? "—"}
                    </span>
                  </span>
                  <span className="flex items-center gap-3">
                    <span className="text-muted-foreground text-xs tabular-nums">
                      {fmtRunDuration(r.duration_ms)} · {fmtRunTokens(r.total_tokens)} tok ·{" "}
                      {fmtRunMoney(r)}
                    </span>
                    <Badge variant={VERDICT_VARIANT[r.verdict] ?? "muted"}>{r.verdict}</Badge>
                  </span>
                </button>
              ))}
            </div>
          </section>

          {/* Comentarios (cableados al prompt del agente, Feature C) */}
          {planId && specId ? (
            <TaskComments planId={planId} specId={specId} />
          ) : (
            <p className="text-muted-foreground text-xs italic">{t("commentsOnlyForPlanTasks")}</p>
          )}
        </DialogBody>
        <DialogFooter>
          {/* Las acciones humanas solo desde los estados que el backend acepta
              (el resto es un 409 garantizado) y solo para quien las puede
              ejecutar. `mr-auto` sobre el hueco vacío mantiene «Cerrar» a la
              derecha también cuando no se ofrece ninguna. */}
          <div className="mr-auto">
            {taskId && acceptsHumanAction(detail?.status) ? (
              <RoleGuard min="tenant_admin">
                <TaskHumanActions taskId={taskId} onApplied={onActionApplied} />
              </RoleGuard>
            ) : null}
          </div>
          {/* Editar es la única puerta a los ocho campos que el `PUT` acepta y
              que ninguna pantalla ofrecía (ADR 0162). Va aquí y no sólo en la
              lista del proyecto porque esta ficha es también la que abre el
              tablero por plan: con el botón sólo allí, media plataforma
              seguiría sin poder cambiar el agente de una tarea. */}
          {taskId && projectId ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditOpen(true)}
              data-testid="task-detail-edit"
            >
              {tEdit("open")}
            </Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {tCommon("close")}
          </Button>
        </DialogFooter>
      </DialogContent>
      <TaskEditDialog
        task={taskId && projectId ? { id: taskId, project_id: projectId } : null}
        open={editOpen}
        onOpenChange={setEditOpen}
      />
    </Dialog>
  );
}

interface TaskLite {
  id: string;
  title: string;
}

/** "Depende de" resolved to task TITLES — the raw dependency UUID is internal and
 * conveys nothing to the operator. Looks the titles up in the project's task list
 * (cheap, cached; only fetched when the task actually has dependencies); falls
 * back to a short id if a title can't be resolved. */
function DependsOnSection({ projectId, dependsOn }: { projectId: string; dependsOn: string[] }) {
  const t = useT("taskDetail");
  // PROY2-08: paginado exhaustivo — con >100 tareas en el proyecto, las deps
  // más allá de la primera página se quedaban sin título (solo el UUID).
  const tasksQuery = useQuery({
    queryKey: ["project-task-titles", projectId],
    queryFn: () => fetchAllPages<TaskLite>(`/projects/${projectId}/tasks`),
    enabled: dependsOn.length > 0,
    refetchOnWindowFocus: false,
  });
  const titleById = new Map((tasksQuery.data?.items ?? []).map((t) => [t.id, t.title]));

  return (
    <section className="mb-4" data-testid="task-detail-deps">
      <h4 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
        {t("dependsOn")}
      </h4>
      <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="task-detail-deps-list">
        {dependsOn.map((id) => {
          const title = titleById.get(id);
          return (
            <li key={id}>
              {title ?? <span className="font-mono text-xs">{id.slice(0, 8)}…</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function TaskComments({ planId, specId }: { planId: string; specId: string }) {
  const errorText = useErrorText();
  const t = useT("taskDetail");
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");

  const commentsQuery = useQuery({
    queryKey: ["plan-comments", planId],
    queryFn: () => apiFetch<PlanCommentResponse[]>(`/plans/${planId}/comments`),
    refetchOnWindowFocus: false,
  });

  const mine = (commentsQuery.data ?? []).filter(
    (c) => c.target_kind === "task" && c.target_ref === specId,
  );

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<PlanCommentResponse>(`/plans/${planId}/comments`, {
        method: "POST",
        body: { target_kind: "task", target_ref: specId, content },
      }),
    onSuccess: (created) => {
      queryClient.setQueryData<PlanCommentResponse[]>(["plan-comments", planId], (prev) =>
        prev ? [...prev, created] : [created],
      );
      setContent("");
    },
  });

  const canSubmit = content.trim().length > 0 && !mutation.isPending;

  return (
    <section data-testid="task-comments">
      <h4 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
        {t("commentsHeading")}
      </h4>
      {commentsQuery.isError ? (
        <p className="text-destructive text-sm">
          {t("commentsLoadError")} {errorText(commentsQuery.error)}
        </p>
      ) : null}
      <ul className="mb-3 space-y-2" data-testid="task-comments-list">
        {mine.map((c) => (
          <li
            key={c.id}
            data-testid={`task-comment-${c.id}`}
            className="border-muted rounded border px-3 py-2 text-sm"
          >
            {renderPlanDraft(c.content)}
          </li>
        ))}
        {mine.length === 0 ? (
          <p className="text-muted-foreground text-xs italic" data-testid="task-comments-empty">
            {t("commentsEmpty")}
          </p>
        ) : null}
      </ul>
      <form
        className="space-y-2"
        data-testid="task-comment-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) mutation.mutate();
        }}
      >
        <MarkdownTextarea
          value={content}
          onChange={setContent}
          placeholder={t("commentPlaceholder")}
          rows={3}
          data-testid="task-comment-content"
        />
        <div className="flex justify-end">
          <Button type="submit" disabled={!canSubmit} data-testid="task-comment-submit">
            {t("commentSubmit")}
          </Button>
        </div>
      </form>
    </section>
  );
}
