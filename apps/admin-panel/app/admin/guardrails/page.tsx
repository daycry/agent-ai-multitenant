"use client";

/**
 * task_11_20 — Dashboard de Guardrails del tenant (Plan 11 Fase E).
 *
 * Observabilidad de los guardrails que se disparan sobre el trabajo de
 * ESTE tenant. La tabla `guardrail_events` es **tenant-scoped** (tenant_id
 * + RLS, migración 0052): un tenant ve SÓLO sus propios eventos — nunca los
 * de otro. El detalle de cada evento está **enmascarado**: el secreto / PII
 * que disparó el guardrail nunca se persiste (el recorder lo enmascara), así
 * que esta pantalla nunca muestra el valor crudo.
 *
 * Superficie (todo `tenant_admin`; `<RoleGuard min="tenant_admin">` + el
 * backend gatea con `require_tenant_admin`):
 *   - Recuentos por tipo de guardrail y por severidad (tarjetas + barras).
 *   - Serie temporal por día (sparkline SVG puro — sin dependencia de
 *     gráficas pesada; recharts no está presente).
 *   - Tabla de eventos recientes (tipo, hook, severidad, acción, detalle
 *     enmascarado, cuándo).
 *
 * Endpoints backend (routers/guardrail_events.py):
 *   GET /guardrails/dashboard?window_days=N   — agregados + recientes
 *   GET /guardrails/events                     — lista paginada + filtros
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, ShieldAlert } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.guardrail_events.
// `detail` / `detail_payload` are MASKED — never the raw secret/PII.
// ---------------------------------------------------------------------------
type Severity = "info" | "low" | "medium" | "high" | "critical";

interface GuardrailEvent {
  id: string;
  tenant_id: string;
  guardrail_type: string;
  hook_point: string;
  severity: string;
  action: string | null;
  project_id: string | null;
  agent_id: string | null;
  execution_id: string | null;
  agent_label: string | null;
  detail: string;
  detail_payload: Record<string, unknown>;
  created_at: string;
}

interface TypeCount {
  guardrail_type: string;
  count: number;
}
interface SeverityCount {
  severity: string;
  count: number;
}
interface DayCount {
  day: string;
  count: number;
}

interface GuardrailDashboard {
  total: number;
  window_days: number;
  by_type: TypeCount[];
  by_severity: SeverityCount[];
  by_day: DayCount[];
  recent: GuardrailEvent[];
}

const WINDOW_OPTIONS = [7, 30, 90] as const;

const SEVERITY_BADGE: Record<string, BadgeVariant> = {
  info: "muted",
  low: "info",
  medium: "primary",
  high: "warning",
  critical: "danger",
};

// Severity render order (most severe first) for the breakdown.
const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];

const ACTION_LABEL: Record<string, string> = {
  block: "bloquear",
  redact: "enmascarar",
  warn: "avisar",
  retry_with_feedback: "reintentar",
  escalate_to_human: "escalar",
  transform: "transformar",
};

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/**
 * Pure-SVG sparkline of the per-day event counts. No heavy chart dep
 * (recharts is not present). Returns a flat baseline when there is no data.
 */
function Sparkline({ data }: { data: DayCount[] }) {
  const width = 480;
  const height = 80;
  const pad = 4;
  if (data.length === 0) {
    return (
      <svg
        data-testid="guardrails-sparkline"
        viewBox={`0 0 ${width} ${height}`}
        className="text-muted-foreground/40 h-20 w-full"
        role="img"
        aria-label="Sin eventos en la ventana"
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
  const max = Math.max(...data.map((d) => d.count), 1);
  const n = data.length;
  const stepX = n > 1 ? (width - 2 * pad) / (n - 1) : 0;
  const points = data
    .map((d, i) => {
      const x = pad + i * stepX;
      const y = height - pad - (d.count / max) * (height - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      data-testid="guardrails-sparkline"
      viewBox={`0 0 ${width} ${height}`}
      className="text-primary h-20 w-full"
      role="img"
      aria-label="Eventos por día"
      preserveAspectRatio="none"
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={2} />
    </svg>
  );
}

/** A horizontal bar row for a count breakdown. */
function BarRow({
  label,
  count,
  max,
  badge,
  testid,
}: {
  label: string;
  count: number;
  max: number;
  badge?: BadgeVariant;
  testid?: string;
}) {
  const pct = max > 0 ? Math.round((count / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3" data-testid={testid}>
      <div className="w-32 shrink-0 truncate text-sm">
        {badge ? <Badge variant={badge}>{label}</Badge> : label}
      </div>
      <div className="bg-muted relative h-2 flex-1 overflow-hidden rounded-full">
        <div
          className="bg-primary absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="w-10 shrink-0 text-right text-sm tabular-nums">{count}</div>
    </div>
  );
}

function DashboardBody() {
  const errorText = useErrorText();
  const [windowDays, setWindowDays] = useState<number>(30);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["guardrails-dashboard", windowDays],
    queryFn: () => apiFetch<GuardrailDashboard>(`/guardrails/dashboard?window_days=${windowDays}`),
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <Card>
        <CardContent className="text-destructive pt-5 text-sm" data-testid="guardrails-error">
          No se pudo cargar el dashboard: {errorText(error)}
        </CardContent>
      </Card>
    );
  }

  const maxType = Math.max(...data.by_type.map((t) => t.count), 1);
  const maxSev = Math.max(...data.by_severity.map((s) => s.count), 1);
  const sevByKey = new Map(data.by_severity.map((s) => [s.severity, s.count]));

  return (
    <div className="space-y-6" data-testid="guardrails-dashboard">
      {/* Window selector */}
      <SegmentedControl
        label="Ventana:"
        value={windowDays}
        onChange={setWindowDays}
        options={WINDOW_OPTIONS.map((w) => ({ value: w, label: `${w}d` }))}
        getOptionTestId={(w) => `window-${w}`}
        data-testid="window-selector"
      />

      {/* Totals + trend */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardContent className="pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Eventos ({data.window_days}d)
            </p>
            <p className="mt-1 text-3xl font-semibold tabular-nums" data-testid="total-count">
              {data.total}
            </p>
          </CardContent>
        </Card>
        <Card className="md:col-span-2">
          <CardContent className="pt-5">
            <p className="text-muted-foreground mb-2 text-xs uppercase tracking-wider">
              Tendencia diaria
            </p>
            <Sparkline data={data.by_day} />
          </CardContent>
        </Card>
      </div>

      {/* Breakdowns */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">Por tipo</p>
            <div className="space-y-2" data-testid="by-type">
              {data.by_type.length === 0 ? (
                <p className="text-muted-foreground text-sm">Sin eventos.</p>
              ) : (
                data.by_type.map((t) => (
                  <BarRow
                    key={t.guardrail_type}
                    label={t.guardrail_type}
                    count={t.count}
                    max={maxType}
                    testid={`type-row-${t.guardrail_type}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">Por severidad</p>
            <div className="space-y-2" data-testid="by-severity">
              {data.total === 0 ? (
                <p className="text-muted-foreground text-sm">Sin eventos.</p>
              ) : (
                SEVERITY_ORDER.filter((s) => sevByKey.has(s)).map((s) => (
                  <BarRow
                    key={s}
                    label={s}
                    count={sevByKey.get(s) ?? 0}
                    max={maxSev}
                    badge={SEVERITY_BADGE[s]}
                    testid={`severity-row-${s}`}
                  />
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Recent events */}
      <Card>
        <CardContent className="pt-5">
          <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
            Eventos recientes
          </p>
          {data.recent.length === 0 ? (
            <p className="text-muted-foreground text-sm">Sin eventos recientes.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="recent-events-table">
                <thead className="text-muted-foreground text-left text-xs uppercase">
                  <tr>
                    <th className="py-2 pr-3">Tipo</th>
                    <th className="py-2 pr-3">Hook</th>
                    <th className="py-2 pr-3">Severidad</th>
                    <th className="py-2 pr-3">Acción</th>
                    <th className="py-2 pr-3">Detalle (enmascarado)</th>
                    <th className="py-2 pr-3">Cuándo</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((e) => (
                    <tr
                      key={e.id}
                      className="border-border border-t"
                      data-testid={`event-row-${e.id}`}
                    >
                      <td className="py-2 pr-3 font-medium">{e.guardrail_type}</td>
                      <td className="text-muted-foreground py-2 pr-3">{e.hook_point}</td>
                      <td className="py-2 pr-3">
                        <Badge variant={SEVERITY_BADGE[e.severity] ?? "muted"}>{e.severity}</Badge>
                      </td>
                      <td className="text-muted-foreground py-2 pr-3">
                        {e.action ? (ACTION_LABEL[e.action] ?? e.action) : "—"}
                      </td>
                      <td className="text-muted-foreground max-w-md truncate py-2 pr-3">
                        {e.detail || "—"}
                      </td>
                      <td className="text-muted-foreground whitespace-nowrap py-2 pr-3">
                        {fmtWhen(e.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function GuardrailsDashboardPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 md:px-8" data-testid="guardrails-page">
      <PageHeader
        icon={<ShieldAlert className="h-5 w-5 text-white" />}
        title="Guardrails"
        description="Eventos de guardrails sobre el trabajo de tu tenant. El detalle está enmascarado: el secreto / PII que disparó el guardrail nunca se almacena."
      />
      <div className="mt-6">
        <RoleGuard
          min="tenant_admin"
          fallback={
            <Card>
              <CardContent className="text-muted-foreground flex items-center gap-2 pt-5 text-sm">
                <Activity className="h-4 w-4" />
                Necesitas el rol tenant_admin para ver el dashboard de guardrails.
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
