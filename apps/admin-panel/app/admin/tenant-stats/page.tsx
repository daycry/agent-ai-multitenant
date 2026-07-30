"use client";

/**
 * task_14_12 — Dashboard de ESTADÍSTICAS del tenant + explorador de runs (Plan 14 Fase D).
 *
 * Vista tenant de cómo rinden sus agentes y qué consume, agregada desde la tabla
 * `executions` (una fila por run del loop del agente contra una tarea) — NO desde
 * los roll-ups de EvalRun (eso es el dashboard de CALIDAD, task_14_11). Tres
 * superficies:
 *   - estadísticas de agentes: tasa de éxito por agente, tiempo medio, coste medio,
 *     agentes top/bottom y tendencia temporal;
 *   - resumen de consumo: coste acumulado, tokens (input/output/cached), nº de runs,
 *     coste medio, run más costoso;
 *   - explorador de runs: una fila por execution (timestamp/plan/tarea/agente/rol/
 *     modelo/duración/tokens/coste USD/verdict/retry_count), filtrable + paginado.
 *
 * Multi-tenancy: todo es **tenant-scoped** (tenant_id + RLS); un tenant ve SÓLO sus
 * propias executions. La comparativa cross-tenant es una superficie aparte, sólo
 * para System Admin (task_14_15). Costes en USD canónico — el toggle "moneda del
 * tenant" depende del sistema FX (exchange_rates), no construido (gap de alcance del
 * Plan 11), así que no se ofrece aquí.
 *
 * Superficie (todo `tenant_admin`; `<RoleGuard min="tenant_admin">` + el backend gatea
 * con `require_tenant_admin`):
 *   GET /tenant-stats/dashboard?window_days=N[&agent_id&role&plan_id]
 *   GET /tenant-stats/consumption?window_days=N[&agent_id&plan_id]
 *   GET /tenant-stats/runs?limit&offset&window_days[&agent_id&role&plan_id&task_id&verdict&model&min_cost]
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, BarChart3 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { RoleGuard } from "@/components/ui/role-guard";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.tenant_stats. success_rate are fractions in
// [0,1] serialised as decimal strings (or null when no runs); costs are USD
// decimal strings.
// ---------------------------------------------------------------------------
interface AgentStats {
  agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  run_count: number;
  succeeded: number;
  success_rate: string | null;
  mean_duration_ms: string | null;
  mean_cost_usd: string | null;
  total_cost_usd: string;
  total_tokens: number;
}

interface TrendPoint {
  day: string;
  run_count: number;
  succeeded: number;
  success_rate: string | null;
  total_cost_usd: string;
}

interface StatsDashboard {
  window_days: number;
  currency: string;
  total_runs: number;
  succeeded_runs: number;
  overall_success_rate: string | null;
  mean_duration_ms: string | null;
  mean_cost_usd: string | null;
  total_cost_usd: string;
  by_agent: AgentStats[];
  top_agents: AgentStats[];
  bottom_agents: AgentStats[];
  trend: TrendPoint[];
}

interface CostliestRun {
  execution_id: string;
  task_id: string;
  task_title: string | null;
  agent_name: string | null;
  total_cost_usd: string;
  total_tokens: number;
  created_at: string;
}

interface ConsumptionSummary {
  window_days: number;
  currency: string;
  run_count: number;
  accumulated_cost_usd: string;
  mean_cost_usd: string | null;
  total_tokens: number;
  total_tokens_input: number;
  total_tokens_output: number;
  total_tokens_cached: number;
  costliest_run: CostliestRun | null;
  // Cost segmentation (Plan 16 task_16_12): AI cost (executions) vs human cost
  // (rate * hours from human_work_sessions), and their combined total. All USD.
  ai_cost_usd: string;
  human_cost_usd: string;
  total_cost_usd: string;
  human_hours_logged: string;
}

interface ExecutionRunRow {
  id: string;
  created_at: string;
  task_id: string;
  task_title: string | null;
  plan_id: string | null;
  plan_title: string | null;
  agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  model: string | null;
  verdict: string;
  succeeded: boolean;
  retry_count: number;
  duration_ms: number | null;
  total_tokens: number;
  total_cost_usd: string;
  // FX display-only (Plan 11.1): when the chosen display currency is not USD,
  // the backend converts each row at its OWN run date and carries the applied
  // rate for traceability. Null when no conversion (USD) or no rate for the date.
  display_currency: string | null;
  display_cost: string | null;
  applied_rate: string | null;
  applied_rate_date: string | null;
  started_at: string | null;
  completed_at: string | null;
}

const WINDOW_OPTIONS = [30, 90, 365] as const;
const PAGE_SIZE = 25;

// Display-currency toggle (Plan 11.1 task_11_1_03). USD is canonical; the
// alternatives are converted on the fly at each run's own date (display only —
// the stored USD never changes). Kept short + common; the backend accepts any
// ISO-4217 code for which a rate exists.
const CURRENCY_OPTIONS = ["USD", "EUR", "GBP"] as const;
type DisplayCurrency = (typeof CURRENCY_OPTIONS)[number];

const VERDICT_BADGE: Record<string, BadgeVariant> = {
  done: "success",
  running: "info",
  aborted: "danger",
  failed: "danger",
  awaiting_human_approval: "warning",
};

function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/** A success_rate decimal string -> a percentage label (or "—" when null). */
function pct(rate: string | null): string {
  if (rate === null) return "—";
  const n = Number(rate);
  if (Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

function pctNumber(rate: string | null): number {
  if (rate === null) return 0;
  const n = Number(rate);
  return Number.isNaN(n) ? 0 : Math.round(n * 100);
}

/** Milliseconds -> human label. */
function fmtDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function fmtMeanDuration(ms: string | null): string {
  if (ms === null) return "—";
  const n = Number(ms);
  return Number.isNaN(n) ? "—" : fmtDuration(Math.round(n));
}

function usd(value: string | null): string {
  return value === null ? "—" : `$${value}`;
}

/**
 * The display-currency cell: the converted amount + its code, or a dash with
 * a "no rate" hint when this run's date had no FX rate (the USD figure stands).
 */
function convertedCost(row: ExecutionRunRow, currency: DisplayCurrency): string {
  if (row.display_cost === null) return "—";
  return `${row.display_cost} ${currency}`;
}

/** Pure-SVG sparkline of the per-day success rate (0..1). No heavy chart dep. */
function Sparkline({ data }: { data: TrendPoint[] }) {
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
        aria-label="Sin runs en la ventana"
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
      aria-label="Tasa de éxito por día"
      preserveAspectRatio="none"
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={2} />
    </svg>
  );
}

/** A horizontal bar row showing a success-rate percentage. */
function RateBar({
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

function StatCard({
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

// ---------------------------------------------------------------------------
// Runs explorer (filterable, paginated)
// ---------------------------------------------------------------------------
interface RunFilters {
  role: string;
  verdict: string;
  model: string;
  minCost: string;
}

function RunsExplorer({
  windowDays,
  displayCurrency,
}: {
  windowDays: number;
  displayCurrency: DisplayCurrency;
}) {
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
  // Drive the backend's per-row conversion. USD stays canonical (no param).
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
          Explorador de runs
        </p>

        {/* Filters */}
        <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4" data-testid="run-filters">
          <Input
            placeholder="Rol (ej. backend)"
            value={filters.role}
            onChange={(e) => update({ role: e.target.value })}
            data-testid="filter-role"
          />
          <Input
            placeholder="Verdict (ej. done)"
            value={filters.verdict}
            onChange={(e) => update({ verdict: e.target.value })}
            data-testid="filter-verdict"
          />
          <Input
            placeholder="Modelo"
            value={filters.model}
            onChange={(e) => update({ model: e.target.value })}
            data-testid="filter-model"
          />
          <Input
            type="number"
            min="0"
            step="0.01"
            placeholder="Coste mínimo USD"
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
            No se pudo cargar el explorador: {errorText(runs.error)}
          </p>
        ) : runs.data.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="runs-empty">
            Sin runs para estos filtros.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="runs-table">
              <thead className="text-muted-foreground text-left text-xs uppercase">
                <tr>
                  <th className="py-2 pr-3">Timestamp</th>
                  <th className="py-2 pr-3">Plan</th>
                  <th className="py-2 pr-3">Tarea</th>
                  <th className="py-2 pr-3">Agente</th>
                  <th className="py-2 pr-3">Rol</th>
                  <th className="py-2 pr-3">Modelo</th>
                  <th className="py-2 pr-3">Duración</th>
                  <th className="py-2 pr-3">Tokens</th>
                  <th className="py-2 pr-3">Coste USD</th>
                  {showConverted ? (
                    <th className="py-2 pr-3" data-testid="runs-col-converted">
                      Coste {displayCurrency}
                    </th>
                  ) : null}
                  <th className="py-2 pr-3">Verdict</th>
                  <th className="py-2 pr-3">Reintentos</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id} className="border-border border-t" data-testid={`run-row-${r.id}`}>
                    <td className="text-muted-foreground whitespace-nowrap py-2 pr-3">
                      {fmtWhen(r.created_at)}
                    </td>
                    <td className="text-muted-foreground py-2 pr-3">{r.plan_title ?? "—"}</td>
                    <td className="py-2 pr-3 font-medium">{r.task_title ?? "—"}</td>
                    <td className="text-muted-foreground py-2 pr-3">{r.agent_name ?? "—"}</td>
                    <td className="text-muted-foreground py-2 pr-3">{r.agent_role ?? "—"}</td>
                    <td className="text-muted-foreground py-2 pr-3">{r.model ?? "—"}</td>
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
                            ? `Convertido a ${displayCurrency} con la tasa del ${
                                r.applied_rate_date ?? "—"
                              } (1 USD = ${r.applied_rate} ${displayCurrency})`
                            : "Sin tasa de cambio para la fecha de este run"
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

        {/* Pagination */}
        <div className="mt-4 flex items-center justify-between" data-testid="runs-pagination">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            data-testid="runs-prev"
          >
            Anterior
          </Button>
          <span className="text-muted-foreground text-sm">Página {page + 1}</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => p + 1)}
            disabled={(runs.data?.length ?? 0) < PAGE_SIZE}
            data-testid="runs-next"
          >
            Siguiente
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Cost segmentation: AI cost vs Human cost (Plan 16 task_16_12)
// ---------------------------------------------------------------------------
function CostSegmentation({ cons }: { cons: ConsumptionSummary }) {
  const ai = Number(cons.ai_cost_usd);
  const human = Number(cons.human_cost_usd);
  const total = ai + human;
  // Guard division by zero — an empty window shows a flat, neutral bar.
  const aiPct = total > 0 ? Math.round((ai / total) * 100) : 0;
  const humanPct = total > 0 ? 100 - aiPct : 0;

  return (
    <Card className="mt-4">
      <CardContent className="pt-5" data-testid="cost-segmentation">
        <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
          Segmentación de coste: IA vs Humano
        </p>

        {/* Stacked bar */}
        <div
          className="bg-muted flex h-3 w-full overflow-hidden rounded-full"
          role="img"
          aria-label={`Coste IA ${aiPct}%, coste humano ${humanPct}%`}
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

        {/* Legend + figures */}
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          <div className="flex items-center gap-2">
            <span className="bg-primary h-3 w-3 shrink-0 rounded-sm" aria-hidden="true" />
            <div>
              <p className="text-muted-foreground text-xs">Coste IA</p>
              <p className="text-lg font-semibold tabular-nums" data-testid="ai-cost">
                ${cons.ai_cost_usd}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="bg-info h-3 w-3 shrink-0 rounded-sm" aria-hidden="true" />
            <div>
              <p className="text-muted-foreground text-xs">Coste humano</p>
              <p className="text-lg font-semibold tabular-nums" data-testid="human-cost">
                ${cons.human_cost_usd}
              </p>
              <p className="text-muted-foreground text-xs" data-testid="human-hours">
                {cons.human_hours_logged} h registradas
              </p>
            </div>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">Coste total</p>
            <p className="text-lg font-semibold tabular-nums" data-testid="segment-total-cost">
              ${cons.total_cost_usd}
            </p>
          </div>
        </div>

        <p className="text-muted-foreground mt-3 text-xs">
          El coste IA proviene de las executions; el coste humano es tarifa × horas de las sesiones
          de trabajo (human_work_sessions), convertido a USD. Ambos en USD canónico.
        </p>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------
function StatsBody() {
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
          No se pudo cargar el dashboard: {errorText(dashboard.error)}
        </CardContent>
      </Card>
    );
  }

  const data = dashboard.data;
  const cons = consumption.data;

  return (
    <div className="space-y-6" data-testid="tenant-stats-dashboard">
      {/* Window selector + display-currency toggle */}
      <div className="flex flex-wrap items-center gap-4">
        <SegmentedControl
          label="Ventana:"
          value={windowDays}
          onChange={setWindowDays}
          options={WINDOW_OPTIONS.map((w) => ({ value: w, label: `${w}d` }))}
          getOptionTestId={(w) => `window-${w}`}
          data-testid="window-selector"
        />
        <SegmentedControl
          label="Moneda:"
          value={displayCurrency}
          onChange={setDisplayCurrency}
          options={CURRENCY_OPTIONS.map((c) => ({ value: c, label: c }))}
          getOptionTestId={(c) => `currency-${c}`}
          data-testid="currency-selector"
        />
      </div>

      {/* Headline cards + trend */}
      <div className="grid gap-4 md:grid-cols-4">
        <StatCard
          label={`Tasa de éxito (${data.window_days}d)`}
          value={pct(data.overall_success_rate)}
          testid="overall-success-rate"
        />
        <StatCard label="Runs" value={data.total_runs} testid="total-runs" />
        <StatCard
          label="Tiempo medio"
          value={fmtMeanDuration(data.mean_duration_ms)}
          testid="mean-duration"
        />
        <StatCard label="Coste medio" value={usd(data.mean_cost_usd)} testid="mean-cost" />
      </div>

      <Card>
        <CardContent className="pt-5">
          <p className="text-muted-foreground mb-2 text-xs uppercase tracking-wider">
            Tendencia de tasa de éxito (diaria)
          </p>
          <Sparkline data={data.trend} />
        </CardContent>
      </Card>

      {/* Consumption summary */}
      <div>
        <p className="text-muted-foreground mb-2 text-xs uppercase tracking-wider">
          Resumen de consumo
        </p>
        {consumption.isLoading || !cons ? (
          <div className="flex items-center justify-center py-6">
            <Spinner />
          </div>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-4" data-testid="consumption-summary">
              <StatCard label="Coste total" value={`$${cons.total_cost_usd}`} testid="total-cost" />
              <StatCard label="Runs" value={cons.run_count} testid="consumption-runs" />
              <StatCard
                label="Tokens (in/out/cached)"
                value={`${cons.total_tokens_input}/${cons.total_tokens_output}/${cons.total_tokens_cached}`}
                testid="consumption-tokens"
                span
              />
            </div>

            {/* Cost segmentation: AI vs Human (Plan 16 task_16_12) */}
            <CostSegmentation cons={cons} />
            {cons.costliest_run ? (
              <Card className="mt-4">
                <CardContent className="pt-5" data-testid="costliest-run">
                  <p className="text-muted-foreground text-xs uppercase tracking-wider">
                    Run más costoso
                  </p>
                  <p className="mt-1 text-sm">
                    <span className="font-medium">{cons.costliest_run.task_title ?? "—"}</span>{" "}
                    <span className="text-muted-foreground">
                      ({cons.costliest_run.agent_name ?? "—"})
                    </span>{" "}
                    — <span className="tabular-nums">${cons.costliest_run.total_cost_usd}</span>,{" "}
                    {cons.costliest_run.total_tokens} tokens
                  </p>
                </CardContent>
              </Card>
            ) : null}
          </>
        )}
      </div>

      {/* Top / bottom agents */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Agentes top (tasa de éxito)
            </p>
            <div className="space-y-2" data-testid="top-agents">
              {data.top_agents.length === 0 ? (
                <p className="text-muted-foreground text-sm">Sin runs.</p>
              ) : (
                data.top_agents.map((a) => (
                  <RateBar
                    key={a.agent_id ?? "none"}
                    label={a.agent_name ?? "(agente eliminado)"}
                    rate={a.success_rate}
                    detail={`${a.succeeded}/${a.run_count}`}
                    testid={`top-agent-${a.agent_id ?? "none"}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Agentes bottom (tasa de éxito)
            </p>
            <div className="space-y-2" data-testid="bottom-agents">
              {data.bottom_agents.length === 0 ? (
                <p className="text-muted-foreground text-sm">Sin runs.</p>
              ) : (
                data.bottom_agents.map((a) => (
                  <RateBar
                    key={a.agent_id ?? "none"}
                    label={a.agent_name ?? "(agente eliminado)"}
                    rate={a.success_rate}
                    detail={`${a.succeeded}/${a.run_count}`}
                    testid={`bottom-agent-${a.agent_id ?? "none"}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Per-agent breakdown table */}
      <Card>
        <CardContent className="pt-5">
          <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">Por agente</p>
          {data.by_agent.length === 0 ? (
            <p className="text-muted-foreground text-sm">Sin runs.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="by-agent-table">
                <thead className="text-muted-foreground text-left text-xs uppercase">
                  <tr>
                    <th className="py-2 pr-3">Agente</th>
                    <th className="py-2 pr-3">Rol</th>
                    <th className="py-2 pr-3">Runs</th>
                    <th className="py-2 pr-3">Éxito</th>
                    <th className="py-2 pr-3">Tiempo medio</th>
                    <th className="py-2 pr-3">Coste medio</th>
                    <th className="py-2 pr-3">Coste total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_agent.map((a) => (
                    <tr
                      key={a.agent_id ?? "none"}
                      className="border-border border-t"
                      data-testid={`agent-row-${a.agent_id ?? "none"}`}
                    >
                      <td className="py-2 pr-3 font-medium">
                        {a.agent_name ?? "(agente eliminado)"}
                      </td>
                      <td className="text-muted-foreground py-2 pr-3">{a.agent_role ?? "—"}</td>
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

      {/* Runs explorer */}
      <RunsExplorer windowDays={windowDays} displayCurrency={displayCurrency} />

      <p className="text-muted-foreground text-xs" data-testid="currency-note">
        Costes almacenados en {data.currency} canónico. El selector de moneda convierte cada run a
        la tasa de cambio de su propia fecha (solo visualización; el coste USD no cambia). Los
        tokens cacheados se muestran como 0 hasta que el runtime capture el recuento por llamada.
      </p>
    </div>
  );
}

export default function TenantStatsPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 md:px-8" data-testid="tenant-stats-page">
      <PageHeader
        icon={<BarChart3 className="h-5 w-5 text-white" />}
        title="Estadísticas"
        description="Cómo rinden tus agentes y qué consume tu tenant: tasa de éxito, tiempo y coste medios, agentes top/bottom, tendencia temporal, resumen de consumo y explorador de runs. Sólo tu tenant; costes en USD."
      />
      <div className="mt-6">
        <RoleGuard
          min="tenant_admin"
          fallback={
            <Card>
              <CardContent className="text-muted-foreground flex items-center gap-2 pt-5 text-sm">
                <Activity className="h-4 w-4" />
                Necesitas el rol tenant_admin para ver las estadísticas del tenant.
              </CardContent>
            </Card>
          }
        >
          <StatsBody />
        </RoleGuard>
      </div>
    </div>
  );
}
