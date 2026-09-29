/**
 * `GET /tenant/dashboard` (`task_ui_02`): la forma de la respuesta y los cálculos
 * puros que la pantalla necesita. Sin React, para testearlos sin montar nada.
 *
 * El orden de las secciones lo fijó el operador el 2026-09-09: lo que espera a un
 * humano, el trabajo por proyecto, el dinero, la salud sólo cuando duele, la
 * actividad y los MCP.
 */

export interface PendingItem {
  kind: string;
  id: string;
  title: string;
  project_name: string | null;
  age_seconds: number;
  url_path: string;
}

export interface DashboardPending {
  total: number;
  by_kind: Record<string, number>;
  oldest: PendingItem[];
}

export interface DashboardProject {
  project_id: string;
  project_name: string;
  active_plans: number;
  tasks_done: number;
  tasks_total: number;
  running: number;
  /** El run más reciente fallido o abortado de un plan activo, o `null`. */
  last_failure: { task_title: string; execution_id: string; url_path: string } | null;
  url_path: string;
}

export interface BudgetScope {
  scope: string;
  project_id: string | null;
  project_name: string | null;
  budget_usd: string | null;
  spend_usd: string;
  percent_used: number | null;
  status: string;
  over_budget: boolean;
  period: string;
}

export interface DashboardSpend {
  available: boolean;
  reason?: string;
  currency?: string;
  tenant?: BudgetScope | null;
  projects?: BudgetScope[];
}

export interface DashboardIssue {
  kind: "mcp_egress_blocked" | "budget_paused_project" | "budget_paused_tenant" | string;
  project_id: string | null;
  project_name: string | null;
  count: number;
  url_path: string;
}

export interface DashboardActivity {
  execution_id: string;
  status: string;
  task_title: string;
  project_name: string | null;
  agent_name: string | null;
  at: string | null;
  url_path: string;
}

export interface DashboardMcp {
  project_id: string;
  project_name: string;
  servers: number;
  warnings: number;
}

export interface TenantDashboard {
  pending: DashboardPending;
  projects: DashboardProject[];
  spend: DashboardSpend;
  issues: DashboardIssue[];
  activity: DashboardActivity[];
  mcp: DashboardMcp[];
  generated_at: string;
}

/** ¿Es el error de «no hay tenant activo» (System Admin recién logueado, sin `tid`)? */
export function isNoTenantError(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const { status, body } = error as { status?: unknown; body?: unknown };
  if (status !== 400 && status !== 403) return false;
  return typeof body === "string" && /no active tenant context|active tenant required/.test(body);
}

/** Porcentaje de tareas terminadas, redondeado; `0` si el plan aún no tiene tareas. */
export function progressPercent(done: number, total: number): number {
  if (total <= 0) return 0;
  return Math.round((done / total) * 100);
}

/** Antigüedad legible: minutos, horas o días, en la unidad más grande que quepa. */
export function ageParts(seconds: number): { value: number; unit: "m" | "h" | "d" } {
  const minutes = Math.max(0, Math.floor(seconds / 60));
  if (minutes < 60) return { value: minutes, unit: "m" };
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return { value: hours, unit: "h" };
  return { value: Math.floor(hours / 24), unit: "d" };
}

/** El presupuesto que enseña la tarjeta de dinero: el del tenant si lo hay; si no, el
 *  del proyecto que más porcentaje lleva consumido. `null` si no hay ninguno. */
export function headlineBudget(spend: DashboardSpend): BudgetScope | null {
  if (!spend.available) return null;
  if (spend.tenant) return spend.tenant;
  const projects = [...(spend.projects ?? [])];
  projects.sort((a, b) => (b.percent_used ?? 0) - (a.percent_used ?? 0));
  return projects[0] ?? null;
}
