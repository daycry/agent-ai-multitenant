"use client";

/**
 * Tasks del proyecto — 7ª sub-section del hub.
 *
 * Lista TODAS las tasks del proyecto (incluyendo las que no están
 * asociadas a un plan — `plan_id = null`). Soporta dos vistas:
 *
 *   - **Lista**: filas por created_at, con badges de status + priority.
 *   - **Kanban**: columnas por status, drag-drop entre columnas. El
 *     drop dispara `PUT /projects/{pid}/tasks/{tid}` con el nuevo
 *     status, con optimistic update + revert en error.
 *
 * El filtro chip "Todas / Sin plan / Plan X / Plan Y" aplica a ambas
 * vistas y respeta CLAUDE.md §6 (no mezclar tareas de varios planes
 * en una misma columna).
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListTodo, Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ViewToggle, type ViewMode } from "@/components/ui/view-toggle";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";

interface Task {
  id: string;
  project_id: string;
  plan_id: string | null;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  assigned_agent_id: string | null;
}

interface Plan {
  id: string;
  title: string;
  status: string;
}

const COLUMNS: Array<{ id: string; label: string; variant: BadgeVariant }> = [
  { id: "backlog", label: "Backlog", variant: "muted" },
  { id: "ready", label: "Ready", variant: "info" },
  { id: "in_progress", label: "En curso", variant: "primary" },
  { id: "awaiting_human_approval", label: "Pendiente aprobación", variant: "warning" },
  { id: "in_review", label: "Revisión", variant: "warning" },
  { id: "blocked", label: "Bloqueada", variant: "danger" },
  { id: "done", label: "Hecho", variant: "success" },
  { id: "cancelled", label: "Cancelada", variant: "muted" },
];

const STATUS_VARIANT: Record<string, BadgeVariant> = Object.fromEntries(
  COLUMNS.map((c) => [c.id, c.variant]),
);

const STATUS_LABEL: Record<string, string> = Object.fromEntries(
  COLUMNS.map((c) => [c.id, c.label]),
);

const PRIORITY_VARIANT: Record<string, BadgeVariant> = {
  low: "muted",
  medium: "info",
  high: "warning",
  critical: "danger",
};

const PLAN_FILTER_ALL = "all";
const PLAN_FILTER_NULL = "null";

export default function ProjectTasksPage() {
  const params = useParams<{ id: string }>();
  const projectId = params?.id ?? "";
  const queryClient = useQueryClient();
  const [planFilter, setPlanFilter] = useState<string>(PLAN_FILTER_ALL);
  const [view, setView] = useState<ViewMode>("list");
  const [dragError, setDragError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const tasksQuery = useQuery({
    queryKey: ["project-tasks", projectId],
    queryFn: () => apiFetch<Task[]>(`/projects/${projectId}/tasks`),
    enabled: !!projectId,
    refetchOnWindowFocus: false,
  });

  const plansQuery = useQuery({
    queryKey: ["plans", projectId],
    queryFn: () => apiFetch<Plan[]>(`/projects/${projectId}/plans`),
    enabled: !!projectId,
    refetchOnWindowFocus: false,
  });

  const tasks = tasksQuery.data ?? [];
  const plans = plansQuery.data ?? [];

  const counts = useMemo(() => {
    const map: Record<string, number> = {
      [PLAN_FILTER_ALL]: tasks.length,
      [PLAN_FILTER_NULL]: 0,
    };
    for (const t of tasks) {
      if (t.plan_id === null) {
        map[PLAN_FILTER_NULL] = (map[PLAN_FILTER_NULL] ?? 0) + 1;
      } else {
        map[t.plan_id] = (map[t.plan_id] ?? 0) + 1;
      }
    }
    return map;
  }, [tasks]);

  const visible = useMemo(() => {
    if (planFilter === PLAN_FILTER_ALL) return tasks;
    if (planFilter === PLAN_FILTER_NULL) return tasks.filter((t) => t.plan_id === null);
    return tasks.filter((t) => t.plan_id === planFilter);
  }, [tasks, planFilter]);

  // Drag-drop mutation — only relevant for kanban view.
  const moveTask = useMutation({
    mutationFn: async ({ task, newStatus }: { task: Task; newStatus: string }) => {
      return apiFetch<Task>(`/projects/${task.project_id}/tasks/${task.id}`, {
        method: "PUT",
        body: { status: newStatus },
      });
    },
    onMutate: async ({ task, newStatus }) => {
      const key = ["project-tasks", projectId];
      await queryClient.cancelQueries({ queryKey: key });
      const prev = queryClient.getQueryData<Task[]>(key);
      queryClient.setQueryData<Task[]>(
        key,
        (prev ?? []).map((t) => (t.id === task.id ? { ...t, status: newStatus } : t)),
      );
      return { prev };
    },
    onError: (err, _vars, context) => {
      if (context?.prev) {
        queryClient.setQueryData(["project-tasks", projectId], context.prev);
      }
      setDragError(err instanceof ApiError ? err.body : String(err));
    },
    onSuccess: () => setDragError(null),
  });

  function onDrop(newStatus: string, taskId: string) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task || task.status === newStatus) return;
    moveTask.mutate({ task, newStatus });
  }

  return (
    <div
      className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="project-tasks-page"
    >
      <ProjectBreadcrumb projectId={projectId} current="Tasks" />
      <PageHeader
        icon={<ListTodo className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Tasks del proyecto"
        description="Todas las tareas — incluyendo las que no están asociadas a un plan. Filtra por plan para evitar mezclar contextos."
        actions={
          <div className="flex items-center gap-2">
            <ViewToggle value={view} onChange={setView} />
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCreateOpen(true)}
              data-testid="tasks-create-button"
            >
              <Plus className="mr-1 h-4 w-4" />
              Crear tarea
            </Button>
          </div>
        }
        data-testid="project-tasks-header"
      />

      <div
        className="bg-muted mt-6 inline-flex flex-wrap gap-1 rounded-md p-1"
        data-testid="tasks-plan-filter"
        role="tablist"
        aria-label="Filtrar tareas por plan"
      >
        <FilterChip
          label="Todas"
          value={PLAN_FILTER_ALL}
          count={counts[PLAN_FILTER_ALL] ?? 0}
          active={planFilter === PLAN_FILTER_ALL}
          onClick={() => setPlanFilter(PLAN_FILTER_ALL)}
        />
        <FilterChip
          label="Sin plan"
          value={PLAN_FILTER_NULL}
          count={counts[PLAN_FILTER_NULL] ?? 0}
          active={planFilter === PLAN_FILTER_NULL}
          onClick={() => setPlanFilter(PLAN_FILTER_NULL)}
        />
        {plans.map((p) => (
          <FilterChip
            key={p.id}
            label={p.title}
            value={p.id}
            count={counts[p.id] ?? 0}
            active={planFilter === p.id}
            onClick={() => setPlanFilter(p.id)}
          />
        ))}
      </div>

      {dragError && (
        <div
          className="bg-danger-soft text-danger-soft-foreground mt-4 rounded p-2 text-xs"
          data-testid="tasks-drag-error"
        >
          {dragError}
        </div>
      )}

      <div className="mt-6">
        {tasksQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">Cargando tareas…</p>
        ) : tasksQuery.isError ? (
          <Card>
            <CardHeader>
              <CardTitle>Error al cargar las tareas</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-destructive text-sm" data-testid="tasks-error">
                {tasksQuery.error instanceof ApiError
                  ? tasksQuery.error.body
                  : String(tasksQuery.error)}
              </p>
            </CardContent>
          </Card>
        ) : visible.length === 0 ? (
          <Card>
            <CardContent className="py-8">
              <p className="text-muted-foreground text-sm" data-testid="tasks-empty">
                {tasks.length === 0
                  ? "Este proyecto no tiene tareas todavía."
                  : "Ninguna tarea coincide con el filtro."}
              </p>
            </CardContent>
          </Card>
        ) : view === "kanban" ? (
          <div
            className="grid grid-cols-2 gap-3 overflow-x-auto pb-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-8"
            data-testid="tasks-kanban-columns"
          >
            {COLUMNS.map((col) => (
              <KanbanColumn
                key={col.id}
                status={col.id}
                label={col.label}
                variant={col.variant}
                tasks={visible.filter((t) => t.status === col.id)}
                onDrop={onDrop}
              />
            ))}
          </div>
        ) : (
          <ul className="space-y-2" data-testid="tasks-list">
            {visible.map((task) => (
              <li key={task.id}>
                <TaskRow projectId={projectId} task={task} />
              </li>
            ))}
          </ul>
        )}
      </div>

      <TaskCreateDialog
        projectId={projectId}
        plans={plans}
        defaultPlanId={
          planFilter !== PLAN_FILTER_ALL && planFilter !== PLAN_FILTER_NULL ? planFilter : null
        }
        defaultPlanFilter={planFilter}
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => {
          void queryClient.invalidateQueries({ queryKey: ["project-tasks", projectId] });
          setCreateOpen(false);
        }}
      />
    </div>
  );
}

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
      data-testid={`tasks-filter-${value}`}
      data-active={active ? "true" : "false"}
      className={
        active
          ? "bg-background text-foreground rounded px-3 py-1 text-xs font-medium shadow"
          : "text-muted-foreground hover:text-foreground rounded px-3 py-1 text-xs font-medium"
      }
    >
      {label}
      <span className="ml-1 opacity-60" data-testid={`tasks-filter-count-${value}`}>
        ({count})
      </span>
    </button>
  );
}

function TaskRow({ projectId, task }: { projectId: string; task: Task }) {
  const statusVariant = STATUS_VARIANT[task.status] ?? "muted";
  const priorityVariant = PRIORITY_VARIANT[task.priority] ?? "muted";
  const linkHref = task.plan_id ? `/admin/projects/${projectId}/plans/${task.plan_id}` : null;
  return (
    <Card className="transition-colors hover:bg-muted/30">
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <div className="flex-1">
          <CardTitle className="text-base">
            {linkHref ? (
              <Link
                href={linkHref}
                className="hover:underline"
                data-testid={`task-row-${task.id}-link`}
              >
                {task.title}
              </Link>
            ) : (
              task.title
            )}
          </CardTitle>
          {task.plan_id === null && (
            <p className="text-muted-foreground/70 mt-1 text-xs italic">Sin plan asignado</p>
          )}
        </div>
        <div className="flex flex-row items-center gap-2">
          <Badge variant={priorityVariant} data-testid={`task-row-${task.id}-priority`}>
            {task.priority}
          </Badge>
          <Badge
            variant={statusVariant}
            data-testid={`task-row-${task.id}-status`}
            data-status={task.status}
          >
            {STATUS_LABEL[task.status] ?? task.status}
          </Badge>
        </div>
      </CardHeader>
      {task.description && (
        <CardContent>
          <p className="text-muted-foreground text-sm line-clamp-2">{task.description}</p>
        </CardContent>
      )}
    </Card>
  );
}

function KanbanColumn({
  status,
  label,
  variant,
  tasks,
  onDrop,
}: {
  status: string;
  label: string;
  variant: BadgeVariant;
  tasks: Task[];
  onDrop: (status: string, taskId: string) => void;
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
      data-testid={`tasks-col-${status}`}
      data-status={status}
      className={cn(
        "bg-muted/40 flex min-h-[12rem] flex-col gap-2 rounded-lg p-2 transition-colors",
        over && "bg-primary/10 ring-primary/40 ring-2",
      )}
    >
      <div className="flex items-center justify-between px-1">
        <Badge variant={variant}>{label}</Badge>
        <span
          className="text-muted-foreground text-xs tabular-nums"
          data-testid={`tasks-col-count-${status}`}
        >
          {tasks.length}
        </span>
      </div>

      {tasks.length === 0 ? (
        <p
          className="text-muted-foreground p-2 text-xs italic"
          data-testid={`tasks-col-empty-${status}`}
        >
          Sin tareas
        </p>
      ) : (
        tasks.map((t) => <TaskCard key={t.id} task={t} />)
      )}
    </div>
  );
}

function TaskCard({ task }: { task: Task }) {
  function handleDragStart(e: React.DragEvent<HTMLDivElement>) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", task.id);
  }
  return (
    <div
      draggable
      onDragStart={handleDragStart}
      data-testid={`tasks-card-${task.id}`}
      className={cn(
        "bg-card rounded-md border p-2 text-sm shadow-sm",
        "cursor-grab transition-shadow active:cursor-grabbing",
        "hover:border-primary/40 hover:shadow-md",
      )}
    >
      <p className="font-medium leading-tight">{task.title}</p>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <Badge variant={PRIORITY_VARIANT[task.priority] ?? "muted"}>{task.priority}</Badge>
        {task.plan_id === null && (
          <span className="text-muted-foreground/70 text-[10px] italic">sin plan</span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create dialog — task can be attached to an existing plan or created
// "sin plan" (project-scoped only). The endpoint accepts plan_id=null,
// so we send null when the dropdown is on "Sin plan".
// ---------------------------------------------------------------------------

interface TaskCreatePayload {
  title: string;
  description?: string | null;
  plan_id?: string | null;
  priority: string;
}

function TaskCreateDialog({
  projectId,
  plans,
  defaultPlanId,
  defaultPlanFilter,
  open,
  onOpenChange,
  onCreated,
}: {
  projectId: string;
  plans: Plan[];
  defaultPlanId: string | null;
  defaultPlanFilter: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  // null = "sin plan". Otherwise a plan UUID.
  const [planId, setPlanId] = useState<string | null>(defaultPlanId);
  const [priority, setPriority] = useState<string>("medium");

  // Pre-select sensibly when the dialog reopens: respect the active
  // filter chip — if the user is on "Sin plan", default to null; if on
  // a concrete plan, default to that plan; otherwise leave the last
  // user choice.
  useEffect(() => {
    if (open) {
      setTitle("");
      setDescription("");
      if (defaultPlanFilter === PLAN_FILTER_NULL) {
        setPlanId(null);
      } else if (defaultPlanId !== null) {
        setPlanId(defaultPlanId);
      }
      setPriority("medium");
    }
  }, [open, defaultPlanFilter, defaultPlanId]);

  const mutation = useMutation<Task, ApiError, TaskCreatePayload>({
    mutationFn: (payload) =>
      apiFetch<Task>(`/projects/${projectId}/tasks`, {
        method: "POST",
        body: payload,
      }),
    onSuccess: onCreated,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Crear tarea</DialogTitle>
          <DialogDescription>
            Las tareas pueden colgar de un plan existente o vivir como tareas libres del proyecto
            (sin plan).
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="task-title">Título</Label>
            <Input
              id="task-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              data-testid="create-task-title"
              maxLength={200}
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={5}
              data-testid="create-task-description"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="task-plan">Plan</Label>
            <select
              id="task-plan"
              value={planId ?? PLAN_FILTER_NULL}
              onChange={(e) =>
                setPlanId(e.target.value === PLAN_FILTER_NULL ? null : e.target.value)
              }
              className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
              data-testid="create-task-plan"
            >
              <option value={PLAN_FILTER_NULL}>Sin plan (tarea libre)</option>
              {plans.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="task-priority">Prioridad</Label>
            <select
              id="task-priority"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
              data-testid="create-task-priority"
            >
              <option value="low">Baja</option>
              <option value="medium">Media</option>
              <option value="high">Alta</option>
              <option value="critical">Crítica</option>
            </select>
          </div>

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="create-task-error"
            >
              {mutation.error?.message ?? "Error al crear la tarea"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!title.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                title: title.trim(),
                description: description.trim() || null,
                plan_id: planId,
                priority,
              })
            }
            data-testid="create-task-submit"
          >
            {mutation.isPending ? "Creando…" : "Crear tarea"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
