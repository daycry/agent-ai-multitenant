"use client";

/**
 * task_01_22 — Doble Kanban estático.
 *
 * Doctrine (CLAUDE.md §6): the Kanban is *double*. Top section shows
 * Plans (gerencial); bottom shows Tasks of the *selected* plan only,
 * never a flat board mixing tasks from several plans.
 *
 * Plan 01 doesn't yet have a dedicated `plans` table — Plan 02 will.
 * Meanwhile we treat each non-template project as a "plan placeholder"
 * because tasks are scoped by project and that's the closest unit we
 * have for the demo. The grouping switches to `plan_id` in Plan 02
 * without touching the layout.
 *
 * Drag & drop uses the native HTML5 API (draggable + dataTransfer) to
 * avoid pulling in a new dep just for this. Drop calls
 * `PUT /projects/{pid}/tasks/{tid}` with the new status and optimistically
 * updates the cache; on failure we revert and surface an inline banner.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, Lock, LockOpen } from "lucide-react";

import { STATUS_LABEL as PLAN_STATUS_KEY } from "@/app/admin/projects/[id]/plans/[planId]/plan-spec-types";
import { PageHeader } from "@/components/layout/page-header";
import { TaskDetailSheet } from "@/components/tasks/task-detail-sheet";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";
import { translate, useT, type Lang, type MessageKey } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { fetchAllPages, type PaginatedResult } from "@/lib/paginate";
import { computeDepState } from "@/lib/task-deps";
import { useErrorText } from "@/lib/use-error-text";
import { useWebSocket, wsUrl } from "@/lib/ws";

// --------------------------------------------------------------------------
// Types
// --------------------------------------------------------------------------
interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  team_id: string | null;
  is_template: boolean;
}

interface Task {
  id: string;
  project_id: string;
  plan_id: string | null;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  assigned_agent_id: string | null;
  // Upstream task ids this one depends on (TaskResponse.depends_on). Drives the
  // card padlock + the "can't go ready while a dependency is pending" guard.
  depends_on: string[];
}

// c8/T11 (ADR 0008): the top row shows real PLANS (GET /plans), not projects.
interface Plan {
  id: string;
  project_id: string;
  title: string;
  status: string;
}

// Plan lifecycle → badge colour for the plan cards.
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

// --------------------------------------------------------------------------
// Status columns. Keep cancelled at the end — it's terminal-but-rare.
// --------------------------------------------------------------------------
/**
 * Las ocho columnas, con la CLAVE de su rótulo y no el texto (prod-16
 * `task_prod16_03`). El catálogo `taskStatus` es COMPARTIDO con
 * `projects/[id]/tasks`: éste era su tercer ejemplar con el texto dentro, y
 * mientras el texto vivía aquí traducir una de las dos pantallas dejaba la otra
 * a medias.
 */
const COLUMNS: Array<{
  id: Task["status"];
  labelKey: MessageKey<"taskStatus">;
  variant: BadgeVariant;
}> = [
  { id: "backlog", labelKey: "backlog", variant: "muted" },
  { id: "ready", labelKey: "ready", variant: "info" },
  { id: "in_progress", labelKey: "inProgress", variant: "primary" },
  // ADR 0020 — el agente queda libre; al aprobar la tarea vuelve a backlog,
  // al rechazar pasa a blocked.
  { id: "awaiting_human_approval", labelKey: "awaitingHumanApproval", variant: "warning" },
  { id: "in_review", labelKey: "inReview", variant: "warning" },
  { id: "blocked", labelKey: "blocked", variant: "danger" },
  { id: "done", labelKey: "done", variant: "success" },
  { id: "cancelled", labelKey: "cancelled", variant: "muted" },
];

const PRIORITY_VARIANT: Record<string, BadgeVariant> = {
  low: "muted",
  medium: "info",
  high: "warning",
  critical: "danger",
};

/**
 * Si el valor que manda el backend es una de las cuatro prioridades conocidas.
 *
 * Las claves se leen de `PRIORITY_VARIANT`, no de una lista escrita a mano: son
 * el mismo vocabulario y una copia se desincronizaria. El `task.priority` llega
 * como `string` del API, asi que el dia que el backend añada una quinta
 * prioridad esto la deja pasar en crudo —feo pero legible— en vez de romper.
 */
function isTaskPriority(value: string): value is MessageKey<"taskPriority"> {
  return value in PRIORITY_VARIANT;
}

/** El rótulo traducido de una columna, o el enum crudo si no está en el mapa. */
function columnLabel(status: Task["status"], lang: Lang): string {
  const key = COLUMNS.find((c) => c.id === status)?.labelKey;
  return key ? translate(lang, "taskStatus", key) : status;
}

/**
 * Turn a failed move into a human message. The server gates DAG-forward moves
 * (ready / in_progress / …) and replies 422 `dependencies_not_done`; surface
 * that instead of the raw JSON body.
 *
 * `lang` y `fallback` son obligatorios y sin default (prod-16
 * `task_prod16_03`): la función es pura, así que su castellano no lo veía
 * ninguna de las dos guardas, y con un default el próximo llamante reintroduce
 * el fallo sin enterarse. `fallback` es `errorText` del llamante, para que el
 * cuerpo crudo del backend tampoco salga por la rama de "no era el error
 * estructurado" (`task_prod16_05`).
 */
function describeMoveError(
  err: unknown,
  target: Task["status"],
  lang: Lang,
  fallback: (err: unknown) => string,
): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as {
        detail?: { error?: string; pending?: unknown[]; from?: string; to?: string };
      };
      const status = columnLabel(target, lang);
      if (parsed.detail?.error === "dependencies_not_done") {
        const count = parsed.detail.pending?.length ?? 0;
        return translate(lang, "board", count === 1 ? "moveDepsOne" : "moveDepsMany", {
          status,
          count,
        });
      }
      // c1/T2: the state machine rejected this move (409 illegal_transition).
      if (parsed.detail?.error === "illegal_transition") {
        return translate(lang, "board", "moveIllegal", { status });
      }
    } catch {
      // body wasn't the structured DAG error — fall back to the shared text.
    }
  }
  return fallback(err);
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function BoardPage() {
  const errorText = useErrorText();
  const lang = useLangOptional();
  const t = useT("taskStatus");
  const tPlan = useT("planStatus");
  const tBoard = useT("board");
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dragError, setDragError] = useState<string | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["projects", "tenant"],
    queryFn: () => fetchAllPages<Project>("/projects"),
    refetchOnWindowFocus: false,
  });

  // c8/T11: the top row is a Kanban of real PLANS across the tenant's projects
  // (GET /plans). projectsQuery stays only to label each plan with its project name.
  // PROY2-08: fetchAllPages agota las páginas (>100 planes ya no desaparecen).
  const plansQuery = useQuery({
    queryKey: ["plans", "tenant"],
    queryFn: () => fetchAllPages<Plan>("/plans"),
    refetchOnWindowFocus: false,
  });
  const plans = useMemo(() => plansQuery.data?.items ?? [], [plansQuery.data]);
  const projectsById = useMemo(
    () => new Map((projectsQuery.data?.items ?? []).map((p) => [p.id, p] as const)),
    [projectsQuery.data],
  );

  // Auto-select the first plan once data lands.
  const effectiveSelected =
    selectedId && plans.some((p) => p.id === selectedId) ? selectedId : (plans[0]?.id ?? null);
  const selectedPlan = plans.find((p) => p.id === effectiveSelected) ?? null;

  // §6: the bottom board shows ONLY the selected plan's tasks (never a flat board
  // mixing tasks from several plans). Filter by plan_id within the plan's project.
  // PROY2-08: paginado exhaustivo — un plan de 200 tareas pinta las 200.
  const tasksQuery = useQuery({
    queryKey: ["tasks", "by-plan", effectiveSelected],
    queryFn: () =>
      fetchAllPages<Task>(
        `/projects/${selectedPlan?.project_id}/tasks?plan_id=${effectiveSelected}`,
      ),
    enabled: !!selectedPlan,
    refetchOnWindowFocus: false,
  });
  const boardTruncated = Boolean(plansQuery.data?.truncated || tasksQuery.data?.truncated);
  const tasks = useMemo(() => tasksQuery.data?.items ?? [], [tasksQuery.data]);

  // taskId -> status, for resolving each card's dependency state (the padlock)
  // and the "can't go ready while a dependency is pending" drag guard.
  const statusById = useMemo(() => new Map(tasks.map((t) => [t.id, t.status] as const)), [tasks]);

  // hallazgo #3: desbloquear un plan desde su tarjeta (misma mutación que el
  // detalle y la página escalated — reactiva el plan y re-encola sus blocked).
  const unblockPlan = useMutation({
    mutationFn: async (planId: string) =>
      apiFetch<{ status: string }>(`/plans/${planId}/unblock`, { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["plans", "tenant"] });
      void queryClient.invalidateQueries({ queryKey: ["tasks", "by-plan"] });
    },
  });

  const moveTask = useMutation({
    mutationFn: async ({ task, newStatus }: { task: Task; newStatus: Task["status"] }) => {
      return apiFetch<Task>(`/projects/${task.project_id}/tasks/${task.id}`, {
        method: "PUT",
        body: { status: newStatus },
      });
    },
    onMutate: async ({ task, newStatus }) => {
      // Optimistic cache update so the card jumps columns instantly.
      const key = ["tasks", "by-plan", effectiveSelected];
      await queryClient.cancelQueries({ queryKey: key });
      const prev = queryClient.getQueryData<PaginatedResult<Task>>(key);
      queryClient.setQueryData<PaginatedResult<Task>>(key, {
        truncated: prev?.truncated ?? false,
        items: (prev?.items ?? []).map((t) => (t.id === task.id ? { ...t, status: newStatus } : t)),
      });
      return { prev };
    },
    onError: (err, vars, context) => {
      if (context?.prev) {
        queryClient.setQueryData(["tasks", "by-plan", effectiveSelected], context.prev);
      }
      setDragError(describeMoveError(err, vars.newStatus, lang, errorText));
    },
    onSuccess: () => setDragError(null),
  });

  function onDrop(newStatus: Task["status"], taskId: string) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task || task.status === newStatus) return;
    // Mirror the server DAG guard in the UI: refuse to drag a card into `ready`
    // while an upstream dependency is still pending (the card shows a padlock).
    // Other gated moves (in_progress, …) are caught by the server 422 below.
    if (newStatus === "ready") {
      const dep = computeDepState(task.depends_on, statusById);
      if (dep.blocked) {
        // Mismo texto que la rama `dependencies_not_done` de `describeMoveError`
        // (prod-16 `task_prod16_03`): el motivo es el mismo, así que decirlo con
        // dos redacciones sólo servía para traducir una y olvidar la otra. El
        // mensaje nombra la COLUMNA de destino y no el título de la tarjeta, que
        // es lo que el usuario acaba de arrastrar y tiene delante.
        setDragError(
          tBoard(dep.pendingCount === 1 ? "moveDepsOne" : "moveDepsMany", {
            status: columnLabel(newStatus, lang),
            count: dep.pendingCount,
          }),
        );
        return;
      }
    }
    setDragError(null);
    moveTask.mutate({ task, newStatus });
  }

  // ---- Real-time: tail the selected plan's kanban WebSocket -------------
  // task_02_21 / task_02_23 — a task.status_changed event (from any
  // source: another user, an agent) moves the card live, no refresh.
  const kanbanUrl = useMemo(
    () => (selectedPlan ? wsUrl(`/ws/kanban/${selectedPlan.project_id}`) : null),
    [selectedPlan],
  );

  const onKanbanEvent = useCallback(
    (data: unknown) => {
      if (!effectiveSelected) return;
      const event = data as {
        type?: string;
        task_id?: string;
        payload?: { new_status?: string };
      };
      const key = ["tasks", "by-plan", effectiveSelected];
      const newStatus = event.payload?.new_status;
      if (event.type === "task.status_changed" && event.task_id && newStatus) {
        queryClient.setQueryData<PaginatedResult<Task>>(key, (prev) => ({
          truncated: prev?.truncated ?? false,
          items: (prev?.items ?? []).map((t) =>
            t.id === event.task_id ? { ...t, status: newStatus } : t,
          ),
        }));
      } else if (event.type === "task.created") {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
    [effectiveSelected, queryClient],
  );

  useWebSocket(kanbanUrl, onKanbanEvent);

  // ---- Real-time: la FILA DE PLANES (task_wf_32) ------------------------
  // El socket es de TENANT y no de proyecto: esta fila lista los planes de
  // todos los proyectos, así que uno por proyecto dejaría rancias las demás
  // tarjetas. Sin esto, un plan que pasa a `pending_human_validation` o a
  // `blocked` —las dos transiciones que ocurren SIN gesto humano— se quedaba
  // en su columna hasta que alguien recargara.
  const plansUrl = useMemo(() => wsUrl("/ws/plans"), []);

  const onPlanEvent = useCallback(
    (data: unknown) => {
      const event = data as { type?: string; plan_id?: string; payload?: { new_status?: string } };
      const newStatus = event.payload?.new_status;
      if (event.type !== "plan.status_changed" || !event.plan_id || !newStatus) return;
      queryClient.setQueryData<PaginatedResult<Plan>>(["plans", "tenant"], (prev) => ({
        truncated: prev?.truncated ?? false,
        items: (prev?.items ?? []).map((p) =>
          p.id === event.plan_id ? { ...p, status: newStatus } : p,
        ),
      }));
      // La cabecera del plan abierto (progreso, PR, coste) también envejece con
      // la transición: se invalida en vez de parchearse, porque su contenido no
      // se deduce del estado nuevo.
      void queryClient.invalidateQueries({ queryKey: ["plan-status", event.plan_id] });
    },
    [queryClient],
  );

  useWebSocket(plansUrl, onPlanEvent);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<LayoutGrid className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={tBoard("title")}
        description={tBoard("description")}
      />

      {/* PROY2-08: si se tocó el tope de paginación, decirlo en vez de callar. */}
      {boardTruncated && (
        <Card className="mb-4 border-amber-500 p-3" data-testid="board-truncated-warning">
          <p className="text-sm text-amber-600 dark:text-amber-400">{tBoard("truncated")}</p>
        </Card>
      )}

      {/* ============ Plans row ============ */}
      <section data-testid="plans-row" className="mb-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{tBoard("plansHeading")}</h2>
          {plans.length > 0 && (
            <p className="text-muted-foreground text-xs">
              {tBoard(plans.length === 1 ? "plansCountOne" : "plansCountMany", {
                count: plans.length,
              })}
            </p>
          )}
        </div>

        {plansQuery.isLoading && (
          <p className="text-muted-foreground text-sm">{tBoard("plansLoading")}</p>
        )}

        {plansQuery.isError && (
          <Card className="border-destructive p-4">
            <p className="text-destructive text-sm">
              {tBoard("plansError")} {errorText(plansQuery.error)}
            </p>
          </Card>
        )}

        {plansQuery.data && plans.length === 0 && (
          <Card className="p-8 text-center" data-testid="plans-empty">
            <p className="text-muted-foreground text-sm">{tBoard("plansEmpty")}</p>
          </Card>
        )}

        {plans.length > 0 && (
          <div
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
            data-testid="plans-grid"
          >
            {plans.map((p) => {
              const project = projectsById.get(p.project_id);
              const active = effectiveSelected === p.id;
              return (
                <Card
                  key={p.id}
                  data-testid={`plan-card-${p.id}`}
                  data-active={active ? "true" : "false"}
                  interactive
                  onClick={() => setSelectedId(p.id)}
                  className={cn(active && "border-primary shadow-md ring-1 ring-primary/30")}
                >
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base">{p.title}</CardTitle>
                    {project && (
                      <Badge variant="info" className="w-fit">
                        {project.name}
                      </Badge>
                    )}
                  </CardHeader>
                  <CardContent className="flex items-center justify-end gap-2">
                    {/* hallazgo #3 (QA 2026-07-07): un plan bloqueado ofrece el
                        desbloqueo AQUÍ, no solo en /plans/{id}/escalated. */}
                    {p.status === "blocked" && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={unblockPlan.isPending}
                        data-testid={`plan-unblock-${p.id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          unblockPlan.mutate(p.id);
                        }}
                      >
                        {tBoard("unblockPlan")}
                      </Button>
                    )}
                    <Badge variant={PLAN_STATUS_VARIANT[p.status] ?? "muted"}>
                      {PLAN_STATUS_KEY[p.status] ? tPlan(PLAN_STATUS_KEY[p.status]) : p.status}
                    </Badge>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {/* ============ Tasks board ============ */}
      <section data-testid="tasks-board">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            {tBoard("tasksHeading")}{" "}
            {effectiveSelected && (
              <span
                className="text-muted-foreground text-sm font-normal"
                data-testid="board-selected-name"
              >
                — {selectedPlan?.title ?? ""}
              </span>
            )}
          </h2>
          <div className="flex items-center gap-3">
            {effectiveSelected && (
              <Badge variant="success" data-testid="board-live-indicator">
                {tBoard("live")}
              </Badge>
            )}
            {tasksQuery.data && (
              <p className="text-muted-foreground text-xs">
                {tBoard(tasks.length === 1 ? "tasksCountOne" : "tasksCountMany", {
                  count: tasks.length,
                })}
              </p>
            )}
          </div>
        </div>

        {dragError && (
          <div
            className="bg-danger-soft text-danger-soft-foreground mb-3 rounded p-2 text-xs"
            data-testid="board-drag-error"
          >
            {dragError}
          </div>
        )}

        {!effectiveSelected ? (
          <Card className="p-8 text-center" data-testid="board-no-selection">
            <p className="text-muted-foreground text-sm">{tBoard("noSelection")}</p>
          </Card>
        ) : (
          <div
            className="grid grid-cols-2 gap-3 overflow-x-auto pb-2 sm:grid-cols-3 lg:grid-cols-7"
            data-testid="board-columns"
          >
            {COLUMNS.map((col) => {
              const colTasks = tasks.filter((t) => t.status === col.id);
              return (
                <KanbanColumn
                  key={col.id}
                  status={col.id}
                  label={t(col.labelKey)}
                  variant={col.variant}
                  tasks={colTasks}
                  loading={tasksQuery.isLoading}
                  onDrop={onDrop}
                  statusById={statusById}
                />
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

// --------------------------------------------------------------------------
// Column + Card
// --------------------------------------------------------------------------
function KanbanColumn({
  status,
  label,
  variant,
  tasks,
  loading,
  onDrop,
  statusById,
}: {
  status: Task["status"];
  label: string;
  variant: BadgeVariant;
  tasks: Task[];
  loading: boolean;
  onDrop: (status: Task["status"], taskId: string) => void;
  statusById: ReadonlyMap<string, string>;
}) {
  const tBoard = useT("board");
  const [over, setOver] = useState(false);

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (!over) setOver(true);
  }

  function handleDragLeave() {
    setOver(false);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
    const id = e.dataTransfer.getData("text/plain");
    if (id) onDrop(status, id);
  }

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-testid={`col-${status}`}
      data-status={status}
      className={cn(
        "bg-muted/40 flex min-h-[12rem] flex-col gap-2 rounded-lg p-2",
        "transition-colors",
        over && "bg-primary/10 ring-2 ring-primary/40",
      )}
    >
      <div className="flex items-center justify-between px-1">
        <Badge variant={variant}>{label}</Badge>
        <span
          className="text-muted-foreground text-xs tabular-nums"
          data-testid={`col-count-${status}`}
        >
          {tasks.length}
        </span>
      </div>

      {loading && tasks.length === 0 && (
        <p className="text-muted-foreground p-2 text-xs">{tBoard("colLoading")}</p>
      )}

      {!loading && tasks.length === 0 && (
        <p className="text-muted-foreground p-2 text-xs italic" data-testid={`col-empty-${status}`}>
          {tBoard("colEmpty")}
        </p>
      )}

      {tasks.map((t) => (
        <TaskCard key={t.id} task={t} statusById={statusById} />
      ))}
    </div>
  );
}

function TaskCard({ task, statusById }: { task: Task; statusById: ReadonlyMap<string, string> }) {
  const tBoard = useT("board");
  const tPriority = useT("taskPriority");
  const dep = computeDepState(task.depends_on, statusById);
  const [runsOpen, setRunsOpen] = useState(false);
  // A drag fires dragstart (not click), but guard anyway so a drag that ends on
  // the same card never opens the panel — click-vs-drag (runs-visor C2).
  const draggingRef = useRef(false);

  function handleDragStart(e: React.DragEvent<HTMLDivElement>) {
    draggingRef.current = true;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", task.id);
  }

  function handleClick() {
    if (draggingRef.current) return;
    setRunsOpen(true);
  }

  return (
    <>
      <div
        draggable
        onDragStart={handleDragStart}
        onDragEnd={() => {
          // Clear after the click event would have fired, so the drag never
          // counts as a click that opens the panel.
          window.setTimeout(() => {
            draggingRef.current = false;
          }, 0);
        }}
        onClick={handleClick}
        data-testid={`task-card-${task.id}`}
        data-blocked={dep.blocked ? "true" : "false"}
        className={cn(
          "bg-card rounded-md border p-2 text-sm shadow-sm",
          "cursor-grab transition-shadow active:cursor-grabbing",
          "hover:border-primary/40 hover:shadow-md",
          dep.blocked && "border-danger/40",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <p className="font-medium leading-tight">{task.title}</p>
          {dep.blocked ? (
            <span
              title={tBoard(dep.pendingCount === 1 ? "lockedOne" : "lockedMany", {
                count: dep.pendingCount,
              })}
              data-testid={`task-lock-${task.id}`}
              className="mt-0.5 shrink-0"
            >
              <Lock className="text-danger h-3.5 w-3.5" aria-label={tBoard("lockedAria")} />
            </span>
          ) : (
            dep.hasDeps && (
              <span
                title={tBoard("unlocked")}
                data-testid={`task-lock-open-${task.id}`}
                className="mt-0.5 shrink-0"
              >
                <LockOpen
                  className="text-muted-foreground h-3.5 w-3.5"
                  aria-label={tBoard("unlockedAria")}
                />
              </span>
            )
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <Badge variant={PRIORITY_VARIANT[task.priority] ?? "muted"}>
            {isTaskPriority(task.priority) ? tPriority(task.priority) : task.priority}
          </Badge>
          {task.description && (
            <span className="text-muted-foreground line-clamp-1 text-xs">{task.description}</span>
          )}
        </div>
      </div>
      <TaskDetailSheet
        task={runsOpen ? { id: task.id, project_id: task.project_id, title: task.title } : null}
        open={runsOpen}
        onOpenChange={setRunsOpen}
      />
    </>
  );
}
