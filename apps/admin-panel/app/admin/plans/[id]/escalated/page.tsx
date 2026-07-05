"use client";

/**
 * Escalated-tasks panel (Plan 06 task_06_34b3) + free task form (06_34b5).
 *
 * Lista las tareas en `awaiting_human` de un plan, con las 4 acciones
 * humanas (approve_manual / reassign_with_guidance / block_with_reason /
 * cancel). Las dos acciones que necesitan input (reassign + block) se
 * abren en un Dialog antes de disparar el POST.
 *
 * También permite añadir una tarea libre al plan (no atada a un
 * checkbox), por si el humano detecta trabajo nuevo durante la
 * validación.
 *
 * Endpoints:
 *   GET  /plans/{id}/escalated-tasks
 *   POST /tasks/{id}/human-action   { action, reason?, guidance? }
 *   POST /plans/{id}/free-task      { title, description }
 */

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Check,
  FolderKanban,
  Home,
  Plus,
  RotateCcw,
  Workflow,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Badge } from "@/components/ui/badge";
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
import { ApiError, apiFetch } from "@/lib/api";

interface EscalatedTask {
  id: string;
  title: string;
  description: string;
  retry_count: number;
  history: Array<{ at: number; kind: string; payload: Record<string, unknown> }>;
}

interface EscalatedListResponse {
  tasks: EscalatedTask[];
}

interface PlanBreadcrumb {
  id: string;
  project_id: string;
  title: string;
  status: string;
}

type HumanAction =
  | "approve_manual"
  | "reassign_with_guidance"
  | "block_with_reason"
  | "cancel"
  | "retry";

interface HumanActionPayload {
  action: HumanAction;
  reason?: string;
  guidance?: string;
}

export default function EscalatedPage() {
  const params = useParams<{ id: string }>();
  const planId = params?.id ?? "";
  const queryClient = useQueryClient();

  const [reassignTask, setReassignTask] = useState<EscalatedTask | null>(null);
  const [blockTask, setBlockTask] = useState<EscalatedTask | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const planQuery = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => apiFetch<PlanBreadcrumb>(`/plans/${planId}`),
    enabled: Boolean(planId),
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  const tasksQuery = useQuery({
    queryKey: ["escalated-tasks", planId],
    queryFn: () => apiFetch<EscalatedListResponse>(`/plans/${planId}/escalated-tasks`),
    enabled: Boolean(planId),
    refetchOnWindowFocus: false,
  });

  const plan = planQuery.data;

  const actionMutation = useMutation({
    mutationFn: async ({ taskId, payload }: { taskId: string; payload: HumanActionPayload }) =>
      apiFetch(`/tasks/${taskId}/human-action`, { method: "POST", body: payload }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["escalated-tasks", planId] });
    },
  });

  function runAction(taskId: string, payload: HumanActionPayload) {
    actionMutation.mutate({ taskId, payload });
  }

  // T7c part D: un-stick the whole plan in one gesture (reactivate + re-enqueue all
  // its blocked tasks). Only offered when the plan is actually blocked.
  const unblockMutation = useMutation({
    mutationFn: async () => apiFetch(`/plans/${planId}/unblock`, { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["escalated-tasks", planId] });
      void queryClient.invalidateQueries({ queryKey: ["plan", planId] });
    },
  });

  const tasks = tasksQuery.data?.tasks ?? [];

  return (
    <div
      className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="escalated-page"
    >
      <Breadcrumb
        items={[
          { label: "Proyectos", href: "/admin/projects", icon: <Home className="h-3.5 w-3.5" /> },
          ...(plan
            ? [
                {
                  label: "Proyecto",
                  href: `/admin/projects/${plan.project_id}`,
                  icon: <FolderKanban className="h-3.5 w-3.5" />,
                },
                {
                  label: plan.title,
                  href: `/admin/projects/${plan.project_id}/plans/${plan.id}`,
                  icon: <Workflow className="h-3.5 w-3.5" />,
                },
              ]
            : []),
          { label: "Tareas escaladas" },
        ]}
      />

      <PageHeader
        icon={<AlertTriangle className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Tareas escaladas"
        description="Tareas del plan que llegaron al límite de reintentos del revisor automático y esperan decisión humana."
        actions={
          <div className="flex items-center gap-2">
            {plan?.status === "blocked" && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => unblockMutation.mutate()}
                disabled={unblockMutation.isPending}
                data-testid="plan-unblock"
              >
                <RotateCcw className="mr-1 h-4 w-4" />
                Desbloquear plan
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCreateOpen(true)}
              data-testid="free-task-open"
            >
              <Plus className="mr-1 h-4 w-4" />
              Añadir tarea libre
            </Button>
          </div>
        }
      />

      <Card className="bg-warning-soft border-warning/30 mt-4">
        <CardContent className="text-warning-soft-foreground py-3 text-xs">
          <strong>Esta vista depende del orchestrator productivo (Plan 06.5).</strong> La UI está
          completa, pero los endpoints (<code>/plans/&#123;id&#125;/escalated-tasks</code>,
          <code>/tasks/&#123;id&#125;/human-action</code>,{" "}
          <code>/plans/&#123;id&#125;/free-task</code>) se cablearán al cerrar 06.5. Mientras tanto
          los botones devolverán un error 404 esperado.
        </CardContent>
      </Card>

      {actionMutation.isError && (
        <div
          className="bg-danger-soft text-danger-soft-foreground mt-4 rounded p-3 text-sm"
          data-testid="action-error"
        >
          {actionMutation.error instanceof ApiError
            ? actionMutation.error.body
            : String(actionMutation.error)}
        </div>
      )}

      <section data-testid="escalated-list" className="mt-6 space-y-3">
        {tasksQuery.isLoading && (
          <p className="text-muted-foreground text-sm">Cargando tareas escaladas…</p>
        )}
        {tasksQuery.isError && (
          <Card>
            <CardContent className="py-6">
              <p className="text-destructive text-sm" data-testid="escalated-error">
                {tasksQuery.error instanceof ApiError
                  ? tasksQuery.error.body
                  : String(tasksQuery.error)}
              </p>
            </CardContent>
          </Card>
        )}
        {!tasksQuery.isLoading && !tasksQuery.isError && tasks.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center">
              <p className="text-muted-foreground text-sm" data-testid="escalated-empty">
                Sin tareas escaladas en este plan.
              </p>
            </CardContent>
          </Card>
        )}
        {tasks.map((task) => (
          <EscalatedTaskRow
            key={task.id}
            task={task}
            disabled={actionMutation.isPending}
            onApprove={() => runAction(task.id, { action: "approve_manual" })}
            onRetry={() => runAction(task.id, { action: "retry" })}
            onCancel={() => runAction(task.id, { action: "cancel" })}
            onReassign={() => setReassignTask(task)}
            onBlock={() => setBlockTask(task)}
          />
        ))}
      </section>

      <ReassignDialog
        task={reassignTask}
        onClose={() => setReassignTask(null)}
        onSubmit={(guidance) => {
          if (!reassignTask) return;
          runAction(reassignTask.id, { action: "reassign_with_guidance", guidance });
          setReassignTask(null);
        }}
      />

      <BlockDialog
        task={blockTask}
        onClose={() => setBlockTask(null)}
        onSubmit={(reason) => {
          if (!blockTask) return;
          runAction(blockTask.id, { action: "block_with_reason", reason });
          setBlockTask(null);
        }}
      />

      <FreeTaskDialog
        planId={planId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          void queryClient.invalidateQueries({ queryKey: ["escalated-tasks", planId] });
          setCreateOpen(false);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Task row
// ---------------------------------------------------------------------------

function EscalatedTaskRow({
  task,
  disabled,
  onApprove,
  onRetry,
  onReassign,
  onBlock,
  onCancel,
}: {
  task: EscalatedTask;
  disabled: boolean;
  onApprove: () => void;
  onRetry: () => void;
  onReassign: () => void;
  onBlock: () => void;
  onCancel: () => void;
}) {
  return (
    <Card data-testid={`escalated-${task.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <div className="flex-1">
          <CardTitle className="text-base">{task.title}</CardTitle>
          {task.description && (
            <p className="text-muted-foreground mt-1 text-sm">{task.description}</p>
          )}
        </div>
        <Badge variant="warning" data-testid={`escalated-${task.id}-retries`}>
          {task.retry_count} {task.retry_count === 1 ? "reintento" : "reintentos"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="default"
            onClick={onApprove}
            disabled={disabled}
            data-testid={`approve-${task.id}`}
          >
            <Check className="mr-1 h-3.5 w-3.5" />
            Aprobar manualmente
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onRetry}
            disabled={disabled}
            data-testid={`retry-${task.id}`}
          >
            <RotateCcw className="mr-1 h-3.5 w-3.5" />
            Reintentar
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onReassign}
            disabled={disabled}
            data-testid={`reassign-${task.id}`}
          >
            <Workflow className="mr-1 h-3.5 w-3.5" />
            Reasignar con guía
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onBlock}
            disabled={disabled}
            data-testid={`block-${task.id}`}
          >
            <Ban className="mr-1 h-3.5 w-3.5" />
            Bloquear con motivo
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={onCancel}
            disabled={disabled}
            data-testid={`cancel-${task.id}`}
          >
            Cancelar
          </Button>
        </div>

        {task.history.length > 0 && (
          <details className="text-xs">
            <summary className="text-muted-foreground hover:text-foreground cursor-pointer">
              Ver historial ({task.history.length} eventos)
            </summary>
            <ul className="mt-2 space-y-1 pl-4">
              {task.history.map((event, idx) => (
                <li key={idx} className="text-muted-foreground">
                  <span className="font-mono text-[10px]">
                    {new Date(event.at * 1000).toLocaleString("es-ES")}
                  </span>{" "}
                  · <span className="font-medium">{event.kind}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Reassign dialog
// ---------------------------------------------------------------------------

function ReassignDialog({
  task,
  onClose,
  onSubmit,
}: {
  task: EscalatedTask | null;
  onClose: () => void;
  onSubmit: (guidance: string) => void;
}) {
  const [guidance, setGuidance] = useState("");

  return (
    <Dialog
      open={task !== null}
      onOpenChange={(v) => {
        if (!v) {
          setGuidance("");
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reasignar con guía</DialogTitle>
          <DialogDescription>
            Devuelve la tarea al backlog con instrucciones específicas para el siguiente intento. La
            guía queda en el historial de la tarea.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label>Guía para el agente</Label>
            <MarkdownTextarea
              value={guidance}
              onChange={setGuidance}
              rows={5}
              placeholder="Por ejemplo: 'Intenta otro enfoque usando la librería X en vez de Y.'"
              data-testid="reassign-guidance"
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            disabled={!guidance.trim()}
            onClick={() => onSubmit(guidance.trim())}
            data-testid="reassign-submit"
          >
            Reasignar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Block dialog
// ---------------------------------------------------------------------------

function BlockDialog({
  task,
  onClose,
  onSubmit,
}: {
  task: EscalatedTask | null;
  onClose: () => void;
  onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");

  return (
    <Dialog
      open={task !== null}
      onOpenChange={(v) => {
        if (!v) {
          setReason("");
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bloquear con motivo</DialogTitle>
          <DialogDescription>
            Marca la tarea como bloqueada por una causa externa (falta de acceso, dependencia
            pendiente, decisión de producto…). El motivo queda visible en el historial.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label>Motivo del bloqueo</Label>
            <MarkdownTextarea
              value={reason}
              onChange={setReason}
              rows={4}
              placeholder="Por ejemplo: 'Esperando credencial de la API del cliente.'"
              data-testid="block-reason"
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="destructive"
            disabled={!reason.trim()}
            onClick={() => onSubmit(reason.trim())}
            data-testid="block-submit"
          >
            Bloquear
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Free task dialog
// ---------------------------------------------------------------------------

function FreeTaskDialog({
  planId,
  open,
  onClose,
  onCreated,
}: {
  planId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");

  const mutation = useMutation<unknown, ApiError, { title: string; description: string }>({
    mutationFn: (payload) =>
      apiFetch(`/plans/${planId}/free-task`, { method: "POST", body: payload }),
    onSuccess: () => {
      setTitle("");
      setDescription("");
      onCreated();
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          setTitle("");
          setDescription("");
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Añadir tarea libre al plan</DialogTitle>
          <DialogDescription>
            Crea una tarea plan-scoped que no esté atada a ningún checkbox de la spec. Útil cuando
            el humano detecta trabajo nuevo durante la validación del plan.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="free-task-title">Título</Label>
            <Input
              id="free-task-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              data-testid="free-task-title"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={5}
              data-testid="free-task-description"
            />
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="free-task-error"
            >
              {mutation.error?.message ?? "Error al crear la tarea libre"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            disabled={!title.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                title: title.trim(),
                description: description.trim(),
              })
            }
            data-testid="free-task-submit"
          >
            {mutation.isPending ? "Creando…" : "Añadir tarea"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
