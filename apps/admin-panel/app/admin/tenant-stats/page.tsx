"use client";

/**
 * `task_14_12` — Dashboard de ESTADÍSTICAS del tenant + explorador de runs
 * (Plan 14 Fase D).
 *
 * Vista tenant de cómo rinden sus agentes y qué consume, agregada desde la tabla
 * `executions` (una fila por run del loop del agente contra una tarea) — NO desde
 * los roll-ups de EvalRun (eso es el dashboard de CALIDAD, `task_14_11`). Tres
 * superficies:
 *   - estadísticas de agentes: tasa de éxito por agente, tiempo medio, coste medio,
 *     agentes top/bottom y tendencia temporal;
 *   - resumen de consumo: coste acumulado, tokens (input/output/cached), nº de runs,
 *     coste medio, run más costoso;
 *   - explorador de runs: una fila por execution, filtrable + paginado.
 *
 * Multi-tenancy: todo es **tenant-scoped** (tenant_id + RLS); un tenant ve SÓLO sus
 * propias executions. La comparativa cross-tenant es una superficie aparte, sólo
 * para System Admin (`task_14_15`).
 *
 * Superficie (todo `tenant_admin`; `<RoleGuard min="tenant_admin">` + el backend gatea
 * con `require_tenant_admin`):
 *   GET /tenant-stats/dashboard?window_days=N[&agent_id&role&plan_id]
 *   GET /tenant-stats/consumption?window_days=N[&agent_id&plan_id]
 *   GET /tenant-stats/runs?limit&offset&window_days[&filtros]
 *
 * **Partición** (prod-16 `task_prod16_08`): esta pantalla tenía 861 líneas. El
 * cuerpo vive ahora en `stats-body.tsx`, y de él cuelgan `runs-explorer.tsx`,
 * `cost-segmentation.tsx`, `stats-visuals.tsx`, `stats-format.ts` y
 * `stats-types.ts`. Aquí sólo quedan la cabecera y el gate de rol.
 */

import { Activity, BarChart3 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { useT } from "@/lib/i18n";

import { StatsBody } from "./stats-body";

export default function TenantStatsPage() {
  const t = useT("tenantStats");

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 md:px-8" data-testid="tenant-stats-page">
      <PageHeader
        icon={<BarChart3 className="h-5 w-5 text-white" />}
        title={t("title")}
        description={t("description")}
      />
      <div className="mt-6">
        <RoleGuard
          min="tenant_admin"
          fallback={
            <Card>
              <CardContent className="text-muted-foreground flex items-center gap-2 pt-5 text-sm">
                <Activity className="h-4 w-4" />
                {t("forbidden")}
              </CardContent>
            </Card>
          }
        >
          <StatsBody />
        </RoleGuard>
      </div>
    </div>
  );
}
