"use client";

/**
 * La Oficina — renderer 2D en <canvas> (ADR 0118, sistema estilo miniverse:
 * grid + sprites + motor de animación). Piso cenital: salas (mesas por plan), la
 * puerta del humano y la planta común. Los agentes son personajes que se MUEVEN
 * DE VERDAD, como en miniverse: los libres DEAMBULAN por la planta (caminan a un
 * punto, pausan, vuelven a caminar), los que trabajan caminan a su mesa y teclean,
 * los escalados pasean por la puerta del humano. Animación de andar (contoneo +
 * sombra), burbujas de diálogo y estados. Solo refleja lo que `buildWorld` decide.
 *
 * El canvas es aria-hidden decorativo; la lista semántica (accesible + tests)
 * vive en la página. jsdom-safe: sin contexto 2D es un no-op.
 */

import { useEffect, useRef } from "react";

import {
  roleEmoji,
  STATE_BADGE,
  WORLD_H,
  WORLD_W,
  type Citizen,
  type Rect,
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
  shadow: string;
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
  shadow: "rgba(0,0,0,0.14)",
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
  shadow: "rgba(0,0,0,0.35)",
  bubbleFill: "#26251f",
  bubbleStroke: "#3a382f",
  bubbleText: "#e6dfce",
};

interface Mover {
  x: number;
  y: number;
  tx: number;
  ty: number;
  moving: boolean;
  pauseUntil: number;
  faceLeft: boolean;
  seeded: boolean;
}

const HIT_RADIUS = 30;
const WALK_SPEED = 115; // unidades-mundo por segundo
const EMOJI_FONT =
  '30px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",system-ui,sans-serif';
const BADGE_FONT = '15px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif';
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

function randInRect(r: Rect, pad = 26): { x: number; y: number } {
  return {
    x: r.x + pad + Math.random() * Math.max(1, r.w - pad * 2),
    y: r.y + pad + Math.random() * Math.max(1, r.h - pad * 2),
  };
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
  const moversRef = useRef<Map<string, Mover>>(new Map());
  const scaleRef = useRef(1);

  worldRef.current = world;
  onSelectRef.current = onSelect;

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    // jsdom (tests) no implementa el contexto 2D ni ResizeObserver/rAF → no-op.
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
    const g = ctx;

    let raf = 0;
    let cssW = 0;
    let last = performance.now();

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      cssW = wrap.clientWidth;
      const cssH = (cssW * WORLD_H) / WORLD_W;
      scaleRef.current = cssW / WORLD_W;
      canvas.style.height = `${cssH}px`;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    const entrance = { x: WORLD_W / 2, y: WORLD_H - 8 };

    // Zona por la que se mueve/pasea cada ciudadano según su estado.
    const roamRect = (c: Citizen, w: World): Rect | null => {
      if (c.zone === "door") return w.door;
      if (c.zone === "lounge") return w.commons; // los libres deambulan por la planta
      return null; // desk: se queda en su silla (no deambula)
    };

    const draw = (nowMs: number) => {
      const w = worldRef.current;
      const pal = document.documentElement.classList.contains("dark") ? DARK : LIGHT;
      const now = nowMs / 1000;
      const dt = Math.min(0.05, (nowMs - last) / 1000);
      last = nowMs;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const s = (cssW / WORLD_W) * dpr;
      g.setTransform(s, 0, 0, s, 0, 0);
      g.clearRect(0, 0, WORLD_W, WORLD_H);

      // Suelo + baldosas.
      g.fillStyle = pal.floor;
      g.fillRect(0, 0, WORLD_W, WORLD_H);
      g.strokeStyle = pal.tile;
      g.lineWidth = 1;
      for (let x = 0; x <= WORLD_W; x += 40) {
        g.beginPath();
        g.moveTo(x, 0);
        g.lineTo(x, WORLD_H);
        g.stroke();
      }
      for (let y = 0; y <= WORLD_H; y += 40) {
        g.beginPath();
        g.moveTo(0, y);
        g.lineTo(WORLD_W, y);
        g.stroke();
      }

      // Zonas.
      const drawZone = (r: Rect, fill: string, stroke: string, label: string) => {
        g.fillStyle = fill;
        g.strokeStyle = stroke;
        g.lineWidth = 1.5;
        roundRect(g, r.x, r.y, r.w, r.h, 14);
        g.fill();
        g.stroke();
        g.fillStyle = pal.zoneLabel;
        g.font = LABEL_FONT;
        g.textAlign = "left";
        g.textBaseline = "top";
        g.fillText(label, r.x + 14, r.y + 12);
      };
      drawZone(w.door, pal.doorFill, pal.doorStroke, "🚪  Puerta del humano");
      drawZone(w.lounge, pal.loungeFill, pal.loungeStroke, "🛋️  Planta / descanso");

      // Mesas (salas por plan).
      for (const desk of w.desks) {
        g.fillStyle = pal.deskFill;
        g.strokeStyle = pal.deskStroke;
        g.lineWidth = 1.5;
        roundRect(g, desk.x, desk.y, desk.w, desk.h, 14);
        g.fill();
        g.stroke();
        g.fillStyle = pal.deskLabel;
        g.font = LABEL_FONT;
        g.textAlign = "left";
        g.textBaseline = "top";
        const title = desk.title.length > 30 ? `${desk.title.slice(0, 30)}…` : desk.title;
        g.fillText(`🗄️  ${title}`, desk.x + 12, desk.y + 10);
      }

      // Prune movers de ciudadanos que ya no están.
      const alive = new Set(w.citizens.map((c) => c.key));
      for (const k of [...moversRef.current.keys()]) if (!alive.has(k)) moversRef.current.delete(k);

      for (const c of w.citizens) {
        let m = moversRef.current.get(c.key);
        if (!m) {
          // Entra caminando desde la puerta de la oficina.
          m = {
            x: entrance.x,
            y: entrance.y,
            tx: c.x,
            ty: c.y,
            moving: true,
            pauseUntil: 0,
            faceLeft: false,
            seeded: true,
          };
          moversRef.current.set(c.key, m);
        }

        // Objetivo: la silla (desk) SIEMPRE la posición actual del asiento; en
        // door/lounge el objetivo es el punto de deambulación (se renueva al llegar).
        if (c.zone === "desk") {
          m.tx = c.x;
          m.ty = c.y;
        }

        const dx = m.tx - m.x;
        const dy = m.ty - m.y;
        const dist = Math.hypot(dx, dy);
        const canWalk = c.state !== "dizzy";
        if (canWalk && dist > 1.5) {
          const step = Math.min(dist, WALK_SPEED * dt);
          m.x += (dx / dist) * step;
          m.y += (dy / dist) * step;
          m.moving = true;
          if (Math.abs(dx) > 0.5) m.faceLeft = dx < 0;
        } else {
          if (m.moving) {
            // Acaba de llegar → pausa antes del próximo destino.
            m.moving = false;
            m.pauseUntil = now + 0.4 + Math.random() * (c.zone === "door" ? 1.4 : 2.2);
          } else if (now > m.pauseUntil) {
            const area = roamRect(c, w);
            if (area) {
              const p = randInRect(area);
              m.tx = p.x;
              m.ty = p.y;
            }
          }
        }

        // Sombra en el suelo (vende el "andar/saltar").
        const walkBob = m.moving ? Math.abs(Math.sin(now * 9)) * 4 : 0;
        const waddle = m.moving ? Math.sin(now * 9) * 0.12 : 0;
        g.fillStyle = pal.shadow;
        g.beginPath();
        g.ellipse(m.x, m.y + 16, 12, 4, 0, 0, Math.PI * 2);
        g.fill();

        g.save();
        g.translate(m.x, m.y - walkBob);
        // Animación por estado sobre el cuerpo.
        let idleBob = 0;
        const phase = (c.key.charCodeAt(0) || 0) % 7;
        if (!m.moving) {
          if (c.state === "working") idleBob = Math.sin(now * 6 + phase) * 2.2;
          else if (c.state === "waiting_human") idleBob = Math.sin(now * 3 + phase) * 1.6;
        }
        g.font = EMOJI_FONT;
        g.textAlign = "center";
        g.textBaseline = "middle";
        if (c.state === "dizzy") {
          g.save();
          g.rotate((now * 3 + phase) % (Math.PI * 2));
          g.fillText(roleEmoji(c.role), 0, 0);
          g.restore();
        } else {
          g.save();
          g.rotate(waddle);
          g.fillText(roleEmoji(c.role), 0, idleBob);
          g.restore();
        }

        // Badge de estado.
        g.font = BADGE_FONT;
        g.fillText(STATE_BADGE[c.state], 15, -12);
        if (c.state === "idle" && !m.moving) g.fillText("💤", 16, -20);

        // Nombre.
        g.font = NAME_FONT;
        g.fillStyle = pal.name;
        g.textBaseline = "top";
        const nm = c.name.length > 16 ? `${c.name.slice(0, 16)}…` : c.name;
        g.fillText(nm, 0, 20);

        // Burbuja de diálogo (estados "hablando", quieto o andando).
        if (
          c.bubble &&
          (c.state === "working" || c.state === "reviewing" || c.state === "waiting_human")
        ) {
          drawBubble(g, c.bubble, pal);
        }
        g.restore();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    const pick = (ev: MouseEvent): Citizen | null => {
      const rect = canvas.getBoundingClientRect();
      const wx = (ev.clientX - rect.left) / scaleRef.current;
      const wy = (ev.clientY - rect.top) / scaleRef.current;
      let best: Citizen | null = null;
      let bestD = HIT_RADIUS * HIT_RADIUS;
      for (const c of worldRef.current.citizens) {
        const m = moversRef.current.get(c.key);
        const px = m?.x ?? c.x;
        const py = m?.y ?? c.y;
        const d = (px - wx) ** 2 + (py - wy) ** 2;
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
  const by = -24 - boxH;
  ctx.fillStyle = pal.bubbleFill;
  ctx.strokeStyle = pal.bubbleStroke;
  ctx.lineWidth = 1;
  roundRect(ctx, bx, by, boxW, boxH, 8);
  ctx.fill();
  ctx.stroke();
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
