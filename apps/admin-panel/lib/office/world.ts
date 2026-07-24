/**
 * La Oficina — construcción PURA del mundo 2D (ADR 0118, réplica del sistema
 * miniverse: grid + salas + ciudadanos con estado espacial).
 *
 * Traduce la telemetría real (runs activos, escalados, agentes del catálogo) a
 * una escena espacial determinista: salas (una por PLAN con runs), la puerta del
 * humano y el sofá de descanso, con cada agente-ciudadano colocado en su zona y
 * su posición-objetivo calculada. Sin React, sin canvas, sin fetch, sin azar →
 * testeable en aislado; el canvas solo pinta y anima lo que este módulo decide.
 * La semántica de estado/burbuja se reutiliza de `lib/office/mapping`.
 */

import { agentVisualState, stepBubble, type AgentVisualState } from "@/lib/office/mapping";

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

export type Zone = "desk" | "door" | "lounge";

export interface Citizen {
  /** Clave estable para seguir su posición entre frames (por agente, con fallback al run). */
  key: string;
  /** id para el testid / navegación de la lista accesible (agent_id o run id). */
  id: string;
  name: string;
  role: string | null;
  state: AgentVisualState;
  bubble?: string;
  zone: Zone;
  deskId?: string;
  runId?: string;
  /** Posición-OBJETIVO en coordenadas del mundo (0..WIDTH, 0..HEIGHT). */
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Desk extends Rect {
  id: string;
  title: string;
}

export interface World {
  width: number;
  height: number;
  desks: Desk[];
  door: Rect;
  lounge: Rect;
  citizens: Citizen[];
}

// Mundo virtual de tamaño fijo; el canvas lo escala a su contenedor.
export const WORLD_W = 1000;
export const WORLD_H = 660;

const DESK_TOP = 70;
const DESK_AREA_H = 380;
const ZONE_Y = DESK_TOP + DESK_AREA_H + 24; // franja inferior: puerta + sofá
const ZONE_H = WORLD_H - ZONE_Y - 20;
const DESK_W = 226;
const DESK_H = 150;
const DESK_GAP_X = 34;
const DESK_GAP_Y = 34;
const SEAT_GAP = 46;

function _seatsUnder(desk: Desk, n: number): Array<{ x: number; y: number }> {
  // Sillas repartidas en filas bajo la etiqueta de la mesa, centradas.
  const perRow = Math.max(1, Math.min(4, Math.floor((desk.w - 24) / SEAT_GAP)));
  const seats: Array<{ x: number; y: number }> = [];
  for (let i = 0; i < n; i += 1) {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const rowCount = Math.min(perRow, n - row * perRow);
    const rowWidth = (rowCount - 1) * SEAT_GAP;
    seats.push({
      x: desk.x + desk.w / 2 - rowWidth / 2 + col * SEAT_GAP,
      y: desk.y + 78 + row * 44,
    });
  }
  return seats;
}

function _grid(
  count: number,
  area: Rect,
  cellW: number,
  cellH: number,
  gapX: number,
  gapY: number,
) {
  const cols = Math.max(1, Math.min(count, Math.floor((area.w + gapX) / (cellW + gapX)) || 1));
  const rects: Rect[] = [];
  for (let i = 0; i < count; i += 1) {
    const col = i % cols;
    const row = Math.floor(i / cols);
    rects.push({
      x: area.x + col * (cellW + gapX),
      y: area.y + row * (cellH + gapY),
      w: cellW,
      h: cellH,
    });
  }
  return rects;
}

function _packInto(rect: Rect, n: number, gap = 52): Array<{ x: number; y: number }> {
  if (n === 0) return [];
  const perRow = Math.max(1, Math.floor((rect.w - 24) / gap));
  const pts: Array<{ x: number; y: number }> = [];
  for (let i = 0; i < n; i += 1) {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const rowCount = Math.min(perRow, n - row * perRow);
    const rowWidth = (rowCount - 1) * gap;
    pts.push({
      x: rect.x + rect.w / 2 - rowWidth / 2 + col * gap,
      y: rect.y + 56 + row * 46,
    });
  }
  return pts;
}

/**
 * Construye el mundo a partir de la telemetría. Determinista (sin azar) para que
 * el layout sea estable frame a frame y testeable.
 */
export function buildWorld(input: {
  running: OfficeRun[];
  escalated: OfficeRun[];
  agents: OfficeAgent[];
}): World {
  const running = input.running ?? [];
  const escalated = input.escalated ?? [];
  const agents = input.agents ?? [];

  // Agentes ocupados (con run activo o escalado) — el resto va al sofá.
  const busy = new Set<string>();
  for (const r of running) if (r.agent_id) busy.add(r.agent_id);
  for (const r of escalated) if (r.agent_id) busy.add(r.agent_id);

  // Mesas: una por PLAN con runs activos, en orden de aparición.
  const deskOrder: string[] = [];
  const runsByDesk = new Map<string, OfficeRun[]>();
  for (const r of running) {
    const id = r.plan_id ?? "sin-plan";
    if (!runsByDesk.has(id)) {
      runsByDesk.set(id, []);
      deskOrder.push(id);
    }
    runsByDesk.get(id)!.push(r);
  }

  const deskArea: Rect = { x: 30, y: DESK_TOP, w: WORLD_W - 60, h: DESK_AREA_H };
  const rects = _grid(deskOrder.length, deskArea, DESK_W, DESK_H, DESK_GAP_X, DESK_GAP_Y);
  const desks: Desk[] = deskOrder.map((id, i) => ({
    id,
    title: runsByDesk.get(id)![0].plan_title ?? "Sin plan",
    ...rects[i],
  }));

  const citizens: Citizen[] = [];

  // Ciudadanos sentados en sus mesas.
  desks.forEach((desk) => {
    const runs = runsByDesk.get(desk.id)!;
    const seats = _seatsUnder(desk, runs.length);
    runs.forEach((run, i) => {
      const state = agentVisualState({
        id: run.id,
        status: run.verdict,
        abort_code: null,
        is_review: (run.agent_role ?? "") === "reviewer",
        project_id: null,
      });
      citizens.push({
        key: run.agent_id ?? run.id,
        id: run.agent_id ?? run.id,
        name: run.agent_name ?? "Agente",
        role: run.agent_role,
        state,
        bubble: stepBubble({ kind: "tool_call", summary: run.task_title }),
        zone: "desk",
        deskId: desk.id,
        runId: run.id,
        x: seats[i].x,
        y: seats[i].y,
      });
    });
  });

  // Zona puerta del humano (abajo-izquierda) y sofá (abajo-derecha).
  const door: Rect = { x: 30, y: ZONE_Y, w: (WORLD_W - 90) * 0.42, h: ZONE_H };
  const lounge: Rect = {
    x: door.x + door.w + 30,
    y: ZONE_Y,
    w: WORLD_W - 60 - door.w - 30,
    h: ZONE_H,
  };

  const doorPts = _packInto(door, escalated.length);
  escalated.forEach((run, i) => {
    citizens.push({
      key: run.agent_id ?? run.id,
      id: run.agent_id ?? run.id,
      name: run.agent_name ?? "Agente",
      role: run.agent_role,
      state: "waiting_human",
      bubble: stepBubble({ kind: "tool_call", summary: run.task_title }),
      zone: "door",
      runId: run.id,
      x: doorPts[i].x,
      y: doorPts[i].y,
    });
  });

  const idle = agents.filter((a) => !busy.has(a.id));
  const loungePts = _packInto(lounge, idle.length);
  idle.forEach((agent, i) => {
    citizens.push({
      key: agent.id,
      id: agent.id,
      name: agent.name,
      role: agent.role,
      state: "idle",
      zone: "lounge",
      x: loungePts[i].x,
      y: loungePts[i].y,
    });
  });

  return { width: WORLD_W, height: WORLD_H, desks, door, lounge, citizens };
}

const ROLE_EMOJI: Record<string, string> = {
  project_manager: "🗂️",
  architect: "📐",
  backend_dev: "💻",
  frontend_dev: "🎨",
  qa: "🧪",
  reviewer: "🔍",
  devops: "⚙️",
  security: "🛡️",
  technical_writer: "✍️",
};

export function roleEmoji(role: string | null): string {
  return ROLE_EMOJI[role ?? ""] ?? "🤖";
}

export const STATE_BADGE: Record<AgentVisualState, string> = {
  idle: "😴",
  working: "⌨️",
  reviewing: "🔍",
  waiting_human: "🚪",
  dizzy: "💫",
  aborted: "🛑",
  done: "✅",
};

const STATE_LABEL: Record<AgentVisualState, string> = {
  idle: "descansando",
  working: "trabajando",
  reviewing: "revisando",
  waiting_human: "esperando a un humano",
  dizzy: "atascado (dando vueltas)",
  aborted: "abortado",
  done: "terminado",
};

export function stateLabel(state: AgentVisualState): string {
  return STATE_LABEL[state];
}
