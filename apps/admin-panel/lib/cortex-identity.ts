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
import { translate } from "@/lib/i18n";

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
  /** "Lo que sabe de ti": el owner-model que deriva la reflexión (solo-lectura). */
  relationship_model: Record<string, string>;
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

/**
 * POST /owner/cortex/identity/onboarding — el córtex se propone a sí mismo.
 *
 * Dos pasos sobre la MISMA ruta: sin `confirm` corre un turno y propone (no
 * persiste nada, `onboarded_at` sigue nulo); con `confirm` guarda lo que el
 * owner acepte, posiblemente editado.
 */
export interface CortexOnboardingResult {
  /** Ya estaba onboardado: este POST no gastó turno ni reescribió nada. */
  already_onboarded: boolean;
  /** Este POST persistió la identidad. */
  applied: boolean;
  /** El turno literal del córtex proponiéndose. Vacío fuera del paso de propuesta. */
  proposal: string;
  /** El CANDIDATO en el paso de propuesta; el vigente tras confirmar. */
  identity: CortexIdentity;
  /** Lo que cambiaría (o cambió), en el formato `{campo:{before,after}}`. */
  diff: IdentityDiff;
  /** Aviso honesto bilingüe: la pantalla rotula el del idioma activo. */
  honesty: { note_es: string; note_en: string };
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

/**
 * Paso 1: pide al córtex que se proponga nombre, valores y narrativa.
 *
 * No persiste nada. El cuerpo va vacío a propósito —`confirm` es `false` por
 * defecto en el schema— para que la propuesta no pueda escribir por accidente.
 */
export function proposeCortexOnboarding(): Promise<CortexOnboardingResult> {
  return cortexFetch<CortexOnboardingResult>("/identity/onboarding", {
    method: "POST",
    body: {},
  });
}

/**
 * Paso 2: el owner acepta la propuesta (posiblemente editada) y ESO se guarda.
 *
 * Los campos que no se envíen conservan su valor actual, así que aceptar la
 * propuesta tal cual es mandar exactamente lo que el paso 1 devolvió.
 */
export function confirmCortexOnboarding(
  body: CortexIdentityUpdate,
): Promise<CortexOnboardingResult> {
  return cortexFetch<CortexOnboardingResult>("/identity/onboarding", {
    method: "POST",
    body: { ...body, confirm: true },
  });
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

// ---------------------------------------------------------------------------
// Timeline de versiones (F3.6) — `GET /owner/cortex/identity/history?limit=`
// ---------------------------------------------------------------------------

/**
 * Una versión del histórico (`cortex_identity_history`).
 *
 * Contrato del endpoint que está construyendo el carril de backend: versiones
 * más reciente primero, cada una con su `diff` `{campo: {before, after}}` (el que
 * persiste `cortex/identity.py::compute_diff`), quién la escribió y por qué.
 */
export interface CortexIdentityVersion {
  version: number;
  created_at: string;
  /** `reflection` | `owner_override` | … (quién movió la identidad). */
  updated_by?: string | null;
  /** Motivo textual de la reflexión, si lo dejó. */
  reason: string | null;
  /** `{campo: {before, after}}` — sólo los campos que cambiaron. */
  diff: IdentityDiff;
}

/** El `diff` tal cual viaja en el JSONB: por campo, el antes y el después. */
export type IdentityDiff = Record<string, { before?: unknown; after?: unknown } | null | undefined>;

/**
 * El timeline de versiones de la identidad (más reciente primero).
 *
 * OJO: endpoint EN CONSTRUCCIÓN por el carril de backend (la auditoría del
 * 2026-07-27 lo marcó como el bloqueante de esta UI). El componente que lo
 * consume trata el 404 como "todavía no disponible" en vez de como un error del
 * owner.
 */
export function getCortexIdentityHistory(limit = 20): Promise<CortexIdentityVersion[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  return cortexFetch<CortexIdentityVersion[]>(`/identity/history?${params.toString()}`);
}

/** Los dos idiomas soportados (CLAUDE.md §12). */
export type CortexLang = "es" | "en";

/** Etiqueta de versión, sin depender del locale del navegador. */
export function identityVersionLabel(version: number, lang: CortexLang = "es"): string {
  return translate(lang, "cortexIdentity", "versionLabel", { n: version });
}

/**
 * Orden CANÓNICO de los campos en el resumen del diff.
 *
 * Importa: las claves del JSONB llegan en el orden que quiera el backend
 * (`compute_diff` recorre una unión de sets, que en Python no está ordenada), así
 * que sin este orden fijo la misma versión se leería distinta en cada refresco.
 */
const DIFF_FIELD_ORDER = [
  "name",
  "core_values",
  "narrative",
  "language",
  "learning_goals",
  "traits",
  "mood_baseline",
  "relationship_model",
] as const;

const DIFF_FIELD_LABELS: Record<string, Record<CortexLang, string>> = {
  name: { es: "nombre", en: "name" },
  core_values: { es: "valores", en: "core values" },
  narrative: { es: "narrativa", en: "narrative" },
  language: { es: "idioma", en: "language" },
  learning_goals: { es: "objetivos", en: "learning goals" },
  traits: { es: "rasgos", en: "traits" },
  mood_baseline: { es: "ánimo base", en: "mood baseline" },
  relationship_model: { es: "lo que sabe de ti", en: "owner model" },
};

function fieldLabel(field: string, lang: CortexLang): string {
  return DIFF_FIELD_LABELS[field]?.[lang] ?? field;
}

/** Cuántas claves difieren entre dos objetos planos (para rasgos/baseline). */
function changedKeyCount(before: unknown, after: unknown): number {
  const a = isPlainObject(before) ? before : {};
  const b = isPlainObject(after) ? after : {};
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  let changed = 0;
  for (const key of keys) {
    if (a[key] !== b[key]) changed += 1;
  }
  return changed;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function shortValue(value: unknown, lang: CortexLang): string {
  if (value === null || value === undefined || value === "") {
    return translate(lang, "cortexIdentity", "unset");
  }
  const text = String(value);
  return text.length > 40 ? `${text.slice(0, 39)}…` : text;
}

function describeField(
  field: string,
  change: { before?: unknown; after?: unknown },
  lang: CortexLang,
): string {
  const label = fieldLabel(field, lang);
  const { before, after } = change;

  // Listas: su tamaño cuenta la historia (3 valores → 4) mejor que su contenido.
  if (Array.isArray(before) || Array.isArray(after)) {
    const n = (v: unknown) => (Array.isArray(v) ? v.length : 0);
    return `${label}: ${n(before)} → ${n(after)}`;
  }
  // Objetos numéricos (rasgos, baseline): cuántas dimensiones se movieron.
  if (isPlainObject(before) || isPlainObject(after)) {
    const count = changedKeyCount(before, after);
    return translate(lang, "cortexIdentity", count === 1 ? "changesOne" : "changesMany", {
      label,
      n: count,
    });
  }
  // Texto largo: no se vuelca en el timeline, se dice que se reescribió.
  const asText = typeof after === "string" ? after : "";
  if (field === "narrative" || asText.length > 40) {
    return translate(lang, "cortexIdentity", "rewritten", { label });
  }
  return `${label}: ${shortValue(before, lang)} → ${shortValue(after, lang)}`;
}

/**
 * Resumen legible de un `diff` de identidad, para el timeline de versiones.
 *
 * Un solo renglón, en orden estable, sin claves crudas ni JSON volcado:
 * «nombre: sin definir → Atlas · rasgos: 2 ajustes». Un diff vacío devuelve
 * "sin cambios" (una versión sin cambios observables existe: la reflexión puede
 * reescribir el estado con el mismo contenido) — un resumen en blanco parecería
 * un fallo de render.
 *
 * Defensivo a propósito: el `diff` viene de un JSONB y puede traer `null` o un
 * escalar donde se esperaba `{before, after}`; el timeline no puede caerse por eso.
 */
export function identityDiffSummary(diff: IdentityDiff, lang: CortexLang = "es"): string {
  const fields = Object.keys(diff ?? {});
  const ordered = [
    ...DIFF_FIELD_ORDER.filter((f) => fields.includes(f)),
    ...fields.filter((f) => !(DIFF_FIELD_ORDER as readonly string[]).includes(f)).sort(),
  ];
  const parts: string[] = [];
  for (const field of ordered) {
    const change = diff[field];
    if (!isPlainObject(change)) {
      // Entrada sucia: al menos que el campo aparezca (un cambio real que no se
      // ve en el timeline es peor que un nombre técnico).
      parts.push(fieldLabel(field, lang));
      continue;
    }
    parts.push(describeField(field, change, lang));
  }
  if (parts.length === 0) return translate(lang, "cortexIdentity", "noChanges");
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Radar Big-Five (F3.6) — geometría pura
// ---------------------------------------------------------------------------

/** Un eje del radar: su extremo (rejilla) y el vértice del valor. */
export interface RadarAxis {
  key: keyof CortexTraits;
  /** Etiqueta ES del rasgo (el radar también se lee como texto). */
  label: string;
  /** Valor clampado a [0,1]. */
  value: number;
  /** Vértice del polígono (posición del valor). */
  x: number;
  y: number;
  /** Extremo del eje (valor 1) — la rejilla, que NO se mueve con el dato. */
  axisX: number;
  axisY: number;
}

/** Orden canónico de las cinco dimensiones (el mismo que el backend). */
const TRAIT_ORDER: (keyof CortexTraits)[] = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
];

/**
 * Geometría del radar Big-Five: cinco ejes repartidos a 72°, el PRIMERO
 * apuntando arriba (12 en punto) para que el gráfico se lea igual siempre.
 *
 * Un rasgo a 0 cae en el centro (no en el borde) y a 1 en el extremo del eje;
 * los valores sucios se clampan, así que el polígono nunca se sale de la rejilla.
 */
export function traitRadarAxes(
  traits: CortexTraits,
  opts: { cx?: number; cy?: number; radius?: number } = {},
): RadarAxis[] {
  const cx = opts.cx ?? 50;
  const cy = opts.cy ?? 50;
  const radius = opts.radius ?? 40;
  return TRAIT_ORDER.map((key, i) => {
    // -90° = arriba; en SVG el ángulo crece en sentido horario visualmente.
    const angle = (-90 + i * (360 / TRAIT_ORDER.length)) * (Math.PI / 180);
    const raw = traits[key];
    const value = Math.min(1, Math.max(0, Number.isFinite(raw) ? raw : 0));
    return {
      key,
      label: TRAIT_LABELS_ES[key],
      value,
      x: cx + Math.cos(angle) * radius * value,
      y: cy + Math.sin(angle) * radius * value,
      axisX: cx + Math.cos(angle) * radius,
      axisY: cy + Math.sin(angle) * radius,
    };
  });
}

/** Serializa los vértices al atributo `points` de un `<polygon>` SVG. */
export function radarPolygon(axes: readonly RadarAxis[]): string {
  return axes.map((a) => `${round2(a.x)},${round2(a.y)}`).join(" ");
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
