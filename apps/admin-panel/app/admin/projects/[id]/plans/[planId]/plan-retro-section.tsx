"use client";

/**
 * La retrospectiva del plan (`task_wf_34`, ADR 0124).
 *
 * El beat `plan_retro` la escribe a los pocos minutos de cerrarse el plan —
 * tareas hechas/canceladas, runs, escalados, coste, duración y la lección del
 * PM— como memoria `project_shared`, para que los agentes del siguiente plan la
 * recuerden. **Ningún humano la había visto nunca**: se guardaba con `tags`
 * fijo, así que no se podía saber de qué plan era.
 *
 * Solo se pide en planes cerrados. Y si no hay (404), la sección no se pinta:
 * un plan cerrado antes del etiquetado no tiene retro atribuible y decirlo con
 * un cartel de error sería ruido por algo que no se puede arreglar.
 */

import { useQuery } from "@tanstack/react-query";
import { GraduationCap } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { renderPlanDraft } from "@/lib/plan-draft-md";

interface PlanRetro {
  plan_id: string;
  memory_id: string;
  content: string;
  created_at: string;
}

/** Estados en los que ya puede existir retro. El beat solo mira planes
 * `completed`/`cancelled`; pedirla antes es una llamada que siempre da 404. */
const CLOSED_STATUSES = new Set(["completed", "cancelled"]);

export function planHasRetro(status: string | null | undefined): boolean {
  return status != null && CLOSED_STATUSES.has(status);
}

export function PlanRetroSection({ planId, status }: { planId: string; status: string }) {
  const retroQuery = useQuery({
    queryKey: ["plan-retro", planId],
    queryFn: () => apiFetch<PlanRetro>(`/plans/${planId}/retro`),
    enabled: planHasRetro(status),
    refetchOnWindowFocus: false,
    // Un 404 es «todavía no» o «nunca»: reintentar no lo cambia.
    retry: false,
  });

  if (!planHasRetro(status) || !retroQuery.data) return null;

  return (
    <Card className="mt-6" data-testid="plan-retro">
      <CardHeader className="flex flex-row items-center gap-2">
        <GraduationCap className="text-muted-foreground h-5 w-5" />
        <CardTitle>Retrospectiva</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="text-sm" data-testid="plan-retro-content">
          {renderPlanDraft(retroQuery.data.content)}
        </div>
        <p className="text-muted-foreground text-xs">
          Escrita automáticamente al cerrarse el plan y guardada en la memoria del proyecto: los
          agentes del siguiente plan la recuerdan.
        </p>
      </CardContent>
    </Card>
  );
}
