"use client";

/**
 * Espacio PAD 2D con estela — Panel de Mente (Córtex F2, ADR 0075).
 *
 * La línea de mood del panel enseña UNA dimensión en el tiempo; esto enseña las
 * DOS que definen el cuadrante emocional a la vez, y por dónde ha pasado el
 * córtex:
 *
 *   - eje X = **valencia** ∈ [-1,1] (desagradable ← → agradable),
 *   - eje Y = **activación** ∈ [0,1], con el 1 ARRIBA (apagado abajo),
 *   - la **estela** son los snapshots de `/affect/timeseries`, desvaneciéndose
 *     hacia el pasado, y la **cabeza** es el estado VIVO (el frame del WS mueve
 *     el punto sin esperar al polling),
 *   - el color de cada punto es el de su cuadrante de `mood_label`.
 *
 * Toda la aritmética es pura y vive en `lib/cortex-affect.ts` (proyección,
 * estela, color); aquí sólo se pinta — así los invariantes (clamp, orientación
 * de los ejes, desvanecido monótono) se prueban sin montar el SVG.
 *
 * Honestidad de producto OBLIGATORIA (ADR 0075 §6): es una simulación
 * computacional determinista, NO emociones reales. El rótulo va DENTRO de esta
 * superficie y no se puede quitar sin romper su test.
 *
 * Idioma: ES+EN por tabla local (CLAUDE.md §12). El admin-panel NO tiene capa
 * i18n todavía —la crea el plan prod-16— y el resto del Panel de Mente sigue con
 * rótulos ES fijos: esa deuda es previa y sigue abierta. Esta superficie, que es
 * nueva, no la agranda. Las etiquetas de mood ya llegan traducidas del backend.
 */

import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import {
  DEFAULT_PAD_BOX,
  moodLabelColor,
  padToCanvasXY,
  trailFromSnapshots,
  trailPolyline,
  type AffectSnapshotLike,
} from "@/lib/cortex-affect";
import { type CortexLang } from "@/lib/cortex-curiosity";
import { useLangOptional } from "@/lib/lang-context";

const COPY: Record<CortexLang, Record<string, string>> = {
  es: {
    title: "Espacio PAD (valencia × activación) con estela",
    simulated: "simulado",
    error: "No se pudo cargar la serie afectiva; el espacio PAD se queda sin estela.",
    empty:
      "Aún no hay estado afectivo que situar. Conversa con el córtex: cada turno deja un punto en este espacio.",
    arousalHigh: "Activación alta",
    arousalLow: "Activación baja",
    valenceLow: "Valencia −",
    valenceHigh: "Valencia +",
    svgLabel:
      "Espacio PAD: valencia en el eje horizontal y activación en el vertical, con la estela de los últimos estados",
    honesty:
      "Modelo computacional de afecto: la posición y la estela salen de un motor determinista, no son emociones reales ni consciencia.",
  },
  en: {
    title: "PAD space (valence × arousal) with trail",
    simulated: "simulated",
    error: "Could not load the affect series; the PAD space has no trail.",
    empty:
      "No affective state to place yet. Talk to the cortex: every turn leaves a dot in this space.",
    arousalHigh: "High arousal",
    arousalLow: "Low arousal",
    valenceLow: "Valence −",
    valenceHigh: "Valence +",
    svgLabel:
      "PAD space: valence on the horizontal axis and arousal on the vertical one, with the trail of the latest states",
    honesty:
      "Computational model of affect: position and trail come from a deterministic engine; these are not real emotions nor consciousness.",
  },
};

/** El estado afectivo vivo (subconjunto de `CortexMind`). */
export interface LivePadState {
  valence: number;
  arousal: number;
  mood_label: string;
}

export function MindPadSpace({
  current,
  snapshots,
  isLoading = false,
  isError = false,
}: {
  /** Estado vivo (último `/mind` o frame del WS). `null` mientras no hay. */
  current: LivePadState | null;
  /** Serie histórica en orden cronológico ASC (como la devuelve el endpoint). */
  snapshots: readonly AffectSnapshotLike[];
  isLoading?: boolean;
  isError?: boolean;
}) {
  const copy = COPY[useLangOptional()];
  const series: AffectSnapshotLike[] = current
    ? [
        ...snapshots,
        { valence: current.valence, arousal: current.arousal, mood_label: current.mood_label },
      ]
    : [...snapshots];
  const trail = trailFromSnapshots(series);
  const headPoint = trail.length > 0 ? trail[trail.length - 1] : null;
  const { width, height } = DEFAULT_PAD_BOX;
  const center = padToCanvasXY(0, 0.5);

  return (
    <Card data-testid="cortex-pad-space">
      <CardContent className="pt-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-muted-foreground text-xs uppercase tracking-wider">{copy.title}</p>
          {headPoint && current ? (
            <span
              className="inline-flex items-center gap-1.5 text-xs"
              data-testid="cortex-pad-current"
            >
              <span
                aria-hidden="true"
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: moodLabelColor(current.mood_label) }}
              />
              <span className="text-foreground font-medium">{current.mood_label || "—"}</span>
              <span className="text-muted-foreground">· {copy.simulated}</span>
            </span>
          ) : null}
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-10">
            <Spinner />
          </div>
        ) : isError ? (
          <p className="text-destructive mt-3 text-sm" data-testid="cortex-pad-error">
            {copy.error}
          </p>
        ) : trail.length === 0 || !headPoint ? (
          <p className="text-muted-foreground mt-3 text-sm" data-testid="cortex-pad-empty">
            {copy.empty}
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-[auto_1fr] gap-x-2">
            {/* Rótulos del eje Y (activación): arriba alta, abajo baja. */}
            <div className="text-muted-foreground flex flex-col justify-between py-1 text-[10px] leading-none">
              <span data-testid="cortex-pad-axis-arousal-high">{copy.arousalHigh}</span>
              <span data-testid="cortex-pad-axis-arousal-low">{copy.arousalLow}</span>
            </div>
            <svg
              data-testid="cortex-pad-canvas"
              viewBox={`0 0 ${width} ${height}`}
              className="h-56 w-full"
              role="img"
              aria-label={copy.svgLabel}
              preserveAspectRatio="xMidYMid meet"
            >
              {/* Cruz de cuadrantes: valencia 0 (vertical) y activación media. */}
              <line
                x1={center.x}
                y1={0}
                x2={center.x}
                y2={height}
                className="text-border"
                stroke="currentColor"
                strokeWidth={0.6}
                strokeDasharray="2 2"
              />
              <line
                x1={0}
                y1={center.y}
                x2={width}
                y2={center.y}
                className="text-border"
                stroke="currentColor"
                strokeWidth={0.6}
                strokeDasharray="2 2"
              />
              {/* La estela: la línea recorrida + un punto por snapshot. */}
              <polyline
                data-testid="cortex-pad-trail"
                points={trailPolyline(trail)}
                fill="none"
                className="text-muted-foreground"
                stroke="currentColor"
                strokeWidth={0.5}
                strokeOpacity={0.5}
              />
              {trail.slice(0, -1).map((p, i) => (
                <circle
                  key={`${p.createdAt}-${i}`}
                  cx={p.x}
                  cy={p.y}
                  r={p.radius}
                  fill={p.color}
                  fillOpacity={p.opacity}
                />
              ))}
              {/* La cabeza: el estado actual, mayor y a plena opacidad. */}
              <circle
                data-testid="cortex-pad-head"
                cx={headPoint.x}
                cy={headPoint.y}
                r={headPoint.radius}
                fill={headPoint.color}
                stroke="currentColor"
                strokeWidth={0.7}
                className="text-background"
              />
            </svg>
            {/* Rótulos del eje X (valencia). */}
            <div />
            <div className="text-muted-foreground flex justify-between text-[10px]">
              <span data-testid="cortex-pad-axis-valence-low">{copy.valenceLow}</span>
              <span data-testid="cortex-pad-axis-valence-high">{copy.valenceHigh}</span>
            </div>
          </div>
        )}

        <p className="text-muted-foreground mt-3 text-[11px]">{copy.honesty}</p>
      </CardContent>
    </Card>
  );
}
