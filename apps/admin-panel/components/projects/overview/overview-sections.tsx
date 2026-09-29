"use client";

import Link from "next/link";

import { SectionCard, SectionEmpty } from "@/components/dashboard/section-card";
import { progressPercent } from "@/lib/dashboard/tenant-dashboard";
import { useT, type Translator } from "@/lib/i18n";
import type {
  OverviewActivity,
  OverviewPlan,
  OverviewRuns,
  OverviewSpend,
  OverviewWarning,
} from "@/lib/projects/project-overview";

const LINK = "text-xs font-medium hover:underline";

/** Planes activos con su progreso; enlaza a la pestaña de planes. */
export function PlansBlock({ plans, base }: { plans: OverviewPlan[]; base: string }) {
  const t = useT("projectOverview");
  return (
    <SectionCard
      title={t("plansTitle")}
      testid="overview-plans"
      action={
        <Link href={`${base}/plans`} className={LINK} data-testid="overview-plans-link">
          {t("plansOpen")}
        </Link>
      }
    >
      {plans.length === 0 ? (
        <SectionEmpty testid="overview-plans-empty">{t("plansEmpty")}</SectionEmpty>
      ) : (
        <ul className="flex flex-col gap-3">
          {plans.map((p) => {
            const pct = progressPercent(p.tasks_done, p.tasks_total);
            return (
              <li key={p.plan_id}>
                <Link href={p.url_path} className="text-sm font-medium hover:underline">
                  {p.title}
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
                  {t("planProgress", { done: p.tasks_done, total: p.tasks_total })} · {p.status}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </SectionCard>
  );
}

/** Runs en curso y último fallo; enlaza a las tareas del proyecto. */
export function RunsBlock({ runs }: { runs: OverviewRuns }) {
  const t = useT("projectOverview");
  return (
    <SectionCard
      title={t("runsTitle")}
      testid="overview-runs"
      action={
        <Link href={runs.url_path} className={LINK} data-testid="overview-runs-link">
          {t("runsOpen")}
        </Link>
      }
    >
      {runs.running > 0 ? (
        <span className="text-2xl font-semibold">{t("runsRunning", { count: runs.running })}</span>
      ) : (
        <SectionEmpty testid="overview-runs-idle">{t("runsIdle")}</SectionEmpty>
      )}
      {runs.last_failure && (
        <Link
          href={runs.last_failure.url_path}
          className="text-danger block text-xs hover:underline"
          data-testid="overview-runs-failure"
        >
          {t("runsLastFailure", { task: runs.last_failure.task_title })}
        </Link>
      )}
    </SectionCard>
  );
}

/** El gasto del proyecto contra su presupuesto. Sin permiso para verlo, no se pinta. */
export function SpendBlock({ spend }: { spend: OverviewSpend }) {
  const t = useT("projectOverview");
  if (spend.reason === "forbidden") return null;
  return (
    <SectionCard title={t("spendTitle")} testid="overview-spend">
      {!spend.available ? (
        <SectionEmpty testid="overview-spend-none">{t("spendNoBudget")}</SectionEmpty>
      ) : (
        <div className="flex flex-col gap-1">
          <span className="text-2xl font-semibold" data-testid="overview-spend-percent">
            {t("spendPercent", { percent: Math.round(spend.percent_used ?? 0) })}
          </span>
          <span className="text-muted-foreground text-xs">
            {t("spendUsed", { spent: spend.spend_usd ?? "0", budget: spend.budget_usd ?? "—" })}
          </span>
          {spend.over_budget && (
            <span className="text-danger text-xs font-medium">{t("spendOver")}</span>
          )}
        </div>
      )}
      <Link href="/admin/tenant-stats" className={LINK} data-testid="overview-spend-link">
        {t("spendOpen")}
      </Link>
    </SectionCard>
  );
}

function warningText(t: Translator<"projectOverview">, w: OverviewWarning): string {
  const names = w.names.join(", ");
  switch (w.kind) {
    case "mcp_not_connected":
      return t("warnMcpNotConnected", { names });
    case "mcp_no_tools":
      return t("warnMcpNoTools", { names });
    case "mcp_egress_blocked":
      return t("warnMcpEgress", { count: w.count });
    case "repo_not_synced":
      return t("warnRepoNotSynced");
    case "budget_paused_project":
      return t("warnBudgetPaused");
    case "role_without_agent":
      return t("warnRoleWithoutAgent", { names });
    default:
      return t("warnUnknown", { kind: w.kind });
  }
}

/** Los avisos del proyecto, cada uno enlazando a la pestaña donde se arregla. */
export function WarningsBlock({ warnings }: { warnings: OverviewWarning[] }) {
  const t = useT("projectOverview");
  return (
    <SectionCard title={t("warningsTitle")} testid="overview-warnings">
      {warnings.length === 0 ? (
        <SectionEmpty testid="overview-warnings-none">{t("warningsNone")}</SectionEmpty>
      ) : (
        <ul className="flex flex-col gap-2">
          {warnings.map((w) => (
            <li key={w.kind}>
              <Link
                href={w.url_path}
                className="text-warning text-sm hover:underline"
                data-testid={`overview-warning-${w.kind}`}
              >
                {warningText(t, w)}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

/** Los runs más recientes del proyecto. */
export function ActivityBlock({ activity }: { activity: OverviewActivity[] }) {
  const t = useT("projectOverview");
  return (
    <SectionCard title={t("activityTitle")} testid="overview-activity">
      {activity.length === 0 ? (
        <SectionEmpty testid="overview-activity-empty">{t("activityEmpty")}</SectionEmpty>
      ) : (
        <ul className="flex flex-col gap-2">
          {activity.map((run) => (
            <li key={run.execution_id} className="flex flex-col">
              <Link href={run.url_path} className="text-sm font-medium hover:underline">
                {run.task_title}
              </Link>
              <span className="text-muted-foreground text-xs">
                {run.agent_name ?? t("activityUnknownAgent")} · {run.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
