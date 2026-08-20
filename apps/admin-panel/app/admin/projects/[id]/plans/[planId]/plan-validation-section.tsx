"use client";

// Validación humana del plan (sesión de review + app-preview).
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ClipboardList, ExternalLink, Rocket, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { numberLocale } from "./plan-spec-types";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";

// --------------------------------------------------------------------------
// Human validation — preview access to the running app (ADR 0062)
// --------------------------------------------------------------------------
// Exportada: CorrectionsSection (plan-corrections-section) consume la misma
// sesión de review para leer el rejection_reason (ADR 0107).
export interface ReviewSessionInfo {
  session_id: string;
  status: string;
  verdict: string | null;
  rejection_reason: string | null;
  expires_at: string | null;
  review_url: string;
  app_url: string;
  verdict_url: string;
}

export function HumanValidationSection({ planId, status }: { planId: string; status: string }) {
  const t = useT("planDetail");
  const lang = useLangOptional();
  const queryClient = useQueryClient();
  const [verdictMsg, setVerdictMsg] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reviewQuery = useQuery({
    queryKey: ["plan-review-session", planId],
    queryFn: () => apiFetch<ReviewSessionInfo>(`/plans/${planId}/review-session`),
    enabled: status === "pending_human_validation",
    retry: false,
    refetchOnWindowFocus: false,
  });

  if (status !== "pending_human_validation") return null;

  const rs = reviewQuery.data;

  // El motivo del rechazo ES el feedback que reciben los agentes en el rework
  // — antes iba un texto fijo y la intención del validador se perdía. La modal
  // usa MarkdownTextarea (preferencia del operador: todo textarea con preview).
  const submitVerdict = async (verdict: "approved" | "rejected", reason = "") => {
    if (!rs?.verdict_url) return;
    const rejectionReason = reason.trim() || t("rejectDefaultReason");
    setSubmitting(true);
    setVerdictMsg(null);
    try {
      const body =
        verdict === "rejected" ? { verdict, rejection_reason: rejectionReason } : { verdict };
      const res = await fetch(rs.verdict_url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setVerdictMsg(
        res.ok
          ? verdict === "approved"
            ? t("validationMsgApproved")
            : t("validationMsgRejected")
          : t("validationMsgError"),
      );
      queryClient.invalidateQueries({ queryKey: ["plan", planId] });
      queryClient.invalidateQueries({ queryKey: ["plan-review-session", planId] });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="border-warning/40 mt-6" data-testid="plan-human-validation">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Rocket className="text-primary h-5 w-5" />
          {t("validationTitle")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-muted-foreground text-sm">
          {t("validationIntroBefore")} <code>pending_human_validation</code>
          {t("validationIntroMiddle")} <b>{t("validationIntroStrong")}</b>
          {t("validationIntroAfter")}
        </p>

        {reviewQuery.isLoading && (
          <p className="text-muted-foreground text-sm">{t("validationSearching")}</p>
        )}
        {reviewQuery.isError && (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid="plan-human-validation-none"
          >
            {t("validationNone")}
          </p>
        )}

        {rs && (
          <>
            <div className="flex flex-wrap gap-3">
              <a
                href={rs.app_url}
                target="_blank"
                rel="noreferrer"
                data-testid="plan-open-app"
                className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-semibold"
              >
                <Rocket className="h-4 w-4" />
                {t("validationOpenApp")}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
              <a
                href={rs.review_url}
                target="_blank"
                rel="noreferrer"
                data-testid="plan-open-review-console"
                className="hover:bg-muted/40 inline-flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-semibold"
              >
                <ClipboardList className="h-4 w-4" />
                {t("validationOpenConsole")}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>

            <p className="text-muted-foreground text-xs">
              {t("validationProxyNote", {
                date: rs.expires_at
                  ? new Date(rs.expires_at).toLocaleString(numberLocale(lang))
                  : "—",
              })}
            </p>

            <div className="flex items-center gap-3 border-t pt-4">
              <Button
                onClick={() => void submitVerdict("approved")}
                disabled={submitting || !!rs.verdict}
                data-testid="plan-verdict-approve"
              >
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
                {t("lifecycleApprove")}
              </Button>
              <Button
                variant="outline"
                onClick={() => setRejectOpen(true)}
                disabled={submitting || !!rs.verdict}
                data-testid="plan-verdict-reject"
              >
                <XCircle className="mr-1.5 h-4 w-4" />
                {t("validationReject")}
              </Button>
              {verdictMsg && (
                <span className="text-sm" data-testid="plan-verdict-msg">
                  {verdictMsg}
                </span>
              )}
              {rs.verdict && (
                <Badge variant={rs.verdict === "approved" ? "success" : "danger"}>
                  {rs.verdict === "approved"
                    ? t("validationVerdictApproved")
                    : t("validationVerdictRejected")}
                </Badge>
              )}
            </div>

            <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
              <DialogContent data-testid="plan-reject-dialog">
                <DialogHeader>
                  <DialogTitle>{t("rejectDialogTitle")}</DialogTitle>
                </DialogHeader>
                <DialogBody className="space-y-3">
                  <p className="text-muted-foreground text-sm">{t("rejectDialogHelp")}</p>
                  <MarkdownTextarea
                    value={rejectReason}
                    onChange={setRejectReason}
                    placeholder={t("rejectPlaceholder")}
                    rows={6}
                    data-testid="plan-reject-reason"
                  />
                </DialogBody>
                <DialogFooter>
                  <Button
                    variant="outline"
                    onClick={() => setRejectOpen(false)}
                    disabled={submitting}
                  >
                    {t("cancel")}
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={submitting}
                    data-testid="plan-reject-confirm"
                    onClick={() => {
                      setRejectOpen(false);
                      void submitVerdict("rejected", rejectReason);
                    }}
                  >
                    <XCircle className="mr-1.5 h-4 w-4" />
                    {t("rejectDialogTitle")}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </>
        )}
      </CardContent>
    </Card>
  );
}
