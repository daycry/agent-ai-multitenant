"use client";

// Desglose de coste del plan (IA + humano) por tarea.
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

// --------------------------------------------------------------------------
// Cost breakdown (task_03_24)
// --------------------------------------------------------------------------
interface CostBreakdownTaskHuman {
  task_id: string;
  title: string;
  hours: string;
  cost: string;
}

interface CostBreakdownTaskAI {
  task_id: string;
  title: string;
  complexity: string;
  model_id: string;
  tokens_in_min: number;
  tokens_in_max: number;
  tokens_out_min: number;
  tokens_out_max: number;
  cost_min: string;
  cost_max: string;
}

interface CostBreakdownResponse {
  human: {
    currency: string;
    hourly_rate: string;
    total_hours: string;
    total_cost: string;
    tasks: CostBreakdownTaskHuman[];
  };
  ai: {
    currency: string;
    default_model_id: string;
    cost_min: string;
    cost_max: string;
    tasks: CostBreakdownTaskAI[];
    missing_models: string[];
  };
}

/**
 * ¿Se ha tarificado TODO el plan con el modelo por defecto? (carril D)
 *
 * El backend resuelve el modelo de cada tarea por la cadena agente → equipo →
 * proyecto → plataforma (ADR 0065) y sólo cae al `default_model_id` para las
 * tareas que no resuelve. Cuando el proyecto no tiene equipo,
 * `resolve_plan_task_models` devuelve `{}` y caen TODAS: la tabla sigue
 * pintándose igual de segura, con un modelo que nadie eligió.
 *
 * La respuesta de `/cost-breakdown` no trae hoy ninguna señal de esa caída —
 * `model_id` es el modelo EFECTIVO, y una tarea resuelta a `gpt-4o` y otra
 * caída a `gpt-4o` son indistinguibles. Lo que sí se deriva de lo que ya llega
 * es esta condición más débil: **ninguna fila trae un modelo distinto del por
 * defecto**. Es la que se usa aquí, y por eso el aviso habla de «causa
 * habitual» en vez de afirmar que no hay equipo: con un equipo cuyos agentes
 * hereden justo el modelo por defecto, esta función también da `true`, y ahí el
 * número SÍ está medido.
 *
 * Con cero tareas no hay nada que avisar: de eso ya habla el estado vacío.
 */
export function pricedWithDefaultModelOnly(ai: CostBreakdownResponse["ai"]): boolean {
  if (ai.tasks.length === 0) return false;
  return ai.tasks.every((task) => task.model_id === ai.default_model_id);
}

export function CostBreakdownSection({
  planId,
  projectId,
}: {
  planId: string;
  // Sólo para enlazar al sitio donde se asigna el equipo: el aviso sin el
  // «dónde se arregla» deja al lector igual de atascado que sin aviso.
  projectId: string;
}) {
  const t = useT("planCost");
  const query = useQuery({
    queryKey: ["plan-cost-breakdown", planId],
    queryFn: () => apiFetch<CostBreakdownResponse>(`/plans/${planId}/cost-breakdown`),
    refetchOnWindowFocus: false,
  });

  if (query.isLoading) {
    return (
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Desglose de coste</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">Calculando…</p>
        </CardContent>
      </Card>
    );
  }
  if (query.isError || !query.data) {
    return null;
  }

  const { human, ai } = query.data;
  const noTasks = human.tasks.length === 0 && ai.tasks.length === 0;

  return (
    <Card className="mt-6" data-testid="plan-cost-breakdown">
      <CardHeader>
        <CardTitle>Desglose de coste</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {noTasks ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid="plan-cost-breakdown-empty"
          >
            El plan aún no tiene tareas para calcular el coste.
          </p>
        ) : (
          <>
            {/* Human cost table */}
            <div data-testid="plan-cost-human">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
                Coste humano · {human.currency} · {human.hourly_rate} {human.currency}/h
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-left">
                    <tr className="border-muted border-b">
                      <th className="py-1 pr-2 font-semibold">ID</th>
                      <th className="py-1 pr-2 font-semibold">Tarea</th>
                      <th className="py-1 pr-2 font-semibold text-right">Horas</th>
                      <th className="py-1 pr-2 font-semibold text-right">Coste</th>
                    </tr>
                  </thead>
                  <tbody>
                    {human.tasks.map((t) => (
                      <tr
                        key={t.task_id}
                        data-testid={`plan-cost-human-row-${t.task_id}`}
                        className="border-muted/40 border-b"
                      >
                        <td className="py-1 pr-2 font-mono">{t.task_id}</td>
                        <td className="py-1 pr-2">{t.title}</td>
                        <td className="py-1 pr-2 text-right">{t.hours}</td>
                        <td className="py-1 pr-2 text-right">
                          {t.cost} {human.currency}
                        </td>
                      </tr>
                    ))}
                    <tr className="font-semibold">
                      <td colSpan={2} className="py-1 pr-2 text-right">
                        Total
                      </td>
                      <td
                        className="py-1 pr-2 text-right"
                        data-testid="plan-cost-human-total-hours"
                      >
                        {human.total_hours}
                      </td>
                      <td className="py-1 pr-2 text-right" data-testid="plan-cost-human-total">
                        {human.total_cost} {human.currency}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            {/* AI cost table — range (min / max) */}
            <div data-testid="plan-cost-ai">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
                Coste IA · {ai.currency} · modelo por defecto{" "}
                <span className="font-mono">{ai.default_model_id}</span>
              </p>
              {/*
                El aviso va ANTES de la tabla a propósito: debajo se leería
                después de haber creído los números.
              */}
              {pricedWithDefaultModelOnly(ai) ? (
                <div
                  className="mb-2 rounded border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
                  data-testid="plan-cost-ai-default-only"
                >
                  <p className="font-semibold">
                    {t("defaultOnlyTitle", { model: ai.default_model_id })}
                  </p>
                  <p className="mt-1">{t("defaultOnlyCause")}</p>
                  <Link
                    href={`/admin/projects/${projectId}`}
                    className="mt-1 inline-block underline underline-offset-2"
                    data-testid="plan-cost-ai-default-only-link"
                  >
                    {t("defaultOnlyLink")}
                  </Link>
                </div>
              ) : null}
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-left">
                    <tr className="border-muted border-b">
                      <th className="py-1 pr-2 font-semibold">ID</th>
                      <th className="py-1 pr-2 font-semibold">Tarea</th>
                      <th className="py-1 pr-2 font-semibold">Compl.</th>
                      <th className="py-1 pr-2 font-semibold">Modelo</th>
                      <th className="py-1 pr-2 font-semibold text-right">Coste mín</th>
                      <th className="py-1 pr-2 font-semibold text-right">Coste máx</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ai.tasks.map((t) => (
                      <tr
                        key={t.task_id}
                        data-testid={`plan-cost-ai-row-${t.task_id}`}
                        className="border-muted/40 border-b"
                      >
                        <td className="py-1 pr-2 font-mono">{t.task_id}</td>
                        <td className="py-1 pr-2">{t.title}</td>
                        <td className="py-1 pr-2">{t.complexity}</td>
                        <td className="py-1 pr-2 font-mono">{t.model_id}</td>
                        <td className="py-1 pr-2 text-right">
                          {t.cost_min} {ai.currency}
                        </td>
                        <td className="py-1 pr-2 text-right">
                          {t.cost_max} {ai.currency}
                        </td>
                      </tr>
                    ))}
                    <tr className="font-semibold">
                      <td colSpan={4} className="py-1 pr-2 text-right">
                        Total (rango)
                      </td>
                      <td className="py-1 pr-2 text-right" data-testid="plan-cost-ai-total-min">
                        {ai.cost_min} {ai.currency}
                      </td>
                      <td className="py-1 pr-2 text-right" data-testid="plan-cost-ai-total-max">
                        {ai.cost_max} {ai.currency}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              {ai.missing_models.length > 0 ? (
                <p
                  className="text-destructive mt-2 text-xs"
                  data-testid="plan-cost-ai-missing-models"
                >
                  Modelos sin precio en el catálogo: {ai.missing_models.join(", ")}
                </p>
              ) : null}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
