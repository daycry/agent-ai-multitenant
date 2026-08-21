"use client";

/**
 * Cuerpo del dashboard de estadísticas del tenant (`task_14_12`): selectores de
 * ventana y moneda, cifras de cabecera, tendencia, resumen de consumo, agentes
 * top/bottom, desglose por agente y explorador de runs.
 *
 * Sección colocada extraída de `page.tsx` por `task_prod16_08`. Lo que queda en
 * `page.tsx` es sólo la cabecera y el gate de rol.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { SegmentedControl } from "@/components/shared/segmented-control";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { CostSegmentation } from "./cost-segmentation";
import { RunsExplorer } from "./runs-explorer";
import { DASH, fmtMeanDuration, pct, usd } from "./stats-format";
import {
  CURRENCY_OPTIONS,
  WINDOW_OPTIONS,
  type ConsumptionSummary,
  type DisplayCurrency,
  type StatsDashboard,
} from "./stats-types";
import { RateBar, Sparkline, StatCard } from "./stats-visuals";

export function StatsBody() {
  const t = useT("tenantStats");
  const errorText = useErrorText();
  const [windowDays, setWindowDays] = useState<number>(90);
  const [displayCurrency, setDisplayCurrency] = useState<DisplayCurrency>("USD");

  const dashboard = useQuery({
    queryKey: ["tenant-stats-dashboard", windowDays],
    queryFn: () => apiFetch<StatsDashboard>(`/tenant-stats/dashboard?window_days=${windowDays}`),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const consumption = useQuery({
    queryKey: ["tenant-stats-consumption", windowDays],
    queryFn: () =>
      apiFetch<ConsumptionSummary>(`/tenant-stats/consumption?window_days=${windowDays}`),
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (dashboard.isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner />
      </div>
    );
  }

  if (dashboard.isError || !dashboard.data) {
    return (
      <Card>
        <CardContent className="text-destructive pt-5 text-sm" data-testid="stats-error">
          {t("dashboardError", { detail: errorText(dashboard.error) })}
        </CardContent>
      </Card>
    );
  }

  const data = dashboard.data;
  const cons = consumption.data;

  return (
    <div className="space-y-6" data-testid="tenant-stats-dashboard">
      <div className="flex flex-wrap items-center gap-4">
        <SegmentedControl
          label={t("windowLabel")}
          value={windowDays}
          onChange={setWindowDays}
          options={WINDOW_OPTIONS.map((w) => ({ value: w, label: `${w}d` }))}
          getOptionTestId={(w) => `window-${w}`}
          data-testid="window-selector"
        />
        <SegmentedControl
          label={t("currencyLabel")}
          value={displayCurrency}
          onChange={setDisplayCurrency}
          options={CURRENCY_OPTIONS.map((c) => ({ value: c, label: c }))}
          getOptionTestId={(c) => `currency-${c}`}
          data-testid="currency-selector"
        />
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard
          label={t("successRateWindow", { days: data.window_days })}
          value={pct(data.overall_success_rate)}
          testid="overall-success-rate"
        />
        <StatCard label={t("runs")} value={data.total_runs} testid="total-runs" />
        <StatCard
          label={t("meanDuration")}
          value={fmtMeanDuration(data.mean_duration_ms)}
          testid="mean-duration"
        />
        <StatCard label={t("meanCost")} value={usd(data.mean_cost_usd)} testid="mean-cost" />
      </div>

      <Card>
        <CardContent className="pt-5">
          <p className="text-muted-foreground mb-2 text-xs uppercase tracking-wider">
            {t("trendTitle")}
          </p>
          <Sparkline data={data.trend} />
        </CardContent>
      </Card>

      <div>
        <p className="text-muted-foreground mb-2 text-xs uppercase tracking-wider">
          {t("consumptionTitle")}
        </p>
        {consumption.isLoading || !cons ? (
          <div className="flex items-center justify-center py-6">
            <Spinner />
          </div>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-4" data-testid="consumption-summary">
              <StatCard
                label={t("totalCost")}
                value={`$${cons.total_cost_usd}`}
                testid="total-cost"
              />
              <StatCard label={t("runs")} value={cons.run_count} testid="consumption-runs" />
              <StatCard
                label={t("tokensBreakdown")}
                value={`${cons.total_tokens_input}/${cons.total_tokens_output}/${cons.total_tokens_cached}`}
                testid="consumption-tokens"
                span
              />
            </div>

            <CostSegmentation cons={cons} />

            {cons.costliest_run ? (
              <Card className="mt-4">
                <CardContent className="pt-5" data-testid="costliest-run">
                  <p className="text-muted-foreground text-xs uppercase tracking-wider">
                    {t("costliestTitle")}
                  </p>
                  <p className="mt-1 text-sm">
                    <span className="font-medium">{cons.costliest_run.task_title ?? DASH}</span>{" "}
                    <span className="text-muted-foreground">
                      ({cons.costliest_run.agent_name ?? DASH})
                    </span>{" "}
                    — <span className="tabular-nums">${cons.costliest_run.total_cost_usd}</span>,{" "}
                    {cons.costliest_run.total_tokens} {t("tokensSuffix")}
                  </p>
                </CardContent>
              </Card>
            ) : null}
          </>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <AgentRanking
          title={t("topAgents")}
          agents={data.top_agents}
          testid="top-agents"
          rowPrefix="top-agent"
        />
        <AgentRanking
          title={t("bottomAgents")}
          agents={data.bottom_agents}
          testid="bottom-agents"
          rowPrefix="bottom-agent"
        />
      </div>

      <Card>
        <CardContent className="pt-5">
          <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
            {t("byAgentTitle")}
          </p>
          {data.by_agent.length === 0 ? (
            <p className="text-muted-foreground text-sm">{t("noRuns")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="by-agent-table">
                <thead className="text-muted-foreground text-left text-xs uppercase">
                  <tr>
                    <th className="py-2 pr-3">{t("colAgent")}</th>
                    <th className="py-2 pr-3">{t("colRole")}</th>
                    <th className="py-2 pr-3">{t("runs")}</th>
                    <th className="py-2 pr-3">{t("colSuccess")}</th>
                    <th className="py-2 pr-3">{t("meanDuration")}</th>
                    <th className="py-2 pr-3">{t("meanCost")}</th>
                    <th className="py-2 pr-3">{t("totalCost")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_agent.map((a) => (
                    <tr
                      key={a.agent_id ?? "none"}
                      className="border-border border-t"
                      data-testid={`agent-row-${a.agent_id ?? "none"}`}
                    >
                      <td className="py-2 pr-3 font-medium">{a.agent_name ?? t("deletedAgent")}</td>
                      <td className="text-muted-foreground py-2 pr-3">{a.agent_role ?? DASH}</td>
                      <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                        {a.run_count}
                      </td>
                      <td className="py-2 pr-3 tabular-nums">{pct(a.success_rate)}</td>
                      <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                        {fmtMeanDuration(a.mean_duration_ms)}
                      </td>
                      <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                        {usd(a.mean_cost_usd)}
                      </td>
                      <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                        ${a.total_cost_usd}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <RunsExplorer windowDays={windowDays} displayCurrency={displayCurrency} />

      <p className="text-muted-foreground text-xs" data-testid="currency-note">
        {t("currencyNote", { currency: data.currency })}
      </p>
    </div>
  );
}

/**
 * Las dos listas de agentes (top y bottom) eran el MISMO bloque copiado, con
 * sólo el título y el testid cambiados. Al extraer la sección se unifican: dos
 * copias de un ranking son dos sitios donde arreglar el mismo fallo.
 */
function AgentRanking({
  title,
  agents,
  testid,
  rowPrefix,
}: {
  title: string;
  agents: StatsDashboard["top_agents"];
  testid: string;
  rowPrefix: string;
}) {
  const t = useT("tenantStats");
  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <p className="text-muted-foreground text-xs uppercase tracking-wider">{title}</p>
        <div className="space-y-2" data-testid={testid}>
          {agents.length === 0 ? (
            <p className="text-muted-foreground text-sm">{t("noRuns")}</p>
          ) : (
            agents.map((a) => (
              <RateBar
                key={a.agent_id ?? "none"}
                label={a.agent_name ?? t("deletedAgent")}
                rate={a.success_rate}
                detail={`${a.succeeded}/${a.run_count}`}
                testid={`${rowPrefix}-${a.agent_id ?? "none"}`}
              />
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}
