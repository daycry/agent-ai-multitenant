import { describe, expect, it } from "vitest";

import {
  customChatModeUnavailable,
  memoryDetectorState,
  placeholderFieldNote,
  privateScopeMemoryWarning,
  UNAVAILABLE_LABEL,
} from "@/lib/memory/honesty";

/**
 * Plan 06.17 task_06_17_06 — Honestidad de estado en UI.
 *
 * El subsistema de memoria semántica puede estar "vacío de verdad": si NINGUNA
 * memoria tiene `embedding` (Plan 06.17 task_06_17_03/04: el back-fill aún no
 * corrió o Ollama está caído), el detector de similares y el slider de umbral no
 * sirven para nada — la UI NO debe fingir que están activos. Igual con el scope
 * `private`: un agente IA con `memory_scope=private` NO memoriza, y la ficha debe
 * avisarlo en vez de prometer recuerdo entre runs.
 *
 * Este módulo es lógica pura (sin DOM, sin React) para que la decisión de "qué
 * estado honesto mostrar" se testee aislada y sea la fuente ÚNICA que consumen
 * settings/memories, memories y agents/[id].
 */

describe("memory honesty — detector de similares / umbral", () => {
  it("sin embeddings (has_embedding=false en todas) → 'No disponible aún'", () => {
    const state = memoryDetectorState(false, "es");
    expect(state.available).toBe(false);
    expect(state.label).toBe(UNAVAILABLE_LABEL.es);
    expect(state.label).toBe("No disponible aún");
    // El motivo explica por qué (back-fill / embeddings ausentes), no es vacío.
    expect(state.note.length).toBeGreaterThan(0);
  });

  it("la etiqueta 'No disponible aún' es bilingüe ES+EN", () => {
    expect(memoryDetectorState(false, "es").label).toBe("No disponible aún");
    expect(memoryDetectorState(false, "en").label).toBe("Not available yet");
  });

  it("con al menos un embedding → disponible (sin placeholder)", () => {
    const state = memoryDetectorState(true, "es");
    expect(state.available).toBe(true);
    expect(state.label).toBeNull();
  });
});

describe("memory honesty — aviso de memory_scope=private", () => {
  it("private → aviso de que el agente IA NO memoriza", () => {
    const warn = privateScopeMemoryWarning("private", "es");
    expect(warn).not.toBeNull();
    expect(warn).toContain("no memoriza");
  });

  it("el aviso de private es bilingüe ES+EN", () => {
    expect(privateScopeMemoryWarning("private", "en")).toContain("does not");
  });

  it("cualquier scope que NO sea private → sin aviso (null)", () => {
    expect(privateScopeMemoryWarning("team_shared", "es")).toBeNull();
    expect(privateScopeMemoryWarning("project_shared", "es")).toBeNull();
    expect(privateScopeMemoryWarning("global", "es")).toBeNull();
  });
});

describe("memory honesty — campos placeholder / modo custom", () => {
  it("placeholder de rag_knowledge_bases / mcp_servers es bilingüe y dice 'placeholder'", () => {
    expect(placeholderFieldNote("es").toLowerCase()).toContain("placeholder");
    expect(placeholderFieldNote("en").toLowerCase()).toContain("placeholder");
  });

  it("el modo de chat custom → 'No disponible aún' (no creable end-to-end aún)", () => {
    expect(customChatModeUnavailable("es")).toBe("No disponible aún");
    expect(customChatModeUnavailable("en")).toBe("Not available yet");
  });
});
