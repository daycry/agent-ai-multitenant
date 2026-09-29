/**
 * `GET /projects/{id}/overview` (`task_ui_10`): la forma de la respuesta del Resumen
 * del proyecto. Sin React. Reutiliza la forma del gasto del dashboard del tenant.
 */

import type { BudgetScope } from "@/lib/dashboard/tenant-dashboard";

export interface OverviewPlan {
  plan_id: string;
  title: string;
  status: string;
  tasks_done: number;
  tasks_total: number;
  url_path: string;
}

export interface OverviewRuns {
  running: number;
  last_failure: { task_title: string; execution_id: string; url_path: string } | null;
  url_path: string;
}

/** El gasto del proyecto: su fila de presupuesto, o `available: false` con el motivo
 *  (`forbidden` = el rol no lo puede ver; `no_budget_configured` = no hay presupuesto). */
export type OverviewSpend = { available: boolean; reason?: string } & Partial<BudgetScope>;

export interface OverviewWarning {
  /** `mcp_not_connected` · `mcp_no_tools` · `mcp_egress_blocked` · `vault_unavailable` · `repo_not_synced` ·
   *  `budget_paused_project` · `role_without_agent`. Un código desconocido se pinta genérico. */
  kind: string;
  count: number;
  names: string[];
  url_path: string;
}

export interface OverviewActivity {
  execution_id: string;
  status: string;
  task_title: string;
  agent_name: string | null;
  at: string | null;
  url_path: string;
}

export interface ProjectOverviewData {
  project: { id: string; name: string; status: string; team_id: string | null };
  plans: OverviewPlan[];
  runs: OverviewRuns;
  spend: OverviewSpend;
  warnings: OverviewWarning[];
  activity: OverviewActivity[];
  generated_at: string;
}

/** Las pantallas del proyecto que ya no tienen entrada propia en la barra lateral. */
export const OVERVIEW_MORE_LINKS = [
  { suffix: "chat", labelKey: "projectChat" },
  { suffix: "commands", labelKey: "projectCommands" },
  { suffix: "agent-tools-diagnostic", labelKey: "projectToolsDiagnostic" },
  { suffix: "memories", labelKey: "projectMemories" },
  { suffix: "incoming-webhooks", labelKey: "projectWebhooks" },
  { suffix: "dep-cache", labelKey: "projectDepCache" },
] as const;
