"use client";

/**
 * La Oficina — renderer 2D en <canvas> (ADR 0118, sistema estilo miniverse:
 * grid + sprites + motor de animación). Pinta un piso cenital: salas (mesas por
 * plan), la puerta del humano y el sofá; los agentes son personajes-emoji que
 * CAMINAN a su sitio según su run real, con burbuja de diálogo y animación por
 * estado (teclear/mareo/dormir/esperar). Solo pinta lo que `buildWorld` decide.
 *
 * Determinismo/estado: las posiciones-objetivo vienen del mundo (puro); aquí solo
 * se interpola la posición ACTUAL hacia el objetivo (efecto "andar"). Accesible:
 * el canvas es aria-hidden decorativo; la lista semántica vive en la página.
 */

import { useEffect, useRef } from "react";

import {
  roleEmoji,
  STATE_BADGE,
  WORLD_H,
  WORLD_W,
  type Citizen,
  type World,
} from "@/lib/office/world";

interface Palette {
  floor: string;
  tile: string;
  deskFill: string;
  deskStroke: string;
  deskLabel: string;
  doorFill: string;
  doorStroke: string;
  loungeFill: string;
  loungeStroke: string;
  zoneLabel: string;
  name: string;
  bubbleFill: string;
  bubbleStroke: string;
  bubbleText: string;
}

const LIGHT: Palette = {
  floor: "#f4f1ea",
  tile: "rgba(0,0,0,0.04)",
  deskFill: "#ffffff",
  deskStroke: "#d9d2c4",
  deskLabel: "#4b4436",
  doorFill: "rgba(245,158,11,0.10)",
  doorStroke: "rgba(245,158,11,0.45)",
  loungeFill: "rgba(120,120,120,0.08)",
  loungeStroke: "rgba(120,120,120,0.28)",
  zoneLabel: "#6b6455",
  name: "#3a352c",
  bubbleFill: "#ffffff",
  bubbleStroke: "#d9d2c4",
  bubbleText: "#2c2820",
};

const DARK: Palette = {
  floor: "#1b1a17",
  tile: "rgba(255,255,255,0.035)",
  deskFill: "#26251f",
  deskStroke: "#3a382f",
  deskLabel: "#cbc4b2",
  doorFill: "rgba(245,158,11,0.12)",
  doorStroke: "rgba(245,158,11,0.40)",
  loungeFill: "rgba(255,255,255,0.05)",
  loungeStroke: "rgba(255,255,255,0.14)",
  zoneLabel: "#9a9280",
  name: "#d6cfbe",
  bubbleFill: "#26251f",
  bubbleStroke: "#3a382f",
  bubbleText: "#e6dfce",
};

interface LivePos {
  x: number;
  y: number;
  born: boolean;
}

const HIT_RADIUS = 30;
const EMOJI_FONT =
  '30px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",system-ui,sans-serif';
const LABEL_FONT = '600 13px system-ui,-apple-system,"Segoe UI",sans-serif';
const NAME_FONT = '11px system-ui,-apple-system,"Segoe UI",sans-serif';
const BUBBLE_FONT = '11px system-ui,-apple-system,"Segoe UI",sans-serif';

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

export function OfficeCanvas({
  world,
  onSelect,
}: {
  world: World;
  onSelect: (c: Citizen) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const worldRef = useRef(world);
  const onSelectRef = useRef(onSelect);
  const posRef = useRef<Map<string, LivePos>>(new Map());
  const scaleRef = useRef(1);

  worldRef.current = world;
  onSelectRef.current = onSelect;

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    // jsdom (tests) no implementa el contexto 2D ni ResizeObserver/rAF: getContext
    // devuelve null o lanza → el renderer es un no-op y la capa semántica manda.
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      return;
    }
    if (!ctx) return;
    if (typeof ResizeObserver === "undefined" || typeof requestAnimationFrame === "undefined") {
      return;
    }

    let raf = 0;
    let cssW = 0;
    let cssH = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      cssW = wrap.clientWidth;
      cssH = (cssW * WORLD_H) / WORLD_W;
      scaleRef.current = cssW / WORLD_W;
      canvas.style.height = `${cssH}px`;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    const spawnPoint = () => ({ x: WORLD_W / 2, y: WORLD_H - 6 });

    const draw = () => {
      const w = worldRef.current;
      const pal = document.documentElement.classList.contains("dark") ? DARK : LIGHT;
      const now = performance.now() / 1000;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const s = (cssW / WORLD_W) * dpr;
      ctx.setTransform(s, 0, 0, s, 0, 0);
      ctx.clearRect(0, 0, WORLD_W, WORLD_H);

      // Suelo + baldosas.
      ctx.fillStyle = pal.floor;
      ctx.fillRect(0, 0, WORLD_W, WORLD_H);
      ctx.strokeStyle = pal.tile;
      ctx.lineWidth = 1;
      for (let x = 0; x <= WORLD_W; x += 40) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, WORLD_H);
        ctx.stroke();
      }
      for (let y = 0; y <= WORLD_H; y += 40) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(WORLD_W, y);
        ctx.stroke();
      }

      // Zonas: puerta del humano + sofá.
      const drawZone = (r: typeof w.door, fill: string, stroke: string, label: string) => {
        ctx.fillStyle = fill;
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 1.5;
        roundRect(ctx, r.x, r.y, r.w, r.h, 14);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = pal.zoneLabel;
        ctx.font = LABEL_FONT;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        ctx.fillText(label, r.x + 14, r.y + 12);
      };
      drawZone(w.door, pal.doorFill, pal.doorStroke, "🚪  Puerta del humano");
      drawZone(w.lounge, pal.loungeFill, pal.loungeStroke, "🛋️  Descanso");

      // Mesas (salas por plan).
      for (const desk of w.desks) {
        ctx.fillStyle = pal.deskFill;
        ctx.strokeStyle = pal.deskStroke;
        ctx.lineWidth = 1.5;
        roundRect(ctx, desk.x, desk.y, desk.w, desk.h, 14);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = pal.deskLabel;
        ctx.font = LABEL_FONT;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        const title = desk.title.length > 30 ? `${desk.title.slice(0, 30)}…` : desk.title;
        ctx.fillText(`🗄️  ${title}`, desk.x + 12, desk.y + 10);
      }

      // Prune posiciones de ciudadanos que ya no están.
      const alive = new Set(w.citizens.map((c) => c.key));
      for (const k of [...posRef.current.keys()]) if (!alive.has(k)) posRef.current.delete(k);

      // Ciudadanos: interpolar hacia su objetivo (efecto andar) y pintar.
      for (const c of w.citizens) {
        let p = posRef.current.get(c.key);
        if (!p) {
          p = { ...spawnPoint(), born: true };
          posRef.current.set(c.key, p);
        }
        p.x += (c.x - p.x) * 0.14;
        p.y += (c.y - p.y) * 0.14;

        ctx.save();
        ctx.translate(p.x, p.y);

        // Animación por estado.
        let bob = 0;
        const phase = (c.key.charCodeAt(0) || 0) % 7;
        if (c.state === "working") bob = Math.sin(now * 5 + phase) * 2.2;
        else if (c.state === "waiting_human") bob = Math.sin(now * 3 + phase) * 1.6;
        else if (c.state === "reviewing") bob = Math.sin(now * 3.5 + phase) * 1.4;

        ctx.font = EMOJI_FONT;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        if (c.state === "dizzy") {
          ctx.save();
          ctx.rotate((now * 3 + phase) % (Math.PI * 2));
          ctx.fillText(roleEmoji(c.role), 0, 0);
          ctx.restore();
        } else {
          ctx.fillText(roleEmoji(c.role), 0, bob);
        }

        // Badge de estado (esquina).
        ctx.font = '15px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif';
        ctx.fillText(STATE_BADGE[c.state], 15, bob - 12);
        if (c.state === "idle") ctx.fillText("💤", 16, bob - 20);

        // Nombre.
        ctx.font = NAME_FONT;
        ctx.fillStyle = pal.name;
        ctx.textBaseline = "top";
        const nm = c.name.length > 16 ? `${c.name.slice(0, 16)}…` : c.name;
        ctx.fillText(nm, 0, 20);

        // Burbuja de diálogo (solo con texto y en estados "hablando").
        if (
          c.bubble &&
          (c.state === "working" || c.state === "reviewing" || c.state === "waiting_human")
        ) {
          drawBubble(ctx, c.bubble, pal);
        }
        ctx.restore();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    // Hit-test compartido por click y hover.
    const pick = (ev: MouseEvent): Citizen | null => {
      const rect = canvas.getBoundingClientRect();
      const wx = (ev.clientX - rect.left) / scaleRef.current;
      const wy = (ev.clientY - rect.top) / scaleRef.current;
      let best: Citizen | null = null;
      let bestD = HIT_RADIUS * HIT_RADIUS;
      for (const c of worldRef.current.citizens) {
        const p = posRef.current.get(c.key) ?? c;
        const d = (p.x - wx) ** 2 + (p.y - wy) ** 2;
        if (d < bestD) {
          bestD = d;
          best = c;
        }
      }
      return best;
    };
    const onClick = (ev: MouseEvent) => {
      const c = pick(ev);
      if (c) onSelectRef.current(c);
    };
    const onMove = (ev: MouseEvent) => {
      canvas.style.cursor = pick(ev) ? "pointer" : "default";
    };
    canvas.addEventListener("click", onClick);
    canvas.addEventListener("mousemove", onMove);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("mousemove", onMove);
    };
  }, []);

  return (
    <div ref={wrapRef} className="w-full">
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="border-border w-full rounded-2xl border shadow-sm"
        style={{ display: "block" }}
      />
    </div>
  );
}

function drawBubble(ctx: CanvasRenderingContext2D, text: string, pal: Palette): void {
  ctx.font = BUBBLE_FONT;
  const maxW = 168;
  // Envolver a un máximo de 2 líneas.
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let cur = "";
  for (const word of words) {
    const trial = cur ? `${cur} ${word}` : word;
    if (ctx.measureText(trial).width > maxW && cur) {
      lines.push(cur);
      cur = word;
      if (lines.length === 2) break;
    } else {
      cur = trial;
    }
  }
  if (lines.length < 2 && cur) lines.push(cur);
  if (lines.length === 2 && ctx.measureText(lines[1]).width > maxW) {
    while (lines[1].length > 3 && ctx.measureText(`${lines[1]}…`).width > maxW) {
      lines[1] = lines[1].slice(0, -1);
    }
    lines[1] = `${lines[1]}…`;
  }
  const lineH = 14;
  const boxW = Math.min(maxW + 16, Math.max(...lines.map((l) => ctx.measureText(l).width)) + 16);
  const boxH = lines.length * lineH + 10;
  const bx = -boxW / 2;
  const by = -22 - boxH;
  ctx.fillStyle = pal.bubbleFill;
  ctx.strokeStyle = pal.bubbleStroke;
  ctx.lineWidth = 1;
  roundRect(ctx, bx, by, boxW, boxH, 8);
  ctx.fill();
  ctx.stroke();
  // Rabito.
  ctx.beginPath();
  ctx.moveTo(-5, by + boxH);
  ctx.lineTo(0, by + boxH + 6);
  ctx.lineTo(5, by + boxH);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = pal.bubbleText;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  lines.forEach((l, i) => ctx.fillText(l, 0, by + 5 + i * lineH));
}
