/**
 * Córtex F3 (bloque 2) — tipos + helpers de la identidad evolutiva del córtex.
 *
 * La identidad es un SINGLETON del System Owner (gated por `require_system_owner`
 * en el backend — 403 si no eres owner, DB-authoritative, ADR 0074). El owner
 * co-diseña `name`/`core_values`/`narrative`/`language` y fija `learning_goals`;
 * los rasgos Big-Five, el `mood_baseline` y el modelo del owner los DERIVA la
 * reflexión periódica de forma clampeada y versionada — el owner NO los pisa a
 * mano (guardrail de auto-modificación, ADR 0074).
 *
 * Honestidad de producto: la identidad es un MODELO COMPUTACIONAL que evoluciona,
 * NO consciencia ni un "yo" real. El copy de la UI no insinúa lo contrario.
 */

import { cortexFetch } from "@/lib/cortex";

// ---------------------------------------------------------------------------
// Contrato de endpoints (routers/cortex_mind.py + schemas/cortex_identity.py)
// ---------------------------------------------------------------------------

/** Rasgos Big-Five ∈ [0,1] (derivados por la reflexión; solo-lectura). */
export interface CortexTraits {
  openness: number;
  conscientiousness: number;
  extraversion: number;
  agreeableness: number;
  neuroticism: number;
}

/** Set-point PAD del mood (derivado por la reflexión; solo-lectura). */
export interface CortexBaseline {
  valence: number;
  arousal: number;
  dominance: number;
}

/** GET /owner/cortex/identity — la identidad actual del córtex del owner. */
export interface CortexIdentity {
  name: string | null;
  core_values: string[];
  narrative: string;
  language: string;
  learning_goals: string[];
  /** Derivados por la reflexión (no editables por el owner). */
  traits: CortexTraits;
  mood_baseline: CortexBaseline;
  version: number;
  updated_by: string;
  /** NULL ⇒ onboarding pendiente (la UI lo destaca). */
  onboarded_at: string | null;
}

/** PUT /owner/cortex/identity — onboarding / override del owner (campos editables). */
export interface CortexIdentityUpdate {
  name?: string | null;
  core_values?: string[] | null;
  narrative?: string | null;
  language?: string | null;
  learning_goals?: string[] | null;
}

/** POST /owner/cortex/reflect — disparo manual de la reflexión. */
export interface CortexReflectResult {
  enqueued: boolean;
}

// ---------------------------------------------------------------------------
// Límites de campo — espejo del schema Pydantic (schemas/cortex_identity.py).
// ---------------------------------------------------------------------------
export const CORTEX_IDENTITY_LIMITS = {
  name: { max: 120 },
  narrative: { max: 8000 },
  core_values: { max: 20 },
  learning_goals: { max: 20 },
} as const;

/** Los idiomas soportados (ES + EN únicamente — Principio rector 12). */
export const CORTEX_LANGUAGES = ["es", "en"] as const;

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

/** La identidad actual del córtex (System Owner). */
export function getCortexIdentity(): Promise<CortexIdentity> {
  return cortexFetch<CortexIdentity>("/identity");
}

/** Onboarding / override del owner (validado server-side; 422 si toca derivados). */
export function updateCortexIdentity(body: CortexIdentityUpdate): Promise<CortexIdentity> {
  return cortexFetch<CortexIdentity>("/identity", { method: "PUT", body });
}

/** Dispara una pasada de reflexión bajo demanda (best-effort). */
export function reflectCortexIdentity(): Promise<CortexReflectResult> {
  return cortexFetch<CortexReflectResult>("/reflect", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Helpers puros — testeables sin React ni red.
// ---------------------------------------------------------------------------

/** ¿Falta el onboarding co-diseñado? (`onboarded_at` NULL). */
export function needsOnboarding(identity: Pick<CortexIdentity, "onboarded_at">): boolean {
  return identity.onboarded_at === null;
}

/** Parsea un textarea de "una por línea" a una lista limpia (sin vacíos, recortada). */
export function parseLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/** El inverso: una lista a un bloque de texto "una por línea" para el textarea. */
export function joinLines(values: readonly string[]): string {
  return values.join("\n");
}

/**
 * Resumen legible de un trait Big-Five (etiqueta ES) — para mostrar el radar como
 * lista accesible. Pura → testeable.
 */
export const TRAIT_LABELS_ES: Record<keyof CortexTraits, string> = {
  openness: "Apertura",
  conscientiousness: "Responsabilidad",
  extraversion: "Extraversión",
  agreeableness: "Amabilidad",
  neuroticism: "Neuroticismo",
};

/** Un valor ∈ [0,1] → porcentaje [0,100] (clampado) para la barra del trait. */
export function traitToPercent(value: number): number {
  return Math.min(100, Math.max(0, value * 100));
}
