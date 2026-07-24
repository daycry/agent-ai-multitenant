// @vitest-environment node
// La Oficina — builder PURO del mundo 2D (ADR 0118). Agrupa la telemetría en
// zonas espaciales (mesas por plan, puerta del humano, sofá), asigna estado y
// burbuja reales, y coloca a cada ciudadano dentro de los límites del mundo.

import { describe, expect, it } from "vitest";

import { buildWorld, WORLD_H, WORLD_W, type OfficeAgent, type OfficeRun } from "@/lib/office/world";

const run = (over: Partial<OfficeRun>): OfficeRun => ({
  id: "run-x",
  verdict: "running",
  agent_id: "ag-x",
  agent_name: "Agente",
  agent_role: "backend_dev",
  task_id: "t-x",
  task_title: "hacer algo",
  plan_id: "plan-x",
  plan_title: "Plan X",
  ...over,
});

const agent = (over: Partial<OfficeAgent>): OfficeAgent => ({
  id: "ag-x",
  name: "Agente",
  role: "backend_dev",
  ...over,
});

describe("buildWorld", () => {
  it("crea una mesa por PLAN con runs activos y sienta a sus agentes con burbuja real", () => {
    const w = buildWorld({
      running: [
        run({
          id: "r1",
          agent_id: "a1",
          plan_id: "p1",
          plan_title: "Plan CI4",
          task_title: "endpoint",
        }),
        run({
          id: "r2",
          agent_id: "a2",
          plan_id: "p1",
          plan_title: "Plan CI4",
          task_title: "tests",
        }),
        run({
          id: "r3",
          agent_id: "a3",
          plan_id: "p2",
          plan_title: "Plan API",
          task_title: "auth",
        }),
      ],
      escalated: [],
      agents: [],
    });
    expect(w.desks.map((d) => d.id).sort()).toEqual(["p1", "p2"]);
    const p1 = w.citizens.filter((c) => c.zone === "desk" && c.deskId === "p1");
    expect(p1).toHaveLength(2);
    expect(p1[0].state).toBe("working");
    expect(p1[0].bubble).toBe("endpoint");
    expect(p1[0].runId).toBe("r1");
  });

  it("manda los runs escalados a la puerta y los agentes sin run al sofá", () => {
    const w = buildWorld({
      running: [run({ id: "r1", agent_id: "a1" })],
      escalated: [
        run({ id: "r2", agent_id: "a2", verdict: "needs_human_review", task_title: "valida" }),
      ],
      agents: [agent({ id: "a1" }), agent({ id: "a2" }), agent({ id: "a3", name: "Idle" })],
    });
    const door = w.citizens.filter((c) => c.zone === "door");
    expect(door).toHaveLength(1);
    expect(door[0].state).toBe("waiting_human");
    expect(door[0].runId).toBe("r2");
    // a1 (working) y a2 (escalado) están ocupados → solo a3 al sofá.
    const lounge = w.citizens.filter((c) => c.zone === "lounge");
    expect(lounge.map((c) => c.id)).toEqual(["a3"]);
    expect(lounge[0].state).toBe("idle");
  });

  it("un run de reviewer se pinta como 'reviewing'", () => {
    const w = buildWorld({
      running: [run({ id: "r1", agent_id: "a1", agent_role: "reviewer" })],
      escalated: [],
      agents: [],
    });
    expect(w.citizens[0].state).toBe("reviewing");
  });

  it("coloca a todos los ciudadanos dentro de los límites del mundo (posiciones finitas)", () => {
    const w = buildWorld({
      running: [
        run({ id: "r1", agent_id: "a1", plan_id: "p1" }),
        run({ id: "r2", agent_id: "a2", plan_id: "p2" }),
      ],
      escalated: [run({ id: "r3", agent_id: "a3", verdict: "needs_human_review" })],
      agents: Array.from({ length: 8 }, (_, i) => agent({ id: `idle${i}`, name: `Idle ${i}` })),
    });
    for (const c of w.citizens) {
      expect(Number.isFinite(c.x)).toBe(true);
      expect(Number.isFinite(c.y)).toBe(true);
      expect(c.x).toBeGreaterThanOrEqual(0);
      expect(c.x).toBeLessThanOrEqual(WORLD_W);
      expect(c.y).toBeGreaterThanOrEqual(0);
      expect(c.y).toBeLessThanOrEqual(WORLD_H);
    }
  });

  it("oficina vacía: sin mesas ni ciudadanos", () => {
    const w = buildWorld({ running: [], escalated: [], agents: [] });
    expect(w.desks).toHaveLength(0);
    expect(w.citizens).toHaveLength(0);
  });
});
