---
plan_id: prod-01-despliegue-ejecutable
title: Despliegue ejecutable — imágenes, compose de apps, migraciones y TLS
status: pending_human_validation
blocking_plan: null
started_at: 2026-06-11
completed_at: null
estimated_duration_calendar: 5-6 semanas
estimated_effort_person_days: 23
estimated_cost_human_eur: 10.350 € – 13.800 €
estimated_cost_ai_eur: 150 € – 250 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P0
---

# Plan prod-01 — Despliegue ejecutable: imágenes, compose de apps, migraciones y TLS

## Cabecera

| Campo                              | Valor                                |
| ---------------------------------- | ------------------------------------ |
| **ID del Plan**                    | `prod-01-despliegue-ejecutable`      |
| **Estado**                         | `pending_approval`                   |
| **Prioridad**                      | P0                                   |
| **Bloqueado por**                  | — (primero de la serie correctiva)   |
| **Tiempo estimado (calendario)**   | 5-6 semanas                          |
| **Tiempo estimado (persona-días)** | 23                                   |
| **Rama git sugerida**              | `plan/prod-01-despliegue-ejecutable` |

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que **hoy no existe ningún camino ejecutable a producción**: el instalador de la fase 15 es un simulacro de punta a punta (`FakeStepExecutor` + `StubCredentialBuilder` que imprime `stub-admin-password` como si fuera real — `cli.py:415-430`), el compose generado referencia imágenes `ghcr.io/agentic-platform/*` que el repo no puede construir (cero Dockerfiles de apps, cero pipelines de publicación), las variables de entorno se inyectan **sin los prefijos** `API_SERVER_*`/`WORKERS_*` que las Settings leen (con lo que las apps arrancarían con defaults dev y el guard anti-secretos-dev jamás se activaría), el servicio `workers` no tiene socket Docker ni red `agentic-agents` ni perfiles seccomp/AppArmor fijados (rompiendo el Principio Rector nº2), no hay paso de migraciones Alembic en el pipeline de instalación y no hay TLS/reverse proxy pese a que la config de Vault lo asume.

Este plan convierte el simulacro en un despliegue real:

1. **Fase A**: Dockerfiles + pipeline de publicación de imágenes para api-server, workers, orchestrator, notification-dispatcher y admin-panel.
2. **Fase B**: compose de apps correcto — envs prefijadas por servicio (incluidos JWT_SECRET y VAULT_TOKEN), servicio workers funcional, healthchecks, límites de recursos retro-portados al compose canónico.
3. **Fase C**: cableado del sandbox — acceso al daemon Docker (socket-proxy), red `agentic-agents`, perfiles seccomp/AppArmor pinneados, API interna del agente alcanzable.
4. **Fase D**: migraciones Alembic como paso del despliegue (one-shot container) y procedimiento de upgrade.
5. **Fase E**: reverse proxy con TLS como única superficie publicada.
6. **Fase F**: instalador con bindings reales (o fallo ruidoso mientras no los tenga) y un e2e de instalación que verifique el stack vivo.

## Alcance

**Entra**:

- Dockerfiles multi-stage de las 5 apps + workflow de build & push versionado coherente con `PLATFORM_IMAGE_TAG`/`PLATFORM_REGISTRY` (`compose_generator.py:84-85`).
- Corrección de `_app_environment` y de los builders de servicio en `apps/installer/backend/src/installer_backend/compose_generator.py` (envs prefijadas, volúmenes, redes, healthchecks, command).
- Cableado del worker como lanzador de sandboxes: socket Docker (vía proxy con ACL), red `agentic-agents`, `WORKERS_SECCOMP_PROFILE`/`WORKERS_APPARMOR_PROFILE`, `WORKERS_EGRESS_PROXY_URL`.
- Fix de `internal_api.py` del agent-runtime (`trust_env`/`NO_PROXY`) + ruta de red al api-server + rebuild de la imagen.
- Paso `run_migrations` en `INSTALL_STEP_ORDER` (`install.py:86-92`) y runbook de upgrade.
- Servicio reverse proxy TLS en `CORE_SERVICES` y retirada de puertos host de api-server/admin-panel.
- Bindings reales del instalador (StepExecutor, PrereqChecker, CredentialBuilder, StackTeardown, DataPurger) + e2e de instalación.
- Retro-port de `deploy.resources.limits` + `cap_drop` al compose canónico (`docker/docker-compose.yml`).

**Queda fuera** (cubierto por otros planes de la serie — ver coordinación):

- Backup/restore correcto (DSN pg*dump, bind mounts, repos git) → **prod-04** (deploy-4, deploy-5). Este plan solo garantiza que el `.env`/compose generados emiten variables `WORKERS_BACKUP*\*` con nombres prefijados correctos.
- Guard fail-open de secretos dev y entropía mínima (secrets-3), AppRole/renovación de tokens Vault (secrets-4), auto-unseal y alerta "Vault sealed" (deploy-8/secrets-5) → **prod-10** y **prod-08**.
- Triggers y gates de CI (el workflow de publicación se crea aquí, pero la reanimación general de CI es **prod-02**).
- Lockfile de Python y pin por digest (quality-5) → **prod-11**. Los Dockerfiles de la Fase A se diseñan para consumir el lockfile cuando exista.
- `network_policy='open'` del test-runtime (sandbox-3) y reaper de contenedores huérfanos (sandbox-5) → **prod-12**.
- Watchdog como servicio y cadena de alertas (deploy-10) → **prod-08**.

## Decisiones clave

1. **Acceso del worker al daemon Docker: socket crudo vs docker-socket-proxy.** Recomendación: **docker-socket-proxy** (ACL mínima: containers/images/networks, sin volúmenes ni exec sobre contenedores ajenos), porque el worker orquesta código no confiable y montar `/var/run/docker.sock` crudo equivale a root en el host. Requiere **ADR propuesto** (la decisión afecta al modelo de amenaza del Principio nº2); el plan arranca con la opción recomendada y el ADR formaliza el trade-off.
2. **Reverse proxy: Caddy vs nginx.** Recomendación: **Caddy** (TLS interno automático, config mínima, recarga sin downtime), aceptando certificado corporativo cuando exista PKI; nginx como alternativa si el equipo de sistemas lo exige. **ADR propuesto** en task_prod01_14 — decisión de producto/operaciones, la toma un humano.
3. **Mecanismo de migraciones: one-shot container vs entrypoint del api-server.** Recomendación: servicio **one-shot `migrations`** con la imagen del api-server (`alembic upgrade head` + `pg_advisory_lock` en `env.py`), y `depends_on: condition: service_completed_successfully` en las apps. Evita migraciones concurrentes con réplicas y deja el upgrade auditable.
4. **Transición del instalador: bindings reales por defecto, fakes solo bajo flag.** `build_default_installer` cablea los ejecutores reales; los stubs actuales quedan accesibles únicamente vía `--dry-run` explícito que imprime un banner inequívoco de simulación. Mientras un seam real no esté implementado, el CLI **falla con exit≠0** en vez de simular en silencio.
5. **Inyección de envs: emisión explícita prefijada por servicio, no `env_file` global.** Pasar el `.env` completo a cada contenedor daría a cada servicio secretos que no necesita (p. ej. el admin-panel recibiría `API_SERVER_JWT_SECRET`). Cada builder emite exactamente las claves que su Settings lee, y un test de contrato cruza generador ↔ `env_prefix` reales.

## Tareas

### Fase A — Imágenes de aplicación y pipeline de publicación

#### `task_prod01_01` — Dockerfiles de las apps backend

- [x] **Título**: Crear Dockerfiles multi-stage para api-server, orchestrator, workers y notification-dispatcher
  - **Implementación / decisión**: `apps/api-server/Dockerfile` es el image **pesado** (multi-stage; el builder compila el binding nativo `xmlsec` de python3-saml contra `libxmlsec1-dev`, el runtime slim conserva solo `libxml2`/`libxmlsec1`; instala los 5 path-packages `shared-*` + el app; no-root uid 1000; `HEALTHCHECK` contra `/healthz` vía urllib). Como **workers y orchestrator importan `api_server`** (y notification-dispatcher usa `api_server.db.notification`), los otros 3 Dockerfiles **comparten la imagen api-server como base** (`ARG BASE_IMAGE`, `FROM ${BASE_IMAGE}`) en vez de recompilar xmlsec 3 veces: solo instalan su propio paquete + su CMD (workers/notif = `celery -A …:app worker`; orchestrator = `python -m orchestrator`). **Contexto de build = raíz del repo** (para COPYar `packages/`). **Coordinación con task_03**: el publish workflow debe construir api-server PRIMERO (base) y con contexto=raíz (el `Build each app` actual usa contexto=app-dir → se corrige en task_03). **Verificado en local: los 4 images construyen e importan OK** (`docker run … python -c "import …"`).
- **Descripción**: en `apps/{api-server,orchestrator,workers,notification-dispatcher}/Dockerfile`, base `python:3.12-slim`, stage de build que instala `packages/shared-*` + la app (contexto raíz del repo, COPY selectivos — el `.dockerignore` debe excluir `vault-init-output/`, `.env*` y `*.log`, coordinado con prod-11/quality-3), usuario no root, `HEALTHCHECK` donde aplique. La imagen del api-server incluye Alembic y las migraciones (la reutiliza task_prod01_12). La de workers incluye el cliente `docker` (SDK) pero **no** el binario del daemon.
- **Tiempo**: 2,5 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_01_a
    runtime: python-pytest
    command: "pytest tests/smoke/test_app_images_build.py -v"
  ```

#### `task_prod01_02` — Dockerfile del admin-panel ✅

> **Implementación**: `apps/admin-panel/Dockerfile` multi-stage (deps → builder → runner) sobre la salida `output:'standalone'` de Next 14 (ya configurada). Contexto = el dir del app (el frontend no importa los paquetes Python). Reusa el usuario `node` (uid/gid 1000) de `node:20-slim` — NO crea uno (GID 1000 ya existe). `NEXT_PUBLIC_API_URL` como ARG inyectable (fallback → frontend-8/prod-09). **Verificado en local: build 337MB + `docker run` sirve HTTP / → 200 ("Ready in 182ms").**

- [x] **Título**: Imagen Next.js standalone del admin-panel
- **Descripción**: `apps/admin-panel/Dockerfile` con `output: 'standalone'` en `next.config`, build args para la URL pública del API, usuario no root, puerto 3000. Verificar que `npm ci` usa el `package-lock.json` existente.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_02_a
    runtime: node-jest
    command: "npm run build --prefix apps/admin-panel && npm test --prefix apps/admin-panel"
  ```

#### `task_prod01_03` — Workflow de publicación de imágenes

- [x] **Título**: `release-images.yml` — build & push de las 5 apps al registro
  - **Implementación**: `.github/workflows/release-images.yml` (trigger tag `v*` + `workflow_dispatch`): job `prep` resuelve el tag, `api-server` (base) hace build&push, `backend` (matrix workers/orchestrator/notification-dispatcher) `needs: api-server` y pasa `BASE_IMAGE`, `admin-panel` en paralelo; push a `ghcr.io/agentic-platform/<app>:<tag>` + `:sha`, caché gha, timeouts en todo job. Test `tests/unit/test_release_images_workflow.py` (4 asserts). **Verificado: 4 passed.** **Coordinación prod-02**: el sub-punto «`Build each app` de ci.yml construye los nuevos Dockerfiles como gate de PR» NO se toca aquí para no chocar con los cambios de ci.yml de prod-02 (ambos planes editan ci.yml en ramas separadas); se reconcilia al integrar prod-01↔prod-02 (la build con contexto=raíz de los Dockerfiles backend va ahí).
- **Descripción**: nuevo workflow `.github/workflows/release-images.yml` disparado por tag `v*`: `docker buildx build --push` de las 5 imágenes a `ghcr.io/agentic-platform/<app>:<tag>` (+ `:sha`), alineado con `APP_IMAGE_REGISTRY`/`APP_IMAGE_TAG` que espera `compose_generator.py:84-85`. El job `Build each app` de `ci.yml:334-354` pasa a encontrar y construir (sin push) los nuevos Dockerfiles como gate de PR. **Coordinación prod-02**: este workflow se integra en la reestructuración de CI; **prod-11** añadirá después SCA y pin por digest.
- **Depende de**: task_prod01_01, task_prod01_02
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_03_a
    runtime: python-pytest
    command: "pytest tests/unit/test_release_images_workflow.py -v"
  ```

#### `task_prod01_04` — Test de contrato imágenes ↔ compose

- [x] **Título**: Toda imagen referenciada por el compose generado tiene Dockerfile y entrada en el workflow
  - **Implementación**: `tests/unit/test_compose_images_contract.py` — invoca `generate_compose`, extrae las imágenes con prefijo `APP_IMAGE_REGISTRY`, y exige que cada app tenga `apps/<app>/Dockerfile` y aparezca en `release-images.yml`. **Verificado: 1 passed** (5 apps: api-server, workers, orchestrator, notification-dispatcher, admin-panel).
- **Descripción**: test que parsea los `image:` emitidos por `generate_compose` (compose_generator) y verifica que cada app referenciada tiene `apps/<app>/Dockerfile` y aparece en la matriz de `release-images.yml`. Evita que quality-2/deploy-2 reaparezcan en silencio.
- **Depende de**: task_prod01_03
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_04_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_images_contract.py -v"
  ```

### Fase B — Compose de apps correcto: envs, workers funcional, healthchecks, límites

#### `task_prod01_05` — Envs prefijadas por servicio en el compose generado

- [x] **Título**: `_app_environment` emite las claves que cada Settings lee de verdad
- **Descripción**: reescribir `_app_environment` y los builders por servicio (`compose_generator.py:389-399, 402-422`) para emitir nombres prefijados: `API_SERVER_*` (incl. `API_SERVER_JWT_SECRET`, `API_SERVER_VAULT_TOKEN`, `API_SERVER_ENVIRONMENT=prod`, `API_SERVER_DATABASE_URL`...), `WORKERS_*`, `ORCHESTRATOR_*`, `NOTIFY_*`, vía `_env_ref` sin fallback (fail-loud si falta en `.env`). Añadir **test de contrato** que cruce las claves emitidas por `config_generators.py:228-249` y por cada builder contra el `env_prefix` real de cada Settings (`api_server/config.py:412`, `workers/config.py:619`, etc.) para que no vuelvan a divergir. Cierra secrets-2 y la pata (1) de deploy-3; al llegar `API_SERVER_ENVIRONMENT=prod` el guard anti-defaults se activa (el rediseño fail-closed del guard es prod-10).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_05_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py tests/unit/test_compose_env_contract.py -v"
  ```

#### `task_prod01_06` — Servicio `workers` funcional en el compose generado

- [x] **Título**: command explícito, volúmenes, lane `privileged` separada
- **Descripción**: en `_workers_service` (`compose_generator.py:439-457`): (a) `command` explícito `celery -A workers worker --queues=...`; (b) bind de `{data_root}` (repos bare/worktrees) y de los perfiles seccomp; (c) envs `WORKERS_*` completas (broker DB1, result DB2, database*url, data_root) — ya prefijadas por task_prod01_05; (d) **servicio separado** `workers-privileged` para la cola `privileged` (backups, rotación) con hardening propio, como exige el propio runbook `06-capacity-management.md:160-166`; (e) emitir `WORKERS_BACKUP*\*` con nombres correctos (los **valores** correctos — DSN al servicio postgres, captura por bind-mount — son de **prod-04**, anotar TODO cruzado). Cierra workers-6 junto con task_prod01_09.
- **Depende de**: task_prod01_05
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py -k workers -v"
  ```

#### `task_prod01_07` — Healthchecks y depends_on de las apps de fondo

- [x] **Título**: orchestrator/workers/notification-dispatcher con healthcheck y arranque ordenado
- **Descripción**: añadir healthchecks (p. ej. `celery inspect ping` para workers, probe HTTP/propio para orchestrator y dispatcher) y `depends_on` con condiciones a los tres servicios de fondo del compose generado (pata (3) de deploy-3). Incluir límites de memoria/cpu coherentes con `_hardening`.
- **Depende de**: task_prod01_06
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py -k healthcheck -v"
  ```

#### `task_prod01_08` — Retro-port de hardening al compose canónico

- [x] **Título**: `deploy.resources.limits` + `cap_drop: [ALL]` en `docker/docker-compose.yml`
- **Descripción**: portar al compose canónico el mismo criterio que ya aplica `compose_generator._hardening` (`compose_generator.py:174-187`): límites cpus/memory y `cap_drop: [ALL]` por servicio, con la excepción `IPC_LOCK` de Vault. Un solo criterio de hardening entre fichero canónico y generado. Cierra deploy-12.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_08_a
    runtime: python-pytest
    command: "pytest tests/unit/test_canonical_compose_hardening.py -v"
  ```

### Fase C — Cableado del sandbox: daemon Docker, redes, perfiles, API interna

#### `task_prod01_09` — Acceso al daemon Docker + red `agentic-agents` para workers

- [x] **Título**: docker-socket-proxy con ACL mínima y red de agentes en los servicios workers
- **Descripción**: añadir a `_BUILDERS`/`CORE_SERVICES` un servicio `docker-socket-proxy` (ACL: containers/images/networks ON, exec/volumes/swarm OFF) en una red dedicada; los servicios `workers`/`workers-privileged` reciben `DOCKER_HOST=tcp://docker-socket-proxy:2375` y se unen a `agentic-agents` (arreglando el comentario contradictorio de `compose_generator.py:450-452`). Inyectar `WORKERS_EGRESS_PROXY_URL` apuntando al egress-proxy. Redactar el **ADR propuesto** de la decisión clave 1. Cierra sandbox-1 y la pata Docker de workers-6 y deploy-3.
- **Depende de**: task_prod01_06
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py -k 'socket or agents_network' -v"
  ```

#### `task_prod01_10` — Perfiles seccomp/AppArmor pinneados en producción

- [x] **Título**: `WORKERS_SECCOMP_PROFILE` + `WORKERS_APPARMOR_PROFILE` fijados por el instalador
- **Descripción**: el compose generado monta `docker/seccomp/agent-runtime.json` en el servicio workers e inyecta `WORKERS_SECCOMP_PROFILE` (ruta in-container) y `WORKERS_APPARMOR_PROFILE=agent-runtime` (hoy defaults `""` en `workers/config.py:111,120` → los sandboxes corren con perfiles Docker por defecto). El instalador añade un paso/prereq que carga el perfil AppArmor en el host (o avisa y degrada documentadamente a solo-seccomp si el host no tiene AppArmor). Actualizar el runbook de instalación. Cierra sandbox-2.
- **Depende de**: task_prod01_09
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_10_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py -k 'seccomp or apparmor' -v"
  ```

#### `task_prod01_11` — API interna del agente alcanzable desde el sandbox

- [x] **Título**: `internal_api` sin proxy + ruta de red al api-server + fallo ruidoso
- **Descripción**: (a) en `docker/agent-runtimes/agent-runtime/agent_runtime/internal_api.py:73`, crear el `httpx.Client` con `trust_env=False` (o `NO_PROXY` para el host interno) para que las llamadas a `/internal/agent/*` no salgan por el egress-proxy deny-by-default (`docker/egress-proxy/filter.txt` no incluye `api-server`); (b) dar ruta de red: unir api-server a `agentic-agents` (solo listener interno) o publicar un alias dedicado — decidir en el ADR de la decisión 1; (c) sustituir la degradación silenciosa por un check de arranque que falle ruidosamente si la API interna no responde con agente asignado; (d) **rebuild y republish de la imagen agent-runtime** (depende del pipeline de Fase A para el versionado). Cierra sandbox-4.
- **Depende de**: task_prod01_09
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_11_a
    runtime: python-pytest
    command: "pytest tests/integration/test_internal_api_reachability.py -v"
  ```

### Fase D — Migraciones Alembic en el despliegue

#### `task_prod01_12` — Paso `run_migrations` en el pipeline del instalador

- [x] **Título**: one-shot container `alembic upgrade head` entre el arranque de postgres y el de las apps
- **Descripción**: añadir `RUN_MIGRATIONS` a `INSTALL_STEP_ORDER` (`install.py:86-92`, hoy: generate_config → pull_images → start_stack → bootstrap_vault → seed_tenant — sin migraciones, `seed_tenant` no tendría esquema). Implementación: servicio one-shot `migrations` en el compose generado con la imagen del api-server, `ADMIN_DATABASE_URL`, `pg_advisory_lock` en `alembic/env.py` contra upgrades concurrentes, y las apps con `depends_on: migrations: condition: service_completed_successfully`. Cierra deploy-6 (instalación).
- **Depende de**: task_prod01_01, task_prod01_05
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_12_a
    runtime: python-pytest
    command: "pytest tests/unit/installer/test_install_steps.py tests/migrations -v"
  ```

#### `task_prod01_13` — Procedimiento de upgrade: backup → pull → migrate → up

- [x] **Título**: Runbook de upgrade sin checkout local del repo
- **Descripción**: reescribir `docs/06-runbooks/03-system-upgrade.md:112-126` para que el upgrade use el mismo one-shot container (no `python -m alembic` desde un checkout en la máquina del operador), con el orden backup → pull → migrate → up y verificación post-upgrade. Cierra deploy-6 (upgrade).
- **Depende de**: task_prod01_12
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_13_a
    runtime: python-pytest
    command: "pytest tests/docs/test_runbooks_consistency.py -v"
  ```

### Fase E — TLS y reverse proxy

#### `task_prod01_14` — ADR: reverse proxy y terminación TLS

- [x] **Título**: ADR propuesto — Caddy vs nginx, gestión de certificados, superficie publicada
- **Descripción**: redactar `docs/05-architecture-decisions/00XX-reverse-proxy-tls.md` con opciones (Caddy con TLS interno/ACME, nginx con cert corporativo, proxy externo preexistente como prerequisito), recomendación (Caddy) y la decisión pendiente de humano. Incluye política de puertos: solo el proxy publica al host; api-server/admin-panel quedan en redes internas (hoy publican en 0.0.0.0 en HTTP plano, `compose_generator.py:406,482`, mientras `docker/vault/config.hcl:2-12` asume un proxy "de fase 15" que no existe).
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**: no aplica (documento); la revisión humana del ADR es el gate.

#### `task_prod01_15` — Servicio proxy TLS en el compose generado

- [x] **Título**: Proxy como única superficie publicada, api-server/admin-panel solo en red interna
- **Descripción**: añadir el builder del proxy a `CORE_SERVICES`/`_BUILDERS` (`compose_generator.py`, builder `_reverse_proxy_service`) según el ADR aprobado: terminación TLS (cert corporativo o autofirmado con aviso explícito en el instalador), HSTS, proxy_pass a api-server:8000 y admin-panel:3000, retirada de los `ports:` directos de ambos servicios. El instalador pide dominio/cert en el wizard o genera autofirmado marcándolo como acción pendiente. Cierra deploy-7.
- **Depende de**: task_prod01_14
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_15_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py -k 'proxy or tls or ports' -v"
  ```

### Fase F — Instalador con bindings reales y verificación e2e

#### `task_prod01_16` — StepExecutor real

- [x] **Título**: Sustituir `FakeStepExecutor` por un ejecutor que aprovisiona de verdad
- **Descripción**: implementar el `StepExecutor` real que `build_default_installer` (`cli.py:415-430`) cableará por defecto: escribe `.env` + compose generados en el destino, ejecuta `docker compose pull` / `up -d` por subprocess con streaming de salida, corre `run_migrations`, bootstrapea Vault (reutilizando `vault_bootstrap.py`) y siembra el tenant. `FakeStepExecutor` queda solo para tests y `--dry-run`.
- **Depende de**: task_prod01_03, task_prod01_05, task_prod01_12
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_16_a
    runtime: python-pytest
    command: "pytest tests/unit/installer/test_step_executor.py -v"
  ```

#### `task_prod01_17` — PrereqChecker y CredentialBuilder reales

- [x] **Título**: Prerequisitos verificados de verdad y credenciales reales del bootstrap
- **Descripción**: `StubPrereqChecker` → checks reales (docker/compose version, espacio en disco, puertos libres, AppArmor disponible — coordinado con task_prod01_10); `StubCredentialBuilder` (`cli.py:165-173`, imprime `stub-admin-password`/`stub-root-token`/`stub-unseal-1..5`) → credenciales reales producidas por el bootstrap de Vault y la siembra del tenant, mostradas una sola vez sin persistirlas en claro en el árbol del repo (coordinación prod-10 para deploy-11/secrets-1).
- **Depende de**: task_prod01_16
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_17_a
    runtime: python-pytest
    command: "pytest tests/unit/installer/test_prereqs.py tests/unit/installer/test_credentials.py -v"
  ```

#### `task_prod01_18` — StackTeardown y DataPurger reales

- [x] **Título**: uninstall/reinstall dejan de ser no-ops protegidos por dobles confirmaciones
- **Descripción**: implementar `StackTeardown` (compose down, retirada de unidades/cron si los hay) y `DataPurger` (purga de `{data_root}` con confirmación explícita por categoría: BD, repos git, MinIO, Vault) que `build_default_uninstaller` (`uninstall.py:358-370`) cablea por defecto. Las dobles confirmaciones existentes pasan a proteger acciones reales.
- **Depende de**: task_prod01_16
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_18_a
    runtime: python-pytest
    command: "pytest tests/unit/installer/test_uninstall.py -v"
  ```

#### `task_prod01_19` — Fallo ruidoso transicional + sinceramiento del runbook

- [x] **Título**: Ningún seam stub puede ejecutarse en silencio; el runbook no documenta un simulacro como real
- **Descripción**: guard en `run_install`/`run_uninstall` (`cli.py:596,717`): si algún seam cableado es stub/fake y no se pasó `--dry-run`, exit≠0 con mensaje inequívoco. Corregir `docs/06-runbooks/01-installation-from-scratch.md:62` para reflejar el estado real en cada momento del plan (hoy documenta el simulacro como proceso real). Cierra la pata documental de deploy-1; **coordinación prod-15** (gobernanza/sinceramiento documental).
- **Depende de**: task_prod01_16
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_19_a
    runtime: python-pytest
    command: "pytest tests/unit/installer/test_no_silent_stubs.py -v"
  ```

#### `task_prod01_20` — E2E de instalación sobre máquina limpia

- [x] **Título**: install.sh → stack vivo → smoke de agente → uninstall (harness; ejecución real = test humano en runner Linux)
- **Descripción**: test e2e (runner Linux con Docker, nightly o manual por lo pesado — coordinación prod-02): ejecutar `scripts/install.sh` contra imágenes publicadas, verificar `/healthz` del api-server tras `https://` del proxy, login con la credencial real impresa, lanzamiento de una tarea de agente que ejercite el sandbox (socket-proxy + `agentic-agents` + API interna), y `uninstall.sh` con purga verificada. Es el test que la auditoría pide explícitamente para deploy-1/deploy-2/deploy-3.
- **Depende de**: task_prod01_15, task_prod01_17, task_prod01_18, task_prod01_19, task_prod01_09, task_prod01_11
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod01_20_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_install_from_scratch.py -v --timeout=1800"
  ```

## Hallazgos de auditoría cubiertos

| fid       | Severidad | Tarea(s) que lo cierran        |
| --------- | --------- | ------------------------------ |
| deploy-1  | critical  | task_prod01_16, 17, 18, 19, 20 |
| deploy-2  | critical  | task_prod01_01, 02, 03, 04     |
| deploy-3  | high      | task_prod01_05, 06, 07, 09     |
| deploy-6  | high      | task_prod01_12, 13             |
| deploy-7  | medium    | task_prod01_14, 15             |
| deploy-12 | low       | task_prod01_08                 |
| workers-6 | high      | task_prod01_06, 09             |
| sandbox-1 | high      | task_prod01_09                 |
| sandbox-2 | medium    | task_prod01_10                 |
| sandbox-4 | medium    | task_prod01_11                 |
| quality-2 | high      | task_prod01_01, 02, 03, 04     |
| secrets-2 | high      | task_prod01_05                 |

## Riesgos

1. **Registro de imágenes sin decidir**: `ghcr.io/agentic-platform` presupone una organización GitHub con packages habilitados; si la empresa exige registro corporativo, task_prod01_03 cambia de destino. Mitigación: `PLATFORM_REGISTRY` ya es parametrizable; decidir el registro en la primera semana.
2. **El e2e de instalación es pesado y frágil**: necesita runner Linux con Docker anidado o VM dedicada, ~20-30 min por ejecución. Riesgo de convertirse en test permanentemente rojo/ignorado. Mitigación: nightly + ejecución obligatoria antes del cierre del plan, no en cada PR.
3. **`compose_generator` está cubierto por una batería amplia de snapshots**: las fases B/C/E lo reescriben en gran parte y el coste de actualizar tests existentes puede superar lo estimado. Mitigación: refactor por builder, no big-bang.
4. **AppArmor no existe en todos los hosts** (RHEL/rocky usan SELinux): task_prod01_10 puede degradar la postura prevista. Mitigación: degradación documentada a solo-seccomp + aviso del PrereqChecker; perfil SELinux como follow-up.
5. **Solapamiento con prod-02 (CI)**: el workflow de publicación y el e2e tocan los mismos ficheros de CI que prod-02 reestructura. Mitigación: prod-01 crea workflows nuevos sin tocar los gates existentes; prod-02 los integra.
6. **PKI corporativa indisponible a tiempo**: el TLS puede quedarse en autofirmado más de lo deseable. Mitigación: el instalador marca el cert autofirmado como acción pendiente visible (no silencio), y el ADR fija el camino al cert definitivo.

## Tests humanos del Plan

```yaml
- id: human_prod01_01
  description: "Instalación real de punta a punta en una máquina limpia"
  hint: "VM Linux limpia con Docker; seguir el runbook 01-installation-from-scratch.md actualizado"
  checklist:
    - "scripts/install.sh completa sin errores y SIN mencionar 'stub' ni credenciales falsas"
    - "docker compose ps muestra las 5 apps + infra healthy (incl. workers y proxy)"
    - "Las credenciales impresas (admin + Vault) funcionan de verdad"
    - "https://<dominio> sirve el admin-panel con TLS; el puerto 8000/3000 NO responde directo desde otra máquina"
    - "alembic current dentro del contenedor de migraciones == head"

- id: human_prod01_02
  description: "Un agente ejecuta una tarea real en el stack instalado"
  hint: "Crear proyecto + plan mínimo desde el admin-panel del stack recién instalado"
  checklist:
    - "El worker lanza el agent-runtime (visible con docker ps: labels com.agentic-platform.*)"
    - "El agente llama al LLM a través del egress-proxy (logs de tinyproxy muestran el host del proveedor)"
    - "Las tools de memoria/RAG del agente responden (sin degradación silenciosa en los logs del runtime)"
    - "La ejecución termina y el resultado es visible en el Kanban"

- id: human_prod01_03
  description: "Upgrade y desinstalación reales"
  hint: "Sobre la instalación de human_prod01_01"
  checklist:
    - "Seguir 03-system-upgrade.md: backup → pull → migrate → up sin checkout del repo"
    - "uninstall.sh con confirmaciones para el stack; los datos persisten si se elige conservarlos"
    - "uninstall.sh con purga elimina {data_root} previa doble confirmación"
    - "Re-ejecutar install.sh tras purga deja un stack funcional (reinstalación limpia)"

- id: human_prod01_04
  description: "El simulacro es imposible de ejecutar por accidente"
  hint: "Probar los caminos de error del instalador"
  checklist:
    - "python -m installer_backend.cli install --dry-run imprime banner inequívoco de SIMULACIÓN"
    - "Si se fuerza un seam stub sin --dry-run, el CLI sale con código ≠ 0 y mensaje claro"
    - "El runbook ya no documenta como real ningún paso simulado"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. El test de contrato envs↔Settings (auto_prod01_05_a) y el de imágenes↔compose (auto_prod01_04_a) integrados en CI como gate.
3. El e2e `test_install_from_scratch.py` en verde sobre runner/VM Linux.
4. Los 4 tests humanos validados por un humano sobre una instalación real.
5. ADRs de socket-proxy (decisión 1) y reverse proxy TLS (decisión 2) aprobados por humano.
6. Entrada de changelog en `docs/07-changelog/prod-01-despliegue-ejecutable.md`.
7. PR del plan mergeado a `master`.

## Próximo Plan

**prod-02-ci-en-verde** (P0) — CI resucitado y en verde: triggers, gates obligatorios y cobertura. Integra el workflow de publicación de imágenes creado aquí en la reestructuración general de CI y añade los gates (cobertura, npm audit) que este plan deja preparados. Le siguen en la serie P0: prod-03 (guardrails y validación humana), prod-04 (backup/DR — recoge las variables `WORKERS_BACKUP_*` que este plan deja prefijadas) y prod-05 (rotación de claves).
