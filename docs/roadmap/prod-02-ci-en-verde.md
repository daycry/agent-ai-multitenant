---
plan_id: prod-02-ci-en-verde
title: CI resucitado y en verde — triggers, gates obligatorios y cobertura
status: in_progress
blocking_plan: null
started_at: 2026-06-11
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 9
estimated_cost_human_eur: 4.050 € – 5.400 €
estimated_cost_ai_eur: 40 € – 80 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P0
---

# Plan prod-02 — CI resucitado y en verde: triggers, gates obligatorios y cobertura

## Cabecera

| Campo                              | Valor                      |
| ---------------------------------- | -------------------------- |
| **ID del Plan**                    | `prod-02-ci-en-verde`      |
| **Estado**                         | `in_progress`              |
| **Prioridad**                      | P0                         |
| **Bloqueado por**                  | — (null)                   |
| **Tiempo estimado (calendario)**   | 2-3 semanas                |
| **Tiempo estimado (persona-días)** | 9                          |
| **Rama git sugerida**              | `plan/prod-02-ci-en-verde` |

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que el arnés de CI está **funcionalmente muerto**: los tres workflows (`ci.yml`, `build-runtime-templates.yml`, `eval-on-prompt-change.yml`) disparan sobre la rama `main`, pero la rama por defecto del repo es `master` — los PRs reales (#38–#49, ramas `fix/*`, `feat/*`, `docs/*` → master) no ejecutan **ningún** job de CI. Y cuando CI sí corría (pushes a `plan/**`), lleva ~19 runs consecutivos en rojo desde 2026-05-29 (mypy strict en `factory.py`, prettier, markdownlint, y el stack de integración roto por el perfil AppArmor `agentic-default` no cargado en los runners) y se mergeó igualmente: el gate cross-tenant (Principio nº1 de CLAUDE.md) **no se ejecuta desde hace 12 días**.

Este plan resucita CI y lo convierte en gate real:

1. **Triggers correctos**: los 3 workflows disparan sobre `master` (+ `plan/**` + `workflow_dispatch`).
2. **Master en verde**: corregir los fallos reales de mypy/prettier/markdownlint y el arranque del stack (AppArmor) en runners.
3. **Gates recableados**: cross-tenant de nuevo en ejecución, `tests/security` y `tests/docs` cableados a jobs, cobertura medida con umbral, e2e del pipeline de agentes con la imagen `agent-runtime:v1` disponible, smoke de contenedores de apps, y vitest+Playwright del admin-panel en CI.
4. **Robustez del arnés**: `timeout-minutes` en todos los jobs, skips convertidos en fallos en CI, sleeps de timing sustituidos.
5. **Regla de salida**: ningún merge a `master` con CI en rojo — con mecanismo de protección de rama (decisión ADR, ver Decisiones clave).

## Alcance

**Entra**:

- Edición de `.github/workflows/ci.yml`, `build-runtime-templates.yml` y `eval-on-prompt-change.yml` (triggers, jobs nuevos, timeouts, cobertura).
- Correcciones mínimas de código/docs imprescindibles para poner master en verde (`apps/api-server/src/api_server/llm_providers/factory.py`, ficheros con fallos de prettier, MD001/MD004 en `docs/roadmap/06.17-*` y `docs/07-changelog/06.17-*`).
- Overlay `docker/docker-compose.ci.yml` o carga del perfil AppArmor en runners.
- Meta-tests estáticos en `tests/docs/` que pineen las invariantes del arnés (triggers, timeouts, gates presentes) para que CI no pueda volver a degradarse en silencio.
- Configuración de cobertura (`[tool.coverage]` en `pyproject.toml`, `--cov-fail-under`).
- Job de frontend con vitest + subconjunto mockeado de Playwright.
- Mecanismo de protección de `master` (la decisión de plan GitHub/visibilidad es humana, vía ADR).

**Queda fuera**:

- Crear los Dockerfiles de las apps (api-server, workers, orchestrator…) — es `quality-2`, cubierto por **prod-01-despliegue-ejecutable**. La tarea de smoke de contenedores de este plan (Fase D) consume esas imágenes y se coordina con prod-01.
- Arreglar el flujo SSO, sesiones y 401 del admin-panel (frontend-1/2/3) — **prod-09**.
- SCA/Dependabot/lockfiles en CI — **prod-11** (este plan solo deja los jobs listos para que prod-11 añada pasos).
- Migrar la DB de test a una por worker xdist (solo se documenta la limitación, tests-8).
- i18n y partición de componentes del frontend — **prod-16**.

## Decisiones clave

1. **Protección de `master` (requiere ADR — decisión humana)**. El repo es privado en plan GitHub Free: `gh api branches/master/protection` → 403 «Upgrade to GitHub Pro». Opciones:
   - **(a) Subir a GitHub Pro/Team** y activar branch protection con required checks (recomendada: coste bajo, cero cambio de exposición).
   - **(b) Hacer el repo público** (gratis, pero decisión de producto sobre visibilidad del código — no la toma este plan).
   - **(c) Disciplina + verificación**: sin protección server-side, exigir `gh pr checks --watch` verde antes de merge, documentado en conventions.md (débil: no es un gate técnico).
   - Se redactará `docs/05-architecture-decisions/` con las tres opciones; la recomendación del plan es (a), con (c) como medida puente desde el día 1.
2. **AppArmor en CI: cargar el perfil real, no desactivarlo**. Se prefiere `sudo apparmor_parser -r -W docker/apparmor/agentic-default.profile` en el runner (prueba el perfil de producción en cada run) frente a un overlay `apparmor=unconfined`. El overlay `docker-compose.ci.yml` queda como fallback documentado si el parser del runner rechaza el perfil.
3. **Umbral de cobertura tipo ratchet**: no se impone 70/80 de golpe (pondría CI en rojo de salida y violaría la regla «master en verde»). Se mide el valor real, se fija `--cov-fail-under` en ese valor (redondeado a la baja) y se sube gradualmente hasta los 70/80 de `conventions.md`. Si el humano prefiere relajar la convención escrita, eso es un cambio de `conventions.md` que se decide aquí explícitamente, no por omisión.
4. **Triggers: `master` + `plan/**`+`workflow_dispatch`; se elimina `main`** de los tres workflows. Mantener `main` solo conservaría una rama muerta 248 commits por detrás como falsa señal.
5. **Skips en CI = fallos**: en CI (`CI=true`), las precondiciones ausentes (imagen `agent-runtime:v1`, daemon Docker) hacen `pytest.fail`, no `pytest.skip` — el mismo criterio que ya aplica el gate cross-tenant con exit 5.

## Tareas

### Fase A — Resucitar los triggers y poner master en verde

#### `task_prod_02_01` — Triggers main→master en los 3 workflows

- [x] **Título**: Cambiar `branches: [main, "plan/**"]` / `pull_request: [main]` por `[master, "plan/**"]` / `[master]` en `ci.yml:3-7`, `build-runtime-templates.yml:11-19` y `eval-on-prompt-change.yml:14-27`; añadir `workflow_dispatch` a los tres.
- **Tiempo**: 2 h · **Complejidad**: s
- Añadir meta-test estático `tests/docs/test_ci_workflows.py` que parsea los YAML de `.github/workflows/` y falla si algún trigger referencia una rama que no existe en el remoto por defecto (`master`) — evita la regresión inversa.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_01_a
    runtime: python-pytest
    command: "pytest tests/docs/test_ci_workflows.py -v -k triggers"
  ```

#### `task_prod_02_02` — Arreglar los fallos reales que tienen CI en rojo (mypy, prettier, markdownlint)

- [x] **Título**: Corregir `factory.py:111-116` (tipar `kwargs: dict[str, Any]` o pasar argumentos explícitos a `OllamaProvider` — 2 errores arg-type bajo mypy strict), los ficheros con fallos de prettier, y los MD001/MD004 de `docs/roadmap/06.17-*` y `docs/07-changelog/06.17-*`, hasta que `pre-commit run --all-files` y `markdownlint docs/**/*.md` salgan a 0 en local.
- **Tiempo**: 1 día · **Complejidad**: m
- Incluye re-ejecutar la suite completa en local para auditar qué se mergeó en rojo desde 2026-05-29 (recomendación del hallazgo tests-2); las regresiones encontradas se registran y, si exceden este plan, se derivan al plan correctivo correspondiente.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_02_a
    runtime: python-pytest
    command: "pre-commit run --all-files --show-diff-on-failure"
  - id: auto_prod_02_02_b
    runtime: node-jest
    command: "npx --yes markdownlint-cli@0.48.0 --config .markdownlint.jsonc 'docs/**/*.md'"
  ```

#### `task_prod_02_03` — Stack de integración arrancable en runners: perfil AppArmor

- [ ] **Título**: Añadir al job `test-integration` (antes de `docker compose up --wait`, `ci.yml:229-232`) el paso `sudo apparmor_parser -r -W docker/apparmor/agentic-default.profile`; crear como fallback documentado `docker/docker-compose.ci.yml` con `apparmor=unconfined` solo para CI. Corregir el comentario engañoso de `docker-compose.yml:39` («a no-op where AppArmor is absent» — en runners Ubuntu AppArmor SÍ está presente).
- **Tiempo**: 1 día · **Complejidad**: m
- **Depende de**: `task_prod_02_01` (sin triggers correctos no hay run donde verificarlo).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_03_a
    runtime: python-pytest
    command: "pytest tests/integration -m cross_tenant -v"
  ```

#### `task_prod_02_04` — Protección de master: ADR + medida puente

- [ ] **Título**: Redactar ADR (proposed) con las 3 opciones de protección de rama (Pro/público/disciplina, ver Decisiones clave) para decisión humana; aplicar de inmediato la medida puente: documentar en `docs/context/conventions.md` la regla «ningún merge a master con CI en rojo» con el comando de verificación (`gh pr checks <pr> --watch`), y activar la protección server-side en cuanto el humano decida.
- **Tiempo**: 4 h · **Complejidad**: s
- **Depende de**: `task_prod_02_02` (no tiene sentido exigir verde antes de que verde sea alcanzable).

### Fase B — Recablear los gates que faltan

#### `task_prod_02_05` — Cablear tests/security y tests/docs a CI

- [ ] **Título**: Añadir al job `test-unit` de `ci.yml` los pasos `pytest tests/security -v` y `pytest tests/docs -v`, tratando exit 5 (colección vacía) como fallo — mismo patrón que el gate cross-tenant (`ci.yml:277-282`). `tests/security` (4 ficheros: invariantes de socket Docker, cap_drop, privileged, RLS, egress, secretos) y `tests/docs` (3 ficheros) son estáticos: no necesitan stack.
- **Tiempo**: 3 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_05_a
    runtime: python-pytest
    command: "pytest tests/security tests/docs -v"
  ```

#### `task_prod_02_06` — Gate de cobertura con umbral ratchet (tests-5 + quality-6)

- [ ] **Título**: Añadir `--cov=api_server --cov=workers --cov=orchestrator --cov-report=term --cov-fail-under=<valor medido>` al paso de unit tests de `ci.yml:197`; configurar `[tool.coverage.report]`/`[tool.coverage.paths]` en `pyproject.toml`; documentar el plan de subida gradual hasta 70% global / 80% dominio crítico (auth, multi-tenancy, agent loop, orchestrator) o, si el humano lo decide, corregir `conventions.md:233-236` para que la regla escrita sea la real.
- **Tiempo**: 1 día · **Complejidad**: m
- **Depende de**: `task_prod_02_02`.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_06_a
    runtime: python-pytest
    command: "pytest tests/unit --cov=api_server --cov=workers --cov=orchestrator --cov-report=term --cov-fail-under=60"
  ```
  (El `60` es placeholder: el valor real se fija al medir; el meta-test de `test_ci_workflows.py` verifica que el flag `--cov-fail-under` existe en el workflow.)

### Fase C — E2E del pipeline de agentes y smoke de contenedores

#### `task_prod_02_07` — agent-runtime:v1 disponible en el job de integración + skips→fallos en CI

- [ ] **Título**: En el job `test-integration`, construir `agent-runtime:v1` antes de pytest (`docker build -f docker/agent-runtimes/agent-runtime/Dockerfile -t agent-runtime:v1 .`, con caché de buildx para no penalizar ~cada run) de modo que `tests/integration/test_e2e_smoke.py:71-73` deje de saltarse; añadir helper de conftest que en `CI=true` convierta los `pytest.skip` por precondición ausente (imagen, daemon Docker — 22 ficheros `requires_docker`) en `pytest.fail`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Depende de**: `task_prod_02_03` (el stack debe arrancar primero).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_07_a
    runtime: python-pytest
    command: "CI=true pytest tests/integration/test_e2e_smoke.py -v"
  ```

#### `task_prod_02_08` — Smoke mínimo de contenedores de apps tras build-images

- [ ] **Título**: Tras el paso «Build each app» (`ci.yml:347-356`), arrancar el contenedor resultante de api-server contra el stack de infra y verificar `curl /healthz` (o como mínimo `docker run --rm <imagen> python -c "import api_server"`); cablear `tests/smoke` definiendo `SMOKE_BASE_URL` en ese paso para que `tests/smoke/conftest.py:46-52` deje de saltarse siempre.
- **Tiempo**: 1,5 días · **Complejidad**: l
- **Coordinación con prod-01**: los Dockerfiles de las apps NO existen aún (quality-2, asignado a prod-01-despliegue-ejecutable). Esta tarea se implementa contra las imágenes que prod-01 entregue; si prod-01 no ha cerrado, la tarea arranca con la única imagen disponible y deja el paso parametrizado para el resto. No convierte este plan en bloqueado: el paso falla solo para imágenes existentes.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_08_a
    runtime: python-pytest
    command: "SMOKE_BASE_URL=http://localhost:8001 pytest tests/smoke -v"
  ```

### Fase D — Frontend del admin-panel en CI

#### `task_prod_02_09` — Job de vitest del admin-panel

- [ ] **Título**: Añadir paso `npm run test` (vitest, `lib/*.test.ts`) al job `lint-typescript` de `ci.yml:120-133` (o job propio `test-frontend`), con `npm ci` cacheado.
- **Tiempo**: 3 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_09_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run test"
  ```

#### `task_prod_02_10` — Playwright (subconjunto mockeado) en CI + base de API compartida

- [ ] **Título**: Añadir job de e2e que ejecute el subconjunto de los 99 specs que funciona 100% con mocks `page.route` (88 specs, sin api-server real): `npx playwright install chromium` + `next build`/`next start` + `npm run e2e`. Extraer la base hardcodeada `http://localhost:8001` de los specs (p. ej. `e2e/admin-models-prices.spec.ts:96`) a una constante compartida derivada de `NEXT_PUBLIC_API_URL`, para que `E2E_BASE_URL` del `playwright.config.ts:38` funcione contra otro backend.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: `task_prod_02_09`.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_10_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- --project=chromium"
  ```

### Fase E — Robustez del arnés

#### `task_prod_02_11` — timeout-minutes en todos los jobs

- [ ] **Título**: Añadir `timeout-minutes: 30` a cada job de los 3 workflows (`60` para build-images) — hoy ninguno lo define (default 6 h; los runs sanos tardaban 9-11 min). Extender `tests/docs/test_ci_workflows.py` para exigir el campo en todo job.
- **Tiempo**: 1 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_11_a
    runtime: python-pytest
    command: "pytest tests/docs/test_ci_workflows.py -v -k timeout"
  ```

#### `task_prod_02_12` — Sleeps de timing y dependencia de orden de la DB de test

- [ ] **Título**: Sustituir los sleeps reales por inyección de reloj siguiendo el patrón existente de `test_backoff.py` (`test_api_rate_limit.py:263` — 3 s reales por ventana; `test_pool_queue.py:49/104` — 0,2-0,3 s); documentar en `tests/integration/conftest.py` (junto a la fixture session-scoped, líneas 116-117) la prohibición de pytest-xdist y la dependencia de orden de `test_migrations.py:147`, con referencia a un follow-up para DB por worker.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_02_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_api_rate_limit.py tests/integration/test_pool_queue.py -v --timeout=60"
  ```

## Hallazgos de auditoría cubiertos

| fid        | Severidad | Tarea(s) que lo cierran                  |
| ---------- | --------- | ---------------------------------------- |
| tests-1    | critical  | task_prod_02_01                          |
| tests-2    | critical  | task_prod_02_02, task_prod_02_04         |
| tests-3    | high      | task_prod_02_03                          |
| tests-4    | high      | task_prod_02_05                          |
| tests-5    | medium    | task_prod_02_06                          |
| tests-6    | medium    | task_prod_02_07                          |
| tests-7    | medium    | task_prod_02_08 (coordinado con prod-01) |
| tests-8    | low       | task_prod_02_11, task_prod_02_12         |
| frontend-7 | medium    | task_prod_02_09, task_prod_02_10         |
| quality-6  | medium    | task_prod_02_06 (mismo gate que tests-5) |

## Riesgos

1. **La re-ejecución completa de la suite destapa regresiones mergeadas en rojo** (12 días de merges sin CI). Mitigación: task_prod_02_02 incluye la auditoría explícita; lo que exceda el alcance se deriva al plan correctivo correspondiente, no se oculta.
2. **La protección de rama depende de una decisión externa** (plan GitHub Pro o repo público). Hasta que el humano decida, la regla «ningún merge en rojo» es solo disciplina (opción c). Mitigación: la medida puente se activa el día 1 y el ADR fuerza la decisión.
3. **El perfil AppArmor puede no cargar en los runners de GitHub** (versiones del parser, fsmount). Mitigación: fallback `docker-compose.ci.yml` con `apparmor=unconfined` documentado — perdiendo la verificación del perfil en CI, que se anota como deuda.
4. **task_prod_02_08 depende de artefactos de prod-01** (Dockerfiles de apps inexistentes hoy). Mitigación: paso parametrizado que solo valida imágenes existentes; coordinación explícita anotada en ambos planes.
5. **Coste de tiempo de CI**: construir `agent-runtime:v1` y correr Playwright puede añadir 10-20 min por run. Mitigación: caché de buildx y de npm, subconjunto mockeado de Playwright (no los 99 specs con backend real), `concurrency.cancel-in-progress` ya activo.
6. **El umbral de cobertura inicial puede quedar muy por debajo de 70/80** y generar falsa sensación de gate. Mitigación: el plan deja escrito el ratchet con fechas y deja al humano la alternativa de corregir `conventions.md` — lo inaceptable es la divergencia silenciosa actual.

## Tests humanos del Plan

```yaml
- id: human_prod_02_01
  description: "CI dispara y está en verde en el flujo real de PRs"
  hint: "Abrir un PR trivial (docs) contra master y observar los checks"
  checklist:
    - "Abrir un PR desde una rama fix/* o docs/* contra master → los 3 workflows aparecen como checks"
    - "gh run list muestra el run del PR (no solo de plan/**)"
    - "Todos los jobs terminan en verde, incluido el stack de integración (sin error de AppArmor)"
    - "El log del job test-integration muestra el paso 'pytest cross-tenant isolation gate' EJECUTADO con tests recogidos (>0)"
    - "Ningún job supera su timeout-minutes"

- id: human_prod_02_02
  description: "Los gates muerden: una regresión deliberada pone CI en rojo"
  hint: "En una rama de prueba desechable, romper algo que cada gate debe cazar"
  checklist:
    - "Quitar cap_drop de un servicio del compose → tests/security falla en CI"
    - "Introducir un error de tipo en un fichero Python → mypy (pre-commit) falla en CI"
    - "Borrar los tests cross_tenant del árbol → el gate falla por colección vacía (exit 5), no pasa en silencio"
    - "Bajar artificialmente la cobertura (borrar tests de un módulo cubierto) → --cov-fail-under pone el job en rojo"
    - "Cerrar el PR de prueba sin mergear"

- id: human_prod_02_03
  description: "Frontend y e2e protegen contra regresiones"
  hint: "Ver los jobs nuevos en el run de un PR cualquiera"
  checklist:
    - "El run incluye vitest del admin-panel y pasa"
    - "El run incluye el job de Playwright (subconjunto mockeado) y pasa con chromium"
    - "El log de test-integration muestra test_e2e_smoke EJECUTADO (no skipped) con agent-runtime:v1 construida"
    - "Romper un spec de Playwright a propósito → el job se pone rojo"

- id: human_prod_02_04
  description: "Regla de salida: ningún merge a master con CI en rojo"
  hint: "Verificar el mecanismo de protección decidido en el ADR"
  checklist:
    - "El ADR de protección de master está decidido (no proposed) por un humano"
    - "Si opción server-side: intentar mergear un PR con un check en rojo → GitHub lo bloquea"
    - "Si medida puente: conventions.md documenta la regla y el comando gh pr checks"
    - "gh run list de los últimos 7 días: cero merges a master con run asociado en failure"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. `gh run list` muestra runs verdes sobre PRs reales contra `master` (no solo `plan/**`), incluyendo el gate cross-tenant ejecutado con colección > 0.
3. Los 4 tests humanos del plan validados por un humano.
4. ADR de protección de `master` decidido por un humano (aceptado o rechazado con alternativa).
5. Entrada de changelog en `docs/07-changelog/prod-02-ci-en-verde.md`.
6. PR del plan mergeado a `master` **con su propio CI en verde** — este plan es su primera prueba de fuego.

## Próximo Plan

- **prod-03-guardrails-validacion-humana** [P0] — Guardrails cableados y validación humana operativa. Con CI vivo, los tests que ese plan añada sobre los 4 puntos de interceptación (pre_llm, post_llm, pre_tool, post_tool) tendrán un arnés que realmente los ejecute.
- Coordinaciones abiertas de este plan: **prod-01** (Dockerfiles de apps para task_prod_02_08), **prod-11** (los jobs de CI quedan listos para añadir SCA/audit), **prod-16** (la suite Playwright en CI es prerequisito de su refactor de frontend con red de seguridad).
