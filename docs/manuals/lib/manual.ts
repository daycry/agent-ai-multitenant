/**
 * Núcleo reutilizable del generador de manuales.
 *
 * Un manual se define de forma DECLARATIVA (título, intro, pasos). Cada paso
 * navega a una ruta, hace acciones opcionales, captura un pantallazo y se
 * acompaña de una explicación. Al final se renderiza un PDF (Chromium print)
 * en docs/manuals/pdf/<slug>.pdf con TODOS los pantallazos embebidos.
 *
 * Reutilizable: cambia la UI -> reejecuta `npm run manuals` y los PDF se
 * regeneran. Si una pantalla no existe / no es accesible, el paso se registra
 * como "no disponible" en el PDF en lugar de romper el manual entero.
 */
import { Page } from "@playwright/test";
import fs from "fs";
import path from "path";
import { login } from "./auth";

export type Step = {
  /** Título del paso (encabezado en el PDF). */
  title: string;
  /** Explicación en HTML simple (<p>, <ul>, <b>, <code>…). Detallada y paso a paso. */
  body: string;
  /** Ruta a navegar (relativa a baseURL). Si se omite, captura la pantalla actual. */
  goto?: string;
  /** Interacciones antes del pantallazo (abrir un modal, hacer click, etc.). */
  action?: (page: Page) => Promise<void>;
  /** Pantallazo de página completa (def: true). */
  fullPage?: boolean;
  /** Espera extra (ms) tras navegar/actuar, para que asienten datos/animaciones. */
  settleMs?: number;
};

export type ManualDef = {
  /** Nombre del fichero de salida (sin extensión) y de la carpeta de assets. */
  slug: string;
  /** Número de orden para el índice de manuales (00, 01, …). */
  order: string;
  title: string;
  /** A quién va dirigido (rol). */
  audience: string;
  /** Intro en HTML simple (uno o varios <p>). */
  intro: string;
  steps: Step[];
};

const HERE = __dirname;
const OUT_PDF = path.resolve(HERE, "..", "pdf");
const OUT_ASSETS = path.resolve(HERE, "..", "assets");

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Captura los pasos de un manual y renderiza el PDF. */
export async function generateManual(page: Page, def: ManualDef): Promise<void> {
  const captured: { title: string; body: string; img?: string; note?: string }[] = [];
  const assetsDir = path.join(OUT_ASSETS, def.slug);
  fs.mkdirSync(assetsDir, { recursive: true });

  for (let i = 0; i < def.steps.length; i++) {
    const step = def.steps[i];
    const idx = String(i + 1).padStart(2, "0");
    let imgB64: string | undefined;
    let note: string | undefined;
    try {
      if (step.goto) {
        await page
          .goto(step.goto, { waitUntil: "domcontentloaded", timeout: 30_000 })
          .catch(() => {});
        // Si la app nos rebotó a /login (sesión aún no aplicada) y NO pedíamos
        // /login, re-autenticamos y reintentamos: así las pantallas internas
        // (dashboard, etc.) se capturan YA renderizadas, no la pantalla de login.
        const onLogin = new URL(page.url()).pathname.startsWith("/login");
        if (!step.goto.includes("/login") && onLogin) {
          await login(page);
          await page
            .goto(step.goto, { waitUntil: "domcontentloaded", timeout: 30_000 })
            .catch(() => {});
        }
        await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => {});
      }
      if (step.action) await step.action(page);
      // Espera a que la red quede inactiva (datos cargados) + un settle visual.
      await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
      await page.waitForTimeout(step.settleMs ?? 1200);
      const buf = await page.screenshot({ fullPage: step.fullPage ?? true });
      fs.writeFileSync(path.join(assetsDir, `${idx}.png`), buf);
      imgB64 = buf.toString("base64");
    } catch (e) {
      note = `Pantalla no disponible en este entorno (${(e as Error).message.split("\n")[0]}).`;
      // No abortamos: el manual sigue; el lector ve la explicación + la nota.
      try {
        const buf = await page.screenshot({ fullPage: false });
        imgB64 = buf.toString("base64");
      } catch {
        /* sin pantallazo */
      }
    }
    captured.push({ title: step.title, body: step.body, img: imgB64, note });
    // eslint-disable-next-line no-console
    console.log(
      `  [${def.slug}] paso ${idx}/${def.steps.length}: ${step.title}${note ? " — " + note : ""}`,
    );
  }

  fs.mkdirSync(OUT_PDF, { recursive: true });
  const html = renderHtml(def, captured);
  const pdfPage = await page.context().newPage();
  await pdfPage.setContent(html, { waitUntil: "load" });
  await pdfPage.emulateMedia({ media: "print" });
  await pdfPage.pdf({
    path: path.join(OUT_PDF, `${def.slug}.pdf`),
    format: "A4",
    printBackground: true,
    margin: { top: "16mm", bottom: "17mm", left: "15mm", right: "15mm" },
    displayHeaderFooter: true,
    // Cabecera vacía (la portada queda limpia); pie elegante con marca + folio.
    headerTemplate: `<div></div>`,
    footerTemplate: `<div style="font-size:7.5px;width:100%;padding:2px 15mm 0;color:#94a3b8;font-family:'Segoe UI',Arial,sans-serif;display:flex;justify-content:space-between;align-items:center;border-top:0.5px solid #e2e8f0;">
      <span style="font-weight:600;letter-spacing:.3px;">AGENTIC&nbsp;PLATFORM</span>
      <span style="color:#cbd5e1;">${escapeHtml(def.title)}</span>
      <span>pág.&nbsp;<span class="pageNumber"></span>&nbsp;/&nbsp;<span class="totalPages"></span></span>
    </div>`,
  });
  await pdfPage.close();
}

/** Logotipo de la herramienta (cuadrado con gradiente indigo→violet + Sparkles),
 *  como en la pantalla de login. SVG inline para que entre en el PDF. */
function brandMark(px: number): string {
  return `<svg width="${px}" height="${px}" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-label="Agentic Platform">
    <defs><linearGradient id="bmGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#6366f1"/><stop offset="1" stop-color="#8b5cf6"/>
    </linearGradient></defs>
    <rect width="48" height="48" rx="12" fill="url(#bmGrad)"/>
    <g transform="translate(12,12)">
      <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .962 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.962 0z" fill="#fff"/>
      <g stroke="#fff" stroke-width="1.7" stroke-linecap="round" fill="none">
        <path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>
      </g>
    </g>
  </svg>`;
}

export function renderHtml(
  def: ManualDef,
  steps: { title: string; body: string; img?: string; note?: string }[],
): string {
  const today = new Date().toISOString().slice(0, 10);
  const toc = steps
    .map(
      (s, i) =>
        `<li><span class="tocn">${String(i + 1).padStart(2, "0")}</span><span class="toct">${escapeHtml(
          s.title,
        )}</span></li>`,
    )
    .join("\n");
  const body = steps
    .map((s, i) => {
      const img = s.img
        ? `<figure class="shot">
             <div class="shot-bar"><span></span><span></span><span></span></div>
             <img src="data:image/png;base64,${s.img}" alt="${escapeHtml(s.title)}"/>
             <figcaption>Figura ${def.order}.${i + 1} — ${escapeHtml(s.title)}</figcaption>
           </figure>`
        : "";
      const note = s.note
        ? `<div class="note"><span class="note-ic">!</span><div>${escapeHtml(s.note)}</div></div>`
        : "";
      return `<section class="step">
        <h2><span class="n">${i + 1}</span><span class="step-title">${escapeHtml(s.title)}</span></h2>
        <div class="explain">${s.body}</div>
        ${note}
        ${img}
      </section>`;
    })
    .join("\n");

  return `<!doctype html><html lang="es"><head><meta charset="utf-8"/>
  <style>
    * { box-sizing: border-box; }
    :root {
      --indigo: #4f46e5; --violet: #7c3aed; --ink: #0f172a; --body: #334155;
      --muted: #64748b; --line: #e2e8f0; --wash: #f8fafc;
    }
    html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    body { font-family: "Segoe UI", -apple-system, Roboto, Helvetica, Arial, sans-serif; color: var(--body); line-height: 1.55; font-size: 11.5px; margin: 0; }
    .grad-text { background: linear-gradient(100deg,var(--indigo),var(--violet)); -webkit-background-clip: text; background-clip: text; color: transparent; }

    /* ---------- Portada ---------- */
    .cover { position: relative; min-height: 247mm; page-break-after: always; padding: 0; overflow: hidden; }
    .cover .topbar { height: 10px; background: linear-gradient(100deg,var(--indigo),var(--violet)); }
    .cover .inner { padding: 46px 44px 0; }
    .brand-row { display: flex; align-items: center; gap: 14px; }
    .brand-row .wm { font-size: 22px; font-weight: 800; color: var(--ink); letter-spacing: -.3px; line-height: 1; }
    .brand-row .wm small { display: block; font-size: 9.5px; font-weight: 600; letter-spacing: 2.5px; color: var(--muted); text-transform: uppercase; margin-top: 4px; }
    .cover .kicker { margin-top: 70px; display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; color: #fff; background: linear-gradient(100deg,var(--indigo),var(--violet)); padding: 6px 14px; border-radius: 999px; }
    .cover h1 { font-size: 40px; line-height: 1.08; margin: 20px 0 14px; color: var(--ink); font-weight: 800; letter-spacing: -1px; max-width: 92%; }
    .cover .audience { font-size: 13px; color: var(--muted); margin: 0 0 26px; }
    .cover .audience b { color: var(--violet); }
    .cover .rule { height: 3px; width: 64px; background: linear-gradient(100deg,var(--indigo),var(--violet)); border-radius: 2px; margin-bottom: 22px; }
    .cover .intro { font-size: 12.5px; color: var(--body); max-width: 88%; }
    .cover .intro p { margin: 0 0 9px; }
    .cover .chips { position: absolute; bottom: 34px; left: 44px; right: 44px; display: flex; gap: 10px; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 18px; }
    .chip { font-size: 10px; color: var(--muted); background: var(--wash); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; }
    .chip b { color: var(--ink); font-weight: 600; }
    .chip.conf { color: #fff; background: linear-gradient(100deg,var(--indigo),var(--violet)); border: none; }
    .chip.conf b { color: #fff; }

    /* ---------- Índice ---------- */
    .toc { page-break-after: always; padding: 8px 0 0; }
    .toc h2 { font-size: 22px; color: var(--ink); font-weight: 800; margin: 0 0 4px; }
    .toc .toc-rule { height: 3px; width: 56px; background: linear-gradient(100deg,var(--indigo),var(--violet)); border-radius: 2px; margin: 0 0 18px; }
    .toc ul { list-style: none; padding: 0; margin: 0; }
    .toc li { display: flex; align-items: baseline; gap: 12px; padding: 9px 4px; border-bottom: 1px solid var(--line); break-inside: avoid; }
    .toc .tocn { font-size: 12px; font-weight: 800; color: var(--violet); width: 26px; flex: none; font-variant-numeric: tabular-nums; }
    .toc .toct { font-size: 12px; color: var(--ink); }

    /* ---------- Pasos ---------- */
    .step { page-break-inside: avoid; margin: 0 0 22px; }
    .step h2 { display: flex; align-items: center; gap: 11px; font-size: 15.5px; color: var(--ink); font-weight: 700; margin: 14px 0 7px; }
    .step h2 .n { flex: none; display: inline-flex; align-items: center; justify-content: center; background: linear-gradient(135deg,var(--indigo),var(--violet)); color: #fff; border-radius: 9px; width: 26px; height: 26px; font-size: 13px; font-weight: 700; box-shadow: 0 2px 6px rgba(99,102,241,.35); }
    .step .step-title { line-height: 1.25; }
    .explain { color: var(--body); padding-left: 37px; }
    .explain p { margin: 5px 0; }
    .explain ul { margin: 6px 0 6px 18px; padding: 0; }
    .explain li { margin: 3px 0; }
    .explain b { color: var(--ink); }
    .explain code { background: #eef2ff; color: #4338ca; padding: 1px 6px; border-radius: 5px; font-size: 10.5px; font-family: "Consolas","SF Mono",monospace; }
    .note { display: flex; gap: 10px; align-items: flex-start; margin: 9px 0 0 37px; color: #92400e; background: #fffbeb; border: 1px solid #fde68a; border-left: 3px solid #f59e0b; padding: 9px 12px; border-radius: 8px; font-size: 10.8px; }
    .note .note-ic { flex: none; width: 17px; height: 17px; border-radius: 50%; background: #f59e0b; color: #fff; font-weight: 800; text-align: center; line-height: 17px; font-size: 11px; }

    /* ---------- Pantallazo enmarcado (estilo navegador) ---------- */
    .shot { margin: 11px 0 4px 37px; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; box-shadow: 0 4px 14px rgba(15,23,42,.10); break-inside: avoid; }
    .shot-bar { height: 22px; background: linear-gradient(180deg,#f1f5f9,#e7ecf3); border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 6px; padding: 0 11px; }
    .shot-bar span { width: 8px; height: 8px; border-radius: 50%; background: #cbd5e1; }
    .shot-bar span:nth-child(1){ background:#f87171; } .shot-bar span:nth-child(2){ background:#fbbf24; } .shot-bar span:nth-child(3){ background:#34d399; }
    .shot img { display: block; width: 100%; }
    .shot figcaption { font-size: 9.8px; color: var(--muted); padding: 7px 12px; background: var(--wash); border-top: 1px solid var(--line); }
  </style></head><body>
  <div class="cover">
    <div class="topbar"></div>
    <div class="inner">
      <div class="brand-row">
        ${brandMark(46)}
        <div class="wm">Agentic Platform<small>Plataforma Agéntica Multi-Tenant</small></div>
      </div>
      <div class="kicker">Manual ${escapeHtml(def.order)} · Manual de usuario</div>
      <h1>${escapeHtml(def.title)}</h1>
      <div class="audience">Dirigido a: <b>${escapeHtml(def.audience)}</b></div>
      <div class="rule"></div>
      <div class="intro">${def.intro}</div>
      <div class="chips">
        <span class="chip"><b>Generado</b> · ${today}</span>
        <span class="chip"><b>Idioma</b> · Español</span>
        <span class="chip"><b>Versión</b> · Producción</span>
        <span class="chip conf"><b>Confidencial</b> · Comité de Dirección</span>
      </div>
    </div>
  </div>
  <div class="toc"><h2>Contenido</h2><div class="toc-rule"></div><ul>${toc}</ul></div>
  ${body}
  </body></html>`;
}
