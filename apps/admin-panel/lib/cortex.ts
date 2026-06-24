/**
 * Córtex F1 (Tarea 12) — tipos + helper de fetch del córtex del System Owner.
 *
 * El córtex es un singleton del dueño del despliegue (`system_owner`): todos
 * los endpoints están gated por `require_system_owner` en el backend (403 si
 * no eres owner — DB-authoritative, ADR 0074). Este módulo solo carga el
 * contrato de los tres endpoints `/owner/cortex/*` (no se inventa ninguno) y
 * un fino wrapper sobre `apiFetch`.
 *
 * Honestidad de producto (riesgo del plan F1): el córtex F1 es una mente
 * SIMULADA con memoria persistente + deliberación; NO tiene afecto ni
 * consciencia (eso llega en F2). El copy de la UI no debe insinuar emociones.
 */

import { apiFetch, type ApiFetchOptions } from "@/lib/api";

// ---------------------------------------------------------------------------
// Contrato de endpoints (routers/cortex.py)
// ---------------------------------------------------------------------------

/** POST /owner/cortex/turns request body. */
export interface CortexTurnRequest {
  message: string;
  /** Si se omite, el backend crea un hilo nuevo y lo devuelve. */
  conversation_id?: string;
}

/** POST /owner/cortex/turns response. */
export interface CortexTurnResponse {
  conversation_id: string;
  answer: string;
  tools_called: string[];
  rounds: number;
  /** Effort efectivo del turno (null/ausente = sin razonamiento profundo). */
  reasoning_effort?: string | null;
  /** True cuando el córtex degradó (sin Claude Agent SDK) a un camino clásico. */
  degraded: boolean;
}

/** Un turno persistido del hilo (GET /owner/cortex/turns). */
export interface CortexTurnItem {
  id: string;
  role: "user" | "cortex";
  content: string;
  created_at: string;
  model_id?: string | null;
}

/** Un hilo del owner (GET /owner/cortex/conversations). */
export interface CortexConversation {
  id: string;
  title?: string | null;
  model_id?: string | null;
  created_at: string;
  updated_at: string;
  last_turn_preview?: string | null;
}

// ---------------------------------------------------------------------------
// Límites de campo — espejo del schema Pydantic (schemas/cortex.py).
// ---------------------------------------------------------------------------
export const CORTEX_LIMITS = {
  message: { min: 1, max: 8000 },
} as const;

// ---------------------------------------------------------------------------
// Fetch helper
// ---------------------------------------------------------------------------

/**
 * Thin wrapper sobre `apiFetch` que prefija el router `/owner/cortex`. Mantiene
 * un solo sitio donde vive el prefijo del córtex (igual que los helpers del
 * asistente fijan sus rutas). `path` se espera relativo al router (con `/`).
 */
export function cortexFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  return apiFetch<T>(`/owner/cortex${path}`, options);
}

/** Lista los hilos del owner (más recientes primero). */
export function getCortexConversations(): Promise<CortexConversation[]> {
  return cortexFetch<CortexConversation[]>("/conversations");
}

/** Carga los turnos de un hilo en orden cronológico. */
export function getCortexTurns(conversationId: string, limit = 100): Promise<CortexTurnItem[]> {
  const params = new URLSearchParams({ conversation_id: conversationId, limit: String(limit) });
  return cortexFetch<CortexTurnItem[]>(`/turns?${params.toString()}`);
}

/** Envía un turno (crea hilo si no se pasa `conversation_id`). */
export function postCortexTurn(body: CortexTurnRequest): Promise<CortexTurnResponse> {
  return cortexFetch<CortexTurnResponse>("/turns", { method: "POST", body });
}

// ---------------------------------------------------------------------------
// Modelo del córtex (cortex.default_model) — config por UI del System Owner.
//
// El córtex es un singleton del owner: su modelo sale SOLO de un platform-default
// propio (`cortex.default_model`), sin override por tenant. Estos endpoints le dan
// al owner un selector en el panel (espejo del modelo por defecto del asistente)
// en vez de tener que tocar `platform_settings` a mano. El BACKEND valida la
// selección contra el catálogo cerrado (proveedor activo + modelo elegible,
// ADR 0021) y hace el gating por `require_system_owner`; la UI solo lo refleja.
// ---------------------------------------------------------------------------

/** Un proveedor activo + los modelos elegibles en él (fuente del desplegable). */
export interface CortexModelOption {
  provider_id: string;
  kind: string;
  /** Handle kebab-case único — desambigua proveedores del mismo kind. */
  slug: string;
  display_name: string;
  models: string[];
}

/** GET /owner/cortex/model-options — proveedores + razonamiento por kind. */
export interface CortexModelOptions {
  providers: CortexModelOption[];
  /** ADR 0070: opciones de razonamiento por kind de proveedor (off + niveles). */
  reasoning_by_kind?: Record<string, string[]>;
}

/** GET/PUT /owner/cortex/model — la selección del córtex (o sin configurar). */
export interface CortexModel {
  provider_id: string | null;
  model_id: string | null;
  /** False cuando la selección guardada ya no resuelve (proveedor/modelo obsoleto). */
  is_valid: boolean;
  provider_display_name: string | null;
  /** ADR 0070: esfuerzo de razonamiento de la selección (null = sin razonar). */
  reasoning_effort: string | null;
}

/** Proveedores activos + sus modelos — la fuente del selector del córtex. */
export function getCortexModelOptions(): Promise<CortexModelOptions> {
  return cortexFetch<CortexModelOptions>("/model-options");
}

/** La selección de modelo del córtex (System Owner). */
export function getCortexModel(): Promise<CortexModel> {
  return cortexFetch<CortexModel>("/model");
}

/** Fija el modelo del córtex (validado server-side; 422 si es inválido). */
export function setCortexModel(
  providerId: string,
  modelId: string,
  reasoningEffort = "off",
): Promise<CortexModel> {
  return cortexFetch<CortexModel>("/model", {
    method: "PUT",
    body: { provider_id: providerId, model_id: modelId, reasoning_effort: reasoningEffort },
  });
}

/** Desconfigura el modelo del córtex (System Owner). */
export function clearCortexModel(): Promise<CortexModel> {
  return cortexFetch<CortexModel>("/model", {
    method: "PUT",
    body: { provider_id: null, model_id: null },
  });
}

// ---------------------------------------------------------------------------
// Helpers puros (etiquetado del hilo) — testeables sin React.
// ---------------------------------------------------------------------------

/**
 * Etiqueta legible de un hilo en el selector. Prefiere el título; si no, cae a
 * un sello con fecha para que los hilos sin título sigan distinguiéndose.
 */
export function cortexConversationLabel(c: CortexConversation): string {
  if (c.title && c.title.trim()) return c.title.trim();
  const d = new Date(c.created_at);
  if (Number.isNaN(d.getTime())) return "Hilo sin título";
  return `Hilo · ${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}
