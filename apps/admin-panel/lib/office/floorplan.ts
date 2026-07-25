/**
 * La Oficina — planta generada a medida del nº de agentes (ADR 0118).
 *
 * El mundo pixel-art que trae miniverse (`cozy-startup`) está diseñado para ~4
 * ciudadanos: **2 mesas** y **2 puntos de deambular**. Con 10 agentes eso rompía
 * la escena de dos formas (visto en vivo 2026-07-25):
 *
 *   * al nacer, el motor busca un anchor `wander` libre y, si no lo encuentra,
 *     cae al PRIMERO de la lista → todos los ciudadanos apilados en 1-2 tiles;
 *   * el deambular de un ciudadano ocioso (cada 5-13 s pide un anchor
 *     `wander`/`social`/`utility`) fallaba por reserva/bloqueo → se quedaban
 *     QUIETOS.
 *
 * Este módulo genera la planta (props + puntos de deambular) escalada a la
 * plantilla: una MESA por agente (cada mesa aporta un anchor `work`, donde el
 * personaje se sienta al trabajar) más decenas de puntos libres por los que
 * deambular. Los anchors los deriva el motor del ID del prop por palabra clave
 * (`desk`→work, `coffee_machine`→utility, `couch`→rest, `whiteboard`→social), así
 * que los ids son LÓGICOS y `propImages` los mapea a los PNG que servimos.
 *
 * Puro y determinista (sin azar) → testeable y estable entre renders.
 */

export interface FloorplanProp {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  layer: "below" | "above";
}

export interface WanderPoint {
  name: string;
  x: number;
  y: number;
}

export interface Floorplan {
  props: FloorplanProp[];
  wanderPoints: WanderPoint[];
  /** id lógico → ruta del PNG, relativa a la base del mundo. */
  propImages: Record<string, string>;
  /** Mesas colocadas = plazas de trabajo (anchors `work`). */
  deskCount: number;
}

// Rejilla del mundo (world.json de cozy-startup): 16x12 tiles de 32px, con las
// dos primeras filas de pared y el resto suelo.
export const COLS = 16;
export const ROWS = 12;
const FLOOR_TOP = 2;

// Una "unidad de puesto" = mesa 3x2 + silla 1x1 justo debajo; el anchor `work`
// que el motor deriva cae en (x+0.5, y+2) → tile (x, y+2), libre por diseño.
const DESK_W = 3;
const DESK_H = 2;
const BAND_XS = [0, 3, 6, 9, 12];
const BAND_YS = [2, 5, 8];
export const MAX_DESKS = BAND_XS.length * BAND_YS.length; // 15

const PROP_IMAGES: Record<string, string> = {
  wooden_desk_single: "world_assets/props/prop_0_wooden_desk_single.png",
  ergonomic_chair: "world_assets/props/prop_1_ergonomic_chair.png",
  tall_potted_plant: "world_assets/props/prop_2_tall_potted_plant.png",
  bookshelf_packed: "world_assets/props/prop_3_bookshelf_packed.png",
  // ids LÓGICOS elegidos para que el motor derive su anchor por palabra clave:
  coffee_machine: "world_assets/props/prop_5_espresso_machine.png", // → utility
  couch: "world_assets/props/prop_7_bean_bag_chair_dark.png", // → rest
  low_coffee_table: "world_assets/props/prop_9_low_coffee_table.png",
  large_window: "world_assets/props/prop_10_large_window.png",
  area_rug_lounge: "world_assets/props/prop_12_area_rug_lounge.png",
};

function tilesOf(p: FloorplanProp): string[] {
  // Misma cuenta que `PropSystem.getBlockedTiles` (floor/ceil).
  const out: string[] = [];
  for (let y = Math.floor(p.y); y < Math.ceil(p.y + p.h); y += 1) {
    for (let x = Math.floor(p.x); x < Math.ceil(p.x + p.w); x += 1) {
      out.push(`${x},${y}`);
    }
  }
  return out;
}

/**
 * Planta para `capacity` agentes. `capacity` se redondea a bandas completas de
 * 5 puestos (hasta {@link MAX_DESKS}); por encima, los agentes extra comparten
 * el deambular (la escena no se rompe, solo no hay mesa para todos).
 */
export function buildFloorplan(capacity: number): Floorplan {
  const wanted = Math.max(1, Math.floor(capacity) || 1);
  const bands = Math.min(BAND_YS.length, Math.max(1, Math.ceil(wanted / BAND_XS.length)));

  const props: FloorplanProp[] = [];
  const workAnchorTiles = new Set<string>();

  // Ventanas en la pared (decorativas, sin anchors) — dan profundidad al piso.
  for (const x of [1.25, 6.5, 11.75]) {
    props.push({ id: "large_window", x, y: 0, w: 3, h: 2, layer: "below" });
  }

  // Puestos de trabajo: mesa + silla, en bandas.
  let deskCount = 0;
  for (const y of BAND_YS.slice(0, bands)) {
    for (const x of BAND_XS) {
      props.push({ id: "wooden_desk_single", x, y, w: DESK_W, h: DESK_H, layer: "below" });
      props.push({ id: "ergonomic_chair", x: x + 1, y: y + DESK_H, w: 1, h: 1, layer: "above" });
      workAnchorTiles.add(`${x},${y + DESK_H}`);
      deskCount += 1;
    }
  }

  // Columna libre de la derecha: cafetera (anchor `utility`, donde va el
  // revisor "pensando") + una planta. Nunca pisa las mesas (cols 0-14).
  props.push({ id: "coffee_machine", x: COLS - 1, y: FLOOR_TOP, w: 1, h: 1, layer: "below" });
  props.push({
    id: "tall_potted_plant",
    x: COLS - 1,
    y: FLOOR_TOP + 4,
    w: 1,
    h: 2,
    layer: "below",
  });

  // Zona de descanso solo si sobran filas abajo (con 3 bandas el piso está lleno).
  if (bands <= 2) {
    props.push({ id: "area_rug_lounge", x: 10.5, y: 8.5, w: 4, h: 3, layer: "below" });
    props.push({ id: "couch", x: 10, y: 9, w: 1.5, h: 1.5, layer: "below" });
    props.push({ id: "low_coffee_table", x: 12, y: 9.5, w: 1.5, h: 1.5, layer: "below" });
    props.push({ id: "bookshelf_packed", x: 0, y: 8.5, w: 2.3, h: 2, layer: "below" });
  }

  // Puntos de deambular: TODO tile de suelo libre (ni bajo un prop ni asiento de
  // nadie). Es lo que permite al motor esparcir el spawn y pasear a los ociosos.
  const blocked = new Set<string>();
  for (const p of props) for (const t of tilesOf(p)) blocked.add(t);

  const wanderPoints: WanderPoint[] = [];
  for (let y = FLOOR_TOP; y < ROWS; y += 1) {
    for (let x = 0; x < COLS; x += 1) {
      const key = `${x},${y}`;
      if (blocked.has(key) || workAnchorTiles.has(key)) continue;
      wanderPoints.push({ name: `wander_${x}_${y}`, x, y });
    }
  }

  return { props, wanderPoints, propImages: { ...PROP_IMAGES }, deskCount };
}
