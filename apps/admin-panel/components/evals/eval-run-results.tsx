"use client";

/**
 * El desglose por item de una corrida (`task_wf_52b`).
 *
 * Las filas `eval_results` se escribían desde el Plan 14 y **ninguna pantalla
 * las leía**: el historial mostraba «pass rate 60 %» y ahí acababa todo. Un
 * 60 % sin desglose dice que algo va mal y no deja arreglarlo — no sabes qué
 * item falló, con qué salida ni por qué lo suspendió el juez.
 *
 * Los fallos salen primero (el backend ordena por puntuación ascendente): es lo
 * que se viene a mirar cuando una corrida baja de nota.
 *
 * Backend: GET /eval-runs/{id}/results
 */

import { useQuery } from "@tanstack/react-query";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

export interface CriterionScore {
  name?: string;
  score?: number | string | null;
  passed?: boolean;
  rationale?: string | null;
  [key: string]: unknown;
}

export interface EvalResult {
  id: string;
  run_id: string;
  item_id: string | null;
  produced_output: string | null;
  criterion_scores: CriterionScore[];
  verdict: string;
  overall_score: string | null;
  latency_ms: number | null;
  tokens: number | null;
  cost_usd: string | null;
  created_at: string;
}

const VERDICT_BADGE: Record<string, BadgeVariant> = {
  pass: "success",
  fail: "danger",
  error: "warning",
};

/** `"0.750"` → `"75%"`; null → `"—"`. */
export function formatScore(score: string | number | null | undefined): string {
  if (score === null || score === undefined || score === "") return "—";
  const value = typeof score === "number" ? score : Number.parseFloat(score);
  if (Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

/** Recorta la salida producida para la tabla sin ocultar que hay más. */
export function truncateOutput(text: string | null, max = 160): string {
  if (!text) return "—";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= max ? flat : `${flat.slice(0, max)}…`;
}

export function EvalRunResults({ runId }: { runId: string }) {
  const errorText = useErrorText();
  const query = useQuery({
    queryKey: ["eval-run-results", runId],
    queryFn: () => apiFetch<EvalResult[]>(`/eval-runs/${runId}/results`),
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (query.isLoading) {
    return (
      <div className="flex justify-center py-6" data-testid="eval-run-results-loading">
        <Spinner />
      </div>
    );
  }

  if (query.isError) {
    return (
      <p className="text-destructive py-3 text-sm" data-testid="eval-run-results-error">
        No se pudo cargar el desglose: {errorText(query.error)}
      </p>
    );
  }

  const results = query.data ?? [];
  if (results.length === 0) {
    return (
      <p className="text-muted-foreground py-3 text-sm" data-testid="eval-run-results-empty">
        Esta corrida no juzgó ningún item.
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="eval-run-results">
      {results.map((result) => (
        <div
          key={result.id}
          className="border-border rounded-md border p-3"
          data-testid={`eval-result-${result.id}`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={VERDICT_BADGE[result.verdict] ?? "muted"}>{result.verdict}</Badge>
            <span className="text-sm font-medium tabular-nums">
              {formatScore(result.overall_score)}
            </span>
            <span className="text-muted-foreground text-xs tabular-nums">
              {result.tokens ?? 0} tokens
              {result.latency_ms === null ? "" : ` · ${result.latency_ms} ms`}
            </span>
          </div>

          <p className="text-muted-foreground mt-2 text-xs">Salida del sujeto</p>
          <p className="mt-0.5 break-words text-sm" data-testid={`eval-result-output-${result.id}`}>
            {truncateOutput(result.produced_output)}
          </p>

          {result.criterion_scores.length > 0 ? (
            <ul className="mt-2 space-y-1">
              {result.criterion_scores.map((criterion, index) => (
                <li
                  key={`${result.id}-${criterion.name ?? index}`}
                  className="text-muted-foreground flex flex-wrap gap-x-2 text-xs"
                >
                  <span className="text-foreground font-medium">
                    {criterion.name ?? "criterio"}
                  </span>
                  <span className="tabular-nums">{formatScore(criterion.score ?? null)}</span>
                  {criterion.rationale ? <span>— {criterion.rationale}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </div>
  );
}
