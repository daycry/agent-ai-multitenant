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

// ---------------------------------------------------------------------------
// Helpers para las páginas de detalle: buscan el contenedor real del snapshot
// (imagen + estado) y montan una tabla servicio→papel por plano del stack.
// ---------------------------------------------------------------------------
function cont(fragment: string): { name: string; image: string; status: string } | null {
  return stack.containers.find((c) => c.name.includes(fragment)) ?? null;
}

function svcTable(rows: { svc: string; frag?: string; role: string }[]): string {
  const tr = rows
    .map((r) => {
      const c = r.frag ? cont(r.frag) : null;
      return `<tr><td><b>${r.svc}</b></td><td class="mono">${c ? c.image : "—"}</td><td>${
        c ? statusBadge(c.status) : "—"
      }</td><td>${r.role}</td></tr>`;
    })
    .join("");
  return `<table><thead><tr><th>Servicio</th><th>Imagen (snapshot)</th><th>Estado</th><th>Papel</th></tr></thead><tbody>${tr}</tbody></table>`;
}

function tier(title: string, innerHtml: string): string {
  return `<div class="tier"><h2>${title}</h2>${innerHtml}</div>`;
}

function para(text: string): string {
  return `<p style="font-size:12.5px;color:#334155;line-height:1.6;margin:6px 0">${text}</p>`;
}

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
    <p>Tras la vista general, el manual desciende <b>plano a plano</b> y explica el papel de CADA servicio del compose:
    la superficie de entrada (Caddy), el plano de aplicación (api-server, admin-panel), el plano de ejecución
    (orchestrator, los pools de workers Celery y el docker-socket-proxy), el plano de datos (PostgreSQL+pgvector,
    Redis, MinIO, Vault), los servicios de dominio (Docling, ClamAV, Ollama, SearXNG, voz), la salida a internet
    controlada (egress-proxy y registry-proxy), las tres redes, la postura de seguridad de los contenedores, la
    pila de observabilidad (Prometheus, node-exporter, Alertmanager, cAdvisor, Grafana), el proxy firmado de la
    validación humana (ADR 0062) y el subsistema de copias de seguridad. Cierra con el camino de
    <b>instalación en producción</b> bajo un dominio propio (runbook 08).</p>
    <p>Las capturas de este manual reflejan el <b>despliegue en ejecución</b> en el momento de generarlo: las tablas
    de servicios muestran la imagen y el estado REALES del snapshot de <code>docker compose ps</code>.</p>`,
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
    {
      title: "Caddy: la única superficie publicada (single-origin)",
      fullPage: true,
      body: `<p><b>Caddy</b> es el único servicio del stack que publica un puerto al host: en producción termina TLS
        en el 443 (el instalador genera el Caddyfile, ADR 0061); en el stack de demostración sirve HTTP plano en el
        8080. Todo lo que no entra por Caddy simplemente no es alcanzable desde fuera de la máquina.</p>
        <p>Sus reglas de enrutado son deliberadamente mínimas: <code>/healthz</code> responde un 200 propio (es el
        healthcheck del contenedor, sin tocar los upstreams); <code>/api/*</code> se reenvía al api-server <b>quitando
        el prefijo</b> (el api-server ve sus rutas reales: <code>/auth/login</code>, <code>/projects</code>, …); y
        cualquier otra ruta va a la SPA del admin-panel. Las respuestas se comprimen con gzip y la API de
        administración de Caddy está <b>desactivada</b> (<code>admin off</code>) para reducir superficie.</p>
        <p>El diseño <b>single-origin</b> es una decisión de seguridad y de simplicidad: la SPA se compila con
        <code>NEXT_PUBLIC_API_URL=/api</code>, de modo que todas las llamadas REST y WebSocket son del mismo origen —
        sin CORS, sin cookies de terceros, sin listas de orígenes que mantener. Cambiar de dominio solo exige tocar el
        Caddyfile.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Caddy · proxy inverso",
            "Única superficie publicada · TLS · single-origin",
            tier(
              "Papel",
              para(
                "Proxy inverso y terminación TLS. Es el ÚNICO contenedor con puertos publicados al host; el resto del stack vive en redes internas de Docker.",
              ),
            ) +
              tier(
                "Reglas de enrutado",
                `<div class="nodes">
                  <div class="node">/healthz<small>respuesta 200 propia (healthcheck)</small></div>
                  <div class="node proxy">/api/* → api-server:8000<small>handle_path: quita el prefijo /api</small></div>
                  <div class="node">/* → admin-panel:3000<small>SPA Next.js (single-origin)</small></div>
                </div>` +
                  para(
                    "Compresión gzip activada; API de administración de Caddy desactivada (admin off).",
                  ),
              ) +
              tier(
                "Servicio",
                svcTable([
                  {
                    svc: "caddy",
                    frag: "caddy",
                    role: "Proxy inverso único · TLS en producción (443) · :8080 en el stack de demostración",
                  },
                ]),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Plano de aplicación: api-server y admin-panel",
      fullPage: true,
      body: `<p>El <b>api-server</b> (FastAPI + Uvicorn, puerto 8000 interno) es el corazón de la plataforma: expone la
        API REST y los canales WebSocket/SSE, aplica el middleware de tenant en cada request y habla con PostgreSQL a
        través de <b>dos sesiones</b> deliberadamente separadas — la sesión de aplicación (rol <code>app_user</code>,
        sujeto a RLS: físicamente incapaz de leer filas de otro tenant) y la sesión administrativa (rol
        <code>migrations_user</code>, BYPASSRLS, reservada a operaciones globales del System Admin). Arranca solo
        cuando PostgreSQL y Redis reportan healthy, y su propio healthcheck se hace en Python puro porque la imagen
        <code>python:3.12-slim</code> no trae wget/curl.</p>
        <p>Está conectado a DOS redes: <code>agentic-net</code> (la de plataforma) y <code>agentic-agents</code> (la
        interna de los agentes). Esta doble pertenencia es la que le permite ejercer de <b>proxy firmado del
        review-runtime</b> (ADR 0062, paso posterior) y recibir los callbacks internos de los agent-runtime: para cada
        tarea, el worker acuña un token interno de corta vida firmado con el MISMO secreto JWT que usa el api-server,
        y el runtime llama de vuelta a <code>http://api-server:8000</code> con ese token.</p>
        <p>El <b>admin-panel</b> es la SPA Next.js compilada con <code>NEXT_PUBLIC_API_URL=/api</code>: no conoce el
        dominio de la instalación, todas sus llamadas son relativas al origen y viajan a través de Caddy. Sirve en el
        3000 interno y no publica puertos; depende del api-server healthy para arrancar.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Plano de aplicación",
            "api-server (FastAPI) · admin-panel (Next.js)",
            tier(
              "Servicios",
              svcTable([
                {
                  svc: "api-server",
                  frag: "api-server",
                  role: "API REST + WebSocket/SSE · middleware de tenant · RLS con doble sesión (app_user / migrations_user)",
                },
                {
                  svc: "admin-panel",
                  frag: "admin-panel",
                  role: "SPA Next.js compilada con NEXT_PUBLIC_API_URL=/api · solo alcanzable a través de Caddy",
                },
              ]),
            ) +
              tier(
                "Claves del diseño",
                para(
                  "El api-server vive en agentic-net Y agentic-agents: así puede proxyar el review-runtime (ADR 0062) y recibir los callbacks de los agent-runtime autenticados con el token interno por-tarea (JWT compartido con los workers).",
                ) +
                  para(
                    "Ningún servicio de aplicación publica puertos al host: Caddy es el único ingress. El healthcheck del api-server usa python -c urllib (la imagen slim no trae wget/curl).",
                  ),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Plano de ejecución: orchestrator, pools de workers y docker-socket-proxy",
      fullPage: true,
      body: `<p>El <b>orchestrator</b> vigila el DAG de cada plan y despacha las tareas listas a las colas de Celery
        (expone su <code>/healthz</code> en el 8002 interno). Los <b>workers</b> se organizan en pools especializados
        por colas:</p>
        <ul>
          <li><b>workers</b> (colas <code>default, ingestion, test, review</code>, concurrencia 2): el pool principal.
          NUNCA ejecuta código del usuario — lanza contenedores <b>agent-runtime efímeros</b> a través del
          docker-socket-proxy y orquesta su ciclo de vida completo (worktree, presupuesto, timeout, veredicto).</li>
          <li><b>workers-aux</b> (colas <code>test, review</code>): pool auxiliar que evita la auto-inanición — un run
          de agente que espera el resultado de un <i>stack_exec</i> (ADR 0093) no puede ocupar el mismo slot que ese
          comando necesita para ejecutarse.</li>
          <li><b>workers-backup</b> (cola <code>privileged</code>, concurrencia 1): el único pool con privilegios de
          root, dedicado en exclusiva a backups y restores (necesita leer y escribir los datos internos de Redis y
          Vault, que son 0700). No ejecuta runs de agentes.</li>
          <li><b>cortex-beat</b>: el scheduler (Celery beat) que agenda los bucles de fondo del córtex; solo encola por
          horario — y con el kill-switch de autonomía apagado (el valor por defecto) sus ticks son no-op.</li>
        </ul>
        <p>El <b>docker-socket-proxy</b> (ADR 0060) es la pieza que permite a los workers lanzar contenedores sin
        entregarles el socket de Docker: monta <code>/var/run/docker.sock</code> en <b>solo lectura</b> y expone una
        API mínima — solo contenedores, imágenes, redes, POST y exec están habilitados; volúmenes, swarm, secretos,
        system e info están denegados. Vive en una red interna dedicada (<code>agentic-docker</code>) a la que solo se
        conectan los workers; el agent-runtime jamás lo alcanza.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Plano de ejecución",
            "orchestrator · workers Celery por colas · docker-socket-proxy (ADR 0060)",
            tier(
              "Servicios",
              svcTable([
                {
                  svc: "orchestrator",
                  role: "Despacha tareas listas del DAG a las colas Celery · /healthz en :8002",
                },
                {
                  svc: "workers",
                  role: "Pool principal (default, ingestion, test, review) · lanza agent-runtime efímeros, no ejecuta código de usuario",
                },
                {
                  svc: "workers-aux",
                  role: "Pool auxiliar (test, review) · evita la inanición de stack_exec",
                },
                {
                  svc: "workers-backup",
                  role: "Cola privileged (backup/restore) · único pool con root · concurrencia 1",
                },
                {
                  svc: "cortex-beat",
                  role: "Celery beat: agenda bucles de fondo · no-op con la autonomía del córtex apagada",
                },
                {
                  svc: "docker-socket-proxy",
                  role: "Pasarela Docker de mínimo privilegio: socket ro, API mínima, red interna dedicada",
                },
              ]) +
                para(
                  "Los servicios sin imagen/estado no formaban parte del snapshot capturado (se levantan con el overlay de ejecución del stack).",
                ),
            ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Plano de datos: PostgreSQL + pgvector, Redis, MinIO y Vault",
      fullPage: true,
      body: `<p><b>PostgreSQL 16</b> es a la vez la base relacional y la vectorial: la extensión <b>pgvector</b> vive
        en la misma base de datos (embeddings de 768 dimensiones para memoria y RAG), lo que evita operar un segundo
        motor. El aislamiento multi-tenant se aplica <i>en el propio motor</i>: cada tabla lleva
        <code>tenant_id</code> y Row-Level Security activado, con dos roles de conexión — <code>app_user</code>
        (sujeto a RLS) y <code>migrations_user</code> (BYPASSRLS, para migraciones Alembic y administración
        global).</p>
        <p><b>Redis 7</b> cumple tres papeles sobre bases lógicas separadas: cache y streams de eventos en tiempo real
        (db 0, los que alimentan el despacho y los WebSocket), broker de Celery (db 1) y result backend (db 2).
        <b>MinIO</b> es el object storage S3-compatible donde viven los documentos de las bases de conocimiento y los
        artefactos binarios.</p>
        <p><b>Vault</b> es la única vía de credenciales de la plataforma — claves de proveedores LLM, credenciales git
        de proyectos, secretos de MCP y SSO. Nada de esto toca la base de datos ni los logs: la interfaz solo sabe si
        una credencial «está configurada». El contenedor recupera la capability <code>IPC_LOCK</code> (sobre el
        baseline cap-drop ALL) para bloquear su memoria y que los secretos no acaben en swap. Todos estos servicios
        persisten en volúmenes nombrados que entran en el backup diario.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Plano de datos",
            "PostgreSQL+pgvector · Redis · MinIO · Vault — redes internas, sin puertos en producción",
            tier(
              "Servicios",
              svcTable([
                {
                  svc: "postgres",
                  frag: "postgres",
                  role: "BD relacional + vectorial (pgvector, 768 dims) · RLS por tenant · roles app_user / migrations_user",
                },
                {
                  svc: "redis",
                  frag: "redis",
                  role: "Cache + streams de eventos (db 0) · broker Celery (db 1) · result backend (db 2)",
                },
                {
                  svc: "minio",
                  frag: "minio",
                  role: "Object storage S3-compatible: documentos de KB y artefactos",
                },
                {
                  svc: "vault",
                  frag: "vault",
                  role: "Única vía de credenciales (LLM, git, MCP, SSO) · IPC_LOCK para no swapear secretos",
                },
              ]),
            ) +
              tier(
                "Durabilidad",
                para(
                  "Cada servicio persiste en su volumen nombrado (postgres_data, redis_data, minio_data, vault_data) y forma parte del bundle del backup diario. El data-root de agentes (bare repos + worktrees) vive en un volumen EXTERNO que ni siquiera docker compose down -v elimina.",
                ),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Servicios de dominio: Docling, ClamAV, Ollama, SearXNG y voz",
      fullPage: true,
      body: `<p><b>docling-serve</b> (IBM Docling) es el conversor documental de la ingesta: transforma PDF, Office y
        HTML en estructura y realiza el <i>chunking</i> híbrido con el que las bases de conocimiento se trocean antes
        de indexarse para el RAG.</p>
        <p><b>ClamAV</b> escanea cada documento subido ANTES de indexarlo, con política <b>fail-closed</b> (ADR 0105):
        si el antivirus no está disponible, el documento queda en estado <code>pending_scan</code> y se avisa al
        operador — nunca se indexa contenido sin escanear. <b>Ollama</b> sirve los embeddings in-stack (por defecto
        <code>nomic-embed-text</code>, 768 dimensiones) y, opcionalmente, modelos LLM locales; su compañero one-shot
        <b>ollama-bootstrap</b> hace el <code>pull</code> idempotente del modelo de embeddings al levantar el stack, y
        el overlay GPU (docker-compose.gpu.yml) le añade aceleración CUDA si el host la tiene. Es el único servicio
        con límites de recursos ampliados (4 CPU / 8 GiB) junto a Docling.</p>
        <p><b>SearXNG</b> es el meta-buscador self-hosted que da búsqueda web al córtex (ADR 0067) sin depender de una
        API key externa; sirve JSON por configuración montada en solo lectura y no escucha en el host. Los servicios
        de <b>voz</b> (<code>stt</code> con Whisper y <code>tts</code>) dan el modo de voz del asistente (ADR 0073):
        descargan su modelo en el primer arranque (cache en volumen) y el canal de voz es best-effort mientras no
        estén listos.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Servicios de dominio",
            "Ingesta documental · antivirus · embeddings · búsqueda web · voz",
            tier(
              "Servicios",
              svcTable([
                {
                  svc: "docling-serve",
                  frag: "docling",
                  role: "Conversión documental + chunking híbrido de la ingesta de KB",
                },
                {
                  svc: "clamav",
                  frag: "clamav",
                  role: "Antivirus de la ingesta · fail-closed: sin escaneo no se indexa (pending_scan)",
                },
                {
                  svc: "ollama (+bootstrap)",
                  frag: "ollama",
                  role: "Embeddings in-stack (nomic-embed-text, 768 dims) y LLM locales · pull idempotente al arrancar",
                },
                {
                  svc: "searxng",
                  role: "Meta-buscador self-host del córtex (ADR 0067) · JSON · sin puertos al host",
                },
                {
                  svc: "stt / tts",
                  role: "Voz del asistente (ADR 0073): transcripción Whisper + síntesis · best-effort",
                },
              ]) +
                para(
                  "Los servicios sin imagen/estado no formaban parte del snapshot capturado o se añadieron en overlays posteriores.",
                ),
            ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Salida a internet controlada: egress-proxy y registry-proxy",
      fullPage: true,
      body: `<p>Los contenedores de agente viven en una red <b>interna</b> sin NAT: no pueden abrir conexiones a
        internet. Su única vía de salida es el <b>egress-proxy</b> (tinyproxy) con una <b>allowlist de dominios</b>
        limitada a los proveedores LLM y a la búsqueda: <code>api.anthropic.com</code>,
        <code>api.githubcopilot.com</code>, <code>api.github.com</code>,
        <code>copilot-proxy.githubusercontent.com</code>, <code>*.azure-api.net</code>, <code>ollama.com</code>,
        <code>api.search.brave.com</code> y los servicios internos <code>ollama</code>/<code>searxng</code>. Las
        credenciales LLM NO pasan por el proxy (entran al runtime por <code>/run/secrets</code>): el proxy solo decide
        <i>qué hosts</i> se pueden alcanzar.</p>
        <p>El <b>registry-proxy</b> (ADR 0094) es una <b>segunda instancia disjunta</b> de tinyproxy para los
        runtime-templates de tests: su allowlist cubre los registries de paquetes (Composer/Packagist, PyPI, npm, Go
        proxy, Maven/Gradle, RubyGems, crates.io, NuGet) y los hosts git (github.com, gitlab.com, dev.azure.com,
        bitbucket.org). La clave de seguridad es dónde vive: SOLO en <code>agentic-net</code> — el agent-runtime no
        puede alcanzarlo, así que el agente no descarga nada de PyPI o GitHub directamente. Es el worker quien conecta
        este proxy a los <b>bridges efímeros per-task</b> de cada runtime-template, siempre internos y sin NAT
        crudo.</p>
        <p>Ambos proxies corren con el hardening estándar del stack (cap-drop ALL más las capabilities mínimas que
        tinyproxy necesita para degradar a su usuario de servicio, no-new-privileges) y con healthcheck propio.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Egress controlado",
            "Dos proxies con allowlist · deny-by-default hacia internet",
            tier(
              "egress-proxy — allowlist LLM/búsqueda (red agentic-agents)",
              `<div class="nodes">
                <div class="node">api.anthropic.com</div>
                <div class="node">api.githubcopilot.com</div>
                <div class="node">api.github.com</div>
                <div class="node">copilot-proxy.githubusercontent.com</div>
                <div class="node">*.azure-api.net</div>
                <div class="node">ollama.com</div>
                <div class="node">api.search.brave.com</div>
                <div class="node">ollama / searxng (internos)</div>
              </div>`,
            ) +
              tier(
                "registry-proxy — allowlist de registries + git (SOLO agentic-net, ADR 0094)",
                `<div class="nodes">
                  <div class="node">packagist.org · getcomposer.org</div>
                  <div class="node">pypi.org · files.pythonhosted.org</div>
                  <div class="node">registry.npmjs.org</div>
                  <div class="node">proxy.golang.org · sum.golang.org</div>
                  <div class="node">repo.maven.apache.org · repo1.maven.org</div>
                  <div class="node">plugins/services.gradle.org</div>
                  <div class="node">rubygems.org</div>
                  <div class="node">crates.io · static/index.crates.io</div>
                  <div class="node">api.nuget.org</div>
                  <div class="node">github.com · codeload · gitlab.com · dev.azure.com · bitbucket.org</div>
                </div>` +
                  para(
                    "El agent-runtime NO alcanza este proxy: solo los runtime-templates de tests, a través de los bridges efímeros per-task que el worker les conecta.",
                  ),
              ) +
              tier(
                "Servicio en el snapshot",
                svcTable([
                  {
                    svc: "egress-proxy",
                    frag: "egress-proxy",
                    role: "tinyproxy con filtro de dominios · única salida de los agent-runtime",
                  },
                ]),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Las redes del stack: agentic-net, agentic-agents y agentic-docker",
      fullPage: true,
      body: `<p>El stack define tres redes con papeles nítidos. <b>agentic-net</b> es la red de plataforma (bridge
        estándar): en ella conviven Caddy, api-server, admin-panel, los datos (PostgreSQL, Redis, MinIO, Vault), los
        servicios de dominio (ClamAV, Docling, Ollama, SearXNG), los dos proxies de salida y la pila de
        monitorización.</p>
        <p><b>agentic-agents</b> es la red de los agentes y es <b>interna</b> (<code>internal: true</code>): Docker no
        le da NAT, así que ningún contenedor conectado SOLO a ella puede salir a internet. Ahí viven los
        <b>agent-runtime efímeros</b> y los <b>review-runtime</b>, junto al egress-proxy (su única salida), el
        api-server (callbacks internos y proxy de review) y los workers (que los orquestan). La comunicación entre
        contenedores está habilitada — el aislamiento fuerte del sandbox no se fía de la red, sino del perfil
        endurecido de cada runtime (cap-drop, seccomp, sin socket).</p>
        <p><b>agentic-docker</b> es la red interna más pequeña y estricta: SOLO los workers y el docker-socket-proxy
        se conectan a ella; es el canal exclusivo por el que se lanzan contenedores. Por último, para cada tarea de
        tests el worker crea un <b>bridge efímero per-task</b> (también interno) que une el runtime-template con el
        registry-proxy — y lo destruye al terminar; un reaper de fondo recoge los contenedores y redes huérfanos.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Redes del stack",
            "Segmentación por función · redes internas sin NAT",
            tier(
              "agentic-net — plataforma (bridge)",
              `<div class="nodes">
                <div class="node proxy">caddy</div>
                <div class="node">api-server</div><div class="node">admin-panel</div>
                <div class="node">postgres</div><div class="node">redis</div>
                <div class="node">minio</div><div class="node">vault</div>
                <div class="node">clamav</div><div class="node">docling-serve</div>
                <div class="node">ollama</div><div class="node">searxng</div>
                <div class="node">egress-proxy</div><div class="node">registry-proxy</div>
                <div class="node">monitoring (prometheus, grafana, …)</div>
              </div>`,
            ) +
              tier(
                "agentic-agents — agentes (internal: true, sin NAT)",
                `<div class="nodes">
                  <div class="node">agent-runtime efímeros</div>
                  <div class="node">review-runtime (agentic-review-*)</div>
                  <div class="node">egress-proxy (única salida)</div>
                  <div class="node">api-server (callbacks + proxy review)</div>
                  <div class="node">workers</div>
                </div>`,
              ) +
              tier(
                "agentic-docker — lanzamiento de contenedores (internal)",
                `<div class="nodes">
                  <div class="node">workers</div>
                  <div class="node">docker-socket-proxy</div>
                </div>` +
                  para(
                    "Más los bridges efímeros per-task (runtime-template ↔ registry-proxy), creados y destruidos por el worker en cada ejecución de tests; un reaper recoge los huérfanos.",
                  ),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Postura de seguridad de los contenedores",
      fullPage: true,
      body: `<p>El modelo de amenaza distingue dos niveles. Los <b>servicios confiables</b> (imágenes oficiales y
        first-party de larga vida) llevan un baseline uniforme: <code>cap_drop: ALL</code> con una re-adición mínima
        de capabilities de auto-inicialización (CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID — lo justo para que
        postgres/redis/clamav preparen su directorio de datos y degraden a su usuario de servicio; Vault suma
        IPC_LOCK y SETFCAP), <code>no-new-privileges:true</code> (imposible escalar por setuid/file-caps), el perfil
        <b>AppArmor</b> <code>agentic-default</code> cargado en el host, y el <b>seccomp por defecto de Docker</b> —
        la allowlist de ~350 syscalls probada en batalla que ya niega mount, ptrace, kexec, bpf o init_module. Un
        perfil default-deny artesanal se probó aquí y rompía los servicios Go y postgres: para servicios confiables
        era un negativo neto.</p>
        <p>El perfil <b>estricto</b> se reserva para donde importa: los <b>runtimes no confiables</b> (agent-runtime,
        test-runtime, review-runtime) reciben en el lanzamiento un seccomp <b>default-deny</b> propio
        (<code>agent-runtime.json</code>) y su perfil AppArmor específico, además de cap-drop ALL, ninguna vía al
        socket Docker y red interna sin NAT. El worker aplica estos perfiles al crear cada contenedor y lo destruye al
        terminar; su cumplimiento está validado por tests de seguridad y comprobado con enforcement de kernel en
        Linux.</p>
        <p>Completan la postura: <b>límites de recursos</b> por defecto (2 CPU / 2 GiB por servicio, ampliados solo
        donde se justifica — Ollama, Docling) para que un contenedor desbocado no agote la máquina única; imágenes
        <b>pinneadas a tag fijo</b> (nunca :latest) por reproducibilidad y cadena de suministro; y rotación de logs
        json-file (10 MB × 5 ficheros) en todos los servicios.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Postura de seguridad",
            "Dos niveles: baseline confiable · sandbox estricto para runtimes",
            tier(
              "Baseline de los servicios confiables (todo el stack)",
              `<div class="nodes">
                <div class="node">cap_drop: ALL<small>+ caps mínimas de self-init (CHOWN, SETUID, …)</small></div>
                <div class="node">no-new-privileges: true<small>sin escalada setuid/file-caps</small></div>
                <div class="node">AppArmor agentic-default<small>confinamiento MAC en el host</small></div>
                <div class="node">seccomp default de Docker<small>~350 syscalls; niega mount/ptrace/bpf/…</small></div>
                <div class="node">límites 2 CPU / 2 GiB<small>ampliados solo en ollama/docling</small></div>
                <div class="node">imágenes con tag pinneado<small>sin :latest</small></div>
                <div class="node">logs json-file rotados<small>10 MB × 5</small></div>
              </div>`,
            ) +
              tier(
                "Sandbox de los runtimes NO confiables (agent/test/review)",
                `<div class="nodes">
                  <div class="node proxy">seccomp default-deny propio<small>agent-runtime.json, aplicado por el worker</small></div>
                  <div class="node proxy">AppArmor agent-runtime<small>perfil específico del sandbox</small></div>
                  <div class="node">cap-drop ALL · sin socket Docker</div>
                  <div class="node">red interna sin NAT<small>salida solo vía egress-proxy</small></div>
                  <div class="node">efímero<small>destruido al terminar; reaper de huérfanos</small></div>
                </div>` +
                  para(
                    "Validación: tests de seguridad de perfiles (seccomp + AppArmor) y enforcement de kernel comprobado en Linux (metodología de pentest interna).",
                  ),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Observabilidad: Prometheus, node-exporter, Alertmanager, cAdvisor y Grafana",
      fullPage: true,
      body: `<p>El overlay de monitorización añade el plano de métricas y alertas. <b>Prometheus</b> es el scraper y
        la base de series temporales (retención de 15 días): recoge las métricas de node-exporter y cAdvisor y evalúa
        las reglas de alerta de host; corre como usuario <i>nobody</i> con su configuración montada en solo lectura.
        <b>node-exporter</b> exporta las métricas del host (CPU, RAM, disco, swap, red) montando /proc, /sys y / en
        solo lectura, y además re-exporta por su <i>textfile collector</i> las métricas que el motor de backup escribe
        tras cada ejecución — la fuente de la alerta de «último backup fallido».</p>
        <p><b>Alertmanager</b> recibe las alertas que disparan las reglas, las dedup-agrupa y las entrega por webhook
        al notificador de la plataforma, de modo que acaban en el inbox de notificaciones del panel.
        <b>cAdvisor</b> aporta las métricas por-contenedor (CPU, memoria, red, filesystem) y está endurecido
        <b>sin</b> <code>privileged</code> <b>ni</b> <code>/dev/kmsg</code>: obtiene los stats de bind-mounts de solo
        lectura sobre el estado de Docker y cgroups, con cap-drop ALL y no-new-privileges. El trade-off consciente es
        que pierde la decodificación de OOM-kills del kernel; el runbook de monitorización documenta el override
        legacy-privileged como opt-in para el host que lo necesite.</p>
        <p><b>Grafana</b> sirve los dashboards <i>provisionados desde disco</i> (datasource y dashboard host-overview:
        CPU/RAM/disco/red + métricas por contenedor) — los ficheros del repo son la fuente de verdad, sin clics
        manuales. Corre con su uid no-root (472), sin registro de usuarios ni acceso anónimo ni telemetría, y con la
        contraseña de administración inyectada por entorno/Vault.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Observabilidad",
            "Métricas de host y de contenedor · alertas hacia el notificador de la plataforma",
            tier(
              "Servicios",
              svcTable([
                {
                  svc: "prometheus",
                  frag: "prometheus",
                  role: "Scraper + TSDB (retención 15d) · evalúa las reglas de alerta · usuario nobody",
                },
                {
                  svc: "node-exporter",
                  frag: "node-exporter",
                  role: "Métricas del host (/proc, /sys, / en ro) + textfile collector de las métricas de backup",
                },
                {
                  svc: "alertmanager",
                  frag: "alertmanager",
                  role: "Dedup/agrupación de alertas → webhook al notificador de la plataforma",
                },
                {
                  svc: "cadvisor",
                  frag: "cadvisor",
                  role: "Métricas por-contenedor SIN privileged ni /dev/kmsg · mounts ro · cap-drop ALL",
                },
                {
                  svc: "grafana",
                  frag: "grafana",
                  role: "Dashboards provisionados desde disco (host-overview) · uid 472 · sin acceso anónimo",
                },
              ]),
            ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Validación humana: review-runtime y proxy firmado (ADR 0062)",
      fullPage: true,
      body: `<p>Cuando todas las tareas de un plan terminan, el plan pasa a <i>pending_human_validation</i> y el
        sistema levanta un <b>review-runtime</b>: un contenedor persistente con la app del usuario servida en su
        puerto principal y el worktree del plan montado, para que un humano la abra en su navegador y la pruebe antes
        de aprobar o rechazar.</p>
        <p>El problema que resuelve el ADR 0062 es que la red es zero-trust: ese contenedor vive en
        <code>agentic-agents</code> (interna), sin puertos al host y sin ruta estática en Caddy. La solución es que
        <b>el api-server actúa de proxy inverso firmado</b>: la ruta <code>/api/review/{session}/app/…</code> reenvía
        cada petición al contenedor de review — direccionado por su nombre determinista
        (<code>agentic-review-{session}</code>) — y la autenticación es una <b>firma HMAC en la URL</b>
        (<code>?exp=&amp;sig=</code>) con caducidad. No hace falta JWT: el revisor puede no tener cuenta en la
        plataforma, el enlace firmado ES la credencial, y la app jamás se publica directamente.</p>
        <p>El panel del operador obtiene un <b>enlace clicable recién firmado</b> («Abrir app para probar») a través
        de un endpoint protegido con JWT+RBAC. Al emitir el <b>veredicto</b> (aprobar/rechazar), la sesión se marca
        terminal, el contenedor se <b>destruye</b> y el plan transiciona a completado o rechazado. En el snapshot de
        este manual puede verse un contenedor real de demostración (<code>agentic-review-demo</code>) de este
        mecanismo.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Proxy firmado de review",
            "ADR 0062 · validación humana sin publicar puertos",
            tier(
              "Flujo",
              `<div class="nodes">
                <div class="node">Plan → pending_human_validation<small>todas las tareas done</small></div>
                <div class="node">review-runtime<small>agentic-review-{session} · red agentic-agents · sin puertos</small></div>
                <div class="node proxy">api-server: proxy HMAC<small>/api/review/{session}/app/* · firma ?exp=&amp;sig=</small></div>
                <div class="node">Navegador del revisor<small>URL firmada con caducidad · sin cuenta necesaria</small></div>
                <div class="node">Veredicto<small>aprueba/rechaza → destruye el contenedor → transiciona el plan</small></div>
              </div>`,
            ) +
              tier(
                "Ejemplo real del snapshot",
                svcTable([
                  {
                    svc: "review-runtime (demo)",
                    frag: "review-demo",
                    role: "App del usuario levantada para la validación humana · solo alcanzable vía el proxy firmado",
                  },
                ]) +
                  para(
                    "Zero-trust intacto: sin ingress nuevo, sin puertos publicados; la única vía es la URL firmada a través del api-server.",
                  ),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Copias de seguridad y durabilidad de los datos",
      fullPage: true,
      body: `<p>El <b>backup diario</b> (03:00 por defecto, cron configurable desde el panel — manual 09) lo ejecuta
        el pool dedicado <b>workers-backup</b> por la cola <code>privileged</code>, con un único slot de concurrencia.
        Es el único pool que corre como root dentro de su contenedor: necesita leer los datos internos de Redis y de
        Vault (directorios 0700 de otros uids) para empaquetarlos, y un restore además escribe en ellos. Ese pool no
        ejecuta nunca runs de agentes.</p>
        <p>Cada bundle incluye el <b>pg_dump</b> completo de PostgreSQL, el <b>tar</b> de los volúmenes de MinIO,
        Redis y Vault y el <b>data-root de agentes</b> (bare repos y worktrees), más su manifest. Tras verificarse, el
        bundle se sube a los <b>destinos remotos</b> configurados (S3, Backblaze B2, SFTP/NAS, rclone) con credenciales
        resueltas del secret seam (Vault/entorno — nunca de la base de datos), y la retención local elimina los
        bundles antiguos tras un backup correcto.</p>
        <p>La durabilidad se refuerza en dos frentes: los bundles se escriben en un <b>bind del host</b> fuera de los
        volúmenes de Docker (sobreviven incluso a perder el engine entero) y el data-root de agentes es un volumen
        nombrado <b>externo</b> que ni <code>docker compose down -v</code> elimina. El resultado de cada ejecución se
        publica como métrica en el textfile collector de node-exporter, de donde salen las alertas de «último backup
        fallido» y «backup demasiado antiguo» que llegan al notificador de la plataforma.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Backups y durabilidad",
            "Cola privileged dedicada · bundles verificados · destinos remotos",
            tier(
              "Qué entra en cada bundle",
              `<div class="nodes">
                <div class="node">pg_dump completo<small>PostgreSQL (todas las bases del stack)</small></div>
                <div class="node">volúmenes<small>minio_data · redis_data · vault_data</small></div>
                <div class="node">data-root de agentes<small>bare repos + worktrees (volumen externo)</small></div>
                <div class="node">manifest + verificación</div>
              </div>`,
            ) +
              tier(
                "Ciclo",
                `<div class="nodes">
                  <div class="node">1 · cron diario<small>03:00 por defecto (configurable)</small></div>
                  <div class="node">2 · backup + verificación<small>cola privileged, concurrencia 1</small></div>
                  <div class="node">3 · subida a destinos remotos<small>S3 · B2 · SFTP · rclone (credenciales del secret seam)</small></div>
                  <div class="node">4 · retención local<small>borra bundles antiguos tras un backup correcto</small></div>
                  <div class="node">5 · métrica + alertas<small>textfile collector → BackupLastRunFailed / BackupTooOld</small></div>
                </div>` +
                  para(
                    "Los bundles viven en un bind del host FUERA de los volúmenes de Docker; el data-root de agentes es un volumen externo que sobrevive a docker compose down -v. La restauración (completa o por tenant) se opera desde el panel con doble confirmación — ver manual 09.",
                  ),
              ),
          ),
          { waitUntil: "load" },
        );
      },
    },
    {
      title: "Instalación en producción con dominio propio (runbook 08)",
      fullPage: true,
      body: `<p>Todo lo anterior se pone en marcha en producción con el <b>CLI desatendido</b>
        <code>scripts/install.sh</code>, gobernado por un único fichero <code>install.yaml</code> que partes del
        perfil <code>recommended</code>: en él fijas el dominio público (<code>system.domain: example.com</code>),
        <code>environment: production</code>, el modo TLS <code>acme</code> con un email real (Caddy emite y renueva
        el certificado de Let's Encrypt en cuanto el DNS propaga), los recursos de los workers, el almacenamiento y
        al menos un proveedor LLM del catálogo cerrado. El instalador valida los prerrequisitos <b>antes de tocar
        nada</b>, genera la configuración (.env con secretos CSPRNG, Caddyfile, compose), levanta el stack esperando
        los healthchecks, aplica las migraciones, inicializa Vault — las <b>unseal keys y el root token se muestran
        UNA sola vez</b>; cópialos a tu gestor de secretos en ese momento — y siembra el primer tenant con su
        administrador. Con eso, <code>https://example.com/</code> ya sirve la SPA y <code>https://example.com/api/*</code>
        la API (single-origin); solo queda fijar en el panel la <b>URL base pública</b> y el <b>prefijo /api</b>, de
        los que derivan las URLs de callback del SSO. La verificación posterior exige todos los servicios healthy,
        el healthz público en 200 y una <b>prueba de fuego real</b>: un plan de una tarea recorriendo dispatch → run
        → review → validación humana sin intervención. El primer día se endurece la operación: backups verificados
        con un restore de prueba, rotación de claves agendada, alertas y cuentas reales. El paso a paso completo —
        checklist previa, tabla de modos TLS, fases del instalador y problemas frecuentes — vive en el runbook
        <code>docs/06-runbooks/08-instalacion-produccion.md</code>.</p>`,
      action: async (p) => {
        await p.setContent(
          page(
            "Instalación en producción",
            "scripts/install.sh + install.yaml · TLS ACME · https://example.com",
            tier(
              "De cero a servicio publicado (camino feliz)",
              `<div class="nodes">
                <div class="node">1 · DNS<small>example.com → IP pública (propaga mientras instalas)</small></div>
                <div class="node">2 · install.yaml<small>perfil recommended · tls_mode: acme · email TLS</small></div>
                <div class="node proxy">3 · scripts/install.sh --config<small>CLI desatendido — el camino soportado</small></div>
                <div class="node">4 · fases<small>prereqs → config → stack → migraciones → Vault → seed</small></div>
                <div class="node">5 · URL base pública<small>https://example.com + prefijo /api (SSO)</small></div>
                <div class="node">6 · verificación<small>healthz 200 · system-health ok · plan de prueba e2e</small></div>
                <div class="node">7 · hardening día 1<small>backups + restore de prueba · rotación · alertas</small></div>
              </div>`,
            ) +
              tier(
                "Credenciales de un solo revelado",
                para(
                  "Las unseal keys de Vault, el root token y la contraseña inicial del administrador se muestran UNA sola vez durante la instalación. Sin las unseal keys no se desella Vault tras un reinicio; guárdalas fuera de la máquina, en un gestor de secretos.",
                ),
              ) +
              tier(
                "El runbook completo",
                para(
                  "docs/06-runbooks/08-instalacion-produccion.md — checklist previa, install.yaml de referencia comentado, tabla de modos TLS (acme / provided / internal), fases y códigos de salida del instalador, verificación post-instalación y problemas frecuentes. El dominio propio en detalle (topologías, nginx, SCIM, SSO) está en 07-custom-domain.md.",
                ),
              ),
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
