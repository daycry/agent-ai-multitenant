"use client";

/**
 * Primitivos visuales del dashboard de estadísticas (extraídos por
 * `task_prod16_08`): el sparkline SVG, la barra de tasa y la card de cifra.
 *
 * Ninguno hace fetch ni guarda estado: reciben datos ya formateados. Son los
 * tres únicos trozos de la pantalla reutilizables tal cual.
 */

import { useT } from "@/lib/i18n";
import { Card, CardContent } from "@/components/ui/card";

import { pct, pctNumber } from "./stats-format";
import type { TrendPoint } from "./stats-types";

/** Sparkline en SVG puro de la tasa de éxito diaria (0..1). Sin dependencia de gráficas. */
export function Sparkline({ data }: { data: TrendPoint[] }) {
  const t = useT("tenantStats");
  const width = 480;
  const height = 80;
  const pad = 4;

  if (data.length === 0) {
    return (
      <svg
        data-testid="stats-sparkline"
        viewBox={`0 0 ${width} ${height}`}
        className="text-muted-foreground/40 h-20 w-full"
        role="img"
        aria-label={t("sparklineEmptyLabel")}
      >
        <line
          x1={pad}
          y1={height - pad}
          x2={width - pad}
          y2={height - pad}
          stroke="currentColor"
          strokeWidth={1}
        />
      </svg>
    );
  }

  const n = data.length;
  const stepX = n > 1 ? (width - 2 * pad) / (n - 1) : 0;
  const points = data
    .map((d, i) => {
      const x = pad + i * stepX;
      const rate = d.success_rate === null ? 0 : Number(d.success_rate);
      const y = height - pad - rate * (height - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      data-testid="stats-sparkline"
      viewBox={`0 0 ${width} ${height}`}
      className="text-primary h-20 w-full"
      role="img"
      aria-label={t("sparklineLabel")}
      preserveAspectRatio="none"
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={2} />
    </svg>
  );
}

/** Fila con barra horizontal que representa una tasa de éxito. */
export function RateBar({
  label,
  rate,
  detail,
  testid,
}: {
  label: string;
  rate: string | null;
  detail?: string;
  testid?: string;
}) {
  const width = pctNumber(rate);
  return (
    <div className="flex items-center gap-3" data-testid={testid}>
      <div className="w-40 shrink-0 truncate text-sm">{label}</div>
      <div className="bg-muted relative h-2 flex-1 overflow-hidden rounded-full">
        <div
          className="bg-primary absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${width}%` }}
        />
      </div>
      <div className="w-28 shrink-0 text-right text-sm tabular-nums">
        {pct(rate)}
        {detail ? <span className="text-muted-foreground ml-1 text-xs">{detail}</span> : null}
      </div>
    </div>
  );
}

/** Card de una sola cifra con su rótulo. */
export function StatCard({
  label,
  value,
  testid,
  span,
}: {
  label: string;
  value: string | number;
  testid: string;
  span?: boolean;
}) {
  return (
    <Card className={span ? "md:col-span-2" : undefined}>
      <CardContent className="pt-5">
        <p className="text-muted-foreground text-xs uppercase tracking-wider">{label}</p>
        <p className="mt-1 text-3xl font-semibold tabular-nums" data-testid={testid}>
          {value}
        </p>
      </CardContent>
    </Card>
  );
}
