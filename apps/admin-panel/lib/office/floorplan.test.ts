// @vitest-environment node
// Planta generada de La Oficina (ADR 0118). Regresión 2026-07-25: el mundo de
// serie traía 2 mesas y 2 puntos de deambular → con 10 agentes los personajes
// nacían apilados en el mismo tile y no se movían. La planta debe escalar.

import { describe, expect, it } from "vitest";

import { buildFloorplan, COLS, MAX_DESKS, ROWS } from "@/lib/office/floorplan";

describe("buildFloorplan", () => {
  it("da una mesa por agente (bandas completas de 5) hasta el máximo", () => {
    expect(buildFloorplan(1).deskCount).toBe(5);
    expect(buildFloorplan(5).deskCount).toBe(5);
    expect(buildFloorplan(6).deskCount).toBe(10);
    expect(buildFloorplan(10).deskCount).toBe(10);
    expect(buildFloorplan(11).deskCount).toBe(15);
    expect(buildFloorplan(99).deskCount).toBe(MAX_DESKS);
  });

  it("hay MUCHOS más puntos de deambular que agentes (el bug era tener 2)", () => {
    const fp = buildFloorplan(10);
    expect(fp.wanderPoints.length).toBeGreaterThan(20);
    // nunca sobre un prop ni sobre el asiento de una mesa
    const blocked = new Set<string>();
    for (const p of fp.props) {
      for (let y = Math.floor(p.y); y < Math.ceil(p.y + p.h); y += 1) {
        for (let x = Math.floor(p.x); x < Math.ceil(p.x + p.w); x += 1) blocked.add(`${x},${y}`);
      }
    }
    for (const w of fp.wanderPoints) expect(blocked.has(`${w.x},${w.y}`)).toBe(false);
    // nombres únicos (el motor los usa como clave de anchor)
    expect(new Set(fp.wanderPoints.map((w) => w.name)).size).toBe(fp.wanderPoints.length);
  });

  it("todo cae dentro de la rejilla y los asientos en zona de suelo", () => {
    const fp = buildFloorplan(15);
    for (const p of fp.props) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.x + p.w).toBeLessThanOrEqual(COLS);
      expect(p.y + p.h).toBeLessThanOrEqual(ROWS);
    }
    // el anchor `work` que deriva el motor (mesa.y + h) debe seguir en el suelo
    for (const desk of fp.props.filter((p) => p.id === "wooden_desk_single")) {
      expect(desk.y + desk.h).toBeLessThan(ROWS);
    }
    for (const w of fp.wanderPoints) {
      expect(w.x).toBeGreaterThanOrEqual(0);
      expect(w.x).toBeLessThan(COLS);
      expect(w.y).toBeGreaterThanOrEqual(2); // filas 0-1 son pared
      expect(w.y).toBeLessThan(ROWS);
    }
  });

  it("los ids llevan imagen y disparan los anchors por palabra clave", () => {
    const fp = buildFloorplan(10);
    for (const p of fp.props) expect(fp.propImages[p.id]).toBeTruthy();
    const ids = fp.props.map((p) => p.id);
    expect(ids.some((i) => i.includes("desk"))).toBe(true); // → anchor work
    expect(ids.some((i) => i.includes("coffee_machine"))).toBe(true); // → utility
  });

  it("es determinista (misma planta entre renders)", () => {
    expect(buildFloorplan(10)).toEqual(buildFloorplan(10));
  });
});
