import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { dockerStack } from "../lib/seed-helper";
import { generateManual, ManualDef } from "../lib/manual";

/**
 * Manual 11 — Arquitectura y despliegue.
 * Documenta CÓMO corre la plataforma: un stack Docker Compose en una sola
 * máquina, con Caddy como única superficie publicada (single-origin) y los
 * runtimes efímeros aislados. Renderiza el `docker ps` REAL capturado por el
 * runner (assets/dockers.json) como páginas de marca y las captura.
 */
const stack = dockerStack();

const BRAND_CSS = `
  * { box-sizing: border-box; }
  body { margin: 0; font-family: "Segoe UI", Roboto, Arial, sans-serif; color: #0f172a; background: #f8fafc; }
  .wrap { padding: 30px 36px; }
  .hd { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
  .logo { width: 38px; height: 38px; border-radius: 9px; background: linear-gradient(135deg,#6366f1,#8b5cf6); }
  .hd h1 { font-size: 22px; margin: 0; font-weight: 800; letter-spacing: -.3px; }
  .sub { color: #64748b; font-size: 12.5px; margin: 2px 0 20px; }
  .tier { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px; margin: 0 0 14px; box-shadow: 0 2px 8px rgba(15,23,42,.05); }
  .tier h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: #7c3aed; margin: 0 0 10px; }
  .nodes { display: flex; flex-wrap: wrap; gap: 8px; }
  .node { background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 12px; font-size: 12px; font-weight: 600; }
  .node.proxy { background: linear-gradient(135deg,#6366f1,#8b5cf6); color: #fff; border: none; }
  .node small { display: block; font-weight: 400; color: #64748b; font-size: 10.5px; }
  .node.proxy small { color: #e9e7ff; }
  .arrow { text-align: center; color: #94a3b8; font-size: 18px; margin: 2px 0; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; }
  th { text-align: left; font-size: 10.5px; text-transform: uppercase; letter-spacing: .6px; color: #64748b; padding: 9px 12px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; }
  td { padding: 8px 12px; font-size: 11.5px; border-bottom: 1px solid #f1f5f9; }
  td.mono { font-family: Consolas, monospace; color: #334155; font-size: 10.8px; }
  .badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 10px; font-weight: 700; }
  .ok { background: #dcfce7; color: #166534; }
  .legend { font-size: 11px; color: #64748b; margin-top: 12px; }
`;

function page(title: string, sub: string, inner: string): string {
  return `<!doctype html><html lang="es"><head><meta charset="utf-8"><style>${BRAND_CSS}</style></head>
  <body><div class="wrap">
    <div class="hd"><div class="logo"></div><h1>${title}</h1></div>
    <div class="sub">${sub}</div>
    ${inner}
  </div></body></html>`;
}

function statusBadge(s: string): string {
  const ok = /healthy|Up /.test(s) && !/unhealthy/.test(s);
  return `<span class="badge ${ok ? "ok" : ""}">${ok ? "healthy" : s}</span>`;
}

function dockerTable(): string {
  const rows = stack.containers
    .map(
      (c) =>
        `<tr><td><b>${c.name}</b></td><td class="mono">${c.image}</td><td>${statusBadge(
          c.status,
        )}</td><td class="mono">${c.ports || "—"}</td></tr>`,
    )
    .join("");
  return `<table><thead><tr><th>Contenedor</th><th>Imagen</th><th>Estado</th><th>Puertos publicados</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="legend">Snapshot real de <code>docker compose ps</code> (${stack.containers.length} contenedores, capturado ${stack.capturedAt}). En este <b>entorno de demostración</b> algunos servicios publican puertos para depuración; en <b>producción</b> solo el proxy <b>Caddy</b> queda expuesto y el resto vive en redes internas.</p>`;
}

const archInner = `
  <div class="tier"><h2>Acceso</h2><div class="nodes">
    <div class="node">🌐 Navegador del usuario<small>HTTPS</small></div></div></div>
  <div class="arrow">▼</div>
  <div class="tier"><h2>Superficie publicada (única)</h2><div class="nodes">
    <div class="node proxy">Caddy · proxy inverso<small>TLS · superficie única · single-origin</small></div></div>
    <div class="nodes" style="margin-top:10px">
      <div class="node">/ → admin-panel<small>SPA Next.js (:3000)</small></div>
      <div class="node">/api/* → api-server<small>FastAPI (:8000) — Caddy quita /api</small></div>
    </div></div>
  <div class="arrow">▼</div>
  <div class="tier"><h2>Datos e infraestructura (redes internas)</h2><div class="nodes">
    <div class="node">PostgreSQL + pgvector</div><div class="node">Redis</div>
    <div class="node">Vault</div><div class="node">MinIO</div>
    <div class="node">Ollama</div><div class="node">ClamAV</div>
    <div class="node">Docling</div><div class="node">egress-proxy</div></div></div>
  <div class="tier"><h2>Ejecución (orquestación + aislamiento)</h2><div class="nodes">
    <div class="node">orchestrator</div><div class="node">workers (Celery)</div>
    <div class="node">runtimes efímeros<small>agent-runtime / test-runtime · red restringida · sin socket Docker</small></div></div></div>
`;

const isolationInner = `
  <div class="tier"><h2>Principio: los workers NO ejecutan código del usuario</h2>
    <p style="font-size:12.5px;color:#334155;line-height:1.6">Cuando un agente debe ejecutar comandos o tests, el worker
    <b>lanza un contenedor efímero</b> (<code>agent-runtime</code> / <code>test-runtime</code>) con red restringida,
    <b>sin acceso al socket Docker</b>, <code>cap-drop ALL</code> y perfiles seccomp/AppArmor pinneados. El worker solo
    <b>orquesta</b>; el código se ejecuta aislado y el contenedor se destruye al terminar.</p></div>
  <div class="tier"><h2>Imágenes de runtime y app disponibles</h2>
    <table><thead><tr><th>Imagen</th><th>Tamaño</th></tr></thead><tbody>
    ${stack.images.map((i) => `<tr><td class="mono"><b>${i.image}</b></td><td class="mono">${i.size}</td></tr>`).join("")}
    </tbody></table>
    <p class="legend">El catálogo de <b>runtime templates</b> (python-pytest, node-jest, php-phpunit…) son imágenes Docker
    mantenidas; los workers solo las orquestan.</p></div>
`;

const manual: ManualDef = {
  order: "11",
  slug: "11-arquitectura-y-despliegue",
  title: "Arquitectura y despliegue",
  audience:
    "Responsables técnicos, comité de dirección y operadores que necesitan entender cómo se despliega y aísla la plataforma.",
  intro: `<p>La plataforma se opera como un <b>stack Docker Compose en una sola máquina</b> (no Kubernetes). Este
    manual muestra su <b>topología real</b>: un único punto de entrada (el proxy inverso <b>Caddy</b>, que termina TLS
    y sirve la SPA y la API en el mismo origen), una capa de <b>datos e infraestructura</b> en redes internas, y una
    capa de <b>ejecución</b> donde el código del usuario corre en <b>contenedores efímeros aislados</b>.</p>
    <p>Las capturas de este manual reflejan el <b>despliegue en ejecución</b> en el momento de generarlo.</p>`,
  steps: [
    {
      title: "Topología del despliegue (single-origin)",
      fullPage: true,
      body: `<p>El usuario accede por <b>HTTPS</b> a una <b>única superficie publicada</b>: Caddy. Caddy sirve la SPA
        del panel en <code>/</code> y enruta <code>/api/*</code> al api-server (quitando el prefijo). Todo lo demás
        —base de datos, cache, secretos, almacenamiento, modelos— vive en <b>redes internas</b> sin puertos al host.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Topología del despliegue",
            "Caddy como superficie única · single-origin",
            archInner,
          ),
          {
            waitUntil: "load",
          },
        );
      },
    },
    {
      title: "Contenedores en ejecución (docker compose ps)",
      fullPage: true,
      body: `<p>Este es el <b>stack real en ejecución</b>. Cada servicio corre como un contenedor con su healthcheck;
        la plataforma vigila su salud (lo ves también en el <b>Dashboard → Salud de servicios</b>). Solo <b>Caddy</b>
        publica puertos al host: es la única superficie accesible desde fuera.</p>`,
      action: async (p) => {
        await p.setContent(
          page("Contenedores en ejecución", "Salida real de docker compose ps", dockerTable()),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Aislamiento por contenedor (runtimes efímeros)",
      fullPage: true,
      body: `<p>El segundo principio rector del sistema: <b>aislamiento por contenedor</b>. El código del usuario y los
        tests NUNCA corren en el worker, sino en <b>runtimes efímeros</b> lanzados bajo demanda, con red restringida,
        sin socket Docker, <code>cap-drop ALL</code> y perfiles de seguridad pinneados.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Aislamiento por contenedor",
            "Runtimes efímeros · seguridad por defecto",
            isolationInner,
          ),
          { waitUntil: "load" },
        );
      },
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page: pg }) => {
  test.setTimeout(180_000);
  await login(pg);
  await generateManual(pg, manual);
});
