/**
 * La mitad PURA del formulario de edición de tarea (ADR 0162).
 *
 * Aquí viven las dos cosas que no son pintar: qué rechaza el servidor —para
 * poder decirlo ANTES de mandar— y qué campos viajan en el `PUT`.
 *
 * Está separado del diálogo por la misma razón que `lib/acceptance-criteria.ts`
 * lo está de `task-detail-sheet.tsx`: son reglas que espejan al backend, y un
 * espejo hay que poder compararlo con el original sin montar React.
 *
 * **El original es `api_server.schemas.tasks.TaskUpdateRequest`.** Si cambia
 * allí, cambia aquí; una copia que se desincroniza es peor que no tenerla,
 * porque promete una validación que ya no coincide.
 */

/** Espejo de `TaskUpdateRequest.title` (`max_length=200`). */
export const TITLE_MAX = 200;
/** Espejo de `TaskUpdateRequest.max_retries` (`ge=0, le=20`). */
export const RETRIES_MIN = 0;
export const RETRIES_MAX = 20;

/** Espejo del enum `TaskPriority` del backend. */
export const TASK_PRIORITIES = ["low", "medium", "high", "critical"] as const;
/** Espejo del enum `TaskComplexity` del backend. */
export const TASK_COMPLEXITIES = ["xs", "s", "m", "l", "xl"] as const;

/**
 * Los ocho campos de una tarea que este formulario edita.
 *
 * `TaskUpdateRequest` acepta doce. Los otros cuatro se editan en otro sitio y a
 * propósito: `status` se mueve arrastrando en el tablero (y tiene su máquina de
 * estados con su 409), `acceptance_criteria` tiene su editor en la ficha,
 * `depends_on` es el DAG y `inputs` no es dato de operador.
 */
export interface EditableTask {
  title: string;
  description: string | null;
  priority: string;
  plan_id: string | null;
  estimated_complexity: string | null;
  max_retries: number;
  assigned_agent_id: string | null;
  reviewer_agent_id: string | null;
}

/**
 * El formulario tal y como lo devuelve el DOM: todo cadenas, y la cadena vacía
 * como «ninguno». Guardar los reintentos como `number` obligaría a inventar un
 * valor para el hueco a medio teclear, y ese valor inventado es justo lo que
 * impediría avisar de que está vacío.
 */
export interface TaskEditForm {
  title: string;
  description: string;
  priority: string;
  planId: string;
  complexity: string;
  maxRetries: string;
  assignedAgentId: string;
  reviewerAgentId: string;
}

export type TaskFormErrorKey =
  "errorTitleEmpty" | "errorTitleTooLong" | "errorRetriesNotInteger" | "errorRetriesRange";

export interface TaskFormError {
  key: TaskFormErrorKey;
  vars?: Record<string, string | number>;
}

/** El cuerpo del `PUT`: sólo las claves que el operador tocó. */
export interface TaskPatch {
  title?: string;
  description?: string | null;
  priority?: string;
  plan_id?: string | null;
  estimated_complexity?: string | null;
  max_retries?: number;
  assigned_agent_id?: string | null;
  reviewer_agent_id?: string | null;
}

export function formFromTask(task: EditableTask): TaskEditForm {
  return {
    title: task.title,
    description: task.description ?? "",
    priority: task.priority,
    planId: task.plan_id ?? "",
    complexity: task.estimated_complexity ?? "",
    maxRetries: String(task.max_retries),
    assignedAgentId: task.assigned_agent_id ?? "",
    reviewerAgentId: task.reviewer_agent_id ?? "",
  };
}

/**
 * Los motivos por los que el servidor devolvería 422, comprobados aquí.
 *
 * Se devuelven TODOS y no el primero: el operador que tiene dos campos mal
 * prefiere enterarse de los dos que descubrir el segundo al arreglar el primero.
 */
export function validateTaskForm(form: TaskEditForm): TaskFormError[] {
  const errors: TaskFormError[] = [];

  // `_BASE_CONFIG` lleva `str_strip_whitespace=True`: Pydantic recorta ANTES de
  // medir, así que "   " es la cadena vacía y choca con `min_length=1`.
  const title = form.title.trim();
  if (title.length === 0) {
    errors.push({ key: "errorTitleEmpty" });
  } else if (title.length > TITLE_MAX) {
    errors.push({ key: "errorTitleTooLong", vars: { max: TITLE_MAX } });
  }

  const raw = form.maxRetries.trim();
  const retries = raw === "" ? Number.NaN : Number(raw);
  if (!Number.isInteger(retries)) {
    errors.push({ key: "errorRetriesNotInteger" });
  } else if (retries < RETRIES_MIN || retries > RETRIES_MAX) {
    errors.push({ key: "errorRetriesRange", vars: { min: RETRIES_MIN, max: RETRIES_MAX } });
  }

  return errors;
}

/**
 * El diff entre la tarea y el formulario, en la forma que espera el endpoint.
 *
 * Manda SÓLO lo cambiado porque el `PUT` aplica `model_fields_set`: una clave
 * ausente deja la columna como está y un `null` explícito la vacía. Mandar los
 * ocho campos siempre tendría dos precios — pisaría lo que otro actor (un
 * agente, el orquestador) hubiera cambiado mientras el diálogo estaba abierto,
 * y convertiría «abrir y cerrar sin tocar nada» en una escritura.
 *
 * Presupone un formulario ya validado: `validateTaskForm` es la puerta.
 */
export function buildTaskPatch(task: EditableTask, form: TaskEditForm): TaskPatch {
  const patch: TaskPatch = {};

  const title = form.title.trim();
  if (title !== task.title) patch.title = title;

  const description = form.description.trim() === "" ? null : form.description.trim();
  if (description !== (task.description ?? null)) patch.description = description;

  if (form.priority !== task.priority) patch.priority = form.priority;

  const planId = form.planId === "" ? null : form.planId;
  if (planId !== task.plan_id) patch.plan_id = planId;

  const complexity = form.complexity === "" ? null : form.complexity;
  if (complexity !== task.estimated_complexity) patch.estimated_complexity = complexity;

  const retries = Number(form.maxRetries.trim());
  if (retries !== task.max_retries) patch.max_retries = retries;

  const assignee = form.assignedAgentId === "" ? null : form.assignedAgentId;
  if (assignee !== task.assigned_agent_id) patch.assigned_agent_id = assignee;

  const reviewer = form.reviewerAgentId === "" ? null : form.reviewerAgentId;
  if (reviewer !== task.reviewer_agent_id) patch.reviewer_agent_id = reviewer;

  return patch;
}
