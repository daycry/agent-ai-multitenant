// @vitest-environment node
// Puente telemetría → AgentStatus[] de miniverse (ADR 0118). Traduce runs
// activos/escalados + catálogo a los estados que el motor anima, sin inventar.

import { describe, expect, it } from "vitest";

import {
  officeCounts,
  toAgentStatuses,
  type OfficeAgent,
  type OfficeRun,
} from "@/lib/office/miniverse-bridge";

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

describe("toAgentStatuses", () => {
  it("agentes del catálogo sin run → idle", () => {
    const { statuses } = toAgentStatuses({
      running: [],
      escalated: [],
      agents: [agent({ id: "a1", name: "Uno" }), agent({ id: "a2", name: "Dos" })],
    });
    expect(statuses).toHaveLength(2);
    expect(statuses.every((s) => s.state === "idle")).toBe(true);
  });

  it("run activo → working con la tarea en task, y mapea agentId→runId", () => {
    const { statuses, runByAgent } = toAgentStatuses({
      running: [run({ id: "r1", agent_id: "a1", task_title: "endpoint" })],
      escalated: [],
      agents: [agent({ id: "a1" })],
    });
    const s = statuses.find((x) => x.id === "a1")!;
    expect(s.state).toBe("working");
    expect(s.task).toBe("endpoint");
    expect(runByAgent.a1).toBe("r1");
  });

  it("run escalado → waiting; reviewer → thinking", () => {
    const { statuses } = toAgentStatuses({
      running: [run({ id: "r2", agent_id: "a2", agent_role: "reviewer" })],
      escalated: [run({ id: "r3", agent_id: "a3", verdict: "needs_human_review" })],
      agents: [agent({ id: "a2" }), agent({ id: "a3" })],
    });
    expect(statuses.find((x) => x.id === "a2")!.state).toBe("thinking");
    expect(statuses.find((x) => x.id === "a3")!.state).toBe("waiting");
  });

  it("un run sin agent_id no genera estado ni ruta", () => {
    const { statuses, runByAgent } = toAgentStatuses({
      running: [run({ id: "r4", agent_id: null })],
      escalated: [],
      agents: [],
    });
    expect(statuses).toHaveLength(0);
    expect(Object.keys(runByAgent)).toHaveLength(0);
  });
});

describe("officeCounts", () => {
  it("cuenta por estado (thinking=revisando; error/idle=libres)", () => {
    const c = officeCounts([
      { state: "working" },
      { state: "working" },
      { state: "thinking" },
      { state: "waiting" },
      { state: "idle" },
      { state: "error" },
    ]);
    expect(c).toEqual({ working: 2, reviewing: 1, waiting: 1, idle: 2, total: 6 });
  });
});
