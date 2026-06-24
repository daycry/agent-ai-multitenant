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
// Panel de Mente (Córtex F2, ADR 0075) — estado afectivo del córtex.
//
// Tres endpoints owner-only (espejo de schemas/cortex_mind.py) + un frame WS
// (`/ws/owner/cortex/telemetry`). El estado es una SIMULACIÓN computacional de
// afecto determinista, NO sentimientos reales: el bloque `honesty` que devuelve
// `/mind` rotula esto y la UI lo muestra siempre (ADR 0075 §6).
//
// Rangos PAD (apps/api-server/.../cortex/affective.py): valence ∈ [-1,1],
// arousal ∈ [0,1], dominance ∈ [-1,1], intensity ∈ [0,1]. Los drives ∈ [0,1].
// ---------------------------------------------------------------------------

/** Los cuatro drives homeostáticos ∈ [0,1] (CortexDrives). */
export interface CortexDrives {
  curiosity: number;
  bonding: number;
  coherence: number;
  competence: number;
}

/** Bloque de honestidad que la UI rotula SIEMPRE (CortexHonesty, ADR 0075 §6). */
export interface CortexHonesty {
  note_es: string;
  note_en: string;
}

/** GET /owner/cortex/mind — el estado afectivo vivo (CortexMindResponse). */
export interface CortexMind {
  valence: number;
  arousal: number;
  dominance: number;
  intensity: number;
  mood_valence: number;
  mood_arousal: number;
  mood_dominance: number;
  mood_label: string;
  drives: CortexDrives;
  honesty: CortexHonesty;
}

/** Un punto de la serie temporal (CortexAffectPoint). */
export interface CortexAffectPoint {
  created_at: string;
  valence: number;
  arousal: number;
  dominance: number;
  intensity: number;
  mood_valence: number;
  mood_arousal: number;
  mood_dominance: number;
  mood_label: string;
  drives: CortexDrives;
}

/** Una memoria episódica emocional del owner (CortexEpisodeItem). */
export interface CortexEpisode {
  id: string;
  content: string;
  created_at: string;
  mood_label: string | null;
  valence: number | null;
  arousal: number | null;
  dominance: number | null;
  intensity: number | null;
  appraisal_reason: string | null;
}

/** GET /owner/cortex/mind — estado afectivo vivo del córtex (System Owner). */
export function getCortexMind(): Promise<CortexMind> {
  return cortexFetch<CortexMind>("/mind");
}

/**
 * GET /owner/cortex/affect/timeseries — snapshots en orden cronológico (ASC).
 * `since`/`until` ISO-8601 acotan por `created_at`; `limit` los más recientes.
 */
export function getCortexAffectTimeseries(
  opts: {
    since?: string;
    until?: string;
    limit?: number;
  } = {},
): Promise<CortexAffectPoint[]> {
  const params = new URLSearchParams();
  if (opts.since) params.set("since", opts.since);
  if (opts.until) params.set("until", opts.until);
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return cortexFetch<CortexAffectPoint[]>(`/affect/timeseries${qs ? `?${qs}` : ""}`);
}

/**
 * GET /owner/cortex/episodes — episódicas emocionales del owner (más recientes
 * primero). `emotion` filtra por `mood_label`; `limit` acota el número.
 */
export function getCortexEpisodes(
  opts: { emotion?: string; limit?: number } = {},
): Promise<CortexEpisode[]> {
  const params = new URLSearchParams();
  if (opts.emotion) params.set("emotion", opts.emotion);
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return cortexFetch<CortexEpisode[]>(`/episodes${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------
// Helpers puros del Panel de Mente — testeables sin React ni red.
// ---------------------------------------------------------------------------

/** El payload del frame WS de telemetría (`type:'affect'`, events.py). */
export interface CortexAffectFramePayload {
  valence: number;
  arousal: number;
  dominance: number;
  intensity: number;
  mood_valence?: number;
  mood_arousal?: number;
  mood_dominance?: number;
  mood_label: string;
  drives: CortexDrives;
  appraisal_reason?: string | null;
  occurred_at?: string;
}

/** El frame WS completo que reenvía `_pump` (routers/ws.py → _to_event). */
export interface CortexAffectFrame {
  type: string;
  occurred_at?: string;
  id?: string;
  payload: CortexAffectFramePayload;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asNumber(value: unknown, fallback = 0): number {
  const n = typeof value === "string" ? Number(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : fallback;
}

/**
 * Normaliza un frame WS crudo (`unknown` de `useWebSocket`) a un `CortexMind`
 * parcial listo para los diales, o `null` si no es un frame de afecto válido.
 * Defensivo: el `payload` viaja como JSON ya parseado por el backend, pero un
 * frame de otro tipo o malformado nunca debe romper los diales.
 */
export function affectFrameToMind(data: unknown): CortexMind | null {
  if (!isRecord(data) || data.type !== "affect" || !isRecord(data.payload)) return null;
  const p = data.payload as Record<string, unknown>;
  const drives = isRecord(p.drives) ? (p.drives as Record<string, unknown>) : {};
  return {
    valence: asNumber(p.valence),
    arousal: asNumber(p.arousal),
    dominance: asNumber(p.dominance),
    intensity: asNumber(p.intensity),
    mood_valence: asNumber(p.mood_valence, asNumber(p.valence)),
    mood_arousal: asNumber(p.mood_arousal, asNumber(p.arousal)),
    mood_dominance: asNumber(p.mood_dominance, asNumber(p.dominance)),
    mood_label: typeof p.mood_label === "string" ? p.mood_label : "",
    drives: {
      curiosity: asNumber(drives.curiosity, 0.5),
      bonding: asNumber(drives.bonding, 0.5),
      coherence: asNumber(drives.coherence, 0.5),
      competence: asNumber(drives.competence, 0.5),
    },
    // Un frame en vivo no recalcula el bloque honesty; conserva el del último
    // /mind (la UI lo muestra desde el estado, no desde el frame).
    honesty: { note_es: "", note_en: "" },
  };
}

/** Las dimensiones PAD y su rango canónico (mismo orden que los diales). */
export type PadDimension = "valence" | "arousal" | "dominance" | "intensity";

export const PAD_RANGES: Record<PadDimension, { min: number; max: number }> = {
  valence: { min: -1, max: 1 },
  arousal: { min: 0, max: 1 },
  dominance: { min: -1, max: 1 },
  intensity: { min: 0, max: 1 },
};

/**
 * Posición [0,100] de un valor PAD dentro de su rango, para el ancho de la
 * barra/dial. Clampa fuera de rango (un valor sucio nunca desborda la barra).
 * Pura → testeable sin render.
 */
export function padToPercent(dimension: PadDimension, value: number): number {
  const { min, max } = PAD_RANGES[dimension];
  if (max === min) return 0;
  const clamped = Math.min(max, Math.max(min, value));
  return ((clamped - min) / (max - min)) * 100;
}

/** Un drive ∈ [0,1] → porcentaje [0,100] para su barra (clampado). */
export function driveToPercent(value: number): number {
  return Math.min(100, Math.max(0, value * 100));
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

// ---------------------------------------------------------------------------
// Voz del córtex (Córtex F5, ADR 0073 voz + 0075 afecto) — frame de afecto + mapeo
// del avatar. El WS de voz (`/ws/owner/cortex/voice`) emite un frame `affect`
// PLANO (NO anidado en `payload` como el de telemetría de `/mind`): los campos van
// al nivel raíz — ver `cortex/voice_turn.py::affect_frame`. El avatar lo mapea a
// color/expresión/sway, rotulando SIEMPRE que es una mente SIMULADA (ADR 0075 §6).
// ---------------------------------------------------------------------------

/**
 * Frame `{type:'affect', valence, arousal, dominance, intensity, mood_label,
 * drives}` del WS de VOZ del córtex (campos al nivel raíz, distinto del frame de
 * telemetría que los anida en `payload`). Rangos PAD: valence/dominance ∈ [-1,1],
 * arousal/intensity ∈ [0,1]; drives ∈ [0,1].
 */
export interface CortexVoiceAffectFrame {
  type: "affect";
  valence: number;
  arousal: number;
  dominance: number;
  intensity?: number;
  mood_label: string;
  drives: CortexDrives;
}

/**
 * Normaliza un frame WS crudo de VOZ (`unknown`) a un `CortexVoiceAffectFrame`, o
 * `null` si no es un frame de afecto plano válido. Defensivo: un frame de otro
 * tipo o malformado nunca debe romper el avatar (cae al estado neutro).
 */
export function parseVoiceAffectFrame(data: unknown): CortexVoiceAffectFrame | null {
  if (!isRecord(data) || data.type !== "affect") return null;
  // El frame de telemetría anida en `payload`; el de voz lleva los campos en raíz.
  // Sólo aceptamos el plano de voz: si trae `payload`, no es nuestro frame.
  if (isRecord(data.payload)) return null;
  const drives = isRecord(data.drives) ? (data.drives as Record<string, unknown>) : {};
  return {
    type: "affect",
    valence: asNumber(data.valence),
    arousal: asNumber(data.arousal),
    dominance: asNumber(data.dominance),
    intensity: asNumber(data.intensity),
    mood_label: typeof data.mood_label === "string" ? data.mood_label : "",
    drives: {
      curiosity: asNumber(drives.curiosity, 0.5),
      bonding: asNumber(drives.bonding, 0.5),
      coherence: asNumber(drives.coherence, 0.5),
      competence: asNumber(drives.competence, 0.5),
    },
  };
}

/** Estilo visual del avatar derivado del afecto (puro → testeable sin render). */
export interface CortexAvatarStyle {
  /** Tono HSL del rostro: rojo frío (valence −1) → verde cálido (valence +1). */
  hue: number;
  /** Saturación [%] del rostro: sube con la activación (arousal). */
  saturation: number;
  /** Intensidad [0,1] del "sway"/energía de la expresión (arousal). */
  intensity: number;
  /** Duración del ciclo de sway en segundos (más activación → más rápido). */
  swayDurationSec: number;
}

/**
 * Mapea un frame de afecto al estilo del avatar (PURO, sin React ni red):
 *
 *   - **Color/tono** sigue la VALENCIA: valence −1 → rojo (hue 0), 0 → ámbar
 *     (hue ~50), +1 → verde (hue ~130). Positivo = cálido/verde, negativo = rojo.
 *   - **Saturación** y **energía/sway** siguen la ACTIVACIÓN (arousal ∈ [0,1]):
 *     más activación → más saturado y un sway más amplio y rápido.
 *
 * Clampa fuera de rango (un valor sucio nunca produce un color/animación inválida).
 */
export function avatarStyleFromAffect(
  affect: Pick<CortexVoiceAffectFrame, "valence" | "arousal">,
): CortexAvatarStyle {
  const valence = Math.min(1, Math.max(-1, affect.valence));
  const arousal = Math.min(1, Math.max(0, affect.arousal));
  // valence [-1,1] → [0,1] → hue [0 (rojo) .. 130 (verde)].
  const hue = ((valence + 1) / 2) * 130;
  // arousal 0 → 45% (apagado), 1 → 90% (vibrante).
  const saturation = 45 + arousal * 45;
  // Sway más rápido (3.4s → 1.8s) cuanto mayor la activación.
  const swayDurationSec = 3.4 - arousal * 1.6;
  return { hue, saturation, intensity: arousal, swayDurationSec };
}
