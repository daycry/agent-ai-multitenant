/**
 * Tipos + constantes compartidas del detalle de plan (hallazgo #9, refactor por partes).
 *
 * Espejan la forma del backend (`PlanResponse` + `PlanSpecification`) y los mapas de
 * estado del badge. Extraídos de `page.tsx` (monolito de 1703 líneas) para que las
 * secciones (`*-section.tsx`) los importen sin duplicar la definición ni la clave de
 * caché de react-query. NO es una ruta (nombre ≠ page.tsx dentro de `app/**`).
 */

import type { BadgeVariant } from "@/components/ui/badge";

export interface PlanTaskSpec {
  id: string;
  title: string;
  description?: string;
  complexity?: string;
  role?: string;
  depends_on?: string[];
  estimated_hours?: number;
  acceptance_criteria?: string[];
  // ADR 0107: las tareas nacidas de un rechazo humano llevan origin=correction.
  origin?: string;
}

// ADR 0107: meta del ciclo de correcciones en specification.corrections.
export interface PlanCorrectionEntry {
  session_id: string;
  reason?: string;
  task_ids?: string[];
  created_at?: string;
  status?: string; // proposed | accepted
  accepted_task_ids?: string[];
}

export interface PlanPhaseSpec {
  name: string;
  description?: string;
  tasks?: string[];
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

export const STATUS_LABEL: Record<string, string> = {
  draft: "Borrador",
  pending_approval: "Pendiente de aprobación",
  approved: "Aprobado",
  in_progress: "En progreso",
  blocked: "Bloqueado",
  pending_human_validation: "Pendiente validación humana",
  completed: "Completado",
  rejected: "Rechazado",
  cancelled: "Cancelado",
  archived: "Archivado",
};

export function formatCostRange(value: number | [number, number] | undefined): string | null {
  if (value === undefined) return null;
  if (typeof value === "number") return `${value.toLocaleString("es-ES")} €`;
  const [min, max] = value;
  return `${min.toLocaleString("es-ES")} – ${max.toLocaleString("es-ES")} €`;
}
