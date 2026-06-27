/**
 * Runs (executions) API client — the Work-menu Runs list and the Kanban
 * card's run-history panel both read `GET /runs` (member-accessible; same row
 * shape as the admin `/tenant-stats/runs` explorer). See routers/runs.py.
 */

import { apiFetch } from "@/lib/api";

/** One row of the runs explorer (mirrors api_server ExecutionRunRow). */
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
  // ADR 0087: the agent's self-reported finish status (success|failed|partial) or null.
  finish_status: string | null;
  retry_count: number;
  duration_ms: number | null;
  total_tokens: number;
  total_cost_usd: string; // canonical USD (Decimal serialized as string)
  started_at: string | null;
  completed_at: string | null;
  // Display-currency conversion (only when the tenant's currency is not USD).
  display_currency: string | null;
  display_cost: string | null;
  applied_rate: string | null;
  applied_rate_date: string | null;
}

export interface RunFilters {
  task_id?: string;
  plan_id?: string;
  agent_id?: string;
  role?: string;
  verdict?: string;
  model?: string;
  min_cost?: number;
  window_days?: number;
  limit?: number;
  offset?: number;
}

/**
 * Build the `?a=b&c=d` querystring for `GET /runs`, dropping empty / null /
 * blank values so an unset filter is simply absent (not `?task_id=`).
 */
export function runsQuery(filters: RunFilters = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null) continue;
    const str = String(value).trim();
    if (str === "") continue;
    params.set(key, str);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** This tenant's runs, newest first, filtered. Any tenant member. */
export function listRuns(filters: RunFilters = {}): Promise<ExecutionRunRow[]> {
  return apiFetch<ExecutionRunRow[]>(`/runs${runsQuery(filters)}`);
}

// --- shared display formatters (the list page + the Kanban history panel) -----

/** Cost as the tenant's display currency (FX) when set, else canonical USD. */
export function fmtRunMoney(row: ExecutionRunRow): string {
  const cur = row.display_currency;
  const raw =
    cur && cur !== "USD" && row.display_cost != null ? row.display_cost : row.total_cost_usd;
  const num = Number(raw);
  if (!num) return "—";
  return cur && cur !== "USD" ? `${num.toFixed(4)} ${cur}` : `$${num.toFixed(4)}`;
}

export function fmtRunDuration(ms: number | null): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

export function fmtRunTokens(n: number): string {
  return n ? n.toLocaleString() : "—";
}

export function fmtRunWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
