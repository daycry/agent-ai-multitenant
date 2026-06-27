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

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PromoteToDataset } from "@/components/evals/promote-to-dataset";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";
import { useWebSocket, wsUrl } from "@/lib/ws";

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
  [key: string]: unknown;
}

interface Execution {
  id: string;
  task_id: string;
  status: string;
  abort_code: string | null;
  output: string | null;
  steps_log: Step[];
  iterations: number;
  total_tokens: number;
  total_cost_usd: number;
}

// --------------------------------------------------------------------------
// Visual mappings
// --------------------------------------------------------------------------
// Orden por workflow: running (activo) → ok/done (terminal success) →
// aborted (cancelado por humano) → error/failed (fallo).
const STATUS_VARIANT: Record<string, BadgeVariant> = {
  running: "info",
  ok: "success",
  done: "success",
  aborted: "warning",
  error: "danger",
  failed: "danger",
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
  const params = useParams<{ id: string }>();
  const executionId = String(params.id);
  const [liveSteps, setLiveSteps] = useState<Step[]>([]);

  const executionQuery = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => apiFetch<Execution>(`/executions/${executionId}`),
    refetchOnWindowFocus: false,
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

  const onWsMessage = useCallback((data: unknown) => {
    const frame = data as { payload?: unknown };
    const payload = frame?.payload;
    if (payload && typeof payload === "object" && "index" in payload) {
      setLiveSteps((prev) => [...prev, payload as Step]);
    }
  }, []);

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
            No se pudo cargar la ejecución:{" "}
            {executionQuery.error instanceof ApiError
              ? executionQuery.error.body
              : String(executionQuery.error)}
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
            <PromoteToDataset taskId={execution.task_id} executionId={execution.id} />
          </div>
          <ExecutionSummary execution={execution} liveCount={liveSteps.length} />

          <ol className="mt-6 space-y-2" data-testid="execution-timeline">
            {steps.length === 0 && (
              <li className="text-muted-foreground text-sm italic" data-testid="timeline-empty">
                Esta ejecución todavía no tiene pasos registrados.
              </li>
            )}
            {steps.map((step) => (
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
          <Badge
            variant={STATUS_VARIANT[execution.status] ?? "muted"}
            data-testid="execution-status"
          >
            {execution.abort_code
              ? `${execution.status} · ${execution.abort_code}`
              : execution.status}
          </Badge>
        </Metric>
        <Metric label="Iteraciones">
          <span data-testid="execution-iterations">{execution.iterations}</span>
        </Metric>
        <Metric label="Tokens">
          <span data-testid="execution-tokens">{execution.total_tokens.toLocaleString()}</span>
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
      </CardContent>
    </Card>
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
        <Badge variant={STATUS_VARIANT[step.status] ?? "muted"}>{step.status}</Badge>
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
