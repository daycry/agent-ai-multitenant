"use client";

/**
 * La Oficina — monta el MOTOR REAL de miniverse (@miniverse/core vendorizado,
 * github.com/ianscott313/miniverse, MIT) en la página, alimentado con nuestra
 * telemetría. El motor hace TODO el mundo pixel-art: suelo/paredes con tilemap,
 * props (mesas, sillas, plantas, cafetera…), y personajes con sprite-sheets que
 * CAMINAN/deambulan/teclean con ciclo de andar y burbujas — idéntico al repo.
 *
 * Nosotros solo: (1) servimos su mundo + assets desde /public/miniverse, (2) le
 * damos un `signal` mock cuyo fetcher devuelve nuestros `AgentStatus[]`, y (3)
 * enrutamos el clic en un personaje a su run. Cliente puro (canvas/Image): el
 * motor se importa dinámicamente en el efecto; en SSR/tests es un no-op (la
 * lista semántica de la página cubre accesibilidad y tests).
 */

import { useEffect, useRef } from "react";

import type { PropLayout, SceneConfig, SpriteSheetConfig } from "@/vendor/miniverse-core";
import type { AgentStatus } from "@/lib/office/miniverse-bridge";

const WORLD = "cozy-startup";
const BASE = `/miniverse/worlds/${WORLD}`;
const SPRITES = ["morty", "dexter", "nova", "rio"];
const TILE = 32;

interface WanderPoint {
  name: string;
  x: number;
  y: number;
}
interface WorldData {
  gridCols?: number;
  gridRows?: number;
  floor?: string[][];
  tiles?: Record<string, string>;
  propImages?: Record<string, string>;
  props?: PropLayout;
  wanderPoints?: WanderPoint[];
}

/** Config de sprite del ciudadano — como el `createStandardSpriteConfig` del
 * motor pero con NUESTRA base (`/miniverse/universal_assets/citizens`). */
function spriteConfig(sprite: string): SpriteSheetConfig {
  const b = "/miniverse/universal_assets/citizens";
  return {
    sheets: { walk: `${b}/${sprite}_walk.png`, actions: `${b}/${sprite}_actions.png` },
    animations: {
      idle_down: { sheet: "actions", row: 3, frames: 4, speed: 0.5 },
      idle_up: { sheet: "actions", row: 3, frames: 4, speed: 0.5 },
      walk_down: { sheet: "walk", row: 0, frames: 4, speed: 0.15 },
      walk_up: { sheet: "walk", row: 1, frames: 4, speed: 0.15 },
      walk_left: { sheet: "walk", row: 2, frames: 4, speed: 0.15 },
      walk_right: { sheet: "walk", row: 3, frames: 4, speed: 0.15 },
      working: { sheet: "actions", row: 0, frames: 4, speed: 0.3 },
      sleeping: { sheet: "actions", row: 1, frames: 2, speed: 0.8 },
      talking: { sheet: "actions", row: 2, frames: 4, speed: 0.15 },
    },
    frameWidth: 64,
    frameHeight: 64,
  };
}

/** Transforma el world.json (floor de claves + tiles) en un SceneConfig del
 * motor, resolviendo las rutas de tiles relativas contra la base del mundo. */
function buildSceneConfig(
  cols: number,
  rows: number,
  floor: string[][] | undefined,
  tiles: Record<string, string> | undefined,
  basePath: string,
): SceneConfig {
  const safeFloor: string[][] = floor ?? Array.from({ length: rows }, () => Array(cols).fill(""));
  const walkable: boolean[][] = [];
  for (let r = 0; r < rows; r += 1) {
    walkable[r] = [];
    for (let c = 0; c < cols; c += 1) walkable[r][c] = (safeFloor[r]?.[c] ?? "") !== "";
  }
  const resolvedTiles: Record<string, string> = { ...(tiles ?? {}) };
  for (const [key, src] of Object.entries(resolvedTiles)) {
    if (/^(blob:|data:|https?:\/\/)/.test(src)) continue;
    const clean = src.startsWith("/") ? src.slice(1) : src;
    resolvedTiles[key] = `${basePath}/${clean}`;
  }
  return {
    name: "main",
    tileWidth: TILE,
    tileHeight: TILE,
    layers: [safeFloor],
    walkable,
    locations: {},
    tiles: resolvedTiles,
  };
}

export function OfficeMiniverse({
  getStatuses,
  onSelectAgent,
}: {
  getStatuses: () => AgentStatus[];
  onSelectAgent: (agentId: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const getStatusesRef = useRef(getStatuses);
  const onSelectRef = useRef(onSelectAgent);
  getStatusesRef.current = getStatuses;
  onSelectRef.current = onSelectAgent;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let mv: { stop: () => void } | null = null;
    let disposed = false;

    (async () => {
      try {
        const mod = await import("@/vendor/miniverse-core");
        const wd: WorldData = await fetch(`${BASE}/world.json`).then((r) => r.json());
        const cols = wd.gridCols ?? 16;
        const rows = wd.gridRows ?? 12;
        const sceneConfig = buildSceneConfig(cols, rows, wd.floor, wd.tiles, BASE);
        const spriteSheets: Record<string, SpriteSheetConfig> = {};
        for (const s of SPRITES) spriteSheets[s] = spriteConfig(s);
        if (disposed) return;

        const instance = new mod.Miniverse({
          container,
          world: WORLD,
          scene: "main",
          signal: { type: "mock", mockData: () => getStatusesRef.current(), interval: 1500 },
          citizens: [],
          scale: 2,
          width: cols * TILE,
          height: rows * TILE,
          sceneConfig,
          spriteSheets,
          defaultSprites: SPRITES,
          autoSpawn: true,
          objects: [],
        });
        mv = instance;

        const props = new mod.PropSystem(TILE, 2);
        await Promise.all(
          Object.entries(wd.propImages ?? {}).map(([id, src]) =>
            props.loadSprite(id, `${BASE}/${String(src).replace(/^\//, "")}`),
          ),
        );
        props.setLayout(wd.props ?? []);
        if (wd.wanderPoints) props.setWanderPoints(wd.wanderPoints);
        props.setDeadspaceCheck((c, r) => (instance.getFloorLayer()?.[r]?.[c] ?? "") === "");
        instance.setTypedLocations(props.getLocations());
        instance.updateWalkability(props.getBlockedTiles());
        if (disposed) {
          instance.stop();
          return;
        }
        await instance.start();
        instance.addLayer({
          order: 5,
          render: (ctx: CanvasRenderingContext2D) => props.renderBelow(ctx),
        });
        instance.addLayer({
          order: 15,
          render: (ctx: CanvasRenderingContext2D) => props.renderAbove(ctx),
        });
        instance.on("citizen:click", (data: unknown) => {
          const d = data as { agentId?: string };
          if (d?.agentId) onSelectRef.current(d.agentId);
        });

        const canvas = instance.getCanvas();
        canvas.style.width = "100%";
        canvas.style.height = "auto";
        canvas.style.imageRendering = "pixelated";
        canvas.style.display = "block";
      } catch (err) {
        // SSR/jsdom o assets ausentes: nunca romper la página (la lista semántica manda).
        console.error("La Oficina (miniverse) no arrancó:", err);
      }
    })();

    return () => {
      disposed = true;
      try {
        mv?.stop();
      } catch {
        /* noop */
      }
      container.innerHTML = "";
    };
  }, []);

  return (
    <div
      ref={containerRef}
      aria-hidden="true"
      className="border-border w-full overflow-hidden rounded-2xl border shadow-sm"
    />
  );
}
