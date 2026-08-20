"use client";

/**
 * task_wf_30 — La cabecera de estado del plan: progreso, PR y coste real.
 *
 * Tres cegueras que venían del mismo sitio, cosas calculadas y nunca conectadas
 * a su consumidor:
 *
 *   - D-01: el progreso X/Y existía (`compute_plan_progress`) sin endpoint ni
 *     pantalla — el operador no sabía por dónde iba un plan.
 *   - D-02: `pr_url`/`pr_branch`/`pr_error` viajaban en la respuesta del plan
 *     con CERO ocurrencias en el frontend: se aprobaba el plan y no se veía ni
 *     el PR ni, si falló, por qué.
 *   - D-04: el coste estimado se calculaba entero y el real no se agregaba.
 *
 * Un solo componente sobre `GET /plans/{id}/status`, arriba del todo, para que
 * el estado del plan se lea sin desplazarse.
 */

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Lang } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { cn } from "@/lib/utils";

import { numberLocale } from "./plan-spec-types";

export interface PlanStatusResponse {
  plan_id: string;
  status: string;
  progress: { total: number; done: number; open: number; label: string };
  pr: { url: string | null; branch: string | null; error: string | null };
  cost: {
    ai_currency: string;
    human_currency: string;
    estimated_ai_min: string;
    estimated_ai_max: string;
    estimated_human_hours: string;
    estimated_human_cost: string;
    actual_ai_cost: string;
    actual_tokens: number;
    actual_runs: number;
    over_estimate: boolean;
  };
}

/**
 * Percentage of completed tasks, or `null` when there is nothing to measure.
 *
 * `null` and `0` are different states — "this plan has no tasks yet" must not
 * render as "0% done", which reads as stalled work that does not exist.
 */
export function progressPercent(progress: { total: number; done: number }): number | null {
  if (!progress || progress.total <= 0) return null;
  return Math.round((progress.done / progress.total) * 100);
}

/** Money with a currency, trimmed to cents — the API keeps full precision. */
export function formatMoney(raw: string | number | null | undefined, currency: string): string {
  const value = Number(raw ?? 0);
  if (!Number.isFinite(value)) return `— ${currency}`;
  return `${value.toFixed(2)} ${currency}`;
}

/**
 * 812345 → "812,3k". Token counts are read as magnitudes, not exact figures.
 *
 * `lang` es obligatorio y sin default: el separador decimal lo elige el idioma
 * activo, y con `"es-ES"` cableado el panel en inglés escribía «812,3k» donde
 * un lector inglés lee «812.3k» (prod-16 `task_prod16_03`).
 */
export function formatTokens(tokens: number | null | undefined, lang: Lang): string {
  const value = Number(tokens ?? 0);
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value < 1000) return String(value);
  return `${(value / 1000).toLocaleString(numberLocale(lang), { maximumFractionDigits: 1 })}k`;
}

function Metric({
  label,
  children,
  testId,
}: {
  label: string;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <p className="text-muted-foreground text-[10px] uppercase tracking-wide">{label}</p>
      <div className="mt-0.5 text-sm font-medium">{children}</div>
    </div>
  );
}

export function PlanStatusHeader({ planId }: { planId: string }) {
  const t = useT("planDetail");
  const lang = useLangOptional();
  const { data, isLoading } = useQuery({
    queryKey: ["plan-status", planId],
    queryFn: () => apiFetch<PlanStatusResponse>(`/plans/${planId}/status`),
  });

  if (isLoading || !data) {
    return (
      <Card className="mt-2" data-testid="plan-status-header-loading">
        <CardContent className="text-muted-foreground pt-6 text-sm">
          {t("statusLoading")}
        </CardContent>
      </Card>
    );
  }

  const percent = progressPercent(data.progress);

  return (
    <Card className="mt-2" data-testid="plan-status-header">
      <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
        <Metric label={t("statusProgress")} testId="plan-status-progress">
          <span data-testid="plan-status-progress-label">{data.progress.label}</span>
          {percent !== null ? (
            <span className="text-muted-foreground ml-2 text-xs">{percent}%</span>
          ) : (
            <span className="text-muted-foreground ml-2 text-xs">{t("statusNoTasks")}</span>
          )}
          {data.progress.open > 0 ? (
            <span className="text-muted-foreground ml-2 text-xs">
              {data.progress.open === 1
                ? t("statusOpenOne", { count: data.progress.open })
                : t("statusOpenMany", { count: data.progress.open })}
            </span>
          ) : null}
        </Metric>

        <Metric label={t("statusPr")} testId="plan-status-pr">
          {data.pr.url ? (
            <a
              className="inline-flex items-center gap-1 underline"
              href={data.pr.url}
              target="_blank"
              rel="noreferrer noopener"
              data-testid="plan-status-pr-link"
            >
              {data.pr.branch ?? t("statusPrFallback")}
              <ExternalLink className="h-3 w-3" />
            </a>
          ) : data.pr.error ? (
            <span className="text-destructive text-xs" data-testid="plan-status-pr-error">
              {t("statusPrError", { error: data.pr.error })}
            </span>
          ) : (
            <span className="text-muted-foreground text-xs" data-testid="plan-status-pr-none">
              {t("statusPrNone")}
            </span>
          )}
        </Metric>

        <Metric label={t("statusCost")} testId="plan-status-cost">
          <span
            className={cn(data.cost.over_estimate && "text-destructive")}
            data-testid="plan-status-cost-actual"
          >
            {formatMoney(data.cost.actual_ai_cost, data.cost.ai_currency)}
          </span>
          <span className="text-muted-foreground text-xs">
            {" / "}
            {formatMoney(data.cost.estimated_ai_max, data.cost.ai_currency)}{" "}
            {t("statusCostEstimatedSuffix")}
          </span>
          {data.cost.over_estimate ? (
            <Badge variant="danger" className="ml-2" data-testid="plan-status-over-estimate">
              {t("statusOverEstimate")}
            </Badge>
          ) : null}
          <p className="text-muted-foreground mt-0.5 text-[10px]">
            {t(data.cost.actual_runs === 1 ? "statusFootnoteOne" : "statusFootnoteMany", {
              tokens: formatTokens(data.cost.actual_tokens, lang),
              runs: data.cost.actual_runs,
              cost: formatMoney(data.cost.estimated_human_cost, data.cost.human_currency),
            })}
          </p>
        </Metric>
      </CardContent>
    </Card>
  );
}
