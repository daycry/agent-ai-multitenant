/**
 * Pestañas Runs, Equipo y Costes del proyecto (`task_ui_12`): la forma de
 * `GET /projects/{id}/team` y `GET /projects/{id}/costs`, y el filtro de la lista de runs.
 * Sin React.
 */

import { apiFetch } from "@/lib/api";
import type { OverviewSpend } from "@/lib/projects/project-overview";

export interface TeamMember {
  agent_id: string;
  name: string;
  role: string;
  /** `ai` | `human` (`agents.agent_type`). */
  agent_type: string;
  is_team_leader: boolean;
  url_path: string;
}

export interface RoleGapCandidate {
  agent_id: string;
  name: string;
  url_path: string;
}

/** Un rol que piden los planes activos y que el equipo no cubre. */
export interface RoleGap {
  role: string;
  /** Títulos de los planes activos que lo piden. */
  plans: string[];
  /** Agentes del tenant de ese rol que aún no están en el equipo. */
  candidates: RoleGapCandidate[];
}

export interface ProjectTeamData {
  team: { id: string; name: string } | null;
  members: TeamMember[];
  role_gaps: RoleGap[];
}

export interface CostRow {
  runs: number;
  cost_usd: string;
  tokens: number;
}

export interface ProjectCostsData {
  window_days: number;
  run_count: number;
  total_cost_usd: string;
  total_tokens: number;
  budget: OverviewSpend;
  paused_by_budget: boolean;
  by_plan: (CostRow & { plan_id: string | null; title: string | null })[];
  by_agent: (CostRow & { agent_id: string | null; name: string | null })[];
  by_model: (CostRow & { model: string | null })[];
}

/** Agrupa los miembros por rol, conservando el orden en que llegan. */
export function groupByRole(members: TeamMember[]): [string, TeamMember[]][] {
  const groups = new Map<string, TeamMember[]>();
  for (const member of members) {
    groups.set(member.role, [...(groups.get(member.role) ?? []), member]);
  }
  return [...groups.entries()];
}

/** Levanta la pausa por presupuesto del proyecto (`POST /budgets/pause/override`, tenant_admin). */
export function clearProjectBudgetPause(projectId: string): Promise<{ cleared: boolean }> {
  return apiFetch<{ cleared: boolean }>("/budgets/pause/override", {
    method: "POST",
    body: { scope: "project", project_id: projectId },
  });
}
