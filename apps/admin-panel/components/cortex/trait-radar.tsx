"use client";

/**
 * Radar Big-Five de la identidad del córtex (Córtex F3.6, ADR 0074).
 *
 * Sustituye a las cinco barras horizontales que había: el perfil de rasgos es
 * una FORMA (qué domina, qué se hunde) y eso no se lee en barras sueltas. Los
 * rasgos los deriva la reflexión de forma clampeada y versionada; el owner NO los
 * edita (guardrail de auto-modificación), así que esto es solo-lectura.
 *
 * La geometría es pura y vive en `lib/cortex-identity.ts` (`traitRadarAxes` /
 * `radarPolygon`): aquí sólo se pinta. Además del polígono se listan los valores
 * numéricos — el radar tiene que poder leerse como texto (accesibilidad y, de
 * paso, es lo que hace verificable que el dato llegó).
 *
 * Honestidad de producto: es un modelo computacional de identidad, no un "yo"
 * real. El copy honesto lo pone la página que lo monta.
 */

import { radarPolygon, traitRadarAxes, type CortexTraits } from "@/lib/cortex-identity";

// Geometría por defecto de `traitRadarAxes` (centro 50,50 y radio 40 en el
// viewBox 100×100): se deja explícita, pero DEBE coincidir con la de la función
// pura — su test compara el `points` renderizado con el que ella calcula.
const CX = 50;
const CY = 50;
const RADIUS = 40;
/** Anillos de la rejilla (25 %, 50 %, 75 %, 100 %). */
const RINGS = [0.25, 0.5, 0.75, 1];

export function TraitRadar({ traits }: { traits: CortexTraits }) {
  const axes = traitRadarAxes(traits, { cx: CX, cy: CY, radius: RADIUS });

  return (
    <div
      className="flex flex-col gap-3 sm:flex-row sm:items-center"
      data-testid="cortex-trait-radar"
    >
      <svg
        viewBox="0 0 100 100"
        className="text-primary h-48 w-48 shrink-0"
        role="img"
        aria-label={`Radar de rasgos Big-Five: ${axes
          .map((a) => `${a.label} ${a.value.toFixed(2)}`)
          .join(", ")}`}
      >
        {/* Rejilla: anillos concéntricos + un radio por eje. */}
        {RINGS.map((r) => (
          <polygon
            key={r}
            points={radarPolygon(
              axes.map((a) => ({ ...a, x: CX + (a.axisX - CX) * r, y: CY + (a.axisY - CY) * r })),
            )}
            fill="none"
            className="text-border"
            stroke="currentColor"
            strokeWidth={0.4}
          />
        ))}
        {axes.map((a) => (
          <line
            key={a.key}
            x1={CX}
            y1={CY}
            x2={a.axisX}
            y2={a.axisY}
            className="text-border"
            stroke="currentColor"
            strokeWidth={0.4}
          />
        ))}
        {/* El perfil. */}
        <polygon
          data-testid="cortex-trait-polygon"
          points={radarPolygon(axes)}
          className="fill-primary/20 text-primary"
          stroke="currentColor"
          strokeWidth={1}
        />
        {axes.map((a) => (
          <circle key={a.key} cx={a.x} cy={a.y} r={1.4} fill="currentColor" />
        ))}
      </svg>
      <ul className="flex min-w-0 flex-1 flex-col gap-1.5">
        {axes.map((a) => (
          <li key={a.key} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="text-muted-foreground truncate">{a.label}</span>
            <span
              className="tabular-nums"
              data-testid={`cortex-trait-value-${a.key}`}
              title={`${a.label}: ${a.value.toFixed(2)}`}
            >
              {a.value.toFixed(2)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
