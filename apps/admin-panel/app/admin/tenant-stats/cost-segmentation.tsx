"use client";

/**
 * Segmentación de coste IA vs humano (Plan 16 `task_16_12`).
 *
 * Sección colocada extraída de `page.tsx` por `task_prod16_08`. El coste IA sale
 * de `executions`; el humano es tarifa × horas de `human_work_sessions`. Ambos
 * en USD canónico.
 */

import { Card, CardContent } from "@/components/ui/card";
import { useT } from "@/lib/i18n";

import type { ConsumptionSummary } from "./stats-types";

export function CostSegmentation({ cons }: { cons: ConsumptionSummary }) {
  const t = useT("tenantStats");
  const ai = Number(cons.ai_cost_usd);
  const human = Number(cons.human_cost_usd);
  const total = ai + human;
  // Guarda contra la división por cero: una ventana vacía muestra barra neutra.
  const aiPct = total > 0 ? Math.round((ai / total) * 100) : 0;
  const humanPct = total > 0 ? 100 - aiPct : 0;

  return (
    <Card className="mt-4">
      <CardContent className="pt-5" data-testid="cost-segmentation">
        <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
          {t("segTitle")}
        </p>

        <div
          className="bg-muted flex h-3 w-full overflow-hidden rounded-full"
          role="img"
          aria-label={t("segBarLabel", { ai: aiPct, human: humanPct })}
        >
          <div
            className="bg-primary h-full"
            style={{ width: `${aiPct}%` }}
            data-testid="cost-bar-ai"
          />
          <div
            className="bg-info h-full"
            style={{ width: `${humanPct}%` }}
            data-testid="cost-bar-human"
          />
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          <div className="flex items-center gap-2">
            <span className="bg-primary h-3 w-3 shrink-0 rounded-sm" aria-hidden="true" />
            <div>
              <p className="text-muted-foreground text-xs">{t("segAi")}</p>
              <p className="text-lg font-semibold tabular-nums" data-testid="ai-cost">
                ${cons.ai_cost_usd}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="bg-info h-3 w-3 shrink-0 rounded-sm" aria-hidden="true" />
            <div>
              <p className="text-muted-foreground text-xs">{t("segHuman")}</p>
              <p className="text-lg font-semibold tabular-nums" data-testid="human-cost">
                ${cons.human_cost_usd}
              </p>
              <p className="text-muted-foreground text-xs" data-testid="human-hours">
                {t("segHours", { hours: cons.human_hours_logged })}
              </p>
            </div>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">{t("totalCost")}</p>
            <p className="text-lg font-semibold tabular-nums" data-testid="segment-total-cost">
              ${cons.total_cost_usd}
            </p>
          </div>
        </div>

        <p className="text-muted-foreground mt-3 text-xs">{t("segNote")}</p>
      </CardContent>
    </Card>
  );
}
