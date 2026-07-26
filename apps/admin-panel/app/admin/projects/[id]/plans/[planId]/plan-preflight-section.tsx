"use client";

/**
 * Semáforo de preflight antes de aprobar un plan (`task_wf_72`).
 *
 * Aprobar era un acto de fe: los problemas —una tarea con un rol que el equipo
 * no tiene, otra sin criterios, un camino crítico que serializa todo el
 * trabajo— aparecían DESPUÉS, con el plan corriendo y costando desbloquear
 * tareas una a una.
 *
 * No bloquea la aprobación: informa. La decisión sigue siendo del humano; lo
 * que cambia es que la toma sabiendo qué le va a costar en intervenciones
 * manuales. Se potencia con el editor del spec (`task_wf_42`): detectas el
 * problema y lo corriges sin cambiar de pantalla.
 */

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, GitBranch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

interface PreflightFinding {
  code: string;
  severity: string;
  message: string;
  task_ids: string[];
}

interface PreflightReport {
  task_count: number;
  blockers: number;
  warnings: number;
  critical_path: string[];
  critical_path_length: number;
  max_parallelism: number;
  findings: PreflightFinding[];
  cost?: {
    human_hours?: string;
    human_cost?: string;
    human_currency?: string;
    ai_usd_min?: string;
    ai_usd_max?: string;
  };
}

/** Estados en los que el preflight aporta algo.
 *
 * Solo antes de firmar: después, el plan ya está aprobado y el semáforo sería
 * un reproche sobre una decisión tomada, no una ayuda para tomarla. */
const PREFLIGHT_STATUSES = new Set(["draft", "pending_approval", "pending_second_approval"]);

export function planNeedsPreflight(status: string | null | undefined): boolean {
  return status != null && PREFLIGHT_STATUSES.has(status);
}

export function PlanPreflightSection({ planId, status }: { planId: string; status: string }) {
  const preflightQuery = useQuery({
    queryKey: ["plan-preflight", planId],
    queryFn: () => apiFetch<PreflightReport>(`/plans/${planId}/preflight`),
    enabled: planNeedsPreflight(status),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const report = preflightQuery.data;
  if (!planNeedsPreflight(status) || !report) return null;

  const clean = report.findings.length === 0;

  return (
    <Card className="mt-6" data-testid="plan-preflight">
      <CardHeader className="flex flex-row items-center gap-2">
        {clean ? (
          <CheckCircle2 className="text-success h-5 w-5" />
        ) : (
          <AlertTriangle className="text-warning h-5 w-5" />
        )}
        <CardTitle>Antes de aprobar</CardTitle>
        {report.blockers > 0 ? (
          <Badge variant="danger" data-testid="preflight-blockers">
            {report.blockers} {report.blockers === 1 ? "problema serio" : "problemas serios"}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {clean ? (
          <p className="text-muted-foreground text-sm" data-testid="preflight-clean">
            Las {report.task_count} tareas tienen rol asignable y criterios de aceptación, y el
            grafo no tiene ciclos.
          </p>
        ) : (
          <ul className="space-y-2" data-testid="preflight-findings">
            {report.findings.map((finding) => (
              <li key={finding.code} className="flex gap-2 text-sm">
                <Badge variant={finding.severity === "blocker" ? "danger" : "warning"}>
                  {finding.severity === "blocker" ? "serio" : "aviso"}
                </Badge>
                <span>
                  {finding.message}
                  {finding.task_ids.length > 0 ? (
                    <span className="text-muted-foreground block font-mono text-xs">
                      {finding.task_ids.join(", ")}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="text-muted-foreground flex flex-wrap gap-4 text-xs">
          <span className="flex items-center gap-1">
            <GitBranch className="h-3.5 w-3.5" />
            Camino crítico: {report.critical_path_length} de {report.task_count} tareas en serie
          </span>
          <span>Paralelismo máximo: {report.max_parallelism}</span>
          {report.cost?.human_hours ? (
            <span>
              Estimado: {report.cost.human_hours} h ({report.cost.human_cost}{" "}
              {report.cost.human_currency}) · IA {report.cost.ai_usd_min}–{report.cost.ai_usd_max}{" "}
              USD
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
