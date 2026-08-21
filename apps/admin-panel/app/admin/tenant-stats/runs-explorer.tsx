"use client";

/**
 * Explorador de runs: una fila por `execution`, filtrable y paginado
 * (`task_14_12`). Sección colocada extraída de `page.tsx` por `task_prod16_08`.
 *
 * La columna de moneda convertida sólo aparece cuando la moneda elegida no es
 * USD, y su `title` explica con qué tasa y de qué fecha se convirtió — o dice
 * que no había tasa, en vez de fingir un cero.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { DASH, convertedCost, fmtDuration, fmtWhen, usd } from "./stats-format";
import { PAGE_SIZE, type DisplayCurrency, type ExecutionRunRow } from "./stats-types";

const VERDICT_BADGE: Record<string, BadgeVariant> = {
  done: "success",
  running: "info",
  aborted: "danger",
  failed: "danger",
  awaiting_human_approval: "warning",
};

interface RunFilters {
  role: string;
  verdict: string;
  model: string;
  minCost: string;
}

export function RunsExplorer({
  windowDays,
  displayCurrency,
}: {
  windowDays: number;
  displayCurrency: DisplayCurrency;
}) {
  const t = useT("tenantStats");
  const errorText = useErrorText();
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<RunFilters>({
    role: "",
    verdict: "",
    model: "",
    minCost: "",
  });

  const showConverted = displayCurrency !== "USD";

  const params = new URLSearchParams();
  params.set("window_days", String(windowDays));
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(page * PAGE_SIZE));
  if (filters.role.trim()) params.set("role", filters.role.trim());
  if (filters.verdict.trim()) params.set("verdict", filters.verdict.trim());
  if (filters.model.trim()) params.set("model", filters.model.trim());
  if (filters.minCost.trim()) params.set("min_cost", filters.minCost.trim());
  // Dispara la conversión por fila en el backend. USD sigue siendo canónico.
  if (showConverted) params.set("display_currency", displayCurrency);

  const query = params.toString();
  const runs = useQuery({
    queryKey: ["tenant-stats-runs", query],
    queryFn: () => apiFetch<ExecutionRunRow[]>(`/tenant-stats/runs?${query}`),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const update = (patch: Partial<RunFilters>) => {
    setPage(0);
    setFilters((f) => ({ ...f, ...patch }));
  };

  return (
    <Card>
      <CardContent className="pt-5" data-testid="runs-explorer">
        <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
          {t("explorerTitle")}
        </p>

        <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4" data-testid="run-filters">
          <Input
            placeholder={t("filterRole")}
            value={filters.role}
            onChange={(e) => update({ role: e.target.value })}
            data-testid="filter-role"
          />
          <Input
            placeholder={t("filterVerdict")}
            value={filters.verdict}
            onChange={(e) => update({ verdict: e.target.value })}
            data-testid="filter-verdict"
          />
          <Input
            placeholder={t("filterModel")}
            value={filters.model}
            onChange={(e) => update({ model: e.target.value })}
            data-testid="filter-model"
          />
          <Input
            type="number"
            min="0"
            step="0.01"
            placeholder={t("filterMinCost")}
            value={filters.minCost}
            onChange={(e) => update({ minCost: e.target.value })}
            data-testid="filter-min-cost"
          />
        </div>

        {runs.isLoading ? (
          <div className="flex items-center justify-center py-6">
            <Spinner />
          </div>
        ) : runs.isError || !runs.data ? (
          <p className="text-destructive text-sm" data-testid="runs-error">
            {t("runsError", { detail: errorText(runs.error) })}
          </p>
        ) : runs.data.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="runs-empty">
            {t("runsEmpty")}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="runs-table">
              <thead className="text-muted-foreground text-left text-xs uppercase">
                <tr>
                  <th className="py-2 pr-3">{t("colTimestamp")}</th>
                  <th className="py-2 pr-3">{t("colPlan")}</th>
                  <th className="py-2 pr-3">{t("colTask")}</th>
                  <th className="py-2 pr-3">{t("colAgent")}</th>
                  <th className="py-2 pr-3">{t("colRole")}</th>
                  <th className="py-2 pr-3">{t("colModel")}</th>
                  <th className="py-2 pr-3">{t("colDuration")}</th>
                  <th className="py-2 pr-3">{t("colTokens")}</th>
                  <th className="py-2 pr-3">{t("colCostUsd")}</th>
                  {showConverted ? (
                    <th className="py-2 pr-3" data-testid="runs-col-converted">
                      {t("colCostConverted", { currency: displayCurrency })}
                    </th>
                  ) : null}
                  <th className="py-2 pr-3">{t("colVerdict")}</th>
                  <th className="py-2 pr-3">{t("colRetries")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id} className="border-border border-t" data-testid={`run-row-${r.id}`}>
                    <td className="text-muted-foreground whitespace-nowrap py-2 pr-3">
                      {fmtWhen(r.created_at)}
                    </td>
                    <td className="text-muted-foreground py-2 pr-3">{r.plan_title ?? DASH}</td>
                    <td className="py-2 pr-3 font-medium">{r.task_title ?? DASH}</td>
                    <td className="text-muted-foreground py-2 pr-3">{r.agent_name ?? DASH}</td>
                    <td className="text-muted-foreground py-2 pr-3">{r.agent_role ?? DASH}</td>
                    <td className="text-muted-foreground py-2 pr-3">{r.model ?? DASH}</td>
                    <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                      {fmtDuration(r.duration_ms)}
                    </td>
                    <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                      {r.total_tokens}
                    </td>
                    <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                      {usd(r.total_cost_usd)}
                    </td>
                    {showConverted ? (
                      <td
                        className="text-muted-foreground py-2 pr-3 tabular-nums"
                        data-testid={`run-converted-${r.id}`}
                        title={
                          r.applied_rate
                            ? t("convertedTitle", {
                                currency: displayCurrency,
                                date: r.applied_rate_date ?? DASH,
                                rate: r.applied_rate,
                              })
                            : t("noRateTitle")
                        }
                      >
                        {convertedCost(r, displayCurrency)}
                      </td>
                    ) : null}
                    <td className="py-2 pr-3">
                      <Badge variant={VERDICT_BADGE[r.verdict] ?? "muted"}>{r.verdict}</Badge>
                    </td>
                    <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                      {r.retry_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-4 flex items-center justify-between" data-testid="runs-pagination">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            data-testid="runs-prev"
          >
            {t("prev")}
          </Button>
          <span className="text-muted-foreground text-sm">{t("pageN", { n: page + 1 })}</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => p + 1)}
            disabled={(runs.data?.length ?? 0) < PAGE_SIZE}
            data-testid="runs-next"
          >
            {t("next")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
