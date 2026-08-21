"use client";

/**
 * task_02_20 + task_02_22 — Execution Timeline.
 *
 * Loads one execution (`GET /executions/{id}`) and renders its
 * steps_log as a hierarchical, expandable timeline with the cost and
 * timing of every step. A WebSocket (`/ws/executions/{id}`) tails the
 * run live: streamed step events append to the same timeline, so a
 * run in progress fills in before your eyes.
 */

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, ChevronDown, ChevronRight } from "lucide-react";

import { ReplayBar } from "@/components/executions/replay-bar";
import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ExecutionGuidance } from "@/components/executions/execution-guidance";
import { PromoteToDataset } from "@/components/evals/promote-to-dataset";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";
import { runStatusLabel, runStatusVariant } from "@/lib/runs";
import { useWebSocket, wsUrl } from "@/lib/ws";
import { useErrorText } from "@/lib/use-error-text";

// --------------------------------------------------------------------------
// Types
// --------------------------------------------------------------------------
interface Step {
  index: number;
  kind: string;
  node: string;
  status: string;
  summary: string;
  started_at?: string;
  ended_at?: string;
  model?: string;
  tokens_in?: number;
  tokens_out?: number;
  total_tokens?: number;
  cost_usd?: number;
  tool?: string;
  args?: unknown;
  result?: unknown;
  // G5 (ADR 0103): contadores de nudges/trips que el step de finalize adjunta.
  safeguard_stats?: Record<string, number>;
  budgets?: Record<string, number>;
  [key: string]: unknown;
}

interface Execution {
  id: string;
  task_id: string;
  status: string;
  abort_code: string | null;
  output: string | null;
  // ADR 0087: the agent's self-reported finish status (hint), or null.
  finish_status: string | null;
  steps_log: Step[];
  iterations: number;
  total_tokens: number;
  total_cost_usd: number;
}

// --------------------------------------------------------------------------
// Visual mappings
// --------------------------------------------------------------------------
// The execution status → badge variant/label mapping is centralized in
// `lib/runs` (`runStatusVariant`/`runStatusLabel`) so the Runs list and this
// detail header agree (F49/F50: includes `awaiting_human_approval`,
// `cancelled` and `needs_human_review`). Step statuses (`ok`/`error`/…) reuse
// the same variant mapping.

// ADR 0087: the agent's self-reported finish status (success|failed|partial) — a HINT,
// distinct from the execution status. Spanish labels for the badge.
const FINISH_STATUS_VARIANT: Record<string, BadgeVariant> = {
  success: "success",
  partial: "warning",
  failed: "danger",
};
const FINISH_STATUS_LABEL: Record<string, string> = {
  success: "Exitoso",
  partial: "Parcial",
  failed: "Fallido",
};

const KIND_VARIANT: Record<string, BadgeVariant> = {
  node: "muted",
  model_call: "primary",
  tool_call: "info",
  memory_read: "warning",
};

function fmtCost(value: number | undefined): string {
  if (!value) return "—";
  return `$${value.toFixed(4)}`;
}

function fmtDuration(step: Step): string {
  if (!step.started_at || !step.ended_at) return "—";
  const ms = new Date(step.ended_at).getTime() - new Date(step.started_at).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ExecutionTimelinePage() {
  const errorText = useErrorText();
  const params = useParams<{ id: string }>();
  const executionId = String(params.id);
  const [liveSteps, setLiveSteps] = useState<Step[]>([]);
  // Replay (ADR 0119): null = timeline normal; un número = índice activo.
  const [replayIndex, setReplayIndex] = useState<number | null>(null);

  const executionQuery = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => apiFetch<Execution>(`/executions/${executionId}`),
    refetchOnWindowFocus: false,
    // F48: poll while the run is in progress so the header/output/metrics
    // converge even if a live WebSocket frame is missed (mirrors runs/page).
    // Stops once the status is terminal.
    refetchInterval: (query) => (query.state.data?.status === "running" ? 5000 : false),
  });

  // prod-06 cancel_01: cooperative cancellation of a running execution. POSTs the
  // cancel flag (the worker polls it to kill the container + finalise as
  // `cancelled`) and refreshes the row.
  const queryClient = useQueryClient();
  const cancelMutation = useMutation<unknown, ApiError>({
    mutationFn: () => apiFetch(`/executions/${executionId}/cancel`, { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["execution", executionId] });
    },
  });

  const onWsMessage = useCallback(
    (data: unknown) => {
      const frame = data as { type?: string; payload?: unknown };
      const payload = frame?.payload;
      // F47: the worker republishes a step as `payload={"step":{…}}`, so the
      // index lives at `payload.step.index`, not at the payload root — peel the
      // wrapper (falling back to the bare payload for older/raw frames).
      const rawStep =
        payload && typeof payload === "object" && "step" in payload
          ? (payload as { step?: unknown }).step
          : payload;
      if (rawStep && typeof rawStep === "object" && "index" in rawStep) {
        setLiveSteps((prev) => [...prev, rawStep as Step]);
        return;
      }
      // F48: terminal frames carry no `index`. `execution.finished`
      // ({result:{…}}) and `execution.error` ({error}) mark the end of the run
      // — refetch so the header, output and finish_status reflect the persisted
      // terminal state (and the running-only polling stops).
      if (frame?.type === "execution.finished" || frame?.type === "execution.error") {
        void queryClient.invalidateQueries({ queryKey: ["execution", executionId] });
      }
    },
    [queryClient, executionId],
  );

  const streamUrl = useMemo(() => wsUrl(`/ws/executions/${executionId}`), [executionId]);
  useWebSocket(streamUrl, onWsMessage);

  // Persisted steps first, then the live tail — deduped by index so a
  // streamed step that is later also persisted is not shown twice.
  const steps = useMemo(() => {
    const byIndex = new Map<number, Step>();
    for (const step of executionQuery.data?.steps_log ?? []) byIndex.set(step.index, step);
    for (const step of liveSteps) byIndex.set(step.index, step);
    return [...byIndex.values()].sort((a, b) => a.index - b.index);
  }, [executionQuery.data, liveSteps]);

  const execution = executionQuery.data;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <Link
        href="/admin/runs"
        data-testid="execution-back-link"
        className="text-muted-foreground hover:text-foreground mb-4 inline-flex items-center gap-1.5 text-sm transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Volver a Runs
      </Link>
      <PageHeader
        icon={<Activity className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Timeline de ejecución"
        description="Paso a paso de la ejecución del agente, con coste y tiempo por paso. Se actualiza en vivo."
      />

      {executionQuery.isLoading && (
        <p className="text-muted-foreground text-sm">Cargando ejecución…</p>
      )}

      {executionQuery.isError && (
        <Card className="border-destructive p-4" data-testid="execution-error">
          <p className="text-destructive text-sm">
            No se pudo cargar la ejecución: {errorText(executionQuery.error)}
          </p>
        </Card>
      )}

      {execution && (
        <>
          <div className="mb-4 flex justify-end gap-2" data-testid="execution-actions">
            {execution.status === "running" && (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => {
                  if (window.confirm("¿Cancelar esta ejecución en curso?")) {
                    cancelMutation.mutate();
                  }
                }}
                disabled={cancelMutation.isPending}
                data-testid="execution-cancel-button"
              >
                {cancelMutation.isPending ? "Cancelando…" : "Cancelar ejecución"}
              </Button>
            )}
            {execution.status === "running" && <ExecutionGuidance executionId={execution.id} />}
            <PromoteToDataset taskId={execution.task_id} executionId={execution.id} />
          </div>
          <ExecutionSummary execution={execution} liveCount={liveSteps.length} />

          {execution.output && (
            <Card className="mt-4" data-testid="execution-output">
              <CardContent className="py-4">
                <h2 className="text-muted-foreground mb-2 text-xs font-medium uppercase tracking-wide">
                  Resultado de la ejecución
                </h2>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-sm">
                  {execution.output}
                </pre>
              </CardContent>
            </Card>
          )}

          {/* Replay (ADR 0119): reproduce el steps_log en el tiempo — los
              pasos van apareciendo hasta el índice activo del scrubber. */}
          <div className="mt-6 flex items-center justify-between">
            <Button
              size="sm"
              variant={replayIndex === null ? "outline" : "default"}
              data-testid="replay-toggle"
              onClick={() => setReplayIndex(replayIndex === null ? 0 : null)}
              disabled={steps.length === 0}
            >
              🎬 {replayIndex === null ? "Replay" : "Salir del replay"}
            </Button>
          </div>
          {replayIndex !== null && (
            <div className="mt-2">
              <ReplayBar total={steps.length} index={replayIndex} onIndexChange={setReplayIndex} />
            </div>
          )}
          <ol className="mt-4 space-y-2" data-testid="execution-timeline">
            {steps.length === 0 && (
              <li className="text-muted-foreground text-sm italic" data-testid="timeline-empty">
                Esta ejecución todavía no tiene pasos registrados.
              </li>
            )}
            {(replayIndex === null ? steps : steps.slice(0, replayIndex + 1)).map((step) => (
              <TimelineStep key={step.index} step={step} />
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Summary
// --------------------------------------------------------------------------
function ExecutionSummary({ execution, liveCount }: { execution: Execution; liveCount: number }) {
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-x-8 gap-y-3 py-4">
        <Metric label="Estado">
          <Badge variant={runStatusVariant(execution.status)} data-testid="execution-status">
            {execution.abort_code
              ? `${runStatusLabel(execution.status)} · ${execution.abort_code}`
              : runStatusLabel(execution.status)}
          </Badge>
        </Metric>
        {execution.finish_status && (
          <Metric label="Resultado del agente">
            <Badge
              variant={FINISH_STATUS_VARIANT[execution.finish_status] ?? "muted"}
              data-testid="execution-finish-status"
            >
              {FINISH_STATUS_LABEL[execution.finish_status] ?? execution.finish_status}
            </Badge>
          </Metric>
        )}
        <Metric label="Iteraciones">
          <span data-testid="execution-iterations">
            {execution.iterations}
            <BudgetRemaining
              steps={execution.steps_log}
              cap="max_iterations"
              used={execution.iterations}
              testId="execution-iterations-left"
            />
          </span>
        </Metric>
        <Metric label="Tokens">
          <span data-testid="execution-tokens">
            {execution.total_tokens.toLocaleString()}
            <BudgetRemaining
              steps={execution.steps_log}
              cap="max_tokens"
              used={execution.total_tokens}
              testId="execution-tokens-left"
            />
          </span>
        </Metric>
        <Metric label="Coste">
          <span data-testid="execution-cost">{fmtCost(execution.total_cost_usd)}</span>
        </Metric>
        {liveCount > 0 && (
          <Metric label="En vivo">
            <Badge variant="success" data-testid="timeline-live-count">
              +{liveCount}
            </Badge>
          </Metric>
        )}
        <SafeguardStats steps={execution.steps_log} />
      </CardContent>
    </Card>
  );
}

// G11 (plan guardas-research): lo que QUEDA de presupuesto, no solo lo gastado.
// El aviso vivía únicamente dentro del prompt —el modelo lo veía y el operador
// no—, y un contador sin techo contra el que compararlo no dice nada: 12
// iteraciones tranquilizan si el tope es 50 y son una urgencia si es 15.
//
// El sobre lo adjunta el runtime al PRIMER step (`budgets`), así que está
// disponible desde la iteración 1 y es el que ESTE run recibió — recalcularlo
// aquí mostraría el presupuesto configurado hoy y mentiría sobre runs pasados.
function BudgetRemaining({
  steps,
  cap,
  used,
  testId,
}: {
  steps: Step[];
  cap: string;
  used: number;
  testId: string;
}) {
  const budgets = steps.find((s) => s.budgets && Object.keys(s.budgets).length > 0)?.budgets;
  const limit = budgets?.[cap];
  // Runs anteriores a G11 no traen el sobre: se calla en vez de inventar un
  // techo, que es lo único peor que no mostrarlo.
  if (!limit || limit <= 0) return null;
  const left = Math.max(0, limit - used);
  const tight = left <= limit * 0.2;
  return (
    <span
      className={`ml-1 text-xs font-normal ${tight ? "text-warning-soft-foreground" : "text-muted-foreground"}`}
      data-testid={testId}
      title={`Tope de este run: ${limit.toLocaleString()}`}
    >
      · quedan {left.toLocaleString()}
    </span>
  );
}

// G5 (ADR 0103): expone en la cabecera los safeguard_stats que el step de
// finalize adjunta — qué nudges/trips dispararon y cuántas veces. Antes solo
// vivían en steps_log (consultables por SQL) y el visor no los mostraba.
function SafeguardStats({ steps }: { steps: Step[] }) {
  const stats = steps.find(
    (s) => s.safeguard_stats && Object.keys(s.safeguard_stats).length > 0,
  )?.safeguard_stats;
  if (!stats) return null;
  const entries = Object.entries(stats).sort(([a], [b]) => a.localeCompare(b));
  return (
    <Metric label="Salvaguardas">
      <span data-testid="execution-safeguards" className="flex flex-wrap gap-1">
        {entries.map(([kind, count]) => (
          <Badge key={kind} variant={kind.startsWith("trip:") ? "danger" : "muted"}>
            {kind} ×{count}
          </Badge>
        ))}
      </span>
    </Metric>
  );
}

function Metric({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-muted-foreground text-xs uppercase tracking-wide">{label}</span>
      <span className="text-sm font-medium tabular-nums">{children}</span>
    </div>
  );
}

// --------------------------------------------------------------------------
// Step row (expandable)
// --------------------------------------------------------------------------
function TimelineStep({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  const isAction = step.kind !== "node";

  return (
    <li
      data-testid={`timeline-step-${step.index}`}
      data-kind={step.kind}
      className={cn("border-l-2 pl-3", isAction ? "border-primary/40 ml-4" : "border-border")}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid={`step-toggle-${step.index}`}
        className="hover:bg-muted/50 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors"
      >
        {open ? (
          <ChevronDown className="text-muted-foreground h-4 w-4 shrink-0" />
        ) : (
          <ChevronRight className="text-muted-foreground h-4 w-4 shrink-0" />
        )}
        <Badge variant={KIND_VARIANT[step.kind] ?? "muted"}>{step.kind}</Badge>
        <span className="text-muted-foreground text-xs">{step.node}</span>
        <span className="flex-1 truncate text-sm">{step.summary}</span>
        {step.kind === "model_call" && (
          <span
            className="text-muted-foreground text-xs tabular-nums"
            data-testid={`step-cost-${step.index}`}
          >
            {fmtCost(step.cost_usd)}
          </span>
        )}
        <span
          className="text-muted-foreground text-xs tabular-nums"
          data-testid={`step-duration-${step.index}`}
        >
          {fmtDuration(step)}
        </span>
        <Badge variant={runStatusVariant(step.status)}>{step.status}</Badge>
      </button>

      {open && (
        <div
          data-testid={`step-detail-${step.index}`}
          className="text-muted-foreground ml-6 mt-1 space-y-1 rounded-md bg-muted/40 p-3 text-xs"
        >
          {step.kind === "model_call" && (
            <p>
              Modelo <code>{step.model ?? "—"}</code> · {step.tokens_in ?? 0} in /{" "}
              {step.tokens_out ?? 0} out · {step.total_tokens ?? 0} tokens ·{" "}
              {fmtCost(step.cost_usd)}
            </p>
          )}
          {step.kind === "tool_call" && (
            <pre className="overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify({ tool: step.tool, args: step.args, result: step.result }, null, 2)}
            </pre>
          )}
          {step.started_at && <p>Inicio: {step.started_at}</p>}
        </div>
      )}
    </li>
  );
}
