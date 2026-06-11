---
plan_id: prod-11-cadena-suministro
title: "Cadena de suministro: SCA en CI, Dependabot, lockfiles y pin por digest"
status: pending_approval
blocking_plan: [prod-02-ci-en-verde]
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 10
estimated_cost_human_eur: 4.500 € – 6.000 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-11 — Cadena de suministro: SCA en CI, Dependabot, lockfiles y pin por digest

## Cabecera

| Campo                              | Valor                            |
| ---------------------------------- | -------------------------------- |
| **ID del Plan**                    | `prod-11-cadena-suministro`      |
| **Estado**                         | `pending_approval`               |
| **Prioridad**                      | P1                               |
| **Bloqueado por**                  | `prod-02-ci-en-verde`            |
| **Tiempo estimado (calendario)**   | 2-3 semanas                      |
| **Tiempo estimado (persona-días)** | 10                               |
| **Rama git sugerida**              | `plan/prod-11-cadena-suministro` |

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que la cobertura de análisis de
composición de software (SCA) es **cero en las cuatro superficies** del repo:
(1) pip backend sin pip-audit/safety/osv-scanner en ningún workflow, (2) pip de
los runtimes instalado sin constraints ni escaneo, (3) npm sin `npm audit` (y el
frontend del installer ni siquiera entra en CI), y (4) imágenes Docker
construidas sin Trivy/Grype. No existe `.github/dependabot.yml` ni
`renovate.json`, así que tampoco hay vía reactiva: el síntoma visible es
`next 14.2.5` congelado en el admin-panel con **1 vulnerabilidad crítica**
reportada por `npm audit` (fix disponible en la misma línea 14.2.x). Además, los
17 `FROM` bajo `docker/` usan tags flotantes sin `@sha256` — precisamente las 15
imágenes de runtime donde el Principio Rector 2 deposita el aislamiento del
código no confiable —, el catálogo referencia las imágenes por tag mutable
`:v1` sin registry, no existe lockfile Python en todo el monorepo (builds no
reproducibles), Composer se instala vía `curl | php` sin verificar checksum y
las GitHub Actions van pineadas por tag mutable `@vN`.

Este plan cierra el agujero completo en cuatro frentes:

1. **Quick wins**: subir `next` a 14.2.35, crear `dependabot.yml` (pip + npm +
   docker + github-actions), pinear actions por SHA y verificar el checksum del
   instalador de Composer.
2. **SCA como gate en CI**: job `security-scan` con pip-audit, `npm audit`
   (admin-panel + installer) y Trivy sobre las imágenes construidas, con gate
   HIGH/CRITICAL tras un periodo de triage en modo informe.
3. **Lockfile Python** (uv) para builds reproducibles en CI, Dockerfiles y host
   de producción — prerrequisito para que pip-audit y Dependabot den señal
   precisa sobre lo realmente instalado.
4. **Pin por digest** de las bases de las 17 imágenes y ADR para registry con
   tags inmutables del catálogo de runtimes.

**Coordinación con otros planes de la serie**: la publicación de imágenes a
registry y los Dockerfiles de apps son alcance de `prod-01-despliegue-ejecutable`
(aquí solo se decide vía ADR y se pinean digests); la lista de checks
obligatorios de branch protection la gobierna `prod-02-ci-en-verde` (aquí se
añade `security-scan` a esa lista una vez estabilizado); el hardening de
runtimes en ejecución es de `prod-12-hardening-tools-agentes` (aquí solo la
integridad de las imágenes en build).

## Alcance

**Entra**:

- Job `security-scan` en `.github/workflows/ci.yml`: pip-audit sobre los
  editable installs existentes (ci.yml:160-193), `npm audit --audit-level=high`
  en `apps/admin-panel` y `apps/installer`, Trivy (HIGH/CRITICAL, exit-code 1)
  sobre las imágenes de `build-images` y de la matriz de
  `build-runtime-templates.yml`.
- `.github/dependabot.yml` con ecosistemas pip (11 `pyproject.toml` de
  `apps/*` y `packages/*` + `requirements-dev.txt`), npm (`apps/admin-panel`,
  `apps/installer`), docker (`docker/agent-runtimes/*`, `docker/egress-proxy`)
  y github-actions, con agrupación de PRs para no inundar el repo.
- Actualización de `next` 14.2.5 → 14.2.35 en admin-panel e installer +
  regeneración de lockfiles + `npm audit fix`.
- Lockfile Python con `uv` (constraints exportados) consumido por CI y por
  `docker/agent-runtimes/agent-runtime/Dockerfile`, con check de drift en CI.
- Pin por `@sha256` de los 17 `FROM` bajo `docker/` y de las imágenes
  auxiliares `postgres:16-alpine`/`redis:7-alpine` de
  `apps/workers/src/workers/test_runtime.py:306,320`.
- Verificación del SHA-384 del instalador de Composer (o `COPY --from` de la
  imagen oficial pineada) en `php-phpunit` y `php-pest`.
- Pin por SHA de commit de los 17 usos de actions en los 3 workflows.
- ADR propuesto: registry + tags inmutables para el catálogo de runtimes
  (`catalog.py` `_IMAGE_TAG = "v1"`).
- Runbook de triage de vulnerabilidades y política de excepciones.

**Queda fuera**:

- Dockerfiles y pipeline de publicación de las apps de plataforma
  (api-server, workers, orchestrator…) → `prod-01-despliegue-ejecutable`.
- Resurrección general de CI, triggers y cobertura → `prod-02-ci-en-verde`
  (bloqueante de este plan: sin CI en verde un gate nuevo no aporta señal).
- SBOM firmado / attestations (cosign, SLSA): deseable, pero se pospone a un
  follow-up; este plan deja la base (digests + registry vía ADR).
- Escaneo en runtime de contenedores ya desplegados (Trivy operator no aplica:
  no hay Kubernetes; el reaper/egress es de `prod-12`).

## Decisiones clave

- **Herramienta de lockfile Python: `uv` (recomendado) vs `pip-tools`.**
  Recomendación: `uv` — resolución de workspace multi-paquete, `uv export`
  genera `constraints.txt` consumible por el `pip install -e` que CI ya usa, y
  `uv lock --check` detecta drift. `pip-tools` exigiría un `requirements.in`
  por app (11 ficheros) y no entiende el monorepo. Formalizar en ADR propuesto
  `docs/05-architecture-decisions/` antes de la Fase C (decisión técnica de
  toolchain, no de producto: puede aprobarla el tech lead).
- **Escáner de imágenes: Trivy** (action oficial `aquasecurity/trivy-action`,
  DB integrada, soporta `severity: HIGH,CRITICAL` + `exit-code: 1`). Grype
  queda como alternativa si Trivy genera demasiado ruido en bases `-slim`.
- **Gate progresivo, no big-bang**: `security-scan` arranca con
  `continue-on-error: true` durante una semana de triage del backlog de CVEs
  existente; después se convierte en check obligatorio. Evita bloquear todos
  los PRs el día 1 con vulnerabilidades heredadas.
- **Orden duro: Dependabot ANTES que digest-pinning.** Un digest pineado sin
  mecanismo de refresh es peor que un tag flotante (congela CVEs para
  siempre). La Fase D depende de la tarea de Dependabot.
- **Registry de runtimes**: el compose del installer ya asume
  `ghcr.io/agentic-platform` — la opción natural es GHCR con tags inmutables
  versionados (`agent-runtime-python-pytest:v1.0.0@sha256:…`). Es decisión con
  impacto en producto (distribución de imágenes a hosts de tenants), así que
  va como **ADR propuesto** con opciones (GHCR / registry self-hosted en el
  stack / seguir build-local documentado), no se toma aquí. La implementación
  del push es de `prod-01`.
- **Política de excepciones SCA**: vulnerabilidades sin fix upstream se
  suprimen con fichero de ignore versionado (`.trivyignore`,
  `pip-audit --ignore-vuln`), cada entrada con justificación + fecha de
  revisión obligatorias. Sin esto el gate muere por fatiga de alertas.

## Tareas

### Fase A — Quick wins (sin dependencias, paralelizables)

#### `task_next_update_01` — Subir next a 14.2.35 en admin-panel e installer

- [ ] **Título**: Actualizar `next` 14.2.5 → 14.2.35 y limpiar `npm audit`
- **Descripción**: Cambiar el pin en `apps/admin-panel/package.json:25` y
  `apps/installer/package.json:18` a `14.2.35` (misma línea 14.2.x: cubre
  GHSA-h64f-5h5j-jqjh, GHSA-c4j6-fc7j-m34r, GHSA-wfc6-r584-vfw7,
  GHSA-36qx-fr4f-26g5 y el postcss embebido), regenerar ambos
  `package-lock.json`, ejecutar `npm audit fix` y verificar que
  `npm run build` y `npm run typecheck` siguen en verde en ambos frontends.
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_01_a
    runtime: node-jest
    command: "cd apps/admin-panel && npm ci && npm audit --omit=dev --audit-level=high && npm run build"
  - id: auto_prod11_01_b
    runtime: node-jest
    command: "cd apps/installer && npm ci && npm audit --omit=dev --audit-level=high && npm run build"
  ```

#### `task_dependabot_02` — Crear `.github/dependabot.yml` (pip + npm + docker + actions)

- [ ] **Título**: Dependabot con 4 ecosistemas y agrupación de PRs
- **Descripción**: Crear `.github/dependabot.yml` con: `pip` apuntando a los 11
  directorios con `pyproject.toml` (`apps/api-server`, `apps/watchdog`,
  `apps/orchestrator`, `apps/workers`, `apps/notification-dispatcher`,
  `apps/installer/backend`, `packages/shared-*`, `packages/sdk-python`,
  `docker/agent-runtimes/agent-runtime`) y a `requirements-dev.txt`; `npm` en
  `apps/admin-panel` y `apps/installer`; `docker` en cada directorio con
  Dockerfile bajo `docker/`; `github-actions` en `/`. Schedule semanal,
  `groups` por ecosistema (minor+patch agrupados) para limitar el volumen de
  PRs, y `open-pull-requests-limit` razonable (p. ej. 5 por ecosistema).
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k dependabot -v"
  ```
  (test que parsea `.github/dependabot.yml` y asegura que cubre los 4
  ecosistemas y que cada `pyproject.toml`/`package.json`/Dockerfile del repo
  tiene su entrada — falla si se añade un paquete sin registrarlo.)

#### `task_actions_sha_03` — Pinear las GitHub Actions por SHA de commit

- [ ] **Título**: Sustituir `@vN` por `@<sha40> # vN` en los 17 usos de actions
- **Descripción**: En `ci.yml`, `build-runtime-templates.yml` y
  `eval-on-prompt-change.yml`, reemplazar `actions/checkout@v5`,
  `actions/setup-python@v6`, `actions/setup-node@v5`,
  `docker/setup-buildx-action@v3/@v4` y `docker/build-push-action@v6` por el
  SHA completo del tag correspondiente con el tag legible en comentario.
  Dependabot (ecosistema github-actions de `task_dependabot_02`) mantiene los
  SHAs actualizados.
- **Dependencias**: `task_dependabot_02` (sin Dependabot los SHAs se quedan
  congelados sin vía de refresh).
- **Tiempo**: 2 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_03_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k actions_pinned -v"
  ```
  (test que recorre `.github/workflows/*.yml` y falla si algún `uses:`
  referencia un tag mutable en lugar de un SHA de 40 caracteres.)

#### `task_composer_checksum_04` — Verificar el instalador de Composer en las imágenes PHP

- [ ] **Título**: Eliminar el `curl | php` sin checksum de php-phpunit y php-pest
- **Descripción**: En `docker/agent-runtimes/php-phpunit/Dockerfile:21` y
  `docker/agent-runtimes/php-pest/Dockerfile:23`, sustituir el pipe directo por
  `COPY --from=composer:2@sha256:<digest> /usr/bin/composer /usr/local/bin/composer`
  (opción preferida: una capa, digest pineado, lo refresca Dependabot) o, si se
  prefiere mantener el instalador, descargar el installer, comparar su SHA-384
  contra `https://composer.github.io/installer.sig` y abortar el build si no
  coincide.
- **Tiempo**: 3 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_04_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k composer -v"
  ```
  (test que falla si algún Dockerfile bajo `docker/` contiene el patrón
  `getcomposer.org/installer | php`.)

### Fase B — SCA como gate en CI

#### `task_pip_audit_05` — Job pip-audit sobre el árbol Python

- [ ] **Título**: pip-audit en CI tras los editable installs
- **Descripción**: Añadir al nuevo job `security-scan` de
  `.github/workflows/ci.yml` un paso que reproduzca los mismos
  `pip install -e` que `test-unit` (ci.yml:160-193) y ejecute
  `pip-audit --strict` sobre el entorno resultante (así audita lo realmente
  instalado, incluidas transitivas). Soportar fichero de excepciones
  (`--ignore-vuln` con justificación en comentario). Arranca con
  `continue-on-error: true` (ver `task_sca_gate_08`).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_05_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k pip_audit -v"
  - id: auto_prod11_05_b
    runtime: python-pytest
    command: "pip-audit --strict -r <(uv export --no-hashes) || true  # ejecución local de validación del paso"
  ```

#### `task_npm_audit_06` — npm audit en admin-panel e installer dentro de CI

- [ ] **Título**: `npm audit --audit-level=high --omit=dev` en las 2 superficies npm
- **Descripción**: Añadir a `security-scan` dos pasos `npm ci && npm audit
--omit=dev --audit-level=high` en `apps/admin-panel` y `apps/installer`.
  Nota de coordinación con `prod-02-ci-en-verde`: el job `lint-typescript`
  actual solo detecta `apps/admin-panel` (ci.yml:101) y deja fuera el frontend
  del installer; este plan NO arregla el lint del installer (alcance de
  prod-02), pero su `npm audit` sí entra aquí porque es superficie SCA.
- **Dependencias**: `task_next_update_01` (si no, el gate nace en rojo por la
  crítica conocida de next).
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_06_a
    runtime: node-jest
    command: "cd apps/admin-panel && npm audit --omit=dev --audit-level=high"
  - id: auto_prod11_06_b
    runtime: node-jest
    command: "cd apps/installer && npm audit --omit=dev --audit-level=high"
  ```

#### `task_trivy_07` — Trivy sobre imágenes de apps y runtimes

- [ ] **Título**: Escaneo Trivy HIGH/CRITICAL tras cada build de imagen
- **Descripción**: (a) En `ci.yml` job `build-images` (líneas 347-393), añadir
  tras cada `docker build` un paso `aquasecurity/trivy-action` con
  `severity: HIGH,CRITICAL`, `exit-code: 1`, `ignore-unfixed: true` y
  `.trivyignore` versionado. (b) En `build-runtime-templates.yml`, añadir el
  mismo paso tras el build de cada template de la matriz (líneas 61-72),
  sustituyendo el smoke de WORKDIR como único gate (build-runtime-templates.yml:74-78
  se conserva, pero deja de ser la única verificación). Cache de la DB de
  Trivy entre runs para no penalizar el tiempo de CI.
- **Dependencias**: `task_digest_pin_10` recomendable antes del modo gate (las
  bases pineadas estabilizan el resultado del escaneo).
- **Tiempo**: 1,5 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k trivy -v"
  ```
  (test que verifica que cada job que construye imágenes en los workflows va
  seguido de un paso trivy-action con severity y exit-code correctos.)

#### `task_sca_gate_08` — Triage del backlog y conversión en gate obligatorio

- [ ] **Título**: Una semana en modo informe → gate obligatorio de branch protection
- **Descripción**: Ejecutar `security-scan` con `continue-on-error: true`
  durante ~1 semana; triar el backlog inicial (actualizar lo actualizable,
  documentar excepciones en `.trivyignore`/ignore-list de pip-audit con
  justificación + fecha de revisión); después quitar `continue-on-error` y
  añadir `security-scan` a los checks requeridos de branch protection.
  Coordinación: la lista de checks obligatorios la administra
  `prod-02-ci-en-verde`; esta tarea la extiende, no la redefine.
- **Dependencias**: `task_pip_audit_05`, `task_npm_audit_06`, `task_trivy_07`.
- **Tiempo**: 1 día (repartido en la semana de triage) · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_08_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k 'gate and not continue_on_error' -v"
  ```
  (falla si el job `security-scan` conserva `continue-on-error: true` o si
  alguna entrada de las ignore-lists carece de justificación/fecha.)

### Fase C — Lockfile Python y builds reproducibles

#### `task_uv_lock_09` — ADR de toolchain + generar lockfile del monorepo

- [ ] **Título**: ADR uv-vs-pip-tools + `uv.lock`/`constraints.txt` versionados
- **Descripción**: Redactar el ADR propuesto (ver Decisiones clave) y, una vez
  aprobado, configurar el workspace `uv` que cubra las 11 distribuciones
  Python (`apps/*` + `packages/*` + `docker/agent-runtimes/agent-runtime`),
  generar `uv.lock` y exportar `constraints.txt` en la raíz. Los rangos de
  los `pyproject.toml` (p. ej. `fastapi>=0.110,<1` en
  `apps/api-server/pyproject.toml:8`) quedan como restricción de
  compatibilidad; el lock es la verdad reproducible. Incluir
  `requirements-dev.txt` (líneas 8-13, hoy en rangos) en la resolución.
- **Tiempo**: 1,5 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_09_a
    runtime: python-pytest
    command: "uv lock --check"
  ```

#### `task_ci_lock_10` — CI y Dockerfile del agent-runtime instalan desde el lock

- [ ] **Título**: `pip install -e … -c constraints.txt` en CI y en agent-runtime
- **Descripción**: (a) Cambiar los `pip install -e` de los jobs de CI
  (ci.yml:160-193 y equivalentes) a `pip install -e <app> -c constraints.txt`.
  (b) En `docker/agent-runtimes/agent-runtime/Dockerfile` (instalaciones de
  las líneas 38-59), copiar `constraints.txt` y añadir `-c` a cada
  `pip install`. (c) Añadir paso de CI `uv lock --check` para detectar drift
  entre `pyproject.toml` y lock (falla si alguien cambia rangos sin regenerar).
- **Dependencias**: `task_uv_lock_09`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_10_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k constraints -v"
  - id: auto_prod11_10_b
    runtime: python-pytest
    command: "pytest tests/unit -v  # la suite completa sigue verde instalada desde el lock"
  ```

### Fase D — Pin por digest e imágenes inmutables

#### `task_digest_pin_11` — Pinear por `@sha256` los 17 FROM y las imágenes auxiliares

- [ ] **Título**: Digest-pinning de todas las bases bajo `docker/` + aux del worker
- **Descripción**: Pinear por digest (formato
  `FROM python:3.12-slim@sha256:… # 3.12-slim`) los 17 `FROM`:
  `python:3.12-slim` (python-pytest:9, agent-runtime:19 y :62), `node:20-slim`
  (node-jest:4, node-vitest:4), `alpine:3.20` (generic-shell:8,
  generic-http:8, egress-proxy:7), `php:8.3-cli` (php-phpunit:4, php-pest:9),
  `ruby:3.3-slim`, `golang:1.22-bookworm`, `rust:1.79-slim`,
  `maven:3.9-eclipse-temurin-21`, `gradle:8-jdk21`,
  `mcr.microsoft.com/playwright:v1.48.0-jammy` y
  `mcr.microsoft.com/dotnet/sdk:8.0`. Pinear también `postgres:16-alpine` y
  `redis:7-alpine` en `apps/workers/src/workers/test_runtime.py:306,320`
  (constantes del módulo, con el digest en una constante documentada). El
  refresh queda delegado en el ecosistema docker de Dependabot.
- **Dependencias**: `task_dependabot_02` (regla dura: sin refresh automático,
  no se pinea).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_11_a
    runtime: python-pytest
    command: "pytest tests/unit/test_supply_chain_config.py -k digest_pinned -v"
  - id: auto_prod11_11_b
    runtime: python-pytest
    command: "pytest tests/unit/test_runtime_catalog.py -v  # el catálogo sigue consistente con los Dockerfiles"
  ```

#### `task_registry_adr_12` — ADR: registry y tags inmutables para los runtimes

- [ ] **Título**: ADR propuesto — distribución de imágenes runtime por digest
- **Descripción**: Redactar ADR en `docs/05-architecture-decisions/` con
  opciones para sustituir el esquema actual (catalog.py:31
  `_IMAGE_TAG = "v1"`, :41 `agent-runtime-{slug}:v1`, build local con
  `push: false` en build-runtime-templates.yml:66 — cada host construye su
  propia variante irreproducible): (a) GHCR `ghcr.io/agentic-platform` con tag
  inmutable versionado + resolución por digest en catálogo y worker
  (recomendada: el compose del installer ya asume ese registry), (b) registry
  self-hosted dentro del stack Compose, (c) statu quo build-local documentado.
  La implementación del push/login es alcance de
  `prod-01-despliegue-ejecutable`; este plan deja la decisión tomada y el
  catálogo preparado (campo de digest opcional en
  `packages/shared-test-runtimes/src/shared_test_runtimes/catalog.py`).
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**: no aplica (documento ADR); la revisión humana del ADR
  es el gate.

### Fase E — Documentación y runbook

#### `task_runbook_13` — Runbook de triage de vulnerabilidades y política de excepciones

- [ ] **Título**: `docs/06-runbooks/triage-vulnerabilidades.md` + referencia
- **Descripción**: Documentar: cómo leer un fallo de `security-scan`
  (pip-audit/npm audit/Trivy), criterios para actualizar vs suprimir, formato
  obligatorio de las entradas de `.trivyignore` e ignore-list (justificación +
  fecha de revisión), flujo de los PRs de Dependabot (quién los revisa, qué
  checks deben pasar antes de merge) y calendario de revisión de excepciones.
  Añadir resumen en `docs/04-reference/` (cadena de suministro: qué se escanea,
  dónde y con qué umbral).
- **Dependencias**: `task_sca_gate_08`.
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod11_13_a
    runtime: python-pytest
    command: "pytest tests/unit/test_docs_structure.py -k runbook_triage -v"
  ```

## Hallazgos de auditoría cubiertos

| fid       | Severidad | Tarea(s) que lo cierran                                                       |
| --------- | --------- | ----------------------------------------------------------------------------- |
| gap5-1    | high      | `task_pip_audit_05`, `task_npm_audit_06`, `task_trivy_07`, `task_sca_gate_08` |
| gap5-2    | high      | `task_dependabot_02` (vía reactiva), `task_next_update_01` (síntoma)          |
| gap5-3    | high      | `task_digest_pin_11`, `task_registry_adr_12`                                  |
| gap5-4    | medium    | `task_uv_lock_09`, `task_ci_lock_10`                                          |
| gap5-5    | medium    | `task_composer_checksum_04`                                                   |
| gap5-6    | low       | `task_actions_sha_03`                                                         |
| quality-1 | high      | `task_next_update_01`, `task_npm_audit_06` (gate anti-regresión)              |
| quality-5 | medium    | `task_uv_lock_09`, `task_ci_lock_10`                                          |

## Riesgos

1. **Fatiga de alertas / inundación de PRs de Dependabot**: 11 pyprojects + 2
   npm + 17 Dockerfiles + actions pueden generar decenas de PRs semanales.
   Mitigación: `groups` por ecosistema, límite de PRs abiertos y el runbook de
   `task_runbook_13` asignando responsable de revisión.
2. **El gate Trivy se pone rojo por CVEs nuevas sin fix en las bases** (típico
   en `-slim`/`alpine`): bloquearía PRs ajenos al problema. Mitigación:
   `ignore-unfixed: true` + `.trivyignore` con fecha de revisión + periodo de
   gracia de `task_sca_gate_08`.
3. **Digest pinning sin refresh operativo = congelación de CVEs**: si los PRs
   de Dependabot docker no se mergean, el pin es contraproducente. Mitigación:
   dependencia dura `task_dependabot_02` → `task_digest_pin_11` y revisión
   mensual en el runbook.
4. **La resolución congelada del lock rompe algo que los rangos abiertos
   ocultaban** (p. ej. una transitiva que CI resolvía distinta en cada run).
   Mitigación: `task_ci_lock_10` exige la suite completa verde instalada desde
   el lock antes de marcar la tarea.
5. **next 14.2.35 introduce regresiones de build/runtime en admin-panel o
   installer** pese a ser misma línea de parches. Mitigación: `npm run build`
   - typecheck en la propia tarea y smoke humano (ver tests humanos).
6. **Solape de alcance con prod-01/prod-02**: tocar workflows y registry desde
   dos planes a la vez puede generar conflictos. Mitigación: este plan está
   bloqueado por prod-02, y la implementación de registry queda explícitamente
   delegada a prod-01 (aquí solo el ADR).

## Tests humanos del Plan

```yaml
- id: human_prod11_01
  description: "El gate SCA detecta y bloquea una vulnerabilidad introducida a propósito"
  hint: "En una rama de prueba, degradar una dependencia a una versión vulnerable conocida"
  checklist:
    - "Crear rama con next degradado a 14.2.5 en admin-panel → el PR muestra security-scan en rojo (npm audit)"
    - "Crear rama añadiendo una dep Python con CVE conocida → pip-audit falla el job"
    - "Branch protection impide mergear con security-scan en rojo"
    - "Revertir la rama → security-scan vuelve a verde"

- id: human_prod11_02
  description: "Dependabot está vivo y agrupado"
  hint: "Revisar la pestaña Insights → Dependency graph → Dependabot del repo"
  checklist:
    - "Dependabot lista los 4 ecosistemas sin errores de parseo de dependabot.yml"
    - "Existe al menos un PR de Dependabot (o un run programado) tras la primera semana"
    - "Los PRs llegan agrupados por ecosistema, no uno por dependencia"

- id: human_prod11_03
  description: "Build reproducible desde el lockfile"
  hint: "Dos instalaciones limpias del mismo commit deben resolver versiones idénticas"
  checklist:
    - "pip install -e apps/api-server -c constraints.txt en dos venvs limpios → pip freeze idéntico"
    - "uv lock --check pasa en el commit final del plan"
    - "La imagen agent-runtime construida dos veces instala las mismas versiones (pip freeze dentro del contenedor)"

- id: human_prod11_04
  description: "Imágenes runtime íntegras: digest pineado y Composer verificado"
  hint: "Inspeccionar Dockerfiles y construir las imágenes PHP"
  checklist:
    - "grep 'FROM' docker/ -r → todos los FROM llevan @sha256 con tag en comentario"
    - "docker build de php-phpunit y php-pest completa y composer --version funciona dentro"
    - "No queda ningún 'curl … getcomposer.org/installer | php' en el repo"
    - "Admin-panel funciona tras la subida de next: login + 3 páginas principales sin errores de consola"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. `security-scan` es check obligatorio de branch protection y está en verde
   en `master` (sin `continue-on-error`).
3. `.github/dependabot.yml` activo con los 4 ecosistemas y al menos un ciclo
   semanal ejecutado sin errores.
4. `uv lock --check` en verde en CI; ningún `pip install` de CI ni del
   agent-runtime sin `-c constraints.txt`.
5. Cero `FROM` sin `@sha256` bajo `docker/`; ADR de registry revisado por un
   humano (aprobado o con decisión registrada).
6. Los 4 tests humanos del plan validados por un humano.
7. Entrada de changelog en `docs/07-changelog/prod-11-cadena-suministro.md`.
8. PR del plan mergeado a `master`.

## Próximo Plan

- **`prod-12-hardening-tools-agentes`** [P1] — Hardening de tools de agentes:
  SSRF, egress, reaper y marketplace. Complementa este plan aguas abajo: aquí
  se asegura la integridad de las imágenes que entran al sandbox; prod-12
  endurece lo que el código no confiable puede hacer una vez dentro.
- Coordinación pendiente con **`prod-01-despliegue-ejecutable`** (implementar
  el push a registry decidido en `task_registry_adr_12`) y con
  **`prod-02-ci-en-verde`** (lista de checks obligatorios y entrada del
  frontend del installer en el lint de CI).
