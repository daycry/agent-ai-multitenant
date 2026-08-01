/**
 * Tipos y value-sets del dashboard de estadísticas del tenant (task_14_12).
 *
 * Extraídos de `page.tsx` por `task_prod16_08` (la pantalla tenía 861 líneas).
 * Espejan `api_server.schemas.tenant_stats`: las `success_rate` son fracciones
 * en [0,1] serializadas como cadena decimal (o `null` cuando no hay runs); los
 * costes son cadenas decimales en USD.
 */

export interface AgentStats {
  agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  run_count: number;
  succeeded: number;
  success_rate: string | null;
  mean_duration_ms: string | null;
  mean_cost_usd: string | null;
  total_cost_usd: string;
  total_tokens: number;
}

export interface TrendPoint {
  day: string;
  run_count: number;
  succeeded: number;
  success_rate: string | null;
  total_cost_usd: string;
}

export interface StatsDashboard {
  window_days: number;
  currency: string;
  total_runs: number;
  succeeded_runs: number;
  overall_success_rate: string | null;
  mean_duration_ms: string | null;
  mean_cost_usd: string | null;
  total_cost_usd: string;
  by_agent: AgentStats[];
  top_agents: AgentStats[];
  bottom_agents: AgentStats[];
  trend: TrendPoint[];
}

export interface CostliestRun {
  execution_id: string;
  task_id: string;
  task_title: string | null;
  agent_name: string | null;
  total_cost_usd: string;
  total_tokens: number;
  created_at: string;
}

export interface ConsumptionSummary {
  window_days: number;
  currency: string;
  run_count: number;
  accumulated_cost_usd: string;
  mean_cost_usd: string | null;
  total_tokens: number;
  total_tokens_input: number;
  total_tokens_output: number;
  total_tokens_cached: number;
  costliest_run: CostliestRun | null;
  /**
   * Segmentación de coste (Plan 16 `task_16_12`): coste IA (executions) vs
   * coste humano (tarifa × horas de `human_work_sessions`), y su total. Todo USD.
   */
  ai_cost_usd: string;
  human_cost_usd: string;
  total_cost_usd: string;
  human_hours_logged: string;
}

export interface ExecutionRunRow {
  id: string;
  created_at: string;
  task_id: string;
  task_title: string | null;
  plan_id: string | null;
  plan_title: string | null;
  agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  model: string | null;
  verdict: string;
  succeeded: boolean;
  retry_count: number;
  duration_ms: number | null;
  total_tokens: number;
  total_cost_usd: string;
  /**
   * FX sólo de visualización (Plan 11.1): cuando la moneda elegida no es USD el
   * backend convierte cada fila a la tasa de SU PROPIA fecha y arrastra la tasa
   * aplicada para trazabilidad. `null` si no hubo conversión (USD) o si no
   * había tasa para esa fecha.
   */
  display_currency: string | null;
  display_cost: string | null;
  applied_rate: string | null;
  applied_rate_date: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export const WINDOW_OPTIONS = [30, 90, 365] as const;
export const PAGE_SIZE = 25;

/**
 * Selector de moneda de visualización (Plan 11.1 `task_11_1_03`). USD es
 * canónico; las alternativas se convierten al vuelo a la fecha de cada run
 * (sólo visualización — el USD almacenado no cambia). La lista se mantiene
 * corta y común; el backend acepta cualquier código ISO-4217 con tasa.
 */
export const CURRENCY_OPTIONS = ["USD", "EUR", "GBP"] as const;
export type DisplayCurrency = (typeof CURRENCY_OPTIONS)[number];
