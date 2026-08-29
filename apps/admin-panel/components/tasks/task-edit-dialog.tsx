"use client";

/**
 * `TaskEditDialog` — el formulario de edición de tarea (ADR 0162).
 *
 * `PUT /projects/{pid}/tasks/{tid}` acepta doce campos actualizables desde el
 * primer plan del roadmap. El navegador editaba DOS: el estado (arrastrando la
 * tarjeta) y los criterios de aceptación (como texto). Título, descripción,
 * prioridad, plan, complejidad, reintentos y los dos agentes sólo se podían
 * fijar AL CREAR la tarea — después eran inmutables desde el panel aunque el
 * endpoint los aceptara. `assigned_agent_id` y `reviewer_agent_id` no se
 * pintaban en ninguna pantalla: existían como declaración de tipo y nada más.
 *
 * Reutiliza la maquinaria del diálogo de alta (`Dialog` + `apiFetch`), y carga
 * la tarea entera por su cuenta en vez de recibirla por props: las dos listas
 * que lo montan (el Kanban del proyecto y la ficha compartida) traen una vista
 * recortada de la tarea, sin `estimated_complexity`, `max_retries` ni
 * `reviewer_agent_id`. Sembrar el formulario con lo que la lista tenía a mano
 * habría hecho que abrir y guardar pisara los tres campos que no venían.
 *
 * La `queryKey` es la MISMA que usa `task-detail-sheet.tsx` (`["task-detail", id]`),
 * así que abrir el diálogo desde la ficha no cuesta una petición extra y guardar
 * actualiza las dos vistas a la vez.
 */

import { useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
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
import {
  buildTaskPatch,
  formFromTask,
  RETRIES_MAX,
  RETRIES_MIN,
  TASK_COMPLEXITIES,
  TASK_PRIORITIES,
  TITLE_MAX,
  validateTaskForm,
  type EditableTask,
  type TaskEditForm,
  type TaskPatch,
} from "@/components/tasks/task-edit-form";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { fetchAllPages } from "@/lib/paginate";
import { useErrorText } from "@/lib/use-error-text";

/** Lo mínimo que hace falta para ir a buscar la tarea. */
export interface TaskEditTarget {
  id: string;
  project_id: string;
}

/** `TaskResponse` en lo que este formulario necesita. */
interface TaskDetail extends EditableTask {
  id: string;
  project_id: string;
}

interface PlanOption {
  id: string;
  title: string;
}

/** `AgentResponse` recortado a lo que decide qué se ofrece y con qué rótulo. */
interface AgentOption {
  id: string;
  name: string;
  project_id: string | null;
}

const SELECT_CLASS =
  "border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm " +
  "focus-visible:outline-none focus-visible:ring-2";

export function TaskEditDialog({
  task,
  open,
  onOpenChange,
  onSaved,
}: {
  task: TaskEditTarget | null;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  /** Se llama tras un PUT correcto, por si la pantalla quiere hacer algo más
   * que refrescar (el diálogo ya invalida las listas por su cuenta). */
  onSaved?: () => void;
}) {
  const t = useT("taskEdit");
  const errorText = useErrorText();
  const taskId = task?.id ?? null;
  const projectId = task?.project_id ?? null;

  const detailQuery = useQuery({
    queryKey: ["task-detail", taskId],
    queryFn: () => apiFetch<TaskDetail>(`/projects/${projectId}/tasks/${taskId}`),
    enabled: open && !!taskId && !!projectId,
    refetchOnWindowFocus: false,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg">
      <DialogContent data-testid="task-edit-dialog">
        <DialogHeader>
          <DialogTitle>{t("dialogTitle")}</DialogTitle>
          <DialogDescription>{t("dialogDescription")}</DialogDescription>
        </DialogHeader>
        {detailQuery.isLoading || !detailQuery.data ? (
          <DialogBody>
            {detailQuery.isError ? (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="task-edit-load-error"
              >
                {t("loadError")} {errorText(detailQuery.error)}
              </p>
            ) : (
              <p className="text-muted-foreground text-sm" data-testid="task-edit-loading">
                {t("loading")}
              </p>
            )}
          </DialogBody>
        ) : (
          /* `key` por tarea: cambiar de tarjeta con el diálogo montado tiene
             que sembrar el formulario de nuevo, y un remontaje es la forma sin
             efectos de conseguirlo. */
          <TaskEditFormBody
            key={taskId as string}
            projectId={projectId as string}
            taskId={taskId as string}
            detail={detailQuery.data}
            onOpenChange={onOpenChange}
            onSaved={onSaved}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function TaskEditFormBody({
  projectId,
  taskId,
  detail,
  onOpenChange,
  onSaved,
}: {
  projectId: string;
  taskId: string;
  /** Sólo se lee al montar: de aquí salen la instantánea y la semilla. */
  detail: TaskDetail;
  onOpenChange: (next: boolean) => void;
  onSaved?: () => void;
}) {
  const t = useT("taskEdit");
  const tPriority = useT("taskPriority");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const uid = useId();
  /**
   * La tarea TAL Y COMO se le enseñó al operador, congelada al abrir.
   *
   * El diff se calcula contra ella y no contra el dato vivo de la caché, y eso
   * cierra los dos fallos que tiene la alternativa: un refetch en segundo plano
   * no borra lo que se está tecleando, y un campo que el operador NO tocó nunca
   * viaja en el `PUT` — así que si otro actor lo cambió mientras el diálogo
   * estaba abierto, guardar no se lo pisa.
   */
  const [baseline] = useState<TaskDetail>(detail);
  const [form, setForm] = useState<TaskEditForm>(() => formFromTask(detail));

  const plansQuery = useQuery({
    queryKey: ["plans", projectId],
    queryFn: () => apiFetch<PlanOption[]>(`/projects/${projectId}/plans`),
    refetchOnWindowFocus: false,
  });

  // Paginado exhaustivo: `GET /agents` trunca en silencio a DEFAULT_PAGE_SIZE,
  // así que un tenant con muchos agentes vería un desplegable al que le faltan
  // justo los que no caben en la primera página (PROY2-08).
  const agentsQuery = useQuery({
    queryKey: ["agents", "assignable"],
    queryFn: () => fetchAllPages<AgentOption>("/agents"),
    refetchOnWindowFocus: false,
  });

  const mutation = useMutation({
    mutationFn: (patch: TaskPatch) =>
      apiFetch<TaskDetail>(`/projects/${projectId}/tasks/${taskId}`, {
        method: "PUT",
        body: patch,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["task-detail", taskId], updated);
      // Las dos listas que montan este diálogo, más el tablero por plan: mover
      // una tarea de plan o de agente cambia lo que las tres pintan.
      void queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["tasks", "by-plan"] });
      onSaved?.();
      onOpenChange(false);
    },
  });

  /** Sólo los agentes que trabajan aquí: `GET /agents` devuelve también los
   * `project_local` de OTROS proyectos del tenant, y asignar uno de ésos sería
   * poner a trabajar a un agente que no vive en este proyecto. */
  const agents = useMemo(
    () =>
      (agentsQuery.data?.items ?? []).filter(
        (a) => a.project_id === null || a.project_id === projectId,
      ),
    [agentsQuery.data, projectId],
  );

  const plans = plansQuery.data ?? [];
  const planMissing = form.planId !== "" && !plans.some((p) => p.id === form.planId);

  const errors = validateTaskForm(form);
  const patch = buildTaskPatch(baseline, form);
  const hasChanges = Object.keys(patch).length > 0;
  const canSave = errors.length === 0 && hasChanges && !mutation.isPending;
  // El servidor lo acepta, así que se avisa sin bloquear: `_resolve_assignment`
  // (`chat/sync_to_kanban.py`) se niega a emparejar revisor e implementador
  // porque un agente que se revisa a sí mismo no aporta una segunda mirada.
  const reviewerIsAssignee =
    form.assignedAgentId !== "" && form.assignedAgentId === form.reviewerAgentId;

  function set<K extends keyof TaskEditForm>(key: K, value: TaskEditForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <>
      <DialogBody className="space-y-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${uid}-title`}>{t("fieldTitle")}</Label>
          <Input
            id={`${uid}-title`}
            value={form.title}
            onChange={(e) => set("title", e.target.value)}
            maxLength={TITLE_MAX}
            data-testid="task-edit-title"
          />
        </div>

        {/* `MarkdownTextarea` genera su propio id interno, así que la etiqueta
            se asocia por grupo (`aria-labelledby`) en vez de por `htmlFor`. */}
        <div
          className="flex flex-col gap-1.5"
          role="group"
          aria-labelledby={`${uid}-description-label`}
        >
          <Label id={`${uid}-description-label`}>{t("fieldDescription")}</Label>
          <MarkdownTextarea
            value={form.description}
            onChange={(next) => set("description", next)}
            rows={5}
            data-testid="task-edit-description"
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-priority`}>{t("fieldPriority")}</Label>
            <select
              id={`${uid}-priority`}
              value={form.priority}
              onChange={(e) => set("priority", e.target.value)}
              className={SELECT_CLASS}
              data-testid="task-edit-priority"
            >
              {TASK_PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {tPriority(p)}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-complexity`}>{t("fieldComplexity")}</Label>
            <select
              id={`${uid}-complexity`}
              value={form.complexity}
              onChange={(e) => set("complexity", e.target.value)}
              className={SELECT_CLASS}
              data-testid="task-edit-complexity"
            >
              <option value="">{t("complexityNone")}</option>
              {TASK_COMPLEXITIES.map((c) => (
                <option key={c} value={c}>
                  {c.toUpperCase()}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${uid}-plan`}>{t("fieldPlan")}</Label>
          {/* Mientras el catálogo no ha llegado, el `value` del `select` no casa
              con ninguna `option` y el navegador pinta la primera —«Sin plan»—
              como si estuviera elegida. Es una mentira breve pero peligrosa: el
              operador la lee, no toca el campo y se va convencido de que la
              tarea no cuelga de ningún plan. La opción-marcador conserva el
              valor y dice por qué todavía no tiene nombre. */}
          <select
            id={`${uid}-plan`}
            value={form.planId}
            onChange={(e) => set("planId", e.target.value)}
            className={SELECT_CLASS}
            data-testid="task-edit-plan"
          >
            <option value="">{t("planNone")}</option>
            {planMissing ? (
              <option value={form.planId}>
                {plansQuery.isPending ? t("plansLoading") : t("planUnknown", { id: form.planId })}
              </option>
            ) : null}
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${uid}-retries`}>{t("fieldMaxRetries")}</Label>
          <Input
            id={`${uid}-retries`}
            type="number"
            min={RETRIES_MIN}
            max={RETRIES_MAX}
            step={1}
            value={form.maxRetries}
            onChange={(e) => set("maxRetries", e.target.value)}
            data-testid="task-edit-max-retries"
          />
          <p className="text-muted-foreground text-[11px]">
            {t("maxRetriesHint", { min: RETRIES_MIN, max: RETRIES_MAX })}
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <AgentSelect
            id={`${uid}-assignee`}
            label={t("fieldAssignee")}
            value={form.assignedAgentId}
            agents={agents}
            loading={agentsQuery.isPending}
            onChange={(next) => set("assignedAgentId", next)}
            testId="task-edit-assignee"
          />
          <AgentSelect
            id={`${uid}-reviewer`}
            label={t("fieldReviewer")}
            value={form.reviewerAgentId}
            agents={agents}
            loading={agentsQuery.isPending}
            onChange={(next) => set("reviewerAgentId", next)}
            testId="task-edit-reviewer"
          />
        </div>

        {agentsQuery.isError ? (
          <p className="text-muted-foreground text-xs" data-testid="task-edit-agents-error">
            {t("agentsError")}
          </p>
        ) : null}

        {reviewerIsAssignee ? (
          <p
            className="bg-warning-soft text-warning-soft-foreground rounded p-2 text-xs"
            data-testid="task-edit-warning"
          >
            {t("warnReviewerIsAssignee")}
          </p>
        ) : null}

        {errors.length > 0 ? (
          <ul
            className="bg-danger-soft text-danger-soft-foreground space-y-1 rounded p-2 text-xs"
            data-testid="task-edit-validation"
          >
            {errors.map((e) => (
              <li key={e.key}>{t(e.key, e.vars)}</li>
            ))}
          </ul>
        ) : null}

        {mutation.isError ? (
          <p
            className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
            data-testid="task-edit-error"
          >
            {t("saveError")} {errorText(mutation.error)}
          </p>
        ) : null}
      </DialogBody>
      <DialogFooter>
        {!hasChanges && errors.length === 0 ? (
          <span
            className="text-muted-foreground mr-auto text-xs"
            data-testid="task-edit-no-changes"
          >
            {t("noChanges")}
          </span>
        ) : null}
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          {t("cancel")}
        </Button>
        <Button
          disabled={!canSave}
          onClick={() => mutation.mutate(patch)}
          data-testid="task-edit-submit"
        >
          {mutation.isPending ? t("saving") : t("save")}
        </Button>
      </DialogFooter>
    </>
  );
}

/**
 * Los dos desplegables de agente son el mismo control con otra etiqueta.
 *
 * El agente que la tarea ya tiene puede no estar en el catálogo (borrado, o de
 * otro proyecto tras una mudanza): en ese caso se le añade su propia opción en
 * vez de dejar el `select` sin valor casado, que el navegador pinta como si
 * estuviera seleccionado el primero — una mentira que se guardaría al primer
 * cambio de cualquier otro campo.
 */
function AgentSelect({
  id,
  label,
  value,
  agents,
  loading,
  onChange,
  testId,
}: {
  id: string;
  label: string;
  value: string;
  agents: readonly AgentOption[];
  /** El catálogo aún no ha llegado: «no está en la lista» no significa todavía
   * «ya no existe», y decir lo segundo sería alarmar por una espera. */
  loading: boolean;
  onChange: (next: string) => void;
  testId: string;
}) {
  const t = useT("taskEdit");
  const missing = value !== "" && !agents.some((a) => a.id === value);
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={SELECT_CLASS}
        data-testid={testId}
      >
        <option value="">{t("agentNone")}</option>
        {missing ? (
          <option value={value}>
            {loading ? t("agentsLoading") : t("agentUnknown", { id: value })}
          </option>
        ) : null}
        {agents.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </select>
    </div>
  );
}
