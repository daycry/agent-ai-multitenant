// @vitest-environment node
// Planta generada de La Oficina (ADR 0118). Regresión 2026-07-25: el mundo de
// serie traía 2 mesas y 2 puntos de deambular → con 10 agentes los personajes
// nacían apilados en el mismo tile y no se movían. La planta debe escalar.

import { describe, expect, it } from "vitest";

import {
  buildFloorplan,
  COLS,
  DESKS_WITH_LOUNGE,
  MAX_DESKS,
  ROWS,
  type Floorplan,
} from "@/lib/office/floorplan";

/** Tiles ocupados por props — misma cuenta que `PropSystem.getBlockedTiles`. */
function blockedTiles(fp: Floorplan): Set<string> {
  const blocked = new Set<string>();
  for (const p of fp.props) {
    for (let y = Math.floor(p.y); y < Math.ceil(p.y + p.h); y += 1) {
      for (let x = Math.floor(p.x); x < Math.ceil(p.x + p.w); x += 1) blocked.add(`${x},${y}`);
    }
  }
  return blocked;
}

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

  // --- Zonificación (2026-07-25: "que no se vea todo cargado de mesas") -----
  it("con la plantilla habitual hay zona de café, descanso y biblioteca abajo", () => {
    const fp = buildFloorplan(DESKS_WITH_LOUNGE);
    const ids = new Set(fp.props.map((p) => p.id));
    expect(ids.has("coffee_bar_counter")).toBe(true); // rincón del café
    expect(ids.has("coffee_machine")).toBe(true);
    expect(ids.has("area_rug_lounge")).toBe(true); // zona de descanso
    expect(ids.has("couch")).toBe(true);
    expect(ids.has("bookshelf_packed")).toBe(true); // biblioteca
    expect(ids.has("wooden_framed_whiteboard")).toBe(true);
    // …y esa zona vive en la franja INFERIOR, no entre las mesas.
    for (const id of ["coffee_bar_counter", "area_rug_lounge", "bookshelf_packed"]) {
      const p = fp.props.find((q) => q.id === id)!;
      expect(p.y).toBeGreaterThanOrEqual(9);
    }
  });

  it("los anchors que usa el motor caen en tiles LIBRES (mesa, cafetera, pizarra)", () => {
    for (const cap of [10, 15]) {
      const fp = buildFloorplan(cap);
      const blocked = blockedTiles(fp);
      // anchor `work` de cada mesa: (x+0.5, y+h) → tile (x, y+h)
      for (const d of fp.props.filter((p) => p.id === "wooden_desk_single")) {
        expect(blocked.has(`${d.x},${d.y + d.h}`)).toBe(false);
      }
      // anchor `utility` de la cafetera: (x+0.5, y+1.8)
      const machine = fp.props.find((p) => p.id === "coffee_machine")!;
      const utilTile = `${Math.floor(machine.x + 0.5)},${Math.floor(machine.y + 1.8)}`;
      expect(blocked.has(utilTile)).toBe(false);
      // anchor `social` de la pizarra: (x+1, y+1.5) — solo cuando hay pizarra
      const board = fp.props.find((p) => p.id === "wooden_framed_whiteboard");
      if (board) {
        const socialTile = `${Math.floor(board.x + 1)},${Math.floor(board.y + 1.5)}`;
        expect(blocked.has(socialTile)).toBe(false);
      }
    }
  });

  it("deja un pasillo alto libre (fila 2) para circular y acercarse a la pizarra", () => {
    const fp = buildFloorplan(10);
    const blocked = blockedTiles(fp);
    const freeInRow2 = Array.from({ length: COLS }, (_, x) => `${x},2`).filter(
      (t) => !blocked.has(t),
    );
    expect(freeInRow2.length).toBe(COLS);
  });
});
