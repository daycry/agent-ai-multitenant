"use client";

/**
 * TaskDetailSheet — a modal with one task's detail + its runs + its comments.
 *
 * Opened from a Kanban card. Fetches the full task (`GET /projects/{pid}/tasks/{tid}`)
 * for description / acceptance criteria / dependencies, its runs (`GET /runs?task_id=`),
 * and its comments (reusing PlanComment with `target_kind=task`, `target_ref=<spec id>`).
 * Comments added here are threaded into the agent's prompt (Feature C).
 */

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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
import { Input } from "@/components/ui/input";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { cleanCriteria, criterionText, type CriterionDraft } from "@/lib/acceptance-criteria";
import { ApiError, apiFetch } from "@/lib/api";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { fmtRunDuration, fmtRunMoney, fmtRunTokens, fmtRunWhen, listRuns } from "@/lib/runs";

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
  const router = useRouter();
  const taskId = task?.id ?? null;
  const projectId = task?.project_id ?? null;

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

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="xl">
      <DialogContent data-testid="task-detail-sheet">
        <DialogHeader>
          <DialogTitle>{task?.title ?? "Tarea"}</DialogTitle>
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

          {/* Dependencias */}
          {detail?.depends_on?.length ? (
            <section className="mb-4" data-testid="task-detail-deps">
              <h4 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
                Depende de
              </h4>
              <p className="font-mono text-xs">{detail.depends_on.join(", ")}</p>
            </section>
          ) : null}

          {/* Runs */}
          <section className="mb-4" data-testid="task-detail-runs">
            <h4 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
              Runs
            </h4>
            {runsQuery.isLoading ? (
              <p className="text-muted-foreground text-sm">Cargando runs…</p>
            ) : null}
            {!runsQuery.isLoading && rows.length === 0 ? (
              <p
                className="text-muted-foreground text-sm italic"
                data-testid="task-detail-runs-empty"
              >
                Esta tarea no tiene ejecuciones todavía.
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
            <p className="text-muted-foreground text-xs italic">
              Los comentarios están disponibles para tareas de un plan.
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Editable rows carry a stable key so removing a middle row never steals focus
 * from the inputs React would otherwise reuse by index. */
type CriterionRow = CriterionDraft & { key: number };

function CriteriaSection({
  projectId,
  taskId,
  criteria,
}: {
  projectId: string;
  taskId: string;
  criteria: unknown[];
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<CriterionRow[]>([]);
  const keyer = useRef(0);

  function startEdit() {
    keyer.current = 0;
    setRows(criteria.map((c) => ({ key: keyer.current++, text: criterionText(c), original: c })));
    setEditing(true);
  }

  const mutation = useMutation({
    mutationFn: (next: unknown[]) =>
      apiFetch<TaskDetail>(`/projects/${projectId}/tasks/${taskId}`, {
        method: "PUT",
        body: { acceptance_criteria: next },
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["task-detail", taskId], updated);
      setEditing(false);
    },
  });

  if (!editing) {
    return (
      <section className="mb-4" data-testid="task-detail-criteria">
        <div className="mb-1 flex items-center justify-between">
          <h4 className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
            Criterios de aceptación
          </h4>
          <Button variant="outline" size="sm" onClick={startEdit} data-testid="task-criteria-edit">
            Editar
          </Button>
        </div>
        {criteria.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {criteria.map((c, i) => (
              <li key={i}>{criterionText(c)}</li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-xs italic" data-testid="task-criteria-empty">
            Sin criterios de aceptación.
          </p>
        )}
      </section>
    );
  }

  return (
    <section className="mb-4" data-testid="task-detail-criteria">
      <h4 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
        Criterios de aceptación
      </h4>
      <div className="space-y-2">
        {rows.map((row, i) => (
          <div
            key={row.key}
            className="flex items-center gap-2"
            data-testid={`task-criterion-row-${i}`}
          >
            <Input
              value={row.text}
              onChange={(e) =>
                setRows((prev) =>
                  prev.map((r) => (r.key === row.key ? { ...r, text: e.target.value } : r)),
                )
              }
              placeholder="Condición concreta y verificable…"
              data-testid="task-criterion-input"
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRows((prev) => prev.filter((r) => r.key !== row.key))}
              data-testid={`task-criterion-remove-${i}`}
              aria-label="Quitar criterio"
            >
              ×
            </Button>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            setRows((prev) => [...prev, { key: keyer.current++, text: "", original: null }])
          }
          data-testid="task-criterion-add"
        >
          + Añadir criterio
        </Button>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setEditing(false)}
            data-testid="task-criteria-cancel"
          >
            Cancelar
          </Button>
          <Button
            size="sm"
            onClick={() => mutation.mutate(cleanCriteria(rows))}
            disabled={mutation.isPending}
            data-testid="task-criteria-save"
          >
            Guardar
          </Button>
        </div>
      </div>
      {mutation.isError ? (
        <p className="text-destructive mt-1 text-sm">
          No se pudieron guardar los criterios:{" "}
          {mutation.error instanceof ApiError ? mutation.error.body : String(mutation.error)}
        </p>
      ) : null}
    </section>
  );
}

function TaskComments({ planId, specId }: { planId: string; specId: string }) {
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
        Comentarios
      </h4>
      {commentsQuery.isError ? (
        <p className="text-destructive text-sm">
          No se pudieron cargar los comentarios:{" "}
          {commentsQuery.error instanceof ApiError
            ? commentsQuery.error.body
            : String(commentsQuery.error)}
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
            Aún no hay comentarios.
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
          placeholder="Escribe un comentario para el equipo (lo verá el agente)…"
          rows={3}
          data-testid="task-comment-content"
        />
        <div className="flex justify-end">
          <Button type="submit" disabled={!canSubmit} data-testid="task-comment-submit">
            Comentar
          </Button>
        </div>
      </form>
    </section>
  );
}
