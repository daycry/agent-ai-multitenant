"use client";

// Ciclo de vida del plan — transiciones explícitas (draft → aprobación → ejecución).
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

// --------------------------------------------------------------------------
// Plan lifecycle — explicit state transitions (draft → approval → in_progress)
//
// The lifecycle was missing its operator-facing controls: a draft could already
// sync to the Kanban (now blocked server-side) and there was no button to move a
// plan through approval or to start its execution. This action bar surfaces only
// the transition that's legal for the current status.
// --------------------------------------------------------------------------
/**
 * Qué transiciones ofrece la barra para un estado dado.
 *
 * Exportado y puro para poder fijarlo con un test: la regla que importa es que
 * «Aprobar y arrancar» (task_wf_41) SOLO aparece desde `pending_approval`. En
 * `pending_second_approval` no aplica —falta la segunda firma, y arrancar no es
 * cosa del primer firmante—, y ofrecerlo ahí insinuaría que el atajo se salta
 * la doble firma, que es justo lo que el backend impide.
 */
export function lifecycleActions(status: string) {
  return {
    canSendToApproval: status === "draft",
    canApprove: status === "pending_approval" || status === "pending_second_approval",
    canApproveAndStart: status === "pending_approval",
    canStart: status === "approved",
    canUnblock: status === "blocked",
  };
}

export function PlanLifecycleSection({ planId, status }: { planId: string; status: string }) {
  const errorText = useErrorText();
  const t = useT("planDetail");
  const queryClient = useQueryClient();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["plan", planId] });
    queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
  };
  const onErr = (e: unknown) => setErrorMsg(errorText(e));

  const sendToApproval = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/plans/${planId}`, {
        method: "PUT",
        body: { status: "pending_approval" },
      }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });
  const approve = useMutation({
    mutationFn: () => apiFetch<{ status: string }>(`/plans/${planId}/approve`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });
  // task_wf_41: aprobar y arrancar en un gesto. El backend lleva el gate
  // estricto y NO se salta la doble firma — si el plan la necesita, deja la
  // primera y devuelve `pending_second_approval`, así que la UI vuelve a
  // ofrecer «Aprobar plan» para el segundo firmante sin más lógica aquí.
  const approveAndStart = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/plans/${planId}/approve-and-start`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });
  const startExecution = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/plans/${planId}/start-execution`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });
  // hallazgo #3 (QA 2026-07-07): el desbloqueo solo existía en /plans/{id}/escalated
  // y el operador no lo encontró desde el detalle. Misma mutación que allí.
  const unblock = useMutation({
    mutationFn: () => apiFetch<{ status: string }>(`/plans/${planId}/unblock`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidate();
    },
    onError: onErr,
  });

  const { canSendToApproval, canApprove, canApproveAndStart, canStart, canUnblock } =
    lifecycleActions(status);
  // Action bar, not a status display: render nothing when no transition is offered.
  if (!canSendToApproval && !canApprove && !canStart && !canUnblock) return null;

  const pending =
    sendToApproval.isPending ||
    approve.isPending ||
    approveAndStart.isPending ||
    startExecution.isPending ||
    unblock.isPending;

  return (
    <Card className="mt-6" data-testid="plan-lifecycle">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{t("lifecycleTitle")}</CardTitle>
        <div className="flex flex-wrap gap-2">
          {canSendToApproval ? (
            <Button
              onClick={() => sendToApproval.mutate()}
              disabled={pending}
              data-testid="plan-send-to-approval"
            >
              {t("lifecycleSendToApproval")}
            </Button>
          ) : null}
          {canApprove ? (
            <Button
              onClick={() => approve.mutate()}
              disabled={pending}
              variant={canApproveAndStart ? "outline" : "default"}
              data-testid="plan-lifecycle-approve"
            >
              {t("lifecycleApprove")}
            </Button>
          ) : null}
          {canApproveAndStart ? (
            <Button
              onClick={() => approveAndStart.mutate()}
              disabled={pending}
              data-testid="plan-approve-and-start"
            >
              {t("lifecycleApproveAndStart")}
            </Button>
          ) : null}
          {canStart ? (
            <Button
              onClick={() => startExecution.mutate()}
              disabled={pending}
              data-testid="plan-start-execution"
            >
              {t("lifecycleStart")}
            </Button>
          ) : null}
          {canUnblock ? (
            <Button
              onClick={() => unblock.mutate()}
              disabled={pending}
              data-testid="plan-detail-unblock"
            >
              {t("lifecycleUnblock")}
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">
          {canSendToApproval
            ? t("lifecycleHelpDraft")
            : canApprove
              ? canApproveAndStart
                ? t("lifecycleHelpApproveAndStart")
                : t("lifecycleHelpSecondSignature")
              : canUnblock
                ? t("lifecycleHelpBlocked")
                : t("lifecycleHelpApproved")}
        </p>
        {errorMsg ? (
          <p className="text-destructive mt-2 text-xs" data-testid="plan-lifecycle-error">
            {errorMsg}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
