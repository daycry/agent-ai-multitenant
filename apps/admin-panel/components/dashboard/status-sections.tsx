"use client";

import Link from "next/link";

import { SectionCard, SectionEmpty } from "@/components/dashboard/section-card";
import type {
  DashboardActivity,
  DashboardIssue,
  DashboardMcp,
} from "@/lib/dashboard/tenant-dashboard";
import { useT } from "@/lib/i18n";

/** Un servicio del stack que no está `ok`. Sólo llega si el usuario puede verlo. */
export interface DegradedService {
  name: string;
  status: string;
}

/**
 * 4 — La salud, **sólo cuando duele**: si no hay nada, la sección no se pinta.
 * Junta los avisos del tenant con los servicios degradados del stack.
 */
export function IssuesSection({
  issues,
  services,
}: {
  issues: DashboardIssue[];
  services: DegradedService[];
}) {
  const t = useT("tenantDashboard");
  if (issues.length === 0 && services.length === 0) return null;
  const label = (issue: DashboardIssue) => {
    const project = issue.project_name ?? "";
    if (issue.kind === "mcp_egress_blocked") {
      return t("issueMcpEgress", { project, count: issue.count });
    }
    if (issue.kind === "budget_paused_project") return t("issueProjectPaused", { project });
    return t("issueTenantPaused");
  };
  return (
    <SectionCard title={t("issuesTitle")} testid="dashboard-issues">
      <ul className="flex flex-col gap-2">
        {issues.map((issue) => (
          <li key={`${issue.kind}-${issue.project_id ?? "tenant"}`}>
            <Link href={issue.url_path} className="text-warning text-sm hover:underline">
              {label(issue)}
            </Link>
          </li>
        ))}
        {services.map((s) => (
          <li
            key={`service-${s.name}`}
            className="text-danger text-sm"
            data-testid={`service-${s.name}`}
          >
            {t("issueService", { name: s.name, status: s.status })}
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

/** 5 — Los runs más recientes de los agentes. */
export function ActivitySection({ activity }: { activity: DashboardActivity[] }) {
  const t = useT("tenantDashboard");
  return (
    <SectionCard title={t("activityTitle")} testid="dashboard-activity">
      {activity.length === 0 ? (
        <SectionEmpty testid="dashboard-activity-empty">{t("activityEmpty")}</SectionEmpty>
      ) : (
        <ul className="flex flex-col gap-2">
          {activity.map((run) => (
            <li key={run.execution_id} className="flex flex-col">
              <Link href={run.url_path} className="text-sm font-medium hover:underline">
                {run.task_title}
              </Link>
              <span className="text-muted-foreground text-xs">
                {run.agent_name ?? t("activityUnknownAgent")}
                {run.project_name ? ` · ${run.project_name}` : ""} · {run.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

/** 6 — Los proyectos con MCP y sus avisos de egress. */
export function McpSection({ mcp }: { mcp: DashboardMcp[] }) {
  const t = useT("tenantDashboard");
  return (
    <SectionCard title={t("mcpTitle")} testid="dashboard-mcp">
      {mcp.length === 0 ? (
        <SectionEmpty testid="dashboard-mcp-empty">{t("mcpEmpty")}</SectionEmpty>
      ) : (
        <ul className="flex flex-col gap-2">
          {mcp.map((row) => (
            <li key={row.project_id} className="flex flex-col">
              <Link
                href={`/admin/projects/${row.project_id}/mcp-servers`}
                className="text-sm font-medium hover:underline"
              >
                {row.project_name}
              </Link>
              <span className="text-muted-foreground text-xs">
                {t("mcpServers", { count: row.servers })} ·{" "}
                {row.warnings > 0 ? t("mcpWarnings", { count: row.warnings }) : t("mcpOk")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
