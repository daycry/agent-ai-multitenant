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

import { PageHeader } from "@/components/layout/page-header";
import { RunHistorySheet } from "@/components/runs/run-history-sheet";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";
import { computeDepState } from "@/lib/task-deps";
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

interface Team {
  id: string;
  name: string;
}

// --------------------------------------------------------------------------
// Status columns. Keep cancelled at the end — it's terminal-but-rare.
// --------------------------------------------------------------------------
const COLUMNS: Array<{
  id: Task["status"];
  label: string;
  variant: BadgeVariant;
}> = [
  { id: "backlog", label: "Backlog", variant: "muted" },
  { id: "ready", label: "Ready", variant: "info" },
  { id: "in_progress", label: "En curso", variant: "primary" },
  // ADR 0020 — el agente queda libre; al aprobar la tarea vuelve a backlog,
  // al rechazar pasa a blocked.
  { id: "awaiting_human_approval", label: "Pendiente de aprobación", variant: "warning" },
  { id: "in_review", label: "Revisión", variant: "warning" },
  { id: "blocked", label: "Bloqueada", variant: "danger" },
  { id: "done", label: "Hecho", variant: "success" },
  { id: "cancelled", label: "Cancelada", variant: "muted" },
];

const PRIORITY_VARIANT: Record<string, BadgeVariant> = {
  low: "muted",
  medium: "info",
  high: "warning",
  critical: "danger",
};

/**
 * Turn a failed move into a human message. The server gates DAG-forward moves
 * (ready / in_progress / …) and replies 422 `dependencies_not_done`; surface
 * that as a friendly Spanish line instead of the raw JSON body.
 */
function describeMoveError(err: unknown, target: Task["status"]): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as {
        detail?: { error?: string; pending?: unknown[] };
      };
      if (parsed.detail?.error === "dependencies_not_done") {
        const n = parsed.detail.pending?.length ?? 0;
        const label = COLUMNS.find((c) => c.id === target)?.label ?? target;
        return `No se puede mover a «${label}»: ${n} dependencia${n === 1 ? "" : "s"} sin completar.`;
      }
    } catch {
      // body wasn't the structured DAG error — fall back to the raw text.
    }
    return err.body;
  }
  return String(err);
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function BoardPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dragError, setDragError] = useState<string | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["projects", "tenant"],
    queryFn: () => apiFetch<Project[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  const teamsQuery = useQuery({
    queryKey: ["teams", "list"],
    queryFn: () => apiFetch<Team[]>("/teams"),
    refetchOnWindowFocus: false,
  });

  const plans = useMemo(
    () => (projectsQuery.data ?? []).filter((p) => !p.is_template),
    [projectsQuery.data],
  );

  // Auto-select the first plan once data lands.
  const effectiveSelected =
    selectedId && plans.some((p) => p.id === selectedId) ? selectedId : (plans[0]?.id ?? null);

  const teamsById = useMemo(
    () => new Map((teamsQuery.data ?? []).map((t) => [t.id, t] as const)),
    [teamsQuery.data],
  );

  const tasksQuery = useQuery({
    queryKey: ["tasks", "by-project", effectiveSelected],
    queryFn: () => apiFetch<Task[]>(`/projects/${effectiveSelected}/tasks`),
    enabled: !!effectiveSelected,
    refetchOnWindowFocus: false,
  });

  // taskId -> status, for resolving each card's dependency state (the padlock)
  // and the "can't go ready while a dependency is pending" drag guard.
  const statusById = useMemo(
    () => new Map((tasksQuery.data ?? []).map((t) => [t.id, t.status] as const)),
    [tasksQuery.data],
  );

  const moveTask = useMutation({
    mutationFn: async ({ task, newStatus }: { task: Task; newStatus: Task["status"] }) => {
      return apiFetch<Task>(`/projects/${task.project_id}/tasks/${task.id}`, {
        method: "PUT",
        body: { status: newStatus },
      });
    },
    onMutate: async ({ task, newStatus }) => {
      // Optimistic cache update so the card jumps columns instantly.
      const key = ["tasks", "by-project", effectiveSelected];
      await queryClient.cancelQueries({ queryKey: key });
      const prev = queryClient.getQueryData<Task[]>(key);
      queryClient.setQueryData<Task[]>(
        key,
        (prev ?? []).map((t) => (t.id === task.id ? { ...t, status: newStatus } : t)),
      );
      return { prev };
    },
    onError: (err, vars, context) => {
      if (context?.prev) {
        queryClient.setQueryData(["tasks", "by-project", effectiveSelected], context.prev);
      }
      setDragError(describeMoveError(err, vars.newStatus));
    },
    onSuccess: () => setDragError(null),
  });

  function onDrop(newStatus: Task["status"], taskId: string) {
    const task = (tasksQuery.data ?? []).find((t) => t.id === taskId);
    if (!task || task.status === newStatus) return;
    // Mirror the server DAG guard in the UI: refuse to drag a card into `ready`
    // while an upstream dependency is still pending (the card shows a padlock).
    // Other gated moves (in_progress, …) are caught by the server 422 below.
    if (newStatus === "ready") {
      const dep = computeDepState(task.depends_on, statusById);
      if (dep.blocked) {
        setDragError(
          `No se puede mover «${task.title}» a Ready: ${dep.pendingCount} ` +
            `dependencia${dep.pendingCount === 1 ? "" : "s"} sin completar.`,
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
    () => (effectiveSelected ? wsUrl(`/ws/kanban/${effectiveSelected}`) : null),
    [effectiveSelected],
  );

  const onKanbanEvent = useCallback(
    (data: unknown) => {
      if (!effectiveSelected) return;
      const event = data as {
        type?: string;
        task_id?: string;
        payload?: { new_status?: string };
      };
      const key = ["tasks", "by-project", effectiveSelected];
      const newStatus = event.payload?.new_status;
      if (event.type === "task.status_changed" && event.task_id && newStatus) {
        queryClient.setQueryData<Task[]>(key, (prev) =>
          (prev ?? []).map((t) => (t.id === event.task_id ? { ...t, status: newStatus } : t)),
        );
      } else if (event.type === "task.created") {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
    [effectiveSelected, queryClient],
  );

  useWebSocket(kanbanUrl, onKanbanEvent);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<LayoutGrid className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Tablero"
        description="Planes (gerencial) arriba, tareas (operativa) abajo. Arrastra una tarea entre columnas para cambiar su estado."
      />

      {/* ============ Plans row ============ */}
      <section data-testid="plans-row" className="mb-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Planes</h2>
          {plans.length > 0 && (
            <p className="text-muted-foreground text-xs">
              {plans.length} {plans.length === 1 ? "plan" : "planes"}
            </p>
          )}
        </div>

        {projectsQuery.isLoading && (
          <p className="text-muted-foreground text-sm">Cargando planes…</p>
        )}

        {projectsQuery.isError && (
          <Card className="border-destructive p-4">
            <p className="text-destructive text-sm">
              Could not load plans:{" "}
              {projectsQuery.error instanceof ApiError
                ? projectsQuery.error.body
                : String(projectsQuery.error)}
            </p>
          </Card>
        )}

        {projectsQuery.data && plans.length === 0 && (
          <Card className="p-8 text-center" data-testid="plans-empty">
            <p className="text-muted-foreground text-sm">
              Este tenant aún no tiene planes activos. Crea un proyecto desde una plantilla para
              empezar.
            </p>
          </Card>
        )}

        {plans.length > 0 && (
          <div
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
            data-testid="plans-grid"
          >
            {plans.map((p) => {
              const team = p.team_id ? teamsById.get(p.team_id) : null;
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
                    <CardTitle className="text-base">{p.name}</CardTitle>
                    {team && (
                      <Badge variant="info" className="w-fit">
                        {team.name}
                      </Badge>
                    )}
                  </CardHeader>
                  <CardContent className="flex items-center justify-between gap-2">
                    <p className="text-muted-foreground line-clamp-2 text-xs">
                      {p.description ?? "Sin descripción."}
                    </p>
                    <Badge variant={p.status === "active" ? "success" : "muted"}>{p.status}</Badge>
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
            Tareas{" "}
            {effectiveSelected && (
              <span
                className="text-muted-foreground text-sm font-normal"
                data-testid="board-selected-name"
              >
                — {plans.find((p) => p.id === effectiveSelected)?.name ?? ""}
              </span>
            )}
          </h2>
          <div className="flex items-center gap-3">
            {effectiveSelected && (
              <Badge variant="success" data-testid="board-live-indicator">
                Tiempo real
              </Badge>
            )}
            {tasksQuery.data && (
              <p className="text-muted-foreground text-xs">
                {tasksQuery.data.length} {tasksQuery.data.length === 1 ? "tarea" : "tareas"}
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
            <p className="text-muted-foreground text-sm">Selecciona un plan para ver sus tareas.</p>
          </Card>
        ) : (
          <div
            className="grid grid-cols-2 gap-3 overflow-x-auto pb-2 sm:grid-cols-3 lg:grid-cols-7"
            data-testid="board-columns"
          >
            {COLUMNS.map((col) => {
              const colTasks = (tasksQuery.data ?? []).filter((t) => t.status === col.id);
              return (
                <KanbanColumn
                  key={col.id}
                  status={col.id}
                  label={col.label}
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
        <p className="text-muted-foreground p-2 text-xs">Cargando…</p>
      )}

      {!loading && tasks.length === 0 && (
        <p className="text-muted-foreground p-2 text-xs italic" data-testid={`col-empty-${status}`}>
          Sin tareas
        </p>
      )}

      {tasks.map((t) => (
        <TaskCard key={t.id} task={t} statusById={statusById} />
      ))}
    </div>
  );
}

function TaskCard({ task, statusById }: { task: Task; statusById: ReadonlyMap<string, string> }) {
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
              title={`Bloqueada por ${dep.pendingCount} dependencia${
                dep.pendingCount === 1 ? "" : "s"
              } sin completar`}
              data-testid={`task-lock-${task.id}`}
              className="mt-0.5 shrink-0"
            >
              <Lock className="text-danger h-3.5 w-3.5" aria-label="Bloqueada por dependencias" />
            </span>
          ) : (
            dep.hasDeps && (
              <span
                title="Todas las dependencias completadas"
                data-testid={`task-lock-open-${task.id}`}
                className="mt-0.5 shrink-0"
              >
                <LockOpen
                  className="text-muted-foreground h-3.5 w-3.5"
                  aria-label="Dependencias completadas"
                />
              </span>
            )
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <Badge variant={PRIORITY_VARIANT[task.priority] ?? "muted"}>{task.priority}</Badge>
          {task.description && (
            <span className="text-muted-foreground line-clamp-1 text-xs">{task.description}</span>
          )}
        </div>
      </div>
      <RunHistorySheet
        taskId={runsOpen ? task.id : null}
        taskTitle={task.title}
        open={runsOpen}
        onOpenChange={setRunsOpen}
      />
    </>
  );
}
