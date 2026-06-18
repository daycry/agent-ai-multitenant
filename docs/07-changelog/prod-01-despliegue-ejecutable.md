---
plan_id: prod-01-despliegue-ejecutable
title: Despliegue ejecutable — imágenes, compose de apps, migraciones y TLS
started_at: 2026-06-11
completed_at: null
status: pending_human_validation
tasks_done: 20
tasks_total: 20
tasks_pending_local: []
tests_automated_passing: true
human_validations_passing: 0
docs_language: es
---

> **Estado:** `pending_human_validation`. Las 20 tareas están implementadas con
> sus tests automáticos en verde (TDD; ruff/mypy limpios; cada fase verificada
> con workflows de diseño + revisión adversarial). Falta: los **3 tests humanos**
> del plan (`human_prod01_01..03`, requieren una VM Linux con Docker) y el
> **merge del PR** a `master`. El `status: completed` se fija solo tras ambos.

# Changelog — Plan prod-01 · Despliegue ejecutable

Cierra el hallazgo **crítico deploy-1** de la auditoría de producción
(2026-06): el instalador era un simulacro con stubs y el stack no era
desplegable. Tras prod-01 el stack se construye, se publica y se instala de
verdad detrás de TLS.

## Resultado por fases

- **Fase A — Imágenes (tasks 01-04):** Dockerfiles de las 4 apps backend
  (api-server pesado + base compartida para workers/orchestrator/notify) +
  admin-panel (Next standalone) + workflow `release-images.yml` que publica las
  5 a `ghcr.io/agentic-platform`. Cierra deploy-2 / quality-2.
- **Fase B — Compose de apps (tasks 05-08):** `compose_generator` emite envs
  PREFIJADAS por servicio (`API_SERVER_`/`ORCHESTRATOR_`/`WORKERS_`/`NOTIFY_`),
  workers funcional (lane genérico + `workers-privileged`), healthchecks de los
  4 de fondo, y hardening retro-portado (cap_drop ALL + límites). Contrato
  compose↔.env testeado. Cierra deploy-3 / workers-6 / deploy-12 / secrets-2.
- **Fase C — Sandbox (tasks 09-11):** `docker-socket-proxy` (red interna
  dedicada) + perfiles seccomp/AppArmor pinneados + API interna del sandbox
  alcanzable con fallo ruidoso. **ADR 0060.** Cierra sandbox-1/2/4.
- **Fase D — Migraciones (tasks 12-13):** servicio one-shot `migrations`
  (`alembic upgrade head` + `pg_advisory_xact_lock`) entre postgres y las apps;
  runbook de upgrade reescrito sin checkout local. Cierra deploy-6.
- **Fase E — TLS / reverse proxy (tasks 14-15):** **ADR 0061** + servicio `caddy`
  como ÚNICA superficie publicada (80/443), terminación TLS (internal/provided/
  acme) + HSTS, enrutado single-origin (`/api/*` al backend, `/api/v1` sin strip,
  SPA en `/`); retirados los `ports` de api-server/admin-panel. Cierra deploy-7.
- **Fase F — Instalador real + e2e (tasks 16-20):** `RealStepExecutor` que
  aprovisiona de verdad (escribe compose/.env/Caddyfile, `docker compose pull`/
  `up -d --wait`, migraciones, bootstrap de Vault, seed del catálogo built-in +
  `init_tenant`); `check_ports` + `RealCredentialBuilder` + `RealStackTeardown`/
  `RealDataPurger`; **guard no-silent-stubs** + `--dry-run` (no hay instalación
  falsa silenciosa); e2e `install→stack→smoke→uninstall` skip-guarded (corre en
  runner Linux nightly). Cierra deploy-1.

## ADRs generados (proposed → ratificados durante el cierre)

- **ADR 0060** — daemon Docker para los workers + ruta API interna del sandbox.
- **ADR 0061** — reverse proxy y terminación TLS: Caddy como única superficie.

## Decisiones de alcance (delegadas/coordinadas)

- **BOOTSTRAP_VAULT** solo orquesta (init/unseal/KV/políticas). Escribir los
  valores en el KV + mintar tokens por servicio es **prod-10**; los servicios
  arrancan leyendo el `.env` (0600) hasta entonces.
- **Wizard HTTP** (`/api/install/stream`) sigue en SIMULACIÓN; el camino real es
  el CLI (`scripts/install.sh`). Cablear el wizard al ejecutor real = **prod-09**.
- **Frontend same-origin** (wsUrl con base relativa, review page cookie-vs-Bearer,
  fetches inline, preview del wizard) = **prod-09** (documentado en ADR 0061).
- **Playwright** del frontend = **prod-09**.

## Tests humanos pendientes

- `human_prod01_01` — instalación real de punta a punta en máquina limpia.
- `human_prod01_02` — un agente ejecuta una tarea real en el stack instalado.
- `human_prod01_03` — upgrade y desinstalación reales.
- `auto_prod01_20_a` (e2e) sólo acredita deploy-1/2/3 tras una ejecución verde
  con `E2E_INSTALL=1` en un runner Linux con Docker (nightly, coordinación prod-02).

## Correcciones durante validación en vivo (2026-06-18)

Al levantar el stack contenerizado **de verdad** (para generar los manuales de
usuario, ver `docs/manuals/`) emergieron bugs reales en los entregables de
prod-01 que el `.venv` de desarrollo enmascaraba. Todos corregidos con su test
de regresión y su gotcha en `docs/03-guides/gotchas/`:

- **`cap_drop: [ALL]` rompía las imágenes oficiales** (postgres/redis/vault/
  clamav/egress-proxy entraban en crash-loop al recrearse: su entrypoint hace
  `chown`/`chmod` y baja de privilegios). Fix: anchor `x-infra-caps` /
  `_INFRA_CAPS` que devuelve solo las caps de auto-init (CHOWN, DAC_OVERRIDE,
  FOWNER, SETGID, SETUID; vault +SETFCAP) en el compose canónico **y** en
  `compose_generator.py`. Tests `test_official_infra_images_keep_self_init_caps`
  (×2). Gotcha `docker-cap-drop-all-breaks-official-images.md`.
- **La imagen del api-server no arrancaba** (faltaban `celery` y el paquete
  `workers`, que el código importa). Declarados/instalados en el Dockerfile.
  Gotcha `app-image-missing-runtime-deps.md`.
- **Healthchecks rotos** dejaban contenedores `unhealthy` para siempre →
  `depends_on: service_healthy` nunca se cumplía. `python:3.12-slim` no tiene
  wget/curl (api-server/orchestrator → healthcheck con `python` stdlib) y
  docling rechaza el HEAD de `--spider` (→ GET). Fix en ambos composes + test
  `test_python_app_healthchecks_do_not_rely_on_wget`. Gotcha
  `compose-healthcheck-tooling-missing.md`.

Con estos fixes el stack contenerizado **funciona end-to-end** (single-origin
por Caddy, 16 contenedores sanos, login + dashboard + 8 servicios «ok»),
materializando deploy-1/2/3 de forma observable a la espera de los tests humanos
en VM Linux.

## prod-09 (frontend same-origin) — inicio

Adelantado el primer arreglo de prod-09 necesario para servir la UI tras Caddy:
`apps/admin-panel/lib/ws.ts` resolvía mal el WebSocket con base relativa
(`NEXT_PUBLIC_API_URL=/api`). Nueva `resolveWsBase()` deriva el origen
`ws(s)://` absoluto de `window.location` (test `lib/ws.test.ts`). La imagen
`admin-panel` construida con `NEXT_PUBLIC_API_URL=/api` sirve la SPA y la API en
el mismo origen a través de Caddy.

## Validación humana end-to-end — acceso a la app levantada (ADR 0062)

Completado el cableado del **review-runtime** (Plan 06 Fase G), que estaba
diseñado pero **0% conectado**: ahora un humano puede **abrir y probar la app
construida** desde un link clicable del panel antes de aprobar el plan.

- **Ingress de preview sin romper zero-trust**: el **api-server reverse-proxy**
  el contenedor de review por su nombre interno (`agentic-review-{id}` en
  `agentic-agents`); ruta `GET|POST /review/{id}/app/{path}` firmada por HMAC (la
  misma `?exp=&sig=`). No se publica ningún puerto (ADR 0062).
- **URL expuesta a la UI**: `GET /plans/{id}/review-session` (JWT+RBAC) devuelve
  la sesión + URLs firmadas (`app_url`, `review_url`, `verdict_url`). El panel del
  plan muestra el botón **«Abrir app para probar»** + **Aprobar/Rechazar**
  (`apps/admin-panel/.../plans/[planId]`).
- **Veredicto**: `POST /review/{id}/verdict` marca la sesión terminal y
  transiciona el plan (`approved`→`completed`, `rejected`→`rejected`).
- **Worker**: `compose_review_runtime` nombra el contenedor de forma determinista
  - lo une a `agentic-agents` + registra `spec.main_host` para el proxy.
- **Demo + manual reutilizables**: `scripts/dev/seed-review-demo.ps1` levanta la
  app _Hello World PHP_ + la review-session; el **manual 12** la documenta con
  pantallazos REALES (plan con el link → app abierta vía proxy → consola de
  revisión → veredicto). Verificado en vivo: `verdict approved → plan completed`.
- Tests: `tests/integration/test_review_preview.py`. Cierra los GAP 1/3/5 de la
  investigación 2026-06-18 (URL no expuesta, sin veredicto, sin acceso desde UI).
