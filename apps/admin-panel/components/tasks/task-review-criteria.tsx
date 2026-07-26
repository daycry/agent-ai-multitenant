"use client";

/**
 * El veredicto del reviewer, criterio a criterio (`task_wf_61`).
 *
 * El reviewer emitía prosa con UN «criterio que falló». El humano que abría una
 * tarea rechazada no sabía qué se había comprobado, cuáles pasaron ni por qué
 * falló el que falló: tenía la conclusión sin el razonamiento.
 *
 * Se lee del historial de auditoría de la tarea, que ya existía: el desglose
 * viaja en el `review_comment` que el reviewer ya escribía, no en una tabla
 * nueva. Si el reviewer no lo emitió (modelo que se lo saltó, run anterior a
 * esto), la sección no se pinta — nada que enseñar es mejor que un hueco vacío.
 */

import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api";

interface CriterionOutcome {
  text: string;
  passed: boolean;
  evidence?: string;
}

interface AuditEvent {
  id: string;
  at: number;
  kind: string;
  actor: string | null;
  payload: Record<string, unknown> | null;
}

/** El desglose del review MÁS RECIENTE, o `null`.
 *
 * Se busca del final hacia atrás porque una tarea puede haber sido revisada
 * varias veces: enseñar el desglose de un rechazo ya corregido sería peor que
 * no enseñar ninguno. */
export function latestReviewCriteria(events: readonly AuditEvent[]): CriterionOutcome[] | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (event.kind !== "review_comment") continue;
    const raw = event.payload?.["criteria"];
    if (!Array.isArray(raw) || raw.length === 0) continue;
    return raw
      .filter((c): c is Record<string, unknown> => !!c && typeof c === "object")
      .map((c) => ({
        text: String(c["text"] ?? ""),
        passed: Boolean(c["passed"]),
        evidence: typeof c["evidence"] === "string" ? c["evidence"] : undefined,
      }))
      .filter((c) => c.text);
  }
  return null;
}

export function TaskReviewCriteria({ taskId }: { taskId: string }) {
  const historyQuery = useQuery({
    queryKey: ["task-history", taskId],
    queryFn: () => apiFetch<{ events: AuditEvent[] }>(`/tasks/${taskId}/history`),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const criteria = latestReviewCriteria(historyQuery.data?.events ?? []);
  if (!criteria || criteria.length === 0) return null;

  const failed = criteria.filter((c) => !c.passed).length;

  return (
    <section className="mb-4" data-testid="task-review-criteria">
      <div className="mb-1 flex items-center gap-2">
        <h4 className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
          Veredicto del reviewer
        </h4>
        <Badge variant={failed > 0 ? "danger" : "success"}>
          {failed > 0
            ? `${failed} de ${criteria.length} sin cumplir`
            : `${criteria.length} criterios cumplidos`}
        </Badge>
      </div>
      <ul className="space-y-1.5">
        {criteria.map((criterion, index) => (
          <li key={index} className="flex gap-2 text-sm" data-testid={`review-criterion-${index}`}>
            {criterion.passed ? (
              <Check className="text-success mt-0.5 h-4 w-4 shrink-0" aria-label="cumplido" />
            ) : (
              <X className="text-destructive mt-0.5 h-4 w-4 shrink-0" aria-label="sin cumplir" />
            )}
            <span>
              {criterion.text}
              {criterion.evidence ? (
                <span className="text-muted-foreground block text-xs">{criterion.evidence}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
