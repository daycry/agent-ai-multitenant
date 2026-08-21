/**
 * Córtex F4 (Sub-fase 4.5) — helpers PUROS del panel «Lo que está aprendiendo»
 * (ADR 0078: bucles cognitivos de fondo con budget y kill-switch).
 *
 * Aquí vive todo lo que el panel necesita decidir sin React ni red: cómo se
 * llama cada estado del ciclo de vida de una persecución, cómo se formatea el
 * budget del día y cuál de las dos notas honestas que manda la API toca mostrar.
 *
 * Bilingüe ES+EN (CLAUDE.md §12) por parámetro explícito: el admin-panel no
 * tiene capa i18n todavía (la crea el plan prod-16), así que estos helpers
 * reciben el idioma en vez de leerlo de un contexto global. Cuando llegue la
 * capa i18n, el punto de cambio es UNO: quien los llama.
 *
 * Honestidad de producto (ADR 0075 §6 / 0078): el bucle de curiosidad es un
 * comportamiento programado con topes de coste, NO curiosidad consciente.
 */

import { pickLang, translate } from "@/lib/i18n";

/** Los dos idiomas soportados (CLAUDE.md §12). */
export type CortexLang = "es" | "en";

// ---------------------------------------------------------------------------
// Estados del ciclo de vida de una persecución
// ---------------------------------------------------------------------------

/**
 * Catálogo CERRADO de estados, espejo del CHECK de
 * `cortex_curiosity_pursuits.status` (migración 0095).
 */
export const PURSUIT_STATUSES = [
  "selected",
  "searching",
  "digested",
  "surfaced",
  "skipped",
  "failed",
] as const;

export type PursuitStatus = (typeof PURSUIT_STATUSES)[number];

const PURSUIT_STATUS_LABELS: Record<PursuitStatus, Record<CortexLang, string>> = {
  selected: { es: "elegido", en: "picked" },
  searching: { es: "investigando", en: "researching" },
  // El matiz importa: ya lo aprendió, pero todavía no te lo ha contado.
  digested: { es: "aprendido — pendiente de contarlo", en: "learned — not shared yet" },
  surfaced: { es: "comentado en conversación", en: "mentioned in conversation" },
  skipped: { es: "descartado", en: "skipped" },
  failed: { es: "falló", en: "failed" },
};

function isKnownStatus(status: string): status is PursuitStatus {
  return (PURSUIT_STATUSES as readonly string[]).includes(status);
}

/**
 * Etiqueta legible de un estado del ciclo de vida. Un estado que el frontend no
 * conozca todavía devuelve el SLUG tal cual: es más honesto que un hueco (el
 * hueco parece dato ausente; el slug avisa de que falta traducirlo).
 */
export function pursuitStatusLabel(status: string, lang: CortexLang = "es"): string {
  return isKnownStatus(status) ? PURSUIT_STATUS_LABELS[status][lang] : status;
}

/**
 * ¿Esta persecución está esperando la decisión del owner-approval gate?
 *
 * Sólo cuando sigue en `selected` y `approved` está sin decidir (`null`/ausente).
 * En cuanto el owner decide, los botones desaparecen aunque el bucle no haya
 * movido el estado todavía — ofrecerlos otra vez sería una doble aprobación (y
 * un doble gasto).
 */
export function pursuitAwaitsApproval(pursuit: {
  status: string;
  approved?: boolean | null;
}): boolean {
  return pursuit.status === "selected" && (pursuit.approved ?? null) === null;
}

// ---------------------------------------------------------------------------
// Budget del día (búsquedas consumidas vs cap)
// ---------------------------------------------------------------------------

function nonNegativeInt(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.round(value));
}

/**
 * Fracción [0,1] del budget consumido, para la barra de progreso. Clampada por
 * los dos lados: Redis puede quedar por encima del cap si el cap baja a media
 * jornada, y una barra desbordada se lee como un bug. Sin cap ⇒ 0 (no hay nada
 * que llenar).
 */
export function budgetUsageRatio(used: number, cap: number): number {
  const u = nonNegativeInt(used);
  const c = nonNegativeInt(cap);
  if (c <= 0) return 0;
  return Math.min(1, u / c);
}

/**
 * Texto del budget de curiosidad de hoy: consumido, cap y porcentaje.
 * Sin cap configurado lo dice explícitamente en vez de dividir por cero (y en
 * vez de insinuar "sin límite", que es lo contrario de lo que quiere el owner).
 */
export function budgetUsageLabel(used: number, cap: number, lang: CortexLang = "es"): string {
  const u = nonNegativeInt(used);
  const c = nonNegativeInt(cap);
  if (c <= 0) {
    return translate(lang, "cortexCuriosity", "budgetNoCap", { used: u });
  }
  const pct = Math.round(budgetUsageRatio(u, c) * 100);
  return translate(lang, "cortexCuriosity", "budgetUsage", { used: u, cap: c, pct });
}

// ---------------------------------------------------------------------------
// Copy honesto bilingüe
// ---------------------------------------------------------------------------

/**
 * La nota honesta en el idioma activo. Los endpoints del córtex devuelven
 * SIEMPRE las dos (`note_es` + `note_en`); la UI renderizaba sólo la española,
 * así que el aviso obligatorio del ADR 0075 §6 se perdía en EN.
 *
 * Si falta la del idioma pedido cae a la otra: antes un aviso en el idioma
 * equivocado que ningún aviso. Sin ninguna de las dos devuelve `""` y es el
 * llamante quien pone su texto por defecto.
 */
export function honestNote(
  note: { note_es?: string | null; note_en?: string | null },
  lang: CortexLang,
): string {
  return pickLang(lang, { es: note.note_es ?? "", en: note.note_en ?? "" }).trim();
}

/**
 * El aviso honesto de la tarjeta de AUTONOMÍA, con respaldo garantizado.
 *
 * `honestNote` devuelve `""` cuando el backend no manda ninguna de las dos notas
 * y deja al llamante poner su texto por defecto. Para esta tarjeta ese contrato
 * no vale: el aviso NO es removible (ADR 0075 §6) y la tarjeta enseña
 * precisamente el kill-switch y el dinero gastado, o sea lo que el aviso
 * explica. Un `<p>` vacío deja los controles sin su contexto.
 *
 * Por eso el respaldo vive aquí y no en el componente: así lo cubre el mismo
 * test que el resto de helpers, y el texto sale del diccionario (ES+EN) en vez
 * de ser una cadena fija en castellano dentro del JSX.
 */
export function autonomyHonestNote(
  note: { note_es?: string | null; note_en?: string | null },
  lang: CortexLang,
): string {
  return honestNote(note, lang) || translate(lang, "cortexCuriosity", "autonomyHonestyFallback");
}
