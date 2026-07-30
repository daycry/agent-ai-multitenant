"use client";

/**
 * task_14_11 — Dashboard de CALIDAD de evals del tenant (Plan 14 Fase D).
 *
 * Vista tenant de cómo puntúan sus agentes a lo largo del tiempo, agregada
 * desde EvalRun / EvalResult (los roll-ups de Fase B). Desglose por AGENTE,
 * por RELEASE de prompt (`EvalRun.subject_prompt_version`) y por DATASET (el
 * benchmark golden por-tenant: un EvalRun está scoped a dataset, no a proyecto,
 * así que el dataset es la dimensión "por proyecto / por benchmark"). Más el
 * desglose por CRITERIO (pass-rate de cada criterio del juez) y la tendencia
 * diaria de pass-rate, e historial de runs filtrable.
 *
 * Multi-tenancy: todo es **tenant-scoped** (tenant_id + RLS); un tenant ve SÓLO
 * sus propios runs/resultados. La comparativa cross-tenant es una superficie
 * aparte, sólo para System Admin (task_14_15). Costes en USD canónico — el
 * toggle "moneda del tenant" depende del sistema FX (exchange_rates), no
 * construido (gap de alcance del Plan 11), así que no se ofrece aquí.
 *
 * Superficie (todo `tenant_admin`; `<RoleGuard min="tenant_admin">` + el backend
 * gatea con `require_tenant_admin`):
 *   GET /eval-quality/dashboard?window_days=N[&agent_id&dataset_id&prompt_version]
 *   GET /eval-quality/runs?limit&offset[&agent_id&status_filter&...]
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Gauge } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { Spinner } from "@/components/ui/spinner";
import { EvalRunResults } from "@/components/evals/eval-run-results";
import { LaunchEvalRun } from "@/components/evals/launch-eval-run";
import { ApiError, apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.eval_quality. All pass_rate are fractions
// in [0,1] serialised as decimal strings (or null when no items measured).
// ---------------------------------------------------------------------------
interface AgentBreakdown {
  subject_agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  run_count: number;
  total_items: number;
  passed_items: number;
  pass_rate: string | null;
  mean_cost_usd: string | null;
  mean_tokens: string | null;
}

interface PromptVersionBreakdown {
  subject_prompt_version: string | null;
  run_count: number;
  total_items: number;
  passed_items: number;
  pass_rate: string | null;
  mean_cost_usd: string | null;
}

interface DatasetBreakdown {
  dataset_id: string;
  dataset_name: string | null;
  run_count: number;
  total_items: number;
  passed_items: number;
  pass_rate: string | null;
}

interface CriterionBreakdown {
  criterion_id: string | null;
  criterion_name: string | null;
  scored: number;
  passed: number;
  pass_rate: string | null;
}

interface TrendPoint {
  day: string;
  run_count: number;
  total_items: number;
  passed_items: number;
  pass_rate: string | null;
}

interface QualityDashboard {
  window_days: number;
  currency: string;
  total_runs: number;
  total_items: number;
  passed_items: number;
  overall_pass_rate: string | null;
  by_agent: AgentBreakdown[];
  by_prompt_version: PromptVersionBreakdown[];
  by_dataset: DatasetBreakdown[];
  by_criterion: CriterionBreakdown[];
  trend: TrendPoint[];
}

interface RunHistoryItem {
  id: string;
  dataset_id: string;
  dataset_name: string | null;
  status: string;
  subject_agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  subject_prompt_version: string | null;
  judge_model: string | null;
  started_at: string | null;
  finished_at: string | null;
  total_items: number;
  passed_items: number;
  pass_rate: string | null;
  mean_latency_ms: string | null;
  mean_tokens: string | null;
  mean_cost_usd: string | null;
  created_at: string;
}

const WINDOW_OPTIONS = [30, 90, 365] as const;

const STATUS_BADGE: Record<string, BadgeVariant> = {
  completed: "success",
  running: "info",
  pending: "muted",
  failed: "danger",
  cancelled: "warning",
};

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/** A pass_rate decimal string -> a percentage label (or "—" when null). */
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

/** Pure-SVG sparkline of the per-day pass-rate (0..1). No heavy chart dep. */
function Sparkline({ data }: { data: TrendPoint[] }) {
  const width = 480;
  const height = 80;
  const pad = 4;
  if (data.length === 0) {
    return (
      <svg
        data-testid="quality-sparkline"
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
      const rate = d.pass_rate === null ? 0 : Number(d.pass_rate);
      const y = height - pad - rate * (height - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      data-testid="quality-sparkline"
      viewBox={`0 0 ${width} ${height}`}
      className="text-primary h-20 w-full"
      role="img"
      aria-label="Pass rate por día"
      preserveAspectRatio="none"
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={2} />
    </svg>
  );
}

/** A horizontal bar row showing a pass-rate percentage. */
function PassRateBar({
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

function DashboardBody() {
  const [windowDays, setWindowDays] = useState<number>(90);
  // Qué corrida tiene el desglose abierto. Una sola a la vez: abrir varias a
  // la vez dispara una petición por fila y el desglose se viene a mirar de uno
  // en uno.
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const dashboard = useQuery({
    queryKey: ["eval-quality-dashboard", windowDays],
    queryFn: () => apiFetch<QualityDashboard>(`/eval-quality/dashboard?window_days=${windowDays}`),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const runs = useQuery({
    queryKey: ["eval-quality-runs"],
    queryFn: () => apiFetch<RunHistoryItem[]>(`/eval-quality/runs?limit=20`),
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
        <CardContent className="text-destructive pt-5 text-sm" data-testid="quality-error">
          No se pudo cargar el dashboard: {errorText(dashboard.error)}
        </CardContent>
      </Card>
    );
  }

  const data = dashboard.data;

  return (
    <div className="space-y-6" data-testid="quality-dashboard">
      {/* La acción que faltaba: hasta `task_wf_52b` este dashboard solo sabía
          LEER corridas y no había forma de producir ninguna, así que llevaba
          desde el Plan 14 pintando un vacío permanente. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-muted-foreground text-sm">
          Una corrida mide un dataset dorado con un modelo sujeto y lo puntúa con un modelo juez
          distinto.
        </p>
        <LaunchEvalRun />
      </div>

      {/* Window selector */}
      <SegmentedControl
        label="Ventana:"
        value={windowDays}
        onChange={setWindowDays}
        options={WINDOW_OPTIONS.map((w) => ({ value: w, label: `${w}d` }))}
        getOptionTestId={(w) => `window-${w}`}
        data-testid="window-selector"
      />

      {/* Headline cards + trend */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardContent className="pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Pass rate ({data.window_days}d)
            </p>
            <p className="mt-1 text-3xl font-semibold tabular-nums" data-testid="overall-pass-rate">
              {pct(data.overall_pass_rate)}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">Runs</p>
            <p className="mt-1 text-3xl font-semibold tabular-nums" data-testid="total-runs">
              {data.total_runs}
            </p>
          </CardContent>
        </Card>
        <Card className="md:col-span-2">
          <CardContent className="pt-5">
            <p className="text-muted-foreground mb-2 text-xs uppercase tracking-wider">
              Tendencia de pass rate (diaria)
            </p>
            <Sparkline data={data.trend} />
          </CardContent>
        </Card>
      </div>

      {/* Breakdowns: by agent + by prompt version */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">Por agente</p>
            <div className="space-y-2" data-testid="by-agent">
              {data.by_agent.length === 0 ? (
                <p className="text-muted-foreground text-sm">Sin runs.</p>
              ) : (
                data.by_agent.map((a) => (
                  <PassRateBar
                    key={a.subject_agent_id ?? "none"}
                    label={a.agent_name ?? "(agente eliminado)"}
                    rate={a.pass_rate}
                    detail={`${a.passed_items}/${a.total_items}`}
                    testid={`agent-row-${a.subject_agent_id ?? "none"}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Por release de prompt
            </p>
            <div className="space-y-2" data-testid="by-prompt-version">
              {data.by_prompt_version.length === 0 ? (
                <p className="text-muted-foreground text-sm">Sin runs.</p>
              ) : (
                data.by_prompt_version.map((v) => (
                  <PassRateBar
                    key={v.subject_prompt_version ?? "none"}
                    label={v.subject_prompt_version ?? "(sin versión)"}
                    rate={v.pass_rate}
                    detail={`${v.passed_items}/${v.total_items}`}
                    testid={`version-row-${v.subject_prompt_version ?? "none"}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Breakdowns: by dataset + by criterion */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Por dataset (benchmark)
            </p>
            <div className="space-y-2" data-testid="by-dataset">
              {data.by_dataset.length === 0 ? (
                <p className="text-muted-foreground text-sm">Sin runs.</p>
              ) : (
                data.by_dataset.map((d) => (
                  <PassRateBar
                    key={d.dataset_id}
                    label={d.dataset_name ?? "(dataset eliminado)"}
                    rate={d.pass_rate}
                    detail={`${d.passed_items}/${d.total_items}`}
                    testid={`dataset-row-${d.dataset_id}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">Por criterio</p>
            <div className="space-y-2" data-testid="by-criterion">
              {data.by_criterion.length === 0 ? (
                <p className="text-muted-foreground text-sm">Sin criterios puntuados.</p>
              ) : (
                data.by_criterion.map((c) => (
                  <PassRateBar
                    key={c.criterion_id ?? "none"}
                    label={c.criterion_name ?? "(criterio eliminado)"}
                    rate={c.pass_rate}
                    detail={`${c.passed}/${c.scored}`}
                    testid={`criterion-row-${c.criterion_id ?? "none"}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Run history */}
      <Card>
        <CardContent className="pt-5">
          <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
            Historial de runs
          </p>
          {runs.isLoading ? (
            <div className="flex items-center justify-center py-6">
              <Spinner />
            </div>
          ) : runs.isError || !runs.data ? (
            <p className="text-destructive text-sm" data-testid="runs-error">
              No se pudo cargar el historial: {errorText(runs.error)}
            </p>
          ) : runs.data.length === 0 ? (
            <p className="text-muted-foreground text-sm">Sin runs recientes.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="run-history-table">
                <thead className="text-muted-foreground text-left text-xs uppercase">
                  <tr>
                    <th className="py-2 pr-3">Dataset</th>
                    <th className="py-2 pr-3">Agente</th>
                    <th className="py-2 pr-3">Release</th>
                    <th className="py-2 pr-3">Estado</th>
                    <th className="py-2 pr-3">Pass rate</th>
                    <th className="py-2 pr-3">Coste USD</th>
                    <th className="py-2 pr-3">Finalizó</th>
                    <th className="py-2 pr-3">Detalle</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.data.flatMap((r) => [
                    <tr
                      key={r.id}
                      className="border-border border-t"
                      data-testid={`run-row-${r.id}`}
                    >
                      <td className="py-2 pr-3 font-medium">{r.dataset_name ?? "—"}</td>
                      <td className="text-muted-foreground py-2 pr-3">{r.agent_name ?? "—"}</td>
                      <td className="text-muted-foreground py-2 pr-3">
                        {r.subject_prompt_version ?? "—"}
                      </td>
                      <td className="py-2 pr-3">
                        <Badge variant={STATUS_BADGE[r.status] ?? "muted"}>{r.status}</Badge>
                      </td>
                      <td className="py-2 pr-3 tabular-nums">
                        {pct(r.pass_rate)}
                        <span className="text-muted-foreground ml-1 text-xs">
                          {r.passed_items}/{r.total_items}
                        </span>
                      </td>
                      <td className="text-muted-foreground py-2 pr-3 tabular-nums">
                        {r.mean_cost_usd === null ? "—" : `$${r.mean_cost_usd}`}
                      </td>
                      <td className="text-muted-foreground whitespace-nowrap py-2 pr-3">
                        {fmtWhen(r.finished_at)}
                      </td>
                      <td className="py-2 pr-3">
                        <button
                          type="button"
                          className="text-primary text-xs underline-offset-2 hover:underline"
                          onClick={() => setExpandedRun(expandedRun === r.id ? null : r.id)}
                          data-testid={`run-results-toggle-${r.id}`}
                        >
                          {expandedRun === r.id ? "Ocultar" : "Ver items"}
                        </button>
                      </td>
                    </tr>,
                    expandedRun === r.id ? (
                      <tr key={`${r.id}-results`} className="border-border border-t">
                        <td colSpan={8} className="py-3">
                          <EvalRunResults runId={r.id} />
                        </td>
                      </tr>
                    ) : null,
                  ])}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <p className="text-muted-foreground text-xs" data-testid="currency-note">
        Costes en {data.currency} canónico. La conversión a moneda del tenant está pendiente del
        sistema FX (gap de alcance del Plan 11).
      </p>
    </div>
  );
}

export default function EvalQualityDashboardPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 md:px-8" data-testid="eval-quality-page">
      <PageHeader
        icon={<Gauge className="h-5 w-5 text-white" />}
        title="Calidad (Evals)"
        description="Cómo puntúan tus agentes a lo largo del tiempo: pass rate por agente, por release de prompt y por dataset, desglose por criterio e historial de runs. Sólo tu tenant; costes en USD."
      />
      <div className="mt-6">
        <RoleGuard
          min="tenant_admin"
          fallback={
            <Card>
              <CardContent className="text-muted-foreground flex items-center gap-2 pt-5 text-sm">
                <Activity className="h-4 w-4" />
                Necesitas el rol tenant_admin para ver el dashboard de calidad.
              </CardContent>
            </Card>
          }
        >
          <DashboardBody />
        </RoleGuard>
      </div>
    </div>
  );
}
