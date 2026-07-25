/**
 * Puente telemetría-real → estados de @miniverse/core (La Oficina, ADR 0118).
 *
 * miniverse (github.com/ianscott313/miniverse, MIT) mueve/anima a los ciudadanos
 * a partir de una lista de `AgentStatus`; su motor hace el pathfinding, el andar,
 * el teclear y las burbujas. Aquí SOLO traducimos nuestra telemetría (runs
 * activos/escalados + catálogo de agentes) a esos estados — cero movimiento
 * inventado; la semántica de estado se reutiliza de `lib/office/mapping`. Módulo
 * PURO (sin React, sin motor) → testeable en aislado.
 */

import { agentVisualState } from "@/lib/office/mapping";

/** Estados que entiende el motor miniverse (src/citizens/Citizen AgentState). */
export type MiniverseState =
  | "working"
  | "idle"
  | "thinking"
  | "sleeping"
  | "speaking"
  | "error"
  | "waiting";

export interface AgentStatus {
  id: string;
  name: string;
  state: MiniverseState;
  task: string | null;
  energy: number;
}

export interface OfficeRun {
  id: string;
  verdict: string;
  agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  task_id: string;
  task_title: string | null;
  plan_id: string | null;
  plan_title: string | null;
}

export interface OfficeAgent {
  id: string;
  name: string;
  role: string | null;
}

/** Nuestro estado visual (mapping) → estado del motor miniverse. */
function toMiniverseState(run: OfficeRun): MiniverseState {
  const v = agentVisualState({
    id: run.id,
    status: run.verdict,
    abort_code: null,
    is_review: (run.agent_role ?? "") === "reviewer",
    project_id: null,
  });
  switch (v) {
    case "working":
      return "working";
    case "reviewing":
      return "thinking";
    case "waiting_human":
      return "waiting";
    case "dizzy":
    case "aborted":
      return "error";
    default:
      return "idle";
  }
}

export interface BridgeResult {
  statuses: AgentStatus[];
  /** agentId → id del run activo (para enrutar al hacer clic en el personaje). */
  runByAgent: Record<string, string>;
}

/**
 * Traduce la telemetría a `AgentStatus[]` para el Signal de miniverse. Cada
 * agente del catálogo empieza `idle`; un run activo lo pone `working`/`thinking`
 * (con su tarea en `task` → burbuja), uno escalado `waiting`. `energy` se deja
 * a 1 (no la usamos aún). Devuelve también el mapa agentId→runId para el clic.
 */
export function toAgentStatuses(input: {
  running: OfficeRun[];
  escalated: OfficeRun[];
  agents: OfficeAgent[];
}): BridgeResult {
  const running = input.running ?? [];
  const escalated = input.escalated ?? [];
  const agents = input.agents ?? [];

  const byId = new Map<string, AgentStatus>();
  const runByAgent: Record<string, string> = {};

  for (const a of agents) {
    byId.set(a.id, { id: a.id, name: a.name, state: "idle", task: null, energy: 1 });
  }

  const applyRun = (run: OfficeRun) => {
    if (!run.agent_id) return;
    const prev = byId.get(run.agent_id);
    const status: AgentStatus = {
      id: run.agent_id,
      name: run.agent_name ?? prev?.name ?? "Agente",
      state: toMiniverseState(run),
      task: run.task_title,
      energy: 1,
    };
    byId.set(run.agent_id, status);
    runByAgent[run.agent_id] = run.id;
  };

  // Escalados primero, luego running: si un agente tuviera ambos, el run activo
  // (working) manda visualmente sobre el escalado (poco habitual).
  for (const run of escalated) applyRun(run);
  for (const run of running) applyRun(run);

  return { statuses: [...byId.values()], runByAgent };
}

export interface OfficeCounts {
  working: number;
  reviewing: number;
  waiting: number;
  idle: number;
  total: number;
}

/** Recuento por estado para el HUD gerencial (¿quién trabaja/revisa/espera/libre?
 * de un vistazo — ADR 0118). `thinking` = reviewer; `error/sleeping` cuentan como
 * libres (no los pintamos aparte en el HUD). Puro/testeable. */
export function officeCounts(statuses: Pick<AgentStatus, "state">[]): OfficeCounts {
  const c: OfficeCounts = { working: 0, reviewing: 0, waiting: 0, idle: 0, total: statuses.length };
  for (const s of statuses) {
    if (s.state === "working") c.working += 1;
    else if (s.state === "thinking") c.reviewing += 1;
    else if (s.state === "waiting") c.waiting += 1;
    else c.idle += 1;
  }
  return c;
}
