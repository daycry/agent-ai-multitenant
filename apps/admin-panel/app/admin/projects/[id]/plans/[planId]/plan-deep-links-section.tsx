"use client";

// Deep-links del plan (rama git, PR, Kanban filtrado).
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import Link from "next/link";
import { AlertTriangle, ClipboardList, ExternalLink } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// --------------------------------------------------------------------------
// Deep links to per-plan panels (Plan 06.6 task_06_6_12)
// --------------------------------------------------------------------------

export function PlanDeepLinksSection({ planId, status }: { planId: string; status: string }) {
  const inValidation = status === "pending_human_validation";
  return (
    <Card className="mt-6" data-testid="plan-deep-links">
      <CardHeader>
        <CardTitle>Paneles del plan</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Link
            href={`/admin/plans/${planId}/escalated`}
            data-testid="plan-link-escalated"
            className="hover:border-primary/40 hover:bg-muted/30 flex items-start gap-3 rounded-md border p-3 transition-colors"
          >
            <div className="bg-warning-soft text-warning-soft-foreground flex h-10 w-10 shrink-0 items-center justify-center rounded-md">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-1.5 text-sm font-semibold">
                Tareas escaladas y bloqueadas
                <ExternalLink className="text-muted-foreground h-3.5 w-3.5" />
              </div>
              <p className="text-muted-foreground mt-0.5 text-xs">
                Tareas esperando una acción humana (aprobar, reintentar, desbloquear) — incluye las
                bloqueadas por reintentos agotados y el desbloqueo del plan.
              </p>
            </div>
          </Link>

          {inValidation && (
            <Link
              href={`/admin/review/active?plan=${planId}`}
              data-testid="plan-link-review"
              className="hover:border-primary/40 hover:bg-muted/30 flex items-start gap-3 rounded-md border p-3 transition-colors"
            >
              <div className="bg-info-soft text-info-soft-foreground flex h-10 w-10 shrink-0 items-center justify-center rounded-md">
                <ClipboardList className="h-5 w-5" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-1.5 text-sm font-semibold">
                  Sesión de review
                  <ExternalLink className="text-muted-foreground h-3.5 w-3.5" />
                </div>
                <p className="text-muted-foreground mt-0.5 text-xs">
                  El plan está en validación humana — abre la review-runtime con stack + tests.
                </p>
              </div>
            </Link>
          )}
        </div>

        {!inValidation && (
          <p className="text-muted-foreground text-xs italic">
            La sesión de review aparecerá aquí cuando el plan pase a{" "}
            <code>pending_human_validation</code>.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
