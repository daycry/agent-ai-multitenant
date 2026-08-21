/**
 * Tipos y constantes del hub del agente.
 *
 * Extraído de `page.tsx` al partir la pantalla (prod-16 `task_prod16_08`): eran
 * 824 líneas con tres diálogos dentro. `Agent` lo necesitan la página y los
 * tres, así que vivir en el fichero de cualquiera de ellos crearía una
 * dependencia circular en cuanto uno importara del otro.
 *
 * `ROLE_OPTIONS` NO se traduce: son los valores del enum del backend, y lo que
 * se guarda es la cadena tal cual. Traducir la opción visible y enviar otra
 * cosa sería la clase de desalineación que cuesta media tarde de depuración.
 */

import type { BadgeVariant } from "@/components/ui/badge";
import type { ModelConfig, SystemPrompts } from "@/lib/persona/persona";

export interface Agent {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  agent_type: string;
  role: string;
  system_prompt: string;
  // Bilingual persona prompts live under model_config.system_prompts.{es,en}.
  // The backend exposes the column as `model_config` (alias of llm_config).
  model_config?: ModelConfig | null;
  memory_scope: string;
  review_capability: boolean;
  max_concurrent_tasks: number;
  is_template: boolean;
  scope: string;
  project_id: string | null;
  forked_from_agent_id: string | null;
  // ADR 0071: equipos a los que pertenece (vacío = sin equipo). Si pertenece a
  // ≥1, el memory_scope lo gobierna el equipo y el control por-agente se inhabilita.
  teams: { id: string; name: string }[];
}

export interface AgentUpdate {
  name?: string;
  description?: string | null;
  role?: string;
  system_prompt?: string;
  // Sent under the JSON key `model_config` (alias of llm_config). Carries the
  // persona: provider/model/temperature + bilingual system_prompts.{es,en}.
  model_config?: ModelConfig;
  memory_scope?: string;
  review_capability?: boolean;
  max_concurrent_tasks?: number;
}

/** Valores del enum de rol del backend: identificadores, no texto de UI. */
export const ROLE_OPTIONS = [
  "project_manager",
  "architect",
  "backend_dev",
  "frontend_dev",
  "qa",
  "reviewer",
  "leader",
  "worker",
  "specialist",
  "researcher",
  "devops",
  "security",
  "technical_writer",
];

export const SCOPE_BADGE: Record<string, BadgeVariant> = {
  global_builtin: "muted",
  global_tenant_template: "info",
  project_local: "primary",
};

/**
 * Prompts iniciales del editor: prioriza `model_config.system_prompts` (fuente
 * única) y, si está vacía, siembra el ES con el campo plano legacy para que el
 * primer guardado migre el agente al formato bilingüe sin perder el prompt.
 */
export function initialPrompts(agent: Agent): SystemPrompts {
  const bilingual = agent.model_config?.system_prompts;
  if (bilingual && (bilingual.es?.trim() || bilingual.en?.trim())) {
    return { es: bilingual.es ?? "", en: bilingual.en ?? "" };
  }
  return { es: agent.system_prompt ?? "", en: "" };
}
