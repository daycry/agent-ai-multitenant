// Mapeo evento→estado-visual (ADR 0118/0119): la ÚNICA fuente de semántica
// visual para La Oficina (en vivo) y el Replay (histórico). Módulo puro:
// entra telemetría real (executions + steps), sale estado visual — cero
// estados inventados; cada caso de este test cita su fuente de verdad.

import { describe, expect, it } from "vitest";

import { agentVisualState, stepBubble, stepVisual, type ExecutionLike } from "@/lib/office/mapping";

const running = (over: Partial<ExecutionLike> = {}): ExecutionLike => ({
  id: "e1",
  status: "running",
  abort_code: null,
  is_review: false,
  project_id: "p1",
  last_step_summary: "Tool 'write_file' → ok",
  ...over,
});

describe("agentVisualState", () => {
  it("sin execution activa → idle (dormir)", () => {
    expect(agentVisualState(null)).toBe("idle");
  });

  it("run running normal → working", () => {
    expect(agentVisualState(running())).toBe("working");
  });

  it("run de review → reviewing", () => {
    expect(agentVisualState(running({ is_review: true }))).toBe("reviewing");
  });

  it("needs_human_review / awaiting_human_approval → waiting_human", () => {
    expect(agentVisualState(running({ status: "needs_human_review" }))).toBe("waiting_human");
    expect(agentVisualState(running({ status: "awaiting_human_approval" }))).toBe("waiting_human");
  });

  it("abort por bucle → dizzy (dar vueltas)", () => {
    expect(
      agentVisualState(running({ status: "aborted", abort_code: "repetitive_loop_detected" })),
    ).toBe("dizzy");
  });

  it("abort por otra causa → aborted", () => {
    expect(
      agentVisualState(running({ status: "aborted", abort_code: "max_iterations_exceeded" })),
    ).toBe("aborted");
  });

  it("done → done", () => {
    expect(agentVisualState(running({ status: "done" }))).toBe("done");
  });
});

describe("stepBubble", () => {
  it("usa el summary real del step, recortado para la burbuja", () => {
    const long = "x".repeat(200);
    expect(stepBubble({ kind: "tool_call", summary: long })).toHaveLength(121); // 120 + …
    expect(stepBubble({ kind: "node", summary: "Perceived task: hola" })).toBe(
      "Perceived task: hola",
    );
  });

  it("sin summary → cae al kind legible", () => {
    expect(stepBubble({ kind: "model_call" })).toBe("model_call");
  });
});

describe("stepVisual (Replay)", () => {
  it("cada kind conocido tiene icono y etiqueta estables", () => {
    for (const kind of ["node", "model_call", "tool_call", "memory_read", "mcp_wire"]) {
      const v = stepVisual(kind);
      expect(v.icon.length).toBeGreaterThan(0);
      expect(v.label.length).toBeGreaterThan(0);
    }
  });

  it("un kind desconocido no revienta (fallback genérico)", () => {
    expect(stepVisual("algo_nuevo").label).toBe("algo_nuevo");
  });
});
