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
    const rejectionReason = reason.trim() || "Rechazado desde el panel de validación (sin motivo).";
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
            ? "Plan aprobado ✓"
            : "Plan rechazado"
          : "Error al registrar el veredicto",
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
          Validación humana — probar la app
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-muted-foreground text-sm">
          El plan está en <code>pending_human_validation</code>: los agentes han terminado y la
          aplicación se ha <b>levantado en un contenedor de revisión</b>. Ábrela para probarla y, si
          todo está bien, aprueba el plan.
        </p>

        {reviewQuery.isLoading && (
          <p className="text-muted-foreground text-sm">Buscando la sesión de revisión…</p>
        )}
        {reviewQuery.isError && (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid="plan-human-validation-none"
          >
            Aún no hay una sesión de revisión levantada para este plan.
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
                Abrir app para probar
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
                Consola de revisión (terminal + logs + checklist)
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>

            <p className="text-muted-foreground text-xs">
              El enlace abre la app servida por el review-runtime a través del proxy firmado del
              api-server (no se publica ningún puerto). La sesión caduca el{" "}
              {rs.expires_at ? new Date(rs.expires_at).toLocaleString("es-ES") : "—"}.
            </p>

            <div className="flex items-center gap-3 border-t pt-4">
              <Button
                onClick={() => void submitVerdict("approved")}
                disabled={submitting || !!rs.verdict}
                data-testid="plan-verdict-approve"
              >
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
                Aprobar plan
              </Button>
              <Button
                variant="outline"
                onClick={() => setRejectOpen(true)}
                disabled={submitting || !!rs.verdict}
                data-testid="plan-verdict-reject"
              >
                <XCircle className="mr-1.5 h-4 w-4" />
                Rechazar
              </Button>
              {verdictMsg && (
                <span className="text-sm" data-testid="plan-verdict-msg">
                  {verdictMsg}
                </span>
              )}
              {rs.verdict && (
                <Badge variant={rs.verdict === "approved" ? "success" : "danger"}>
                  {rs.verdict === "approved" ? "Aprobado" : "Rechazado"}
                </Badge>
              )}
            </div>

            <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
              <DialogContent data-testid="plan-reject-dialog">
                <DialogHeader>
                  <DialogTitle>Rechazar plan</DialogTitle>
                </DialogHeader>
                <DialogBody className="space-y-3">
                  <p className="text-muted-foreground text-sm">
                    El motivo llega a los agentes como feedback del rework — cuanto más concreto
                    (qué está mal, dónde y qué se espera), mejor corrige el equipo. Tras rechazar
                    podrás generar tareas correctivas desde el motivo y aceptarlas en este mismo
                    plan.
                  </p>
                  <MarkdownTextarea
                    value={rejectReason}
                    onChange={setRejectReason}
                    placeholder="P. ej.: El filtro de Content-Type application/json es global; debe acotarse al grupo api/v1…"
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
                    Cancelar
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
                    Rechazar plan
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
