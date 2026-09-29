"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { SectionCard, SectionEmpty } from "@/components/dashboard/section-card";
import { StateBlock } from "@/components/shared/state-block";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  clearProjectBudgetPause,
  type CostRow,
  type ProjectCostsData,
} from "@/lib/projects/project-hub";
import { useErrorText } from "@/lib/use-error-text";

interface BreakdownItem extends CostRow {
  key: string;
  label: string;
}

function Breakdown({
  title,
  testid,
  items,
}: {
  title: string;
  testid: string;
  items: BreakdownItem[];
}) {
  const t = useT("projectTabs");
  return (
    <SectionCard title={title} testid={testid}>
      {items.length === 0 ? (
        <SectionEmpty testid={`${testid}-empty`}>{t("breakdownEmpty")}</SectionEmpty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground text-xs uppercase">
            <tr>
              <th className="py-1 text-left font-medium" />
              <th className="py-1 text-right font-medium">{t("colRuns")}</th>
              <th className="py-1 text-right font-medium">{t("colCost")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.key} className="border-border border-t">
                <td className="py-1">{item.label}</td>
                <td className="py-1 text-right tabular-nums">{item.runs}</td>
                <td className="py-1 text-right tabular-nums">${item.cost_usd}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SectionCard>
  );
}

function BudgetBlock({ data }: { data: ProjectCostsData }) {
  const t = useT("projectTabs");
  const budget = data.budget;
  return (
    <SectionCard title={t("budgetTitle")} testid="costs-budget-card">
      {!budget.available ? (
        <SectionEmpty testid="costs-budget-none">{t("budgetNone")}</SectionEmpty>
      ) : (
        <div className="flex flex-col gap-1" data-testid="costs-budget">
          <span className="text-sm">
            {t("budgetUsed", {
              percent: Math.round(budget.percent_used ?? 0),
              budget: budget.budget_usd ?? "—",
              spent: budget.spend_usd ?? "0",
            })}
          </span>
          {budget.over_budget && (
            <span className="text-danger text-xs font-medium">{t("budgetOver")}</span>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function PausedNotice({ projectId }: { projectId: string }) {
  const t = useT("projectTabs");
  const errorText = useErrorText();
  const client = useQueryClient();
  const clear = useMutation({
    mutationFn: () => clearProjectBudgetPause(projectId),
    onSuccess: () => client.invalidateQueries({ queryKey: ["project-costs", projectId] }),
  });
  return (
    <div
      className="border-warning bg-warning/10 flex flex-wrap items-center gap-3 rounded-md border p-3"
      role="alert"
      data-testid="costs-paused"
    >
      <span className="text-sm">{t("pausedNotice")}</span>
      <Button
        size="sm"
        variant="outline"
        disabled={clear.isPending}
        onClick={() => clear.mutate()}
        data-testid="costs-paused-clear"
      >
        {clear.isPending ? t("pausedClearing") : t("pausedClear")}
      </Button>
      {clear.isError && (
        <span className="text-danger text-xs" data-testid="costs-paused-error">
          {t("pausedClearError", { detail: errorText(clear.error) })}
        </span>
      )}
    </div>
  );
}

/**
 * La pestaña Costes del proyecto (`task_ui_12`): el gasto de IA del proyecto por plan, agente y
 * modelo contra su presupuesto, y la pausa por presupuesto con su botón para levantarla. Es de
 * `tenant_admin` (el gasto lo es en su endpoint de origen): un miembro raso ve el aviso.
 */
export function ProjectCosts({ projectId }: { projectId: string }) {
  const t = useT("projectTabs");
  const costs = useQuery({
    queryKey: ["project-costs", projectId],
    queryFn: () => apiFetch<ProjectCostsData>(`/projects/${projectId}/costs`),
    retry: false,
  });
  const data = costs.data;
  const forbidden = costs.error instanceof ApiError && costs.error.status === 403;

  return (
    <section data-testid="project-costs" aria-label={t("costsTitle")}>
      <h2 className="mb-3 text-lg font-semibold">{t("costsTitle")}</h2>
      {forbidden ? (
        <p className="text-muted-foreground text-sm" data-testid="costs-forbidden">
          {t("costsForbidden")}
        </p>
      ) : (
        <StateBlock
          isLoading={costs.isLoading}
          isError={costs.isError}
          error={costs.error}
          loadingLabel={t("costsLoading")}
          loadingSkeleton
          skeletonRows={4}
          errorTitle={t("costsError", { detail: "" })}
          errorTestId="costs-error"
        >
          {data && (
            <div className="flex flex-col gap-4">
              {data.paused_by_budget && <PausedNotice projectId={projectId} />}
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <SectionCard title={t("costsTotal")} testid="costs-total">
                  <span className="text-2xl font-semibold">
                    {t("costsTotalValue", { cost: data.total_cost_usd, runs: data.run_count })}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {t("costsWindow", { days: data.window_days })}
                  </span>
                </SectionCard>
                <BudgetBlock data={data} />
                <Breakdown
                  title={t("byPlan")}
                  testid="costs-by-plan"
                  items={data.by_plan.map((r) => ({
                    ...r,
                    key: r.plan_id ?? "none",
                    label: r.title ?? t("noPlan"),
                  }))}
                />
                <Breakdown
                  title={t("byAgent")}
                  testid="costs-by-agent"
                  items={data.by_agent.map((r) => ({
                    ...r,
                    key: r.agent_id ?? "none",
                    label: r.name ?? t("noAgent"),
                  }))}
                />
                <div className="lg:col-span-2">
                  <Breakdown
                    title={t("byModel")}
                    testid="costs-by-model"
                    items={data.by_model.map((r) => ({
                      ...r,
                      key: r.model ?? "none",
                      label: r.model ?? t("noModel"),
                    }))}
                  />
                </div>
              </div>
            </div>
          )}
        </StateBlock>
      )}
    </section>
  );
}
