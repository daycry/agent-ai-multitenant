/**
 * Piezas puras del editor de la especificación del plan (`task_wf_42`).
 *
 * Viven fuera del componente porque son las que tienen reglas que merecen un
 * test: qué estados admiten edición, cómo se poda una dependencia colgante y,
 * sobre todo, cómo se traduce un ciclo del DAG a algo que el operador pueda
 * arreglar. El error del backend llega como `{"error":"dag_cycle","cycle":[...]}`
 * y hasta ahora se pintaba en crudo — un JSON no le dice a nadie qué tarea
 * tocar.
 */

import { ApiError } from "@/lib/api";
import type { PlanTaskSpec } from "@/app/admin/projects/[id]/plans/[planId]/plan-spec-types";

/** Estados en los que la UI ofrece editar la especificación.
 *
 * Es MÁS ESTRECHO que el gate del backend a propósito: allí `in_progress`
 * sigue admitiendo el PUT porque la replanificación en caliente ya existe por
 * esa vía y la gobierna el ADR 0132. Ofrecer el editor en un plan que ya está
 * corriendo insinuaría que replanificar es un gesto resuelto, y no lo es. */
const EDITABLE_STATUSES = new Set(["draft", "pending_approval"]);

export function specEditable(status: string | null | undefined): boolean {
  return status != null && EDITABLE_STATUSES.has(status);
}

/** Fila del editor: el spec con los campos multivalor como texto plano, que es
 * lo que un `<textarea>` puede sostener mientras se escribe. */
export interface TaskDraft {
  key: number;
  id: string;
  title: string;
  description: string;
  role: string;
  complexity: string;
  estimatedHours: string;
  dependsOn: string[];
  /** Un criterio por línea. */
  criteria: string;
  /** Campos del spec que el editor no toca (p. ej. `origin`), preservados
   * tal cual para no perderlos al guardar. */
  rest: Record<string, unknown>;
}

export function toDrafts(tasks: readonly PlanTaskSpec[]): TaskDraft[] {
  return tasks.map((task, index) => {
    const {
      id,
      title,
      description,
      role,
      complexity,
      estimated_hours: hours,
      depends_on: deps,
      acceptance_criteria: criteria,
      ...rest
    } = task;
    return {
      key: index,
      id,
      title: title ?? "",
      description: description ?? "",
      role: role ?? "",
      complexity: complexity ?? "",
      estimatedHours: hours == null ? "" : String(hours),
      dependsOn: [...(deps ?? [])],
      criteria: (criteria ?? []).join("\n"),
      rest,
    };
  });
}

/** Vuelta al shape del spec. Los campos vacíos se OMITEN en vez de viajar como
 * cadena vacía: un `role: ""` persistido es peor que la ausencia del campo,
 * porque la UI lo pintaría como un rol asignado en blanco. */
export function toTaskSpecs(drafts: readonly TaskDraft[]): PlanTaskSpec[] {
  return drafts.map((draft) => {
    const spec: PlanTaskSpec = { ...draft.rest, id: draft.id.trim(), title: draft.title.trim() };
    const description = draft.description.trim();
    if (description) spec.description = description;
    const role = draft.role.trim();
    if (role) spec.role = role;
    const complexity = draft.complexity.trim();
    if (complexity) spec.complexity = complexity;
    const hours = Number.parseFloat(draft.estimatedHours);
    if (Number.isFinite(hours)) spec.estimated_hours = hours;
    if (draft.dependsOn.length > 0) spec.depends_on = [...draft.dependsOn];
    const criteria = draft.criteria
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (criteria.length > 0) spec.acceptance_criteria = criteria;
    return spec;
  });
}

/** Quita una tarea Y toda referencia a ella. Sin la poda, el backend responde
 * «depends on unknown task» — cierto, pero incomprensible: el operador borró
 * una tarea, no una dependencia. */
export function removeTask(drafts: readonly TaskDraft[], key: number): TaskDraft[] {
  const removed = drafts.find((d) => d.key === key);
  if (!removed) return [...drafts];
  return drafts
    .filter((d) => d.key !== key)
    .map((d) => ({ ...d, dependsOn: d.dependsOn.filter((dep) => dep !== removed.id) }));
}

/** Un id libre para una tarea nueva (`t1`, `t2`… saltando los ocupados). */
export function nextTaskId(drafts: readonly TaskDraft[]): string {
  const taken = new Set(drafts.map((d) => d.id));
  for (let n = drafts.length + 1; ; n += 1) {
    const candidate = `t${n}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** Problemas que se ven sin preguntar al servidor. No sustituyen a su
 * validación —el ciclo lo detecta él— pero evitan mandar un PUT que ya se
 * sabe que va a fallar. */
export function localSpecProblems(drafts: readonly TaskDraft[]): string[] {
  const problems: string[] = [];
  const ids = drafts.map((d) => d.id.trim());
  if (ids.some((id) => !id)) problems.push("Toda tarea necesita un identificador.");
  const seen = new Set<string>();
  for (const id of ids) {
    if (id && seen.has(id)) problems.push(`El identificador «${id}» está repetido.`);
    seen.add(id);
  }
  for (const draft of drafts) {
    if (!draft.title.trim()) {
      problems.push(`La tarea «${draft.id || "sin id"}» no tiene título.`);
    }
  }
  return problems;
}

function parseDetail(error: unknown): unknown {
  if (!(error instanceof ApiError)) return undefined;
  try {
    return (JSON.parse(error.body) as { detail?: unknown }).detail;
  } catch {
    return undefined;
  }
}

/**
 * Traduce el fallo del guardado a una frase accionable.
 *
 * El caso que importa es el ciclo: el backend devuelve la cadena de ids y aquí
 * se acompaña de los TÍTULOS, porque `t3 → t7 → t3` no le dice nada a quien
 * acaba de escribir «Migrar el esquema» y «Cargar los datos».
 */
export function describeSaveError(error: unknown, drafts: readonly TaskDraft[]): string {
  const detail = parseDetail(error);
  const obj =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as Record<string, unknown>)
      : null;
  const code = obj?.["error"];

  if (code === "dag_cycle") {
    const raw = obj?.["cycle"];
    const cycle = Array.isArray(raw) ? raw.map(String) : [];
    if (cycle.length > 0) {
      const titleById = new Map(drafts.map((d) => [d.id, d.title.trim()]));
      const chain = cycle.map((id) => {
        const title = titleById.get(id);
        return title ? `${id} («${title}»)` : id;
      });
      return (
        `Dependencia circular: ${chain.join(" → ")}. ` +
        "Quita una de esas dependencias para romper el ciclo."
      );
    }
    return "Hay una dependencia circular entre las tareas.";
  }

  if (code === "spec_not_editable") {
    return typeof obj?.["message"] === "string"
      ? (obj["message"] as string)
      : "Este plan ya no admite cambios en su especificación.";
  }

  // 422 de pydantic: `detail` es la lista de errores. Su `msg` ya es legible
  // («tasks[t2] depends on unknown task 't9'»); el envoltorio JSON no.
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object"
          ? String((item as Record<string, unknown>)["msg"] ?? "")
          : "",
      )
      .filter(Boolean);
    if (messages.length > 0) return messages.join(" · ");
  }

  if (typeof detail === "string") return detail;
  if (error instanceof ApiError) return error.body;
  return String(error);
}
