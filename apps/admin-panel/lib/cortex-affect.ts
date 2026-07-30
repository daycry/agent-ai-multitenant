/**
 * Córtex F2 (FASE H) — helpers PUROS del espacio PAD del Panel de Mente.
 *
 * Toda la aritmética del "espacio PAD 2D con estela" vive aquí, fuera de React:
 * proyección de un estado PAD al lienzo, construcción de la estela a partir de
 * los snapshots de `GET /owner/cortex/affect/timeseries` y color por `mood_label`.
 * El componente (`components/cortex/mind-pad-space.tsx`) sólo pinta.
 *
 * Honestidad de producto (ADR 0075 §6): lo que estos helpers colocan en el
 * lienzo es una SIMULACIÓN computacional determinista de afecto, no emociones ni
 * consciencia. El copy honesto lo pone la superficie que los usa.
 *
 * Nota de idioma (deuda conocida): las etiquetas de mood llegan YA traducidas
 * del backend (`derive_mood_label` es bilingüe), así que este módulo no traduce
 * nada — sólo reconoce las dos variantes del catálogo cerrado. El admin-panel no
 * tiene aún capa i18n (la crea el plan prod-16); los rótulos ES fijos de las
 * superficies siguen siendo deuda, y este módulo la evita no emitiendo texto.
 */

// ---------------------------------------------------------------------------
// moodLabelColor — color por cuadrante del mood
// ---------------------------------------------------------------------------

/** Color de los estados neutros y de cualquier etiqueta desconocida. */
export const MOOD_NEUTRAL_COLOR = "#64748b"; // slate-500

/**
 * Catálogo CERRADO de etiquetas de mood, bilingüe, espejo de
 * `apps/api-server/.../cortex/affective.py::derive_mood_label`:
 * cuadrante (valencia ±, activación alta/baja) → etiqueta ES|EN.
 * Las claves están normalizadas (minúsculas, sin diacríticos).
 */
const MOOD_COLORS: Record<string, string> = {
  // valencia +, activación alta
  alegria: "#16a34a", // emerald-600
  joy: "#16a34a",
  // valencia +, activación baja
  calma: "#0891b2", // cyan-600
  calm: "#0891b2",
  // valencia −, activación alta
  tension: "#dc2626", // red-600
  // valencia −, activación baja
  abatimiento: "#6366f1", // indigo-500
  down: "#6366f1",
  // zona neutra central
  neutral: MOOD_NEUTRAL_COLOR,
};

/** minúsculas + sin diacríticos + sin espacios sobrantes. */
function normalizeLabel(label: string): string {
  return label.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); // marcas diacríticas combinantes
}

/**
 * Color del punto/etiqueta de un `mood_label`. Acepta las variantes ES y EN del
 * catálogo cerrado (el mismo cuadrante da el MISMO color en los dos idiomas) y
 * cae al neutro ante cualquier etiqueta que no reconozca — un mood nuevo del
 * backend nunca deja un punto invisible.
 */
export function moodLabelColor(label: string): string {
  return MOOD_COLORS[normalizeLabel(label)] ?? MOOD_NEUTRAL_COLOR;
}

// ---------------------------------------------------------------------------
// padToCanvasXY — proyección PAD → lienzo 2D
// ---------------------------------------------------------------------------

/** El lienzo destino (unidades del `viewBox`, no píxeles). */
export interface CanvasBox {
  width: number;
  height: number;
  /** Margen interior para que los puntos no se peguen al borde. Por defecto 0. */
  padding?: number;
}

/** Lienzo por defecto: un cuadrado unidad de 100×100 con 6 de margen. */
export const DEFAULT_PAD_BOX: CanvasBox = { width: 100, height: 100, padding: 6 };

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

/** Un número finito, o `fallback` si llega NaN/Infinity (frame sucio de la red). */
function finite(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

/**
 * Proyecta un estado afectivo al lienzo 2D del espacio PAD:
 *
 *   - **X = valencia** ∈ [-1,1]: −1 a la izquierda (desagradable), +1 a la
 *     derecha (agradable).
 *   - **Y = activación** ∈ [0,1] INVERTIDA: 1 arriba (activado), 0 abajo
 *     (apagado) — en SVG/canvas la Y crece hacia abajo.
 *
 * Clampa fuera de rango y tolera NaN (cae al centro, "no sé nada"), de modo que
 * un frame sucio nunca produce un atributo SVG inválido ni un punto fuera de la
 * caja.
 */
export function padToCanvasXY(
  valence: number,
  arousal: number,
  box: CanvasBox = DEFAULT_PAD_BOX,
): { x: number; y: number } {
  const pad = box.padding ?? 0;
  const innerW = Math.max(0, box.width - 2 * pad);
  const innerH = Math.max(0, box.height - 2 * pad);
  const v = clamp(finite(valence, 0), -1, 1);
  const a = clamp(finite(arousal, 0.5), 0, 1);
  return {
    x: pad + ((v + 1) / 2) * innerW,
    y: pad + (1 - a) * innerH,
  };
}

// ---------------------------------------------------------------------------
// trailFromSnapshots — la estela
// ---------------------------------------------------------------------------

/**
 * Lo mínimo que la estela necesita de un snapshot. Es un subconjunto
 * estructural de `CortexAffectPoint` (lib/cortex.ts), así que la serie del
 * endpoint encaja tal cual sin adaptador.
 */
export interface AffectSnapshotLike {
  valence: number;
  arousal: number;
  mood_label?: string;
  created_at?: string;
}

/** Un punto ya listo para pintar en el espacio PAD. */
export interface TrailPoint {
  x: number;
  y: number;
  /** Desvanecido de la estela: el más viejo el más transparente; la cabeza 1. */
  opacity: number;
  /** Radio del punto (la cabeza es mayor: el ojo encuentra el "ahora"). */
  radius: number;
  /** True SÓLO para el último snapshot (el estado actual). */
  isHead: boolean;
  /** Color del cuadrante de su `mood_label`. */
  color: string;
  /** Sello temporal original (para el `<title>` accesible del punto). */
  createdAt: string;
}

/** Cuántos snapshots caben en la estela antes de convertirse en una madeja. */
export const TRAIL_MAX_POINTS = 40;

const TRAIL_MIN_OPACITY = 0.18;
const TRAIL_RADIUS = 1.6;
const HEAD_RADIUS = 3.4;

/**
 * Convierte la serie de snapshots (orden ASC del endpoint: del más viejo al más
 * reciente) en la estela del espacio PAD.
 *
 *   - Conserva los `max` más **RECIENTES** (nunca los primeros: con 500
 *     snapshots la estela mostraría el humor de la semana pasada y el "ahora" no
 *     aparecería).
 *   - El desvanecido es monótono: más viejo ⇒ más transparente, con un suelo
 *     visible.
 *   - El último punto es la CABEZA (opacidad 1, radio mayor).
 */
export function trailFromSnapshots(
  snapshots: readonly AffectSnapshotLike[],
  opts: { box?: CanvasBox; max?: number } = {},
): TrailPoint[] {
  const box = opts.box ?? DEFAULT_PAD_BOX;
  const max = opts.max ?? TRAIL_MAX_POINTS;
  const recent =
    max > 0 && snapshots.length > max ? snapshots.slice(snapshots.length - max) : snapshots;
  const n = recent.length;
  if (n === 0) return [];

  return recent.map((snapshot, i) => {
    const { x, y } = padToCanvasXY(snapshot.valence, snapshot.arousal, box);
    const isHead = i === n - 1;
    // Rampa lineal del suelo a 1 según la posición en la estela.
    const progress = n === 1 ? 1 : i / (n - 1);
    return {
      x,
      y,
      opacity: isHead ? 1 : TRAIL_MIN_OPACITY + progress * (1 - TRAIL_MIN_OPACITY),
      radius: isHead ? HEAD_RADIUS : TRAIL_RADIUS,
      isHead,
      color: moodLabelColor(snapshot.mood_label ?? ""),
      createdAt: snapshot.created_at ?? "",
    };
  });
}

/** Serializa la estela al atributo `points` de un `<polyline>` SVG. */
export function trailPolyline(trail: readonly TrailPoint[]): string {
  return trail.map((p) => `${round(p.x)},${round(p.y)}`).join(" ");
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
