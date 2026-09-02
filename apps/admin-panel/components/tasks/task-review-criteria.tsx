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
import { useT } from "@/lib/i18n";

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

/** La escalada del review MÁS RECIENTE, o `null` (`task_cv_41`, auditoría
 * 2026-09-01 C-05).
 *
 * Las tres escaladas del bucle con reviewer IA —commit perdido, tercer
 * rechazo, run del reviewer muerto— dejaban la tarea en `blocked` con un
 * `review_comment` `escalated: true` que el panel ignoraba (sólo pintaba los
 * criterios). Sólo cuenta si el ÚLTIMO `review_comment` es la escalada: una
 * escalada ya resuelta por un review posterior no se enseña. */
export function latestReviewEscalation(
  events: readonly AuditEvent[],
): { reason: string; abortCode: string | null } | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (event.kind !== "review_comment") continue;
    if (event.payload?.["escalated"] !== true) return null;
    const reason = event.payload?.["reason"];
    const abortCode = event.payload?.["abort_code"];
    return {
      reason: typeof reason === "string" && reason ? reason : "escalated",
      abortCode: typeof abortCode === "string" && abortCode ? abortCode : null,
    };
  }
  return null;
}

export function TaskReviewCriteria({ taskId }: { taskId: string }) {
  const t = useT("taskDetail");
  const historyQuery = useQuery({
    queryKey: ["task-history", taskId],
    queryFn: () => apiFetch<{ events: AuditEvent[] }>(`/tasks/${taskId}/history`),
    refetchOnWindowFocus: false,
    retry: false,
  });
  const events = historyQuery.data?.events ?? [];
  const criteria = latestReviewCriteria(events);
  const escalation = latestReviewEscalation(events);
  const hasCriteria = !!criteria && criteria.length > 0;
  if (!hasCriteria && !escalation) return null;
  const failed = hasCriteria ? criteria.filter((c) => !c.passed).length : 0;
  return (
    <section className="mb-4" data-testid="task-review-criteria">
      {escalation ? (
        <p
          className="text-destructive mb-2 text-sm"
          data-testid="task-review-escalated"
          role="alert"
        >
          {t("reviewEscalated", { reason: escalation.reason })}
          {escalation.abortCode ? (
            <span className="text-muted-foreground block text-xs">{escalation.abortCode}</span>
          ) : null}
        </p>
      ) : null}
      {hasCriteria ? (
        <>
          <div className="mb-1 flex items-center gap-2">
            <h4 className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
              {t("reviewHeading")}
            </h4>
            <Badge variant={failed > 0 ? "danger" : "success"}>
              {failed > 0
                ? t("reviewFailed", { failed, total: criteria.length })
                : t("reviewAllPassed", { total: criteria.length })}
            </Badge>
          </div>
          <ul className="space-y-1.5">
            {criteria.map((criterion, index) => (
              <li
                key={index}
                className="flex gap-2 text-sm"
                data-testid={`review-criterion-${index}`}
              >
                {criterion.passed ? (
                  <Check
                    className="text-success mt-0.5 h-4 w-4 shrink-0"
                    aria-label={t("reviewPassedIcon")}
                  />
                ) : (
                  <X
                    className="text-destructive mt-0.5 h-4 w-4 shrink-0"
                    aria-label={t("reviewFailedIcon")}
                  />
                )}
                <span>
                  {criterion.text}
                  {criterion.evidence ? (
                    <span className="text-muted-foreground block text-xs">
                      {criterion.evidence}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
