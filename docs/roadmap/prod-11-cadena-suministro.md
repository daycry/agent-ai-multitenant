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
| **Prioridad**                      | P1                               |
| **Bloqueado por**                  | `prod-02-ci-en-verde`            |
| **Tiempo estimado (calendario)**   | 2-3 semanas                      |
| **Tiempo estimado (persona-días)** | 10                               |
| **Rama git sugerida**              | `plan/prod-11-cadena-suministro` |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

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
- **Estado verificado (2026-07-31)**: la **subida está aplicada** — `next` y
  `eslint-config-next` en `14.2.35` en los dos `package.json` y en los dos
  `package-lock.json`; `node -e` confirma 14.2.35 instalado; la suite vitest del
  admin-panel en verde (778 tests / 94 ficheros). Lo acreditan dos guardas en
  verde: `test_npm_surfaces_pin_a_patched_next` y
  `test_npm_surfaces_pin_a_matching_eslint_config_next`.
  **La casilla NO se marca porque su test no puede pasar.** `npm audit` medido
  hoy sobre 14.2.35 ya instalado:

  | Comando                                       | admin-panel | installer  |
  | --------------------------------------------- | ----------- | ---------- |
  | `npm audit --omit=dev --audit-level=critical` | exit **0**  | exit **0** |
  | `npm audit --omit=dev --audit-level=high`     | exit **1**  | exit **1** |

  Es decir: **la crítica del hallazgo está cerrada** (`GHSA-955p-x3mx-jcvp`,
  divulgación no autenticada de Server Functions), que era el síntoma que el
  Resumen de este plan describe. Pero quedan **2 avisos `high`** cuyo rango
  abarca **todo 14.x** (uno de ellos arrastra un `postcss` empotrado) y el único
  fix que ofrece npm es **`next@16`**, un salto de major con roturas: eso no cabe
  en una tarea de 4 h ni se hace con `npm audit fix --force` a ciegas.
  **Necesita su propio plan o ADR** (migración a next 16 del admin-panel y del
  frontend del installer). Mientras no exista, `auto_prod11_01_a/b` seguirán en
  rojo por diseño y el gate npm no puede ser obligatorio sin mentir — lo cual
  bloquea a su vez `task_sca_gate_08`.
  Constancia escrita en tres sitios para que nadie repita la medición:
  `docs/06-runbooks/triage-vulnerabilidades.md` §6,
  `docs/04-reference/cadena-suministro.md` §4 y el comentario de cabecera del
  job `security-scan` en `ci.yml`.

- **Re-medido el 2026-08-01, resultado idéntico** (tercera medición): `critical`
  exit 0 y `high` exit 1 en las **dos** superficies, con npm proponiendo
  `next@16.2.12` («which is a breaking change»). Sigue abierta y **no por falta de
  trabajo**: la parte accionable dentro de 14.2.x está entregada, y lo que resta
  es un salto de major.
  Lo que se ha hecho hoy, ya que la medición no aportaba nada nuevo, es que la
  próxima pasada no tenga que repetirla: `docs/04-reference/cadena-suministro.md`
  §4 lleva ahora la **condición de salida escrita** —`npm audit --omit=dev
--audit-level=high` en exit 0 en las dos superficies— y el aviso de que un
  parche nuevo de 14.2.x **no la cumple**, porque el rango vulnerable de los dos
  avisos abarca toda la línea 14. Sin esa frase, el patrón observado es que cada
  ola vuelve a medir lo mismo y concluye lo mismo.

#### `task_dependabot_02` — Crear `.github/dependabot.yml` (pip + npm + docker + actions)

- [x] **Título**: Dependabot con 4 ecosistemas y agrupación de PRs
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

- [x] **Título**: Sustituir `@vN` por `@<sha40> # vN` en los 17 usos de actions
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
- **Recuento verificado (2026-07-31)**: no son 17 usos, son **46** en **4**
  workflows (`ci.yml`, `build-runtime-templates.yml`,
  `eval-on-prompt-change.yml` y `release-images.yml`, que no existía cuando se
  escribió el plan). **Los 46 van pineados por SHA de 40 caracteres**, y los 46
  llevan su tag legible en comentario `# vN`. Guardas ejecutadas en verde:
  `test_actions_pinned_by_commit_sha` y
  `test_pinned_actions_carry_a_readable_tag_comment`, ambas con aserción de que
  el descubrimiento encontró ≥17 — así no pueden pasar vacíamente si alguien
  vacía un workflow.

#### `task_composer_checksum_04` — Verificar el instalador de Composer en las imágenes PHP

- [x] **Título**: Eliminar el `curl | php` sin checksum de php-phpunit y php-pest
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

- [x] **Título**: pip-audit en CI tras los editable installs
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

- [x] **Título**: `npm audit --audit-level=high --omit=dev` en las 2 superficies npm
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
- **Estado verificado (2026-07-31)**: **HECHO**. `ci.yml` job
  `security-scan` tiene los dos pasos (`npm audit (admin-panel)` y
  `npm audit (installer)`), ambos con `--omit=dev --audit-level=high`. Lo
  acreditan dos guardas que se ejecutaron en verde:
  `test_security_scan_runs_npm_audit_on_both_surfaces` y
  `test_npm_audit_uses_the_agreed_threshold` (28 passed en
  `tests/unit/test_supply_chain_config.py`).
  **Ojo con los dos comandos de arriba**: son el gate que la tarea INSTALA, no
  una comprobación de que el paso exista. Hoy salen en **exit 1** en las dos
  superficies por el backlog heredado de `next` (ver `task_next_update_01`), que
  es justamente lo que un escáner debe hacer cuando hay una vulnerabilidad. El
  job corre en modo informe (`continue-on-error: true`) a propósito hasta que
  `task_sca_gate_08` lo convierta en gate.

#### `task_trivy_07` — Trivy sobre imágenes de apps y runtimes

- [x] **Título**: Escaneo Trivy HIGH/CRITICAL tras cada build de imagen
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
- **Estado verificado (2026-07-31)**: **HECHO**, y con más cobertura que la que
  pedía la tarea: **9 pasos `trivy-action` estáticos** repartidos en los tres
  workflows, que a la hora de correr escanean **24 imágenes** — 5 en
  `ci.yml:build-images` (api-server, installer, installer backend,
  agent-runtime, browser-runtime), 14 en la matriz de
  `build-runtime-templates.yml` (donde el smoke de WORKDIR se conserva pero deja
  de ser el único gate) y 5 en `release-images.yml`, donde el escaneo bloquea la
  publicación. Todos con `severity: HIGH,CRITICAL`, `exit-code: "1"`,
  `ignore-unfixed: true`, `.trivyignore` y caché de la DB. Guardas ejecutadas en
  verde: `test_image_building_jobs_are_scanned_by_trivy` (descubrimiento: un job
  nuevo que construya imágenes sin escanearlas sale rojo; la única excepción,
  `ci.yml:test-integration`, está declarada con motivo) y
  `test_trivy_steps_gate_on_high_and_critical`.
  El reparto de cobertura entre los tres workflows está documentado en
  `docs/04-reference/cadena-suministro.md` §1.

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
- **Estado verificado (2026-07-31)**: **NO implementable por un agente, y hoy
  además bloqueada.** Dos motivos independientes:
  1. Quitar el `continue-on-error` y añadir `SCA (pip-audit + npm audit)` a los
     checks requeridos exige **permisos de administración del repo** (Settings →
     Branches). No hay vía de código.
  2. Aunque los hubiera, el gate npm nacería en rojo permanente: ver la medición
     de `task_next_update_01` (2 avisos `high` de `next` sin fix dentro de 14.x).
     **Primero hay que resolver el backlog**, y eso pide un plan de migración a
     next 16.
     Lo que sí está listo para ese día: la mitad de las excepciones está entregada y
     acreditada — `.trivyignore` y `.pip-audit-ignore` existen y **cada entrada
     lleva justificación legible + `# review: YYYY-MM-DD` propio**, verificado por
     `test_sca_ignore_lists_exist_and_document_every_exception` (parametrizado sobre
     los dos ficheros, en verde). El modo del job es explícito, no un olvido
     (`test_security_scan_declares_its_gate_mode`), así que flipearlo es un cambio
     de una línea. El procedimiento completo, en el runbook §6.

### Fase C — Lockfile Python y builds reproducibles

#### `task_uv_lock_09` — ADR de toolchain + generar lockfile del monorepo

- [x] **Título**: ADR uv-vs-pip-tools + `uv.lock`/`constraints.txt` versionados
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
- **Estado verificado (2026-07-31)**: los tres sub-puntos (a), (b) y (c) están
  **implementados y acreditados**:
  - (a) **todos** los `pip install -e` de **todos** los workflows llevan
    `-c constraints.txt` — 20+ invocaciones, incluidas las de
    `eval-on-prompt-change.yml`, que instala la api-server y quedaría con otra
    resolución (`test_ci_installs_python_deps_with_constraints`, en verde);
  - (b) el `Dockerfile` del agent-runtime hace `COPY constraints.txt` y sus 4+
    `pip install` llevan `-c` (`test_agent_runtime_dockerfile_installs_with_constraints`);
  - (c) `uv lock --check` corre en `ci.yml` → `lint-python`, y va ahí a propósito:
    es higiene de repo, no un hallazgo de vulnerabilidad, así que **no hereda el
    modo informe** de `security-scan` (`test_ci_checks_the_lock_for_drift`).

  Ejecutado a mano: `uv lock --check` → **exit 0** (212 paquetes resueltos), y
  `constraints.txt` es **byte a byte** la salida de `uv export …` (198 pines
  `==`, 0 líneas de diferencia). O sea: el lock no ha derivado y el fichero que
  consume CI no está editado a mano.

- **Por qué la casilla NO se marca**: falta `auto_prod11_10_b`, que es
  precisamente el que protege del **riesgo 4** («la resolución congelada rompe
  algo que los rangos abiertos ocultaban»), y es una verificación **de CI**: el
  `.venv` local se instaló desde rangos y **no** desde el lock, así que correr la
  suite con él no prueba nada. Con CI caído no hay dónde ejecutarlo.
  El riesgo residual queda **acotado y nombrado**, medido comparando `pip freeze`
  del venv contra `constraints.txt`: de 170 paquetes comparables, 74 divergen,
  pero **72 son el venv retrasado** (el lock los sube). Solo hay **2 bajadas**, y
  las dos cruzan major:
  - `cryptography` 48.0.0 → **46.0.7**, porque `apps/api-server/pyproject.toml:46`
    declara `cryptography>=42,<47`. El lock hace lo correcto: **es el venv local
    el que viola el rango declarado**. Nuestro código solo usa
    `cryptography.fernet`, `.exceptions`, `hazmat.primitives.ciphers.aead` y
    `hazmat.primitives.serialization`, API estable en ambas ramas.
  - `cbor2` 6.1.1 → **5.9.0**. Ningún módulo del repo lo importa: entra solo por
    `webauthn`, que declara `cbor2>=5.6.5`, así que 5.9.0 está soportado por la
    librería que lo consume.

  Es decir: bajo por inspección, pero **no verificado**.

- 🔴 **`auto_prod11_10_b` EJECUTADO el 2026-08-01, y sale ROJO. El riesgo 4 era
  real y la inspección de ayer lo había dado por bajo.** No hizo falta CI: se
  construyó a mano el entorno que CI construye — `uv venv --seed --python 3.12` +
  los **mismos 12 editable installs del job `test-unit`**, todos con
  `-c constraints.txt` (148 paquetes, exit 0) — y se corrió la suite.

  | Entorno                                  | Resultado                 |
  | ---------------------------------------- | ------------------------- |
  | `.venv` del repo (3.13, desde rangos)    | verde                     |
  | venv limpio (3.12, `-c constraints.txt`) | **4094 passed, 2 failed** |

  Los dos que caen son
  `test_security_headers_middleware.py::test_the_public_api_v1_contract_stays_published_in_prod`
  y `test_metrics_endpoint_wired.py::test_metrics_does_not_shadow_the_authenticated_inbox_metrics`.

  **No es el árbol en movimiento** (había otros cuatro carriles escribiendo): los
  cuatro ficheros implicados se corrieron en los DOS entornos en el mismo minuto —
  39 passed en el del repo, 2 failed en el del lock. **Ni es la versión de
  Python**: bajando sólo `fastapi`/`starlette` a las del repo dentro del **mismo**
  venv 3.12, los dos pasan.

  **Causa raíz**: el lock pina `fastapi==0.141.1` / `starlette==1.3.1`; el `.venv`
  del repo tiene `0.136.1` / `1.0.0`. FastAPI 0.141 dejó de aplanar en
  `app.routes` las rutas que entran por `include_router()`: ahora aparecen
  envueltas en objetos `_IncludedRouter` sin `.path`. Los dos tests recorren
  `app.routes` leyendo `.path`, así que pasan de ver ~300 rutas a ver cuatro y
  concluyen que «desapareció el contrato público de la API».
  **La aplicación está intacta** —las rutas se sirven igual—; lo que cambió es la
  introspección. El fallo se disfraza de regresión de producto y no lo es.

  **Por qué esto vale más que un rojo**: `fastapi` era uno de los «72 paquetes que
  el lock sube» que el análisis de ayer descartó por inspección. Con CI caído
  **nadie estaba ejecutando la resolución del lock**, así que el día que CI vuelva
  estos dos salen rojos en `master` y parecerán rotos por el commit que pase por
  allí. Documentado en
  [`gotchas/venv-local-por-detras-del-lock.md`](../03-guides/gotchas/venv-local-por-detras-del-lock.md),
  con la receta para reproducir el entorno de CI en local.

- **Por qué la casilla NO se marca**: porque su test está en rojo, y ahora se sabe
  exactamente por qué. El arreglo es de los dos tests, no del lock ni de la app
  (leer `app.openapi()["paths"]`, o recorrer `route.routes` recursivamente), y
  vive en `tests/unit/test_metrics_endpoint_wired.py` y
  `test_security_headers_middleware.py` — ficheros de materia ajena a este carril
  y que otros carriles están tocando en esta misma pasada. Se deja **diagnosticado
  y no parcheado** a propósito.

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
- **Estado verificado (2026-07-31)**: la parte de `docker/` está **HECHA y
  acreditada**, con más alcance del que decía la tarea: los `FROM` externos bajo
  `docker/` son **22 en 19 Dockerfiles** (no 17 — el árbol creció desde que se
  escribió el plan) y **los 22 llevan `@sha256:` con el tag dentro de la
  referencia**. Guardas ejecutadas en verde: `test_docker_bases_pinned_by_digest`
  (descubrimiento, con aserción de que encontró ≥20) y
  `test_digest_pinned_bases_keep_their_tag_readable` (prohíbe el
  `FROM python@sha256:…` sin tag, que sería inauditable y dejaría a Dependabot
  sin poder proponer la siguiente versión).
- **Por qué la casilla NO se marca — y no es solo falta de tiempo**: quedan
  `postgres:16-alpine` y `redis:7-alpine` de
  `apps/workers/src/workers/test_runtime.py` (`DEFAULT_POSTGRES` :326 /
  `DEFAULT_REDIS` :340, la numeración se movió respecto al plan). Pinearlos ahí
  **choca con la regla dura de esta misma fase**: la dependencia
  `task_dependabot_02 → task_digest_pin_11` existe porque «sin refresco
  automático, no se pinea», y el ecosistema `docker` de Dependabot parsea
  **Dockerfiles y ficheros compose, no fuentes Python**. Un `@sha256:` en una
  constante de módulo no tendría vehículo de refresco: sería exactamente la
  congelación de CVEs del riesgo 3, y encima en dos imágenes que el worker
  levanta para ejecutar tests de código no confiable.
  **Decisión que falta (no es implementación, es diseño)**, tres salidas:
  (a) mover las dos referencias a un fichero que Dependabot sí parsee —un
  `docker-compose.aux.yml` o un Dockerfile trivial— y pinear allí;
  (b) pinear en Python y aceptar una revisión manual mensual, anotada en el
  runbook con fecha de revisión como las excepciones SCA;
  (c) dejarlas por tag y declararlo excepción razonada (son sidecars efímeros de
  un test, sin datos persistentes ni exposición de red fuera del bridge
  per-tarea).
  Sin esa decisión no se puede cerrar la casilla honestamente.
- **Revisado el 2026-08-01 a la luz del ADR 0148 (ya `accepted` e implementado):
  no cierra esta casilla, pero desarma su argumento principal.** El ADR 0148 va de
  las 14 imágenes que este proyecto **produce**; esta casilla va de dos imágenes
  que **consume**, así que ni el manifiesto ni el pull-por-digest del worker las
  tocan. Lo que sí cambia es la premisa que bloqueaba la decisión: se afirmaba que
  «un digest en Python no tiene vehículo de refresco», y el job `refresh-digests`
  de `build-runtime-templates.yml` es precisamente un vehículo de refresco de
  digests **que no es Dependabot** y que escribe en un artefacto que consume
  código Python. O sea: la opción (b) ya no es la única salida del lado Python,
  y aparece una cuarta —reutilizar ese patrón—, con el matiz de que resolver
  digests de imágenes ajenas en un job propio es un diseño distinto del que el
  0148 firmó. Anotado en `docs/04-reference/cadena-suministro.md` §3.
  **Sigue siendo decisión, no implementación**, y además el cambio vive en
  `apps/workers/src/workers/test_runtime.py`, fuera del carril de este agente.

#### `task_registry_adr_12` — ADR: registry y tags inmutables para los runtimes

- [x] **Título**: ADR propuesto — distribución de imágenes runtime por digest
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
- **Estado verificado (2026-07-31)**: ADR redactado —
  `docs/05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md`,
  `status: proposed`, con las tres opciones del plan, recomendación razonada (a:
  GHCR + digest, con b como mirror opcional) y las dos condiciones para que no
  empeore nada. **Sigue `proposed` a propósito**: dónde vive el registry, quién
  publica y qué red necesita el host de un tenant son decisiones de producto, no
  de toolchain — a diferencia del ADR 0147, que nació `accepted` por ser
  toolchain puro. **La firma humana es el gate** y la rastrea el criterio de
  cierre 5, no esta casilla.
  El campo `digest` opcional en `catalog.py` **NO se ha añadido**: un campo que
  nadie puebla antes de que la decisión se firme sería el patrón que esta base
  repite (mecanismo entregado, cero llamantes — trampa nº5 de
  `verificar-antes-de-implementar.md`) y prejuzgaría la opción. Entra con la
  implementación, que es de `prod-01`.
  Guardas ejecutadas en verde en `tests/docs/test_supply_chain_docs.py`:
  `test_registry_adr_is_proposed_and_points_at_the_plan`,
  `test_registry_adr_offers_the_three_options_with_a_recommendation`,
  `test_registry_adr_names_the_status_quo_it_replaces` y
  `test_registry_adr_does_not_claim_implementation` (esta última se pone roja si
  alguien toca el catálogo sin cerrar el ADR).

### Fase E — Documentación y runbook

#### `task_runbook_13` — Runbook de triage de vulnerabilidades y política de excepciones

- [x] **Título**: `docs/06-runbooks/triage-vulnerabilidades.md` + referencia
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
    command: "pytest tests/docs/test_supply_chain_docs.py -k runbook_triage -v"
  ```
- **Estado verificado (2026-07-31)**: **HECHO**. `docs/06-runbooks/triage-vulnerabilidades.md`
  (7 secciones: qué se escanea, cómo leer cada fallo, actualizar vs suprimir,
  cómo valorar el riesgo real, política de excepciones con formato obligatorio y
  calendario, del modo informe al gate, y flujo de los PRs de Dependabot) +
  el resumen de referencia `docs/04-reference/cadena-suministro.md`, indexado en
  `docs/04-reference/README.md` y enlazado en los dos sentidos con el runbook.
  **Corrección del test id**: el plan apuntaba a
  `tests/unit/test_docs_structure.py`, un fichero que no existe ni existió; los
  invariantes de documentación de este repo viven en `tests/docs/`. La guarda se
  escribió allí (`tests/docs/test_supply_chain_docs.py`, **15 passed**; con
  `-k runbook_triage`, **6 passed**) y el comando de arriba se ha corregido.
  Las guardas son de **descubrimiento**, no de subcadena: la lista de escáneres
  se deriva de los workflows y la de ficheros de excepción de la raíz del repo,
  así que añadir Grype u osv-scanner a CI —o un `.otro-ignore`— sin documentarlo
  las pone en rojo. Ciclo de mutación ejecutado: renombrar el «Calendario de
  revisión», romper el formato `# review: YYYY-MM-DD` y renombrar
  `.pip-audit-ignore` puso 2 guardas en rojo; restaurado, verde otra vez.
  **Dependencia invertida a propósito**: el plan la hacía depender de
  `task_sca_gate_08`, pero documentar el gate DESPUÉS de encenderlo deja al
  operador sin criterio justo en la semana de triage. El runbook §6 documenta el
  estado actual (modo informe) y los dos pasos humanos que faltan para flipearlo.

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
