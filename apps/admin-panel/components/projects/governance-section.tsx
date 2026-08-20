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
 *
 * i18n (prod-16 `task_prod16_03`): los catálogos y los mensajes de validación
 * viven en `lib/project-governance.ts` y guardan la CLAVE del diccionario; esta
 * pantalla los resuelve con el idioma activo.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { useErrorText } from "@/lib/use-error-text";
import {
  BUDGET_PERIODS,
  EXECUTION_BUDGET_CEILING,
  EXECUTION_BUDGET_KEYS,
  executionBudgetLabel,
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
  const t = useT("projectGovernance");
  const tCommon = useT("common");
  const lang = useLangOptional();
  const errorText = useErrorText();
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

  const problems = governanceProblems(form, lang);
  const mode = HUMAN_TASK_REVIEW_MODES.find((m) => m.value === form.humanTaskReviewMode);

  return (
    <Card data-testid="project-governance-section">
      <CardHeader className="flex flex-row items-center gap-2">
        <SlidersHorizontal className="text-muted-foreground h-5 w-5" />
        <CardTitle>{t("title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* --- Presupuesto por run ------------------------------------- */}
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">{t("runBudgetHeading")}</h3>
          <p className="text-muted-foreground text-sm">{t("runBudgetDescription")}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {EXECUTION_BUDGET_KEYS.map((key) => (
              <div key={key} className="space-y-1.5">
                <Label htmlFor={`budget-${key}`}>{executionBudgetLabel(key, lang)}</Label>
                <Input
                  id={`budget-${key}`}
                  data-testid={`exec-budget-${key}`}
                  inputMode="decimal"
                  placeholder={t("ceilingPlaceholder", {
                    ceiling: EXECUTION_BUDGET_CEILING[key].toLocaleString(tCommon("dateLocale")),
                  })}
                  value={form.budgets[key]}
                  onChange={(e) => patch({ budgets: { ...form.budgets, [key]: e.target.value } })}
                />
              </div>
            ))}
          </div>
        </section>

        {/* --- Presupuesto de gasto ------------------------------------ */}
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">{t("spendHeading")}</h3>
          <p className="text-muted-foreground text-sm">{t("spendDescription")}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="budget-amount">{t("amountLabel")}</Label>
              <Input
                id="budget-amount"
                data-testid="budget-amount"
                inputMode="decimal"
                placeholder={t("amountPlaceholder")}
                value={form.budgetAmount}
                onChange={(e) => patch({ budgetAmount: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="budget-currency">{t("currencyLabel")}</Label>
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
              <Label htmlFor="budget-period">{t("periodLabel")}</Label>
              <Select
                id="budget-period"
                data-testid="budget-period"
                value={form.budgetPeriod}
                onChange={(e) => patch({ budgetPeriod: e.target.value })}
              >
                {BUDGET_PERIODS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {t(p.labelKey)}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          {form.budgetPeriod === "custom" ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="budget-start-day">{t("startDayLabel")}</Label>
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
                <Label htmlFor="budget-length">{t("lengthLabel")}</Label>
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
          <h3 className="text-sm font-semibold">{t("humanReviewHeading")}</h3>
          <div className="space-y-1.5 sm:max-w-md">
            <Select
              aria-label={t("humanReviewHeading")}
              data-testid="human-task-review-mode"
              value={form.humanTaskReviewMode}
              onChange={(e) => patch({ humanTaskReviewMode: e.target.value })}
            >
              {HUMAN_TASK_REVIEW_MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {t(m.labelKey)}
                </option>
              ))}
            </Select>
            {mode ? <p className="text-muted-foreground text-xs">{t(mode.hintKey)}</p> : null}
          </div>
        </section>

        {/* --- Guardrails ---------------------------------------------- */}
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">{t("guardrailsHeading")}</h3>
          <p className="text-muted-foreground text-sm">
            {t("guardrailsDescriptionBefore")}
            <code>{'{"guardrails": {"pre_tool": [{"type": "..."}]}}'}</code>
            {t("guardrailsDescriptionHooks")}
            <code>pre_llm</code>, <code>post_llm</code>, <code>pre_tool</code>,{" "}
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
            {save.isPending ? t("saving") : t("save")}
          </Button>
          {saved ? <p className="text-success text-xs">{t("saved")}</p> : null}
          {save.isError ? (
            <p className="text-destructive text-xs" data-testid="governance-error">
              {errorText(save.error)}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
