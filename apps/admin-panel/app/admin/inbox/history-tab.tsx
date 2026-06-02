"use client";

/**
 * Pestaña "Histórico" de la bandeja personal (Plan 16 task_16_10).
 *
 * Muestra, para el usuario logueado:
 *   - sus métricas de performance personales (tiempo medio de aceptación,
 *     tiempo medio de ejecución, % de tareas aprobadas a la primera, horas
 *     medias registradas) — alimentan las estimaciones futuras del PM agente;
 *   - sus tareas pasadas: las HumanWorkSessions cerradas que entregó, con la
 *     tarea, proyecto, plan, ventana de trabajo, horas y nota de salida.
 *
 * Permisos: el backend es la fuente de verdad. Ambos endpoints están
 * estrictamente scoped al propio usuario (user_id == principal) + RLS por
 * tenant, así que un usuario solo ve SU histórico y SUS métricas.
 *
 * Endpoints (routers/human_inbox.py):
 *   GET /inbox/metrics
 *   GET /inbox/history
 */

import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock,
  FolderKanban,
  Gauge,
  History as HistoryIcon,
  Hourglass,
  Timer,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.human_inbox
// ---------------------------------------------------------------------------
interface InboxMetrics {
  tasks_worked: number;
  work_sessions_completed: number;
  assignments_accepted: number;
  mean_acceptance_time_seconds: number | null;
  mean_execution_time_seconds: number | null;
  first_try_approval_rate: number | null;
  mean_hours_logged: number | null;
}

interface InboxHistoryEntry {
  work_session_id: string;
  task_id: string;
  task_title: string;
  task_status: string;
  project_id: string;
  project_name: string | null;
  plan_id: string | null;
  plan_title: string | null;
  start_at: string;
  end_at: string | null;
  hours_logged: string | null;
  comments: string | null;
  attachments_count: number;
}

const NO_DATA = "Sin datos aún";

function apiErrorBody(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

/** Format a duration in seconds as a compact human string ("2 h 30 min"). */
function formatDuration(seconds: number | null): string {
  if (seconds === null) return NO_DATA;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 24) return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours ? `${days} d ${remHours} h` : `${days} d`;
}

/** Format a 0..1 rate as a percentage ("75 %"). */
function formatRate(rate: number | null): string {
  if (rate === null) return NO_DATA;
  return `${Math.round(rate * 100)} %`;
}

/** Format optional mean logged hours ("3.0 h"). */
function formatHours(hours: number | null): string {
  if (hours === null) return NO_DATA;
  return `${hours.toFixed(1)} h`;
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// One metric tile
// ---------------------------------------------------------------------------
function MetricCard({
  icon,
  label,
  value,
  hint,
  testid,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  testid: string;
}) {
  return (
    <Card data-testid={testid}>
      <CardContent className="flex flex-col gap-1 p-4">
        <div className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
          {icon}
          <span>{label}</span>
        </div>
        <p className="text-2xl font-semibold tracking-tight">{value}</p>
        {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// One past-task (work session) row
// ---------------------------------------------------------------------------
function HistoryCard({ item }: { item: InboxHistoryEntry }) {
  return (
    <Card data-testid={`history-entry-${item.work_session_id}`}>
      <CardContent className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="truncate text-sm font-semibold" title={item.task_title}>
            {item.task_title}
          </h3>
          {item.hours_logged && (
            <Badge variant="muted" data-testid={`history-hours-${item.work_session_id}`}>
              {Number(item.hours_logged).toFixed(2)} h
            </Badge>
          )}
        </div>

        <dl className="flex flex-col gap-1.5 text-xs">
          <div className="flex items-center gap-2">
            <FolderKanban className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
            <dd>
              {item.project_name ?? "Proyecto"}
              {item.plan_title ? (
                <span className="text-muted-foreground"> · {item.plan_title}</span>
              ) : null}
            </dd>
          </div>
          <div className="flex items-center gap-2">
            <Clock className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
            <dd className="text-muted-foreground">Entregada: {formatDateTime(item.end_at)}</dd>
          </div>
        </dl>

        {item.comments && (
          <p className="text-muted-foreground line-clamp-3 whitespace-pre-wrap text-xs">
            {item.comments}
          </p>
        )}

        {item.attachments_count > 0 && (
          <p className="text-muted-foreground text-xs">
            {item.attachments_count} {item.attachments_count === 1 ? "adjunto" : "adjuntos"}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// History tab
// ---------------------------------------------------------------------------
export function HistoryTab() {
  const metricsQuery = useQuery({
    queryKey: ["inbox", "metrics"],
    queryFn: () => apiFetch<InboxMetrics>("/inbox/metrics"),
    refetchOnWindowFocus: false,
  });
  const historyQuery = useQuery({
    queryKey: ["inbox", "history"],
    queryFn: () => apiFetch<InboxHistoryEntry[]>("/inbox/history"),
    refetchOnWindowFocus: false,
  });

  const metrics = metricsQuery.data;
  const history = historyQuery.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      {/* ---- Métricas personales ---- */}
      <section>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <Gauge className="h-4 w-4" />
          Mis métricas
        </h2>

        {metricsQuery.isLoading && (
          <p className="text-muted-foreground text-sm" data-testid="metrics-loading">
            Calculando tus métricas…
          </p>
        )}
        {metricsQuery.isError && (
          <Card className="border-destructive p-4" data-testid="metrics-error">
            <p className="text-destructive text-sm">
              No se pudieron cargar tus métricas: {apiErrorBody(metricsQuery.error)}
            </p>
          </Card>
        )}
        {metrics && (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4" data-testid="metrics-grid">
            <MetricCard
              testid="metric-acceptance"
              icon={<Hourglass className="h-3.5 w-3.5" />}
              label="Tiempo medio de aceptación"
              value={formatDuration(metrics.mean_acceptance_time_seconds)}
              hint={`${metrics.assignments_accepted} aceptadas`}
            />
            <MetricCard
              testid="metric-execution"
              icon={<Timer className="h-3.5 w-3.5" />}
              label="Tiempo medio de ejecución"
              value={formatDuration(metrics.mean_execution_time_seconds)}
              hint={`${metrics.work_sessions_completed} sesiones`}
            />
            <MetricCard
              testid="metric-first-try"
              icon={<CheckCircle2 className="h-3.5 w-3.5" />}
              label="Aprobadas a la primera"
              value={formatRate(metrics.first_try_approval_rate)}
              hint={`${metrics.tasks_worked} tareas`}
            />
            <MetricCard
              testid="metric-hours"
              icon={<Clock className="h-3.5 w-3.5" />}
              label="Horas medias registradas"
              value={formatHours(metrics.mean_hours_logged)}
            />
          </div>
        )}
      </section>

      {/* ---- Tareas pasadas ---- */}
      <section>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <HistoryIcon className="h-4 w-4" />
          Tareas pasadas
        </h2>

        {historyQuery.isLoading && (
          <p className="text-muted-foreground text-sm" data-testid="history-loading">
            Cargando tu histórico…
          </p>
        )}
        {historyQuery.isError && (
          <Card className="border-destructive p-4" data-testid="history-error">
            <p className="text-destructive text-sm">
              No se pudo cargar tu histórico: {apiErrorBody(historyQuery.error)}
            </p>
          </Card>
        )}
        {!historyQuery.isLoading && !historyQuery.isError && history.length === 0 && (
          <Card className="p-8" data-testid="history-empty">
            <div className="text-muted-foreground flex flex-col items-center gap-2 text-center text-sm">
              <HistoryIcon className="h-8 w-8 opacity-50" />
              <p>Todavía no has entregado ninguna tarea.</p>
              <p className="text-xs">
                Cuando completes una tarea humana, aparecerá aquí con sus métricas.
              </p>
            </div>
          </Card>
        )}
        {history.length > 0 && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2" data-testid="history-grid">
            {history.map((item) => (
              <HistoryCard key={item.work_session_id} item={item} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
