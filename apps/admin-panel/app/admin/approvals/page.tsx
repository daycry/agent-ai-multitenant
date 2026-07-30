"use client";

/**
 * task_02_25 + task_02_26 — Human-approval queue.
 *
 * The in-app notification feed for human_approval_policy: every pending
 * approval request a reviewer must act on. Each card carries
 * Approve / Reject buttons and an optional reason; resolving one
 * (`POST /approvals/{id}/resolve`) drops it from the list.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ShieldCheck, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ApiError, apiFetch } from "@/lib/api";

interface ApprovalRequest {
  id: string;
  execution_id: string;
  task_id: string;
  project_id: string;
  category: string;
  action: Record<string, unknown>;
  status: string;
  requested_at: string;
}

export default function ApprovalsPage() {
  const query = useQuery({
    queryKey: ["approvals"],
    queryFn: () => apiFetch<ApprovalRequest[]>("/approvals"),
    refetchOnWindowFocus: false,
  });

  const pending = query.data ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<ShieldCheck className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Aprobaciones"
        description="Solicitudes de aprobación humana pendientes. Aprueba o rechaza para que la ejecución continúe."
      />

      <div className="mb-4 flex items-center gap-2">
        <span className="text-sm font-medium">Pendientes</span>
        <Badge variant={pending.length > 0 ? "warning" : "muted"} data-testid="approval-count">
          {pending.length}
        </Badge>
      </div>

      <StateBlock
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={Boolean(query.data) && pending.length === 0}
        loadingLabel="Cargando solicitudes…"
        errorTitle="No se pudieron cargar las solicitudes"
        errorTestId="approvals-error"
        emptyIcon={ShieldCheck}
        emptyTitle="Sin aprobaciones pendientes"
        emptyDescription="No hay solicitudes de aprobación pendientes."
        emptyTestId="approvals-empty"
      >
        <ul className="space-y-3" data-testid="approvals-list">
          {pending.map((request) => (
            <li key={request.id}>
              <ApprovalCard request={request} />
            </li>
          ))}
        </ul>
      </StateBlock>
    </div>
  );
}

function ApprovalCard({ request }: { request: ApprovalRequest }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  // ADR 0114: una pregunta del agente (ask_human) se presenta como pregunta —
  // el texto y las opciones sugeridas en primer plano, y «Aprobar» pasa a ser
  // «Responder» (la respuesta viaja en `reason` y guía el siguiente intento).
  const isQuestion = request.category === "human_question";
  const questionArgs = (request.action as { args?: Record<string, unknown> })?.args ?? {};
  const questionText = isQuestion ? String(questionArgs.question ?? "") : "";
  const questionOptions =
    isQuestion && Array.isArray(questionArgs.options)
      ? (questionArgs.options as unknown[]).map((o) => String(o))
      : [];

  const resolve = useMutation({
    mutationFn: (approved: boolean) =>
      apiFetch(`/approvals/${request.id}/resolve`, {
        method: "POST",
        body: { approved, reason: reason.trim() || null },
      }),
    onSuccess: async () => {
      setError(null);
      // The resolved request drops off the pending feed. Await the
      // invalidation so a refetch failure surfaces here rather than as
      // an unhandled rejection (frontend-admin-panel-7).
      await queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.body : String(err)),
  });

  return (
    <Card data-testid={`approval-card-${request.id}`}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base" data-testid={`approval-category-${request.id}`}>
            {request.category}
          </CardTitle>
          <Badge variant="warning">{request.status}</Badge>
        </div>
        <p className="text-muted-foreground text-xs">
          Solicitada {new Date(request.requested_at).toLocaleString()}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {isQuestion ? (
          <div className="space-y-2" data-testid={`question-${request.id}`}>
            <p className="text-sm font-medium">{questionText}</p>
            {questionOptions.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {questionOptions.map((opt) => (
                  <Badge key={opt} variant="muted">
                    {opt}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        ) : (
          <pre className="bg-muted/40 overflow-x-auto rounded-md p-2 text-xs">
            {JSON.stringify(request.action, null, 2)}
          </pre>
        )}

        <MarkdownTextarea
          data-testid={`reason-${request.id}`}
          value={reason}
          onChange={setReason}
          placeholder={isQuestion ? "Tu respuesta para el agente" : "Motivo (opcional)"}
          rows={2}
        />

        {error && (
          <p className="text-destructive text-xs" data-testid={`approval-error-${request.id}`}>
            {error}
          </p>
        )}

        <div className="flex gap-2">
          <Button
            data-testid={`approve-${request.id}`}
            disabled={resolve.isPending || (isQuestion && !reason.trim())}
            onClick={() => resolve.mutate(true)}
          >
            <Check className="mr-1.5 h-4 w-4" />
            {isQuestion ? "Responder" : "Aprobar"}
          </Button>
          <Button
            variant="outline"
            data-testid={`reject-${request.id}`}
            disabled={resolve.isPending}
            onClick={() => resolve.mutate(false)}
            className="text-danger-soft-foreground hover:bg-danger-soft"
          >
            <X className="mr-1.5 h-4 w-4" />
            Rechazar
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
