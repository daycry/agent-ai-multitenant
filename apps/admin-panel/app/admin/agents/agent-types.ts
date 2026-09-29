export interface Agent {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  role: string;
  agent_type: string;
  scope: "global_builtin" | "global_tenant_template" | "project_local" | string;
  project_id: string | null;
  forked_from_agent_id: string | null;
  is_template: boolean;
  // ADR 0071: equipos a los que pertenece el agente (badge + filtros).
  teams: { id: string; name: string }[];
  // `task_ui_30`: fecha del último run (ventana reciente); null = sin runs.
  last_activity_at?: string | null;
  // Bilingual: the seed stores prompts under model_config.system_prompts.{es,en}.
  // The API exposes it as `model_config` (Pydantic alias of `llm_config`).
  model_config?: {
    system_prompts?: { es?: string; en?: string };
  } | null;
}
