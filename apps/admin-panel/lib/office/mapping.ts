/**
 * Mapeo evento→estado-visual (ADR 0118 La Oficina / ADR 0119 Replay).
 *
 * ÚNICA fuente de semántica visual para ambas superficies: La Oficina lo
 * consume sobre telemetría en vivo y el Replay sobre el `steps_log`
 * histórico. Principio del ADR: cero estados inventados — cada estado
 * visual mapea 1:1 a un dato real de la plataforma (status/abort_code de
 * `executions`, `kind`/`summary` de los steps). Módulo PURO (sin React,
 * sin fetch): testeable en aislado.
 */

export type AgentVisualState =
  "idle" | "working" | "reviewing" | "waiting_human" | "dizzy" | "aborted" | "done";

export interface ExecutionLike {
  id: string;
  status: string;
  abort_code: string | null;
  is_review: boolean;
  project_id: string | null;
  last_step_summary?: string | null;
}

export interface StepLike {
  kind: string;
  summary?: string | null;
}

/** Abort codes que significan «el agente se quedó dando vueltas» (bucles
 * detectados por los safeguards del loop — se pintan como mareo, no como
 * un abort genérico, porque su remedio es distinto: prompt/insumo). */
const LOOP_ABORT_CODES = new Set([
  "repetitive_loop_detected",
  "read_churn_detected",
  "self_review_stalemate",
]);

const WAITING_HUMAN_STATUSES = new Set(["needs_human_review", "awaiting_human_approval"]);

/** Estado visual de un agente a partir de su execution más reciente activa
 * (o null si no tiene ninguna): la fila de `executions` ES la verdad. */
export function agentVisualState(exec: ExecutionLike | null): AgentVisualState {
  if (!exec) return "idle";
  if (WAITING_HUMAN_STATUSES.has(exec.status)) return "waiting_human";
  if (exec.status === "aborted") {
    return LOOP_ABORT_CODES.has(exec.abort_code ?? "") ? "dizzy" : "aborted";
  }
  if (exec.status === "done") return "done";
  if (exec.is_review) return "reviewing";
  return "working";
}

const BUBBLE_MAX = 120;

/** Texto de la burbuja de diálogo: el summary REAL del step (recortado);
 * nunca texto inventado. Sin summary, el kind legible. */
export function stepBubble(step: StepLike): string {
  const text = (step.summary ?? "").trim() || step.kind;
  return text.length > BUBBLE_MAX ? `${text.slice(0, BUBBLE_MAX)}…` : text;
}

export interface StepVisual {
  icon: string;
  label: string;
}

/** Icono + etiqueta estable por `kind` de step (Replay y tooltips de la
 * Oficina). Un kind desconocido degrada al propio kind — el mapeo nunca
 * bloquea la visualización de telemetría nueva. */
const STEP_VISUALS: Record<string, StepVisual> = {
  node: { icon: "🧭", label: "Nodo del grafo" },
  model_call: { icon: "🧠", label: "Llamada al modelo" },
  tool_call: { icon: "🔧", label: "Tool" },
  memory_read: { icon: "📚", label: "Memoria" },
  mcp_wire: { icon: "🔌", label: "Conexión MCP" },
};

export function stepVisual(kind: string): StepVisual {
  return STEP_VISUALS[kind] ?? { icon: "▪️", label: kind };
}
