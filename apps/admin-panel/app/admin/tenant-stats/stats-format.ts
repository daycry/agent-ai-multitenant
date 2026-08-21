/**
 * Formateadores del dashboard de estadísticas (extraídos por `task_prod16_08`).
 *
 * Funciones puras, sin React y sin idioma: todas devuelven números, símbolos o
 * el guion largo `—`, que se lee igual en ES y en EN. El texto traducible vive
 * en el namespace `tenantStats` del diccionario, no aquí.
 */

import type { DisplayCurrency, ExecutionRunRow } from "./stats-types";

/** El marcador de "sin dato". Neutro en los dos idiomas. */
export const DASH = "—";

export function fmtWhen(iso: string | null): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/** Una `success_rate` decimal → etiqueta de porcentaje (o `—` si es null). */
export function pct(rate: string | null): string {
  if (rate === null) return DASH;
  const n = Number(rate);
  if (Number.isNaN(n)) return DASH;
  return `${(n * 100).toFixed(1)}%`;
}

export function pctNumber(rate: string | null): number {
  if (rate === null) return 0;
  const n = Number(rate);
  return Number.isNaN(n) ? 0 : Math.round(n * 100);
}

/** Milisegundos → etiqueta legible. */
export function fmtDuration(ms: number | null): string {
  if (ms === null) return DASH;
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function fmtMeanDuration(ms: string | null): string {
  if (ms === null) return DASH;
  const n = Number(ms);
  return Number.isNaN(n) ? DASH : fmtDuration(Math.round(n));
}

export function usd(value: string | null): string {
  return value === null ? DASH : `$${value}`;
}

/**
 * La celda de moneda de visualización: el importe convertido con su código, o
 * un guion cuando la fecha de ese run no tenía tasa (el importe USD sigue
 * siendo el bueno).
 */
export function convertedCost(row: ExecutionRunRow, currency: DisplayCurrency): string {
  if (row.display_cost === null) return DASH;
  return `${row.display_cost} ${currency}`;
}
