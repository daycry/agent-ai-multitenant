"use client";

/**
 * Gobierno del proyecto: los cuatro ajustes que el backend acepta y ninguna
 * pantalla ofrecía (`task_wf_35`, hallazgo D-06).
 *
 *   · `execution_budgets`      — el techo de UN run del proyecto.
 *   · presupuesto de gasto     — `budget_*`, el techo acumulado del periodo.
 *   · `human_task_review_mode` — qué pasa al entregar una tarea humana.
 *   · `guardrails_config`      — la capa de guardrails del proyecto.
 *
 * Antes de esta sección, el único camino era la API. Y hasta `39f1ebbf` la API
 * aceptaba los dos primeros SIN validar y aguas abajo se descartaban en
 * silencio, así que la pantalla habría mentido igual: primero se cerró el
 * no-op, ahora se abre la puerta.
 *
 * Los guardrails se editan como JSON a propósito. Su forma es
 * `{guardrails: {hook: [{type, …}]}}` con parámetros propios por tipo de
 * guardrail; un formulario tendría que replicar ese catálogo y divergiría del
 * esquema real a la primera. El backend valida con el MISMO parser que el
 * worker, así que un error aquí es exactamente el que habría en ejecución.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import {
  BUDGET_PERIODS,
  EXECUTION_BUDGET_CEILING,
  EXECUTION_BUDGET_KEYS,
  EXECUTION_BUDGET_LABEL,
  governanceProblems,
  HUMAN_TASK_REVIEW_MODES,
  toForm,
  toPayload,
  type GovernanceForm,
  type GovernanceValue,
} from "@/lib/project-governance";

export function ProjectGovernanceSection({
  projectId,
  value,
}: {
  projectId: string;
  value: GovernanceValue | null;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<GovernanceForm>(() => toForm(value));
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: () => apiFetch(`/projects/${projectId}`, { method: "PUT", body: toPayload(form) }),
    onSuccess: () => {
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  function patch(change: Partial<GovernanceForm>) {
    setSaved(false);
    save.reset();
    setForm((prev) => ({ ...prev, ...change }));
  }

  const problems = governanceProblems(form);
  const mode = HUMAN_TASK_REVIEW_MODES.find((m) => m.value === form.humanTaskReviewMode);

  return (
    <Card data-testid="project-governance-section">
      <CardHeader className="flex flex-row items-center gap-2">
        <SlidersHorizontal className="text-muted-foreground h-5 w-5" />
        <CardTitle>Límites y gobierno del proyecto</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* --- Presupuesto por run ------------------------------------- */}
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Presupuesto de un run</h3>
          <p className="text-muted-foreground text-sm">
            El techo de UNA ejecución de agente en este proyecto. Vacío = hereda el de la
            plataforma. Un valor por encima del techo de plataforma se recorta a ese techo (no es un
            error); un valor de cero o negativo se rechaza, porque se descartaría en silencio y
            creerías haber capado el gasto.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {EXECUTION_BUDGET_KEYS.map((key) => (
              <div key={key} className="space-y-1.5">
                <Label htmlFor={`budget-${key}`}>{EXECUTION_BUDGET_LABEL[key]}</Label>
                <Input
                  id={`budget-${key}`}
                  data-testid={`exec-budget-${key}`}
                  inputMode="decimal"
                  placeholder={`plataforma: ${EXECUTION_BUDGET_CEILING[key].toLocaleString("es-ES")}`}
                  value={form.budgets[key]}
                  onChange={(e) => patch({ budgets: { ...form.budgets, [key]: e.target.value } })}
                />
              </div>
            ))}
          </div>
        </section>

        {/* --- Presupuesto de gasto ------------------------------------ */}
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Presupuesto de gasto</h3>
          <p className="text-muted-foreground text-sm">
            El techo ACUMULADO del proyecto por periodo. Al agotarse, el proyecto se pausa.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="budget-amount">Importe</Label>
              <Input
                id="budget-amount"
                data-testid="budget-amount"
                inputMode="decimal"
                placeholder="sin límite"
                value={form.budgetAmount}
                onChange={(e) => patch({ budgetAmount: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="budget-currency">Moneda</Label>
              <Input
                id="budget-currency"
                data-testid="budget-currency"
                maxLength={3}
                placeholder="EUR"
                value={form.budgetCurrency}
                onChange={(e) => patch({ budgetCurrency: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="budget-period">Periodo</Label>
              <Select
                id="budget-period"
                data-testid="budget-period"
                value={form.budgetPeriod}
                onChange={(e) => patch({ budgetPeriod: e.target.value })}
              >
                {BUDGET_PERIODS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          {form.budgetPeriod === "custom" ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="budget-start-day">Día de inicio del periodo</Label>
                <Input
                  id="budget-start-day"
                  data-testid="budget-start-day"
                  inputMode="numeric"
                  placeholder="1-31"
                  value={form.budgetPeriodStartDay}
                  onChange={(e) => patch({ budgetPeriodStartDay: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="budget-length">Duración (días)</Label>
                <Input
                  id="budget-length"
                  data-testid="budget-length"
                  inputMode="numeric"
                  placeholder="1-366"
                  value={form.budgetPeriodLengthDays}
                  onChange={(e) => patch({ budgetPeriodLengthDays: e.target.value })}
                />
              </div>
            </div>
          ) : null}
        </section>

        {/* --- Tareas humanas ------------------------------------------ */}
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Revisión de tareas humanas</h3>
          <div className="space-y-1.5 sm:max-w-md">
            <Select
              aria-label="Revisión de tareas humanas"
              data-testid="human-task-review-mode"
              value={form.humanTaskReviewMode}
              onChange={(e) => patch({ humanTaskReviewMode: e.target.value })}
            >
              {HUMAN_TASK_REVIEW_MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </Select>
            {mode ? <p className="text-muted-foreground text-xs">{mode.hint}</p> : null}
          </div>
        </section>

        {/* --- Guardrails ---------------------------------------------- */}
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Guardrails del proyecto</h3>
          <p className="text-muted-foreground text-sm">
            Capa de guardrails que se fusiona sobre la de plataforma. Vacío = solo la de plataforma.
            Se valida con el mismo parser que usa el worker, así que lo que se guarde aquí es lo que
            se aplicará. Forma: <code>{'{"guardrails": {"pre_tool": [{"type": "..."}]}}'}</code> —
            hooks válidos: <code>pre_llm</code>, <code>post_llm</code>, <code>pre_tool</code>,{" "}
            <code>post_tool</code>.
          </p>
          <textarea
            data-testid="guardrails-config"
            rows={8}
            spellCheck={false}
            value={form.guardrailsJson}
            onChange={(e) => patch({ guardrailsJson: e.target.value })}
            placeholder='{"guardrails": {"pre_tool": []}}'
            className="border-input bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-2 font-mono text-xs leading-relaxed focus-visible:outline-none focus-visible:ring-2"
          />
        </section>

        {problems.length > 0 ? (
          <ul
            className="bg-warning-soft text-warning-soft-foreground list-disc space-y-0.5 rounded p-3 pl-7 text-xs"
            data-testid="governance-problems"
          >
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}

        <div className="flex items-center gap-3">
          <Button
            onClick={() => save.mutate()}
            disabled={problems.length > 0 || save.isPending}
            data-testid="governance-save"
          >
            {save.isPending ? "Guardando…" : "Guardar límites"}
          </Button>
          {saved ? <p className="text-success text-xs">Guardado.</p> : null}
          {save.isError ? (
            <p className="text-destructive text-xs" data-testid="governance-error">
              {save.error instanceof ApiError ? save.error.body : String(save.error)}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
