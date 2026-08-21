"use client";

/**
 * Historico de precios de un modelo, con sparkline SVG puro.
 *
 * Extraido de `model-price-dialogs.tsx` en prod-16 `task_prod16_06`. Corte
 * verbatim. El sparkline se dibuja a mano (sin libreria de graficas) y sigue
 * siendo privado de este modulo: solo lo usa el historico.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { SOURCE_BADGE, fmtDate, fmtUsd, type ModelPrice } from "./model-price-types";
// ===========================================================================
// Price history dialog — effective-dated rows + price-over-time chart
// ===========================================================================
interface PriceHistoryDialogProps {
  target: { provider: string; model_id: string; modality: string };
  onClose: () => void;
}

export function PriceHistoryDialog({ target, onClose }: PriceHistoryDialogProps) {
  const t = useT("modelPrices");
  const errorText = useErrorText();
  const historyQuery = useQuery({
    queryKey: ["model-prices", "history", target],
    queryFn: () => {
      const params = new URLSearchParams({
        provider: target.provider,
        model_id: target.model_id,
        modality: target.modality,
        limit: "200",
      });
      return apiFetch<ModelPrice[]>(`/model-prices?${params.toString()}`);
    },
    refetchOnWindowFocus: false,
  });

  // Oldest → newest for the timeline / chart.
  const series = useMemo(() => {
    const rows = historyQuery.data ?? [];
    return [...rows].sort(
      (a, b) => new Date(a.effective_from).getTime() - new Date(b.effective_from).getTime(),
    );
  }, [historyQuery.data]);

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="2xl">
      <DialogContent data-testid="price-history-dialog">
        <DialogHeader>
          <DialogTitle>
            Histórico — {target.provider} / {target.model_id} ({target.modality})
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          {historyQuery.isLoading ? (
            <p className="text-muted-foreground text-sm" data-testid="history-loading">
              Cargando histórico…
            </p>
          ) : historyQuery.isError ? (
            <p className="text-destructive text-sm" data-testid="history-error">
              {errorText(historyQuery.error)}
            </p>
          ) : series.length === 0 ? (
            <p className="text-muted-foreground text-sm italic" data-testid="history-empty">
              {t("historyEmpty")}
            </p>
          ) : (
            <>
              <PriceSparkline series={series} />
              <div className="overflow-x-auto rounded-lg border" data-testid="history-table">
                <table className="w-full text-sm">
                  <thead className="bg-muted text-muted-foreground">
                    <tr className="text-left">
                      <th className="px-3 py-2 font-medium">{t("historyFrom")}</th>
                      <th className="px-3 py-2 font-medium">{t("historyTo")}</th>
                      <th className="px-3 py-2 font-medium">{t("colInput")}</th>
                      <th className="px-3 py-2 font-medium">{t("colOutput")}</th>
                      <th className="px-3 py-2 font-medium">{t("colCache")}</th>
                      <th className="px-3 py-2 font-medium">{t("colSource")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {series.map((p) => (
                      <tr key={p.id} className="border-t" data-testid={`history-row-${p.id}`}>
                        <td className="px-3 py-2 text-xs">{fmtDate(p.effective_from)}</td>
                        <td className="px-3 py-2 text-xs">
                          {p.effective_to === null ? (
                            <Badge variant="success">{t("current")}</Badge>
                          ) : (
                            fmtDate(p.effective_to)
                          )}
                        </td>
                        <td className="px-3 py-2">{fmtUsd(p.input_price)}</td>
                        <td className="px-3 py-2">{fmtUsd(p.output_price)}</td>
                        <td className="px-3 py-2">{fmtUsd(p.cached_input_price)}</td>
                        <td className="px-3 py-2">
                          <Badge variant={SOURCE_BADGE[p.source] ?? "muted"}>{p.source}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="history-close">
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Lightweight price-over-time chart in pure SVG.
//
// No chart dependency is present (recharts is not installed). Rather than
// pull in a heavy dep just for this, we render input/output price polylines
// on a small SVG canvas — the task explicitly allows a simple alternative.
// ---------------------------------------------------------------------------
function PriceSparkline({ series }: { series: ModelPrice[] }) {
  const t = useT("modelPrices");
  const W = 640;
  const H = 160;
  const PAD = 24;

  const points = series.map((p, i) => ({
    i,
    input: Number(p.input_price),
    output: Number(p.output_price),
  }));

  const allValues = points.flatMap((p) => [p.input, p.output]).filter((v) => !Number.isNaN(v));
  const max = allValues.length > 0 ? Math.max(...allValues) : 1;
  const min = allValues.length > 0 ? Math.min(...allValues) : 0;
  const span = max - min || 1;
  const n = points.length;

  const x = (i: number) => (n <= 1 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (n - 1));
  const y = (v: number) => H - PAD - ((v - min) / span) * (H - 2 * PAD);

  const toPath = (key: "input" | "output") =>
    points
      .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`)
      .join(" ");

  return (
    <figure className="rounded-lg border p-3" data-testid="price-chart">
      <figcaption className="text-muted-foreground mb-2 flex items-center gap-4 text-xs">
        <span className="flex items-center gap-1">
          <span className="bg-primary inline-block h-2 w-3 rounded" /> Input
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded bg-emerald-500" /> Output
        </span>
        <span>{t("historyChartNote")}</span>
      </figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-40 w-full"
        role="img"
        aria-label={t("historyChartLabel")}
        preserveAspectRatio="none"
      >
        <path d={toPath("input")} fill="none" stroke="hsl(var(--primary))" strokeWidth={2} />
        <path d={toPath("output")} fill="none" stroke="rgb(16 185 129)" strokeWidth={2} />
        {points.map((p) => (
          <g key={p.i}>
            <circle cx={x(p.i)} cy={y(p.input)} r={2.5} fill="hsl(var(--primary))" />
            <circle cx={x(p.i)} cy={y(p.output)} r={2.5} fill="rgb(16 185 129)" />
          </g>
        ))}
      </svg>
    </figure>
  );
}
