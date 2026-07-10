"use client";

// Ciclo de vida del plan — transiciones explícitas (draft → aprobación → ejecución).
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Plan lifecycle — explicit state transitions (draft → approval → in_progress)
//
// The lifecycle was missing its operator-facing controls: a draft could already
// sync to the Kanban (now blocked server-side) and there was no button to move a
// plan through approval or to start its execution. This action bar surfaces only
// the transition that's legal for the current status.
// --------------------------------------------------------------------------
export function PlanLifecycleSection({ planId, status }: { planId: string; status: string }) {
  const queryClient = useQueryClient();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["plan", planId] });
    queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
  };
  const onErr = (e: unknown) => setErrorMsg(e instanceof ApiError ? e.body : String(e));

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

  const canSendToApproval = status === "draft";
  const canApprove = status === "pending_approval" || status === "pending_second_approval";
  const canStart = status === "approved";
  const canUnblock = status === "blocked";
  // Action bar, not a status display: render nothing when no transition is offered.
  if (!canSendToApproval && !canApprove && !canStart && !canUnblock) return null;

  const pending =
    sendToApproval.isPending || approve.isPending || startExecution.isPending || unblock.isPending;

  return (
    <Card className="mt-6" data-testid="plan-lifecycle">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Ciclo de vida del plan</CardTitle>
        <div className="flex flex-wrap gap-2">
          {canSendToApproval ? (
            <Button
              onClick={() => sendToApproval.mutate()}
              disabled={pending}
              data-testid="plan-send-to-approval"
            >
              Enviar a aprobación
            </Button>
          ) : null}
          {canApprove ? (
            <Button
              onClick={() => approve.mutate()}
              disabled={pending}
              data-testid="plan-lifecycle-approve"
            >
              Aprobar plan
            </Button>
          ) : null}
          {canStart ? (
            <Button
              onClick={() => startExecution.mutate()}
              disabled={pending}
              data-testid="plan-start-execution"
            >
              Empezar ejecución
            </Button>
          ) : null}
          {canUnblock ? (
            <Button
              onClick={() => unblock.mutate()}
              disabled={pending}
              data-testid="plan-detail-unblock"
            >
              Desbloquear plan
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">
          {canSendToApproval
            ? "El plan está en borrador. Envíalo a aprobación para revisarlo y aprobarlo."
            : canApprove
              ? "El plan espera aprobación. Al aprobarlo podrás sincronizar sus tareas al Kanban."
              : canUnblock
                ? "El plan está bloqueado: ninguna tarea abierta puede avanzar. «Desbloquear plan» lo reactiva y re-encola todas sus tareas bloqueadas (reinicia sus reintentos)."
                : "El plan está aprobado. «Empezar ejecución» lo marca en curso y crea las tareas en el Kanban."}
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
