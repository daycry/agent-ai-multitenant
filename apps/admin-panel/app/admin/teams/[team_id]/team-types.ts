/**
 * Contratos de la ficha de equipo — espejo de los schemas de `routers/teams.py`.
 *
 * Compartidos desde el troceo de `task_prod16_08` por la página y sus cuatro
 * diálogos.
 */

import type { ChatModelConfig } from "@/components/capability/chat-model-section";

export interface TeamMember {
  agent_id: string;
  role_in_team: string | null;
  is_team_leader: boolean;
  assignment_priority: number;
}

export interface Team {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  // Ola C: enlace al built-in origen si el equipo fue adoptado (badge/origen).
  forked_from_team_id: string | null;
  // Ola A: modelo por defecto del equipo (alias JSON `model_config`). {} = hereda.
  model_config: ChatModelConfig;
  // Modelo del CHAT del equipo (Feature B): proveedor concreto + modelo. {} = hereda.
  chat_model_config: ChatModelConfig;
  // ADR 0071: política de memoria del equipo (null = sin política / heredar).
  memory_scope: string | null;
  members: TeamMember[];
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  scope: string;
  project_id: string | null;
  // Plan 06.17 task_06_17_12: el badge Linked/Forked se deriva de este campo,
  // no del scope (que mentía: un fork project_local se mostraba "Forked" aunque
  // no tuviera origen, y un template linked aparecía como "Linked (tenant)").
  forked_from_agent_id: string | null;
}

export interface Project {
  id: string;
  name: string;
  is_template: boolean;
}

export type Mode = "linked" | "forked";

/** Cuerpo de PUT /teams/{id}/members/{agent_id}. */
export interface MemberUpdate {
  is_team_leader: boolean;
  role_in_team: string | null;
  assignment_priority: number;
}

/** Cuerpo de PUT /teams/{id} al renombrar o redescribir el equipo. */
export interface TeamUpdate {
  name?: string;
  description?: string | null;
}
