"use client";

/**
 * Escalated-tasks panel (Plan 06 task_06_34b3) + free task form (06_34b5).
 *
 * Lista las tareas en `awaiting_human` de un plan. Las acciones humanas ya no
 * viven aquí: son `<TaskHumanActions>` (`task_wf_40`), compartido con la ficha
 * de la tarea para que una tarea `blocked` que NO escaló también se pueda
 * desatascar.
 *
 * También permite añadir una tarea libre al plan (no atada a un
 * checkbox), por si el humano detecta trabajo nuevo durante la
 * validación.
 *
 * Endpoints:
 *   GET  /plans/{id}/escalated-tasks
 *   POST /plans/{id}/free-task      { title, description }
 *
 * Bilingüe desde prod-16 `task_prod16_03` (namespace `escalatedTasks`, más
 * `nav.projects` para la primera miga de pan y `common.dateLocale` para la fecha
 * del historial). Es una pantalla de DECISIÓN —quien la abre está desatascando
 * trabajo que el revisor automático rechazó tres veces—, así que leerla a medias
 * en otro idioma era el peor sitio donde dejar la deuda. Contrato en
 * `i18n.test.tsx`.
 */

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FolderKanban, Home, Plus, RotateCcw, Workflow } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { TaskHumanActions } from "@/components/tasks/task-human-actions";
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
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

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

export default function EscalatedPage() {
  const t = useT("escalatedTasks");
  // La primera miga de pan es el MISMO destino que el ítem de la sidebar, así
  // que reusa su clave (`nav.projects`) en vez de duplicar el texto: es lo que
  // hace `ProjectBreadcrumb`, y dos claves para un enlace acaban divergiendo.
  const tNav = useT("nav");
  const errorText = useErrorText();
  const params = useParams<{ id: string }>();
  const planId = params?.id ?? "";
  const queryClient = useQueryClient();

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

  // Cualquier acción humana puede haber desatascado el plan entero
  // (`reactivate_plan_if_unstuck`), así que se refresca también su cabecera:
  // si no, «Desbloquear plan» seguiría ofreciéndose sobre un plan ya activo.
  function onActionApplied() {
    void queryClient.invalidateQueries({ queryKey: ["escalated-tasks", planId] });
    void queryClient.invalidateQueries({ queryKey: ["plan", planId] });
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
      {/*
        La miga de pan entera espera al plan, en vez de pintar los dos extremos
        y colar el medio al llegar. No es cosmética: sin el plan, el rastro que
        se pintaba era «Proyectos › Tareas escaladas», que AFIRMA que esta vista
        cuelga del listado de proyectos. Es falso —cuelga del plan, que cuelga
        del proyecto—, así que durante la carga la miga de pan mentía y además
        saltaba de dos a cuatro eslabones al resolverse la query.
      */}
      {plan && (
        <Breadcrumb
          items={[
            {
              label: tNav("projects"),
              href: "/admin/projects",
              icon: <Home className="h-3.5 w-3.5" />,
            },
            {
              label: t("breadcrumbProject"),
              href: `/admin/projects/${plan.project_id}`,
              icon: <FolderKanban className="h-3.5 w-3.5" />,
            },
            {
              label: plan.title,
              href: `/admin/projects/${plan.project_id}/plans/${plan.id}`,
              icon: <Workflow className="h-3.5 w-3.5" />,
            },
            { label: t("breadcrumbCurrent") },
          ]}
        />
      )}

      <PageHeader
        icon={<AlertTriangle className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
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
                {t("unblockPlan")}
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCreateOpen(true)}
              data-testid="free-task-open"
            >
              <Plus className="mr-1 h-4 w-4" />
              {t("addFreeTask")}
            </Button>
          </div>
        }
      />

      <section data-testid="escalated-list" className="mt-6 space-y-3">
        {tasksQuery.isLoading && <p className="text-muted-foreground text-sm">{t("loading")}</p>}
        {tasksQuery.isError && (
          <Card>
            <CardContent className="py-6">
              <p className="text-destructive text-sm" data-testid="escalated-error">
                {errorText(tasksQuery.error)}
              </p>
            </CardContent>
          </Card>
        )}
        {!tasksQuery.isLoading && !tasksQuery.isError && tasks.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center">
              <p className="text-muted-foreground text-sm" data-testid="escalated-empty">
                {t("empty")}
              </p>
            </CardContent>
          </Card>
        )}
        {tasks.map((task) => (
          <EscalatedTaskRow key={task.id} task={task} onApplied={onActionApplied} />
        ))}
      </section>

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

function EscalatedTaskRow({ task, onApplied }: { task: EscalatedTask; onApplied: () => void }) {
  const t = useT("escalatedTasks");
  // El locale con el que formatear la fecha del historial sale del diccionario
  // (`common.dateLocale`) y no de un `lang === "es" ? "es-ES" : "en-GB"`, que es
  // justo el ternario que `check-i18n` prohíbe. Antes estaba cableado a
  // `"es-ES"`, así que con el toggle en inglés la fecha seguía en formato
  // castellano.
  const tCommon = useT("common");
  const events = task.history.length;

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
          {t(task.retry_count === 1 ? "retriesOne" : "retriesMany", { count: task.retry_count })}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <TaskHumanActions taskId={task.id} onApplied={onApplied} />

        {events > 0 && (
          <details className="text-xs" data-testid={`escalated-${task.id}-history`}>
            <summary className="text-muted-foreground hover:text-foreground cursor-pointer">
              {t(events === 1 ? "historyOne" : "historyMany", { count: events })}
            </summary>
            <ul className="mt-2 space-y-1 pl-4">
              {task.history.map((event, idx) => (
                <li key={idx} className="text-muted-foreground">
                  <span className="font-mono text-[10px]">
                    {new Date(event.at * 1000).toLocaleString(tCommon("dateLocale"))}
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
  const t = useT("escalatedTasks");
  const errorText = useErrorText();
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
      <DialogContent data-testid="free-task-dialog">
        <DialogHeader>
          <DialogTitle>{t("dialogTitle")}</DialogTitle>
          <DialogDescription>{t("dialogDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="free-task-title">{t("fieldTitle")}</Label>
            <Input
              id="free-task-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              data-testid="free-task-title"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t("fieldDescription")}</Label>
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
              {errorText(mutation.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="free-task-cancel">
            {t("cancel")}
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
            {mutation.isPending ? t("creating") : t("submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
