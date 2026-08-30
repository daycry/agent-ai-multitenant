"use client";

// Correcciones del rechazo (ADR 0107): generar, seleccionar y aceptar al MISMO plan.
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { criterionText } from "@/lib/acceptance-criteria";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { useErrorText } from "@/lib/use-error-text";
import type { PlanResponse, PlanSpecification, PlanTaskSpec } from "./plan-spec-types";
import type { ReviewSessionInfo } from "./plan-validation-section";

// --------------------------------------------------------------------------
// Correcciones del rechazo (ADR 0107) — el motivo del veredicto rechazado se
// convierte en tareas correctivas del MISMO plan; aceptarlas las sincroniza
// al Kanban y reactiva el plan (rejected → in_progress).
// --------------------------------------------------------------------------
interface GenerateCorrectionsResponse {
  session_id: string;
  reason: string;
  task_ids: string[];
  tasks: PlanTaskSpec[];
  already_generated: boolean;
}

export function CorrectionsSection({
  planId,
  status,
  spec,
}: {
  planId: string;
  status: string;
  spec: PlanSpecification;
}) {
  const errorText = useErrorText();
  const t = useT("planDetail");
  const queryClient = useQueryClient();
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [emptyGeneration, setEmptyGeneration] = useState(false);

  const isRejected = status === "rejected";
  const corrections = spec.corrections ?? [];
  const proposed = corrections.filter((c) => c.status === "proposed");
  const accepted = corrections.filter((c) => c.status === "accepted");

  // El motivo vive en la sesión de review rechazada; una vez generada la
  // tanda también queda copiado en la entrada de corrections del spec.
  const sessionQuery = useQuery({
    queryKey: ["plan-review-session", planId],
    queryFn: () => apiFetch<ReviewSessionInfo>(`/plans/${planId}/review-session`),
    enabled: isRejected,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["plan", planId] });
    queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
  };
  const onErr = (e: unknown) => setErrorMsg(errorText(e));

  const generate = useMutation({
    mutationFn: () =>
      apiFetch<GenerateCorrectionsResponse>(`/plans/${planId}/generate-corrections`, {
        method: "POST",
      }),
    onSuccess: (res) => {
      setErrorMsg(null);
      setEmptyGeneration(res.task_ids.length === 0);
      invalidate();
    },
    onError: onErr,
  });

  const tasksById = new Map((spec.tasks ?? []).map((t) => [t.id, t]));
  const proposedIds = proposed.flatMap((c) => c.task_ids ?? []);
  const selectedIds = proposedIds.filter((id) => !unchecked.has(id));

  const accept = useMutation({
    mutationFn: () =>
      apiFetch<PlanResponse>(`/plans/${planId}/accept-corrections`, {
        method: "POST",
        body: { task_ids: selectedIds },
      }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });

  // Solo aparece en un plan rechazado (flujo vivo) o con historial de
  // correcciones (lectura tras la aceptación).
  if (!isRejected && corrections.length === 0) return null;

  const reason =
    sessionQuery.data?.rejection_reason ??
    proposed[0]?.reason ??
    accepted[accepted.length - 1]?.reason ??
    null;

  const toggle = (id: string) => {
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Card className="border-destructive/40 mt-6" data-testid="plan-corrections">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <XCircle className="text-destructive h-5 w-5" />
          {t("correctionsTitle")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {reason ? (
          <div
            className="bg-muted/30 rounded-md border p-3 text-sm"
            data-testid="plan-corrections-reason"
          >
            <p className="text-muted-foreground mb-1 text-xs font-semibold uppercase">
              {t("correctionsReasonLabel")}
            </p>
            {renderPlanDraft(reason)}
          </div>
        ) : isRejected && sessionQuery.isError ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid="plan-corrections-no-reason"
          >
            {t("correctionsNoReason")}
          </p>
        ) : null}

        {isRejected && proposed.length === 0 && !sessionQuery.isError ? (
          <div className="space-y-2">
            <p className="text-muted-foreground text-sm">{t("correctionsGenerateHelp")}</p>
            <Button
              onClick={() => generate.mutate()}
              disabled={generate.isPending || !reason}
              data-testid="plan-corrections-generate"
            >
              {generate.isPending ? t("correctionsGenerating") : t("correctionsGenerate")}
            </Button>
            {emptyGeneration ? (
              <p className="text-destructive text-xs" data-testid="plan-corrections-empty">
                {t("correctionsEmptyGeneration")}
              </p>
            ) : null}
          </div>
        ) : null}

        {proposed.length > 0 ? (
          <div className="space-y-3">
            <p className="text-muted-foreground text-sm">{t("correctionsProposedHelp")}</p>
            <ul className="space-y-2">
              {proposedIds.map((id) => {
                const task = tasksById.get(id);
                if (!task) return null;
                return (
                  <li
                    key={id}
                    className="flex items-start gap-3 rounded-md border p-3"
                    data-testid={`plan-correction-task-${id}`}
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={!unchecked.has(id)}
                      onChange={() => toggle(id)}
                      data-testid={`plan-correction-check-${id}`}
                    />
                    <div className="flex-1 text-sm">
                      <p className="font-medium">
                        <span className="text-muted-foreground mr-1.5 font-mono text-xs">{id}</span>
                        {task.title}
                      </p>
                      {task.description ? (
                        <p className="text-muted-foreground mt-0.5 text-xs">{task.description}</p>
                      ) : null}
                      <p className="text-muted-foreground mt-1 text-xs">
                        {task.role ? <>{t("correctionsMetaRole", { role: task.role })} · </> : null}
                        {t("correctionsMetaComplexity", { value: task.complexity ?? "m" })}
                        {task.depends_on && task.depends_on.length > 0 ? (
                          <>
                            {" · "}
                            {t("correctionsMetaDependsOn", { ids: task.depends_on.join(", ") })}
                          </>
                        ) : null}
                      </p>
                      {task.acceptance_criteria && task.acceptance_criteria.length > 0 ? (
                        <ul className="mt-1 list-disc pl-5 text-xs">
                          {/* `criterionText` y no `{c}`: desde el ADR 0162 un
                              criterio puede ser un diccionario que declara con
                              qué se verifica, y React tumba el árbol entero
                              ante un objeto («Objects are not valid as a React
                              child») — con él, la tarjeta de correcciones de un
                              plan rechazado dejaba de pintarse. */}
                          {task.acceptance_criteria.map((c, i) => (
                            <li key={i}>{criterionText(c)}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
            {isRejected ? (
              <Button
                onClick={() => accept.mutate()}
                disabled={accept.isPending || selectedIds.length === 0}
                data-testid="plan-corrections-accept"
              >
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
                {accept.isPending
                  ? t("correctionsAccepting")
                  : t("correctionsAccept", { count: selectedIds.length })}
              </Button>
            ) : null}
          </div>
        ) : null}

        {accepted.length > 0 ? (
          <div className="space-y-1" data-testid="plan-corrections-accepted">
            {accepted.map((entry, i) => (
              <p key={i} className="text-muted-foreground flex items-center gap-2 text-xs">
                <Badge variant="success">{t("correctionsAcceptedBadge")}</Badge>
                {(entry.accepted_task_ids ?? entry.task_ids ?? []).join(", ")}{" "}
                {t("correctionsAcceptedTail")}
              </p>
            ))}
          </div>
        ) : null}

        {errorMsg ? (
          <p className="text-destructive text-xs" data-testid="plan-corrections-error">
            {errorMsg}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
