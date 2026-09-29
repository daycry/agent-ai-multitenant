"use client";

import Link from "next/link";

import { SectionCard, SectionEmpty } from "@/components/dashboard/section-card";
import {
  headlineBudget,
  progressPercent,
  type DashboardProject,
  type DashboardSpend,
} from "@/lib/dashboard/tenant-dashboard";
import { useT } from "@/lib/i18n";

/** 2 — Trabajo en curso: sólo los proyectos con algo en marcha. */
export function ProjectsSection({ projects }: { projects: DashboardProject[] }) {
  const t = useT("tenantDashboard");
  return (
    <SectionCard title={t("projectsTitle")} testid="dashboard-projects">
      {projects.length === 0 ? (
        <SectionEmpty testid="dashboard-projects-empty">{t("projectsEmpty")}</SectionEmpty>
      ) : (
        <ul className="flex flex-col gap-3">
          {projects.map((p) => {
            const pct = progressPercent(p.tasks_done, p.tasks_total);
            return (
              <li key={p.project_id} data-testid={`dashboard-project-${p.project_id}`}>
                <Link href={p.url_path} className="text-sm font-medium hover:underline">
                  {p.project_name}
                </Link>
                <div
                  className="bg-muted mt-1 h-1.5 w-full overflow-hidden rounded"
                  role="progressbar"
                  aria-valuenow={pct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div className="bg-primary h-full" style={{ width: `${pct}%` }} />
                </div>
                <span className="text-muted-foreground text-xs">
                  {t("projectPlans", { count: p.active_plans })} ·{" "}
                  {t("projectTasks", { done: p.tasks_done, total: p.tasks_total })}
                  {p.running > 0 ? ` · ${t("projectRunning", { count: p.running })}` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </SectionCard>
  );
}

/** 3 — Dinero contra presupuesto, o el aviso de que no hay presupuesto. */
export function SpendSection({ spend }: { spend: DashboardSpend }) {
  const t = useT("tenantDashboard");
  const budget = headlineBudget(spend);
  return (
    <SectionCard title={t("spendTitle")} testid="dashboard-spend">
      {budget === null ? (
        <SectionEmpty testid="dashboard-spend-none">{t("spendNoBudget")}</SectionEmpty>
      ) : (
        <div className="flex flex-col gap-1">
          <span className="text-muted-foreground text-xs">
            {budget.scope === "tenant"
              ? t("spendScopeTenant")
              : t("spendScopeProject", { name: budget.project_name ?? "" })}
          </span>
          <span className="text-2xl font-semibold" data-testid="dashboard-spend-percent">
            {t("spendPercent", { percent: Math.round(budget.percent_used ?? 0) })}
          </span>
          <span className="text-muted-foreground text-xs">
            {t("spendUsed", { spent: budget.spend_usd, budget: budget.budget_usd ?? "—" })}
          </span>
          {budget.over_budget && (
            <span className="text-danger text-xs font-medium">{t("spendOver")}</span>
          )}
        </div>
      )}
    </SectionCard>
  );
}
