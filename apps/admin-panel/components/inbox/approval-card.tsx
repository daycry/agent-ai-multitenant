"use client";

/**
 * Tarjeta de una solicitud de `GET /approvals` (task_02_25/26 + ADR 0114).
 *
 * Una solicitud de acción sensible se aprueba o rechaza con motivo opcional; una
 * pregunta del agente (`category === "human_question"`, tool `ask_human`) se
 * presenta como pregunta y «Aprobar» pasa a ser «Responder» (la respuesta viaja
 * en `reason` y guía el siguiente intento). Ambas se resuelven con
 * `POST /approvals/{id}/resolve`.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";

import { PriorApprovalsNotice } from "@/app/admin/approvals/prior-approvals-notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

export interface ApprovalRequest {
  id: string;
  execution_id: string;
  task_id: string;
  project_id: string;
  category: string;
  action: Record<string, unknown>;
  status: string;
  requested_at: string;
}

/** Las preguntas `ask_human` viajan como aprobaciones de esta categoría. */
export const QUESTION_CATEGORY = "human_question";

export function ApprovalCard({ request }: { request: ApprovalRequest }) {
  const t = useT("inboxHub");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const isQuestion = request.category === QUESTION_CATEGORY;
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
      // La solicitud resuelta sale de la lista. Se espera a la invalidación para
      // que un fallo del refetch aflore aquí y no como rechazo sin capturar.
      await queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (err) => setError(errorText(err)),
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
          {t("requestedAt", { date: new Date(request.requested_at).toLocaleString() })}
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
          <>
            {/* ADR 0135 (N3): si es un «casi igual» de algo ya aprobado, el delta primero. */}
            <PriorApprovalsNotice action={request.action} />
            <pre className="bg-muted/40 overflow-x-auto rounded-md p-2 text-xs">
              {JSON.stringify(request.action, null, 2)}
            </pre>
          </>
        )}

        <MarkdownTextarea
          data-testid={`reason-${request.id}`}
          value={reason}
          onChange={setReason}
          placeholder={isQuestion ? t("answerPlaceholder") : t("reasonPlaceholder")}
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
            {isQuestion ? t("answer") : t("approve")}
          </Button>
          <Button
            variant="outline"
            data-testid={`reject-${request.id}`}
            disabled={resolve.isPending}
            onClick={() => resolve.mutate(false)}
            className="text-danger-soft-foreground hover:bg-danger-soft"
          >
            <X className="mr-1.5 h-4 w-4" />
            {t("reject")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
