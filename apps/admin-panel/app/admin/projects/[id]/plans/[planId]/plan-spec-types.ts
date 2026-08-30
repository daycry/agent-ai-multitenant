/**
 * Tipos + constantes compartidas del detalle de plan (hallazgo #9, refactor por partes).
 *
 * Espejan la forma del backend (`PlanResponse` + `PlanSpecification`) y los mapas de
 * estado del badge. Extraídos de `page.tsx` (monolito de 1703 líneas) para que las
 * secciones (`*-section.tsx`) los importen sin duplicar la definición ni la clave de
 * caché de react-query. NO es una ruta (nombre ≠ page.tsx dentro de `app/**`).
 */

import type { BadgeVariant } from "@/components/ui/badge";
import { translate } from "@/lib/i18n";
import type { Lang, MessageKey } from "@/lib/i18n";

export interface PlanTaskSpec {
  id: string;
  title: string;
  description?: string;
  complexity?: string;
  role?: string;
  depends_on?: string[];
  estimated_hours?: number;
  /**
   * `unknown[]` y no `string[]` — ADR 0162, opción A.
   *
   * Un criterio es prosa (la inmensa mayoría) **o** un diccionario que DECLARA
   * cómo se comprueba: el par `runtime`+`command`, o un `check_type` explícito.
   * Desde que `_clean_acceptance_criteria` dejó de aplanarlos
   * (`chat/planning_llm.py`), el spec baja con las dos formas mezcladas.
   *
   * Mientras esto dijo `string[]`, los dos consumidores de este tipo mentían y
   * el compilador les daba la razón: la lista de correcciones pintaba el
   * criterio como hijo de React —«Objects are not valid as a React child», la
   * tarjeta entera abajo— y el editor del spec lo serializaba
   * `[object Object]`, borrando la declaración al guardar. Renderízalo siempre
   * con `criterionText()`.
   */
  acceptance_criteria?: unknown[];
  // ADR 0107: las tareas nacidas de un rechazo humano llevan origin=correction.
  origin?: string;
}

// ADR 0107: meta del ciclo de correcciones en specification.corrections.
// Privado del módulo (M-7, auditoría 2026-07-10): ningún fichero lo importa —
// solo se consume vía PlanSpecification.corrections; espejo del shape backend.
interface PlanCorrectionEntry {
  session_id: string;
  reason?: string;
  task_ids?: string[];
  created_at?: string;
  status?: string; // proposed | accepted
  accepted_task_ids?: string[];
}

export interface PlanPhaseSpec {
  /**
   * A-12: el planner del chat emite `title` (`_normalise_phases`), no `name`.
   * La UI leía sólo `name`, así que la lista de fases salía SIN título y el
   * desplegable de «sincronizar una fase» mostraba opciones EN BLANCO — el
   * operador elegía a ciegas. Ambas claves son opcionales y se resuelven con
   * `phaseLabel()`, que además tolera los specs ya persistidos con `name`.
   */
  title?: string;
  name?: string;
  description?: string;
  tasks?: string[];
}

/**
 * Etiqueta visible de una fase, sea cual sea la clave con la que se guardó.
 *
 * `lang` es OBLIGATORIO y sin default (prod-16 `task_prod16_03`): el rótulo de
 * respaldo («Fase 3») es texto de UI, y con un default el próximo llamante
 * reintroduce el castellano fijo sin enterarse. Es la misma decisión que se
 * tomó en `conversationLabel()`.
 */
export function phaseLabel(phase: PlanPhaseSpec, index: number, lang: Lang): string {
  return (
    (phase.title || phase.name || "").trim() ||
    translate(lang, "planDetail", "phaseFallback", { n: index + 1 })
  );
}

export interface PlanSpecification {
  summary?: {
    title?: string;
    description?: string;
    scope_in?: string[];
    scope_out?: string[];
    decisions?: string[];
    risks?: Array<{ name: string; mitigation?: string } | string>;
  };
  phases?: PlanPhaseSpec[];
  tasks?: PlanTaskSpec[];
  estimates?: {
    duration_calendar?: string;
    effort_person_days?: number;
    cost_human_eur?: number | [number, number];
    cost_ai_eur?: number | [number, number];
  };
  metadata?: Record<string, unknown>;
  corrections?: PlanCorrectionEntry[];
}

export interface PlanResponse {
  id: string;
  title: string;
  description: string | null;
  status: string;
  conversation_id: string | null;
  specification: PlanSpecification;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

// Orden por workflow (ver CLAUDE.md §"Estados Válidos del Frontmatter"):
// draft → pending_approval → approved → in_progress → [blocked] →
// pending_human_validation → completed (o rejected / cancelled) → archived.
export const STATUS_VARIANT: Record<string, BadgeVariant> = {
  draft: "muted",
  pending_approval: "warning",
  approved: "success",
  in_progress: "default",
  blocked: "danger",
  pending_human_validation: "warning",
  completed: "success",
  rejected: "danger",
  cancelled: "muted",
  archived: "muted",
};

/**
 * CLAVE del diccionario con la etiqueta de cada estado (prod-16 `task_prod16_03`).
 *
 * Este mapa existía DOS veces, copiado byte a byte aquí y en
 * `plans/page.tsx`. Dos listas del mismo enum del backend divergen en cuanto
 * alguien añade un estado, y traducirlas por separado lo garantizaba: ahora hay
 * una sola y el listado la importa de aquí.
 */
export const STATUS_LABEL: Record<string, MessageKey<"planStatus">> = {
  draft: "draft",
  pending_approval: "pendingApproval",
  approved: "approved",
  in_progress: "inProgress",
  blocked: "blocked",
  pending_human_validation: "pendingHumanValidation",
  completed: "completed",
  rejected: "rejected",
  cancelled: "cancelled",
  archived: "archived",
};

/**
 * Locale de formateo numérico del idioma activo.
 *
 * Vivía como `"es-ES"` cableado en `formatCostRange` y en `formatTokens`, así
 * que con el panel en inglés los importes salían con separadores castellanos.
 * Sale de `common.dateLocale` —el mismo dato que ya usa el otro carril para las
 * fechas— y no de un mapa propio: dos mecanismos para el mismo dato divergen, y
 * el ternario de comparación de idioma lo prohíbe `check-i18n.mjs` con razón.
 */
export function numberLocale(lang: Lang): string {
  return translate(lang, "common", "dateLocale");
}

export function formatCostRange(
  value: number | [number, number] | undefined,
  lang: Lang,
): string | null {
  if (value === undefined) return null;
  const locale = numberLocale(lang);
  if (typeof value === "number") return `${value.toLocaleString(locale)} €`;
  const [min, max] = value;
  return `${min.toLocaleString(locale)} – ${max.toLocaleString(locale)} €`;
}
