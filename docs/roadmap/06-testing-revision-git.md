---
plan_id: 06-testing-revision-git
title: Testing Heterogéneo, Revisión y Ciclo Git del Plan
status: pending_approval
blocking_plan: [03-chat-planning-aprobacion, 05-mcp-tools-avanzadas]
started_at: null
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 85-105
estimated_cost_human_eur: 34.000 € – 42.000 €
estimated_cost_ai_eur: 200 € – 320 €
created_by: system_architect
spec_sections_referenced: [12.4, 14, 31.12]
docs_language: es
---

# Plan 06 — Testing Heterogéneo, Revisión y Ciclo Git del Plan

## Cabecera

| Campo | Valor |
|-------|-------|
| **ID del Plan** | `06-testing-revision-git` |
| **Estado** | `pending_approval` |
| **Bloqueado por** | `03-chat-planning-aprobacion`, `05-mcp-tools-avanzadas` |
| **Tiempo estimado (calendario)** | 4-5 semanas |
| **Tiempo estimado (persona-días)** | 85-105 |
| **Previsión de coste — humano** | 34.000 € – 42.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA** | 200 € – 320 € |
| **Aprobador propuesto** | System Admin |
| **Rama git** | `plan/06-testing-revision-git` |
| **Secciones del .docx** | [12.4, 14, 31.12] |

---

## Descripción Detallada

### Resumen Ejecutivo

Catálogo de runtime templates (Python/Node/PHP/Go/Java/Ruby/Rust/.NET) como imágenes Docker, contenedores test-runtime efímeros con servicios auxiliares, TestReport canónico al agente revisor, contenedor review-runtime persistente para validación humana del plan, integración Git completa con worktrees y PR automático al finalizar plan.

### Contexto

Esta es la fase más crítica del MVP. Sin ella, los agentes producen código pero nadie lo prueba en stack heterogéneo y nada llega al repo. Al cerrarla, el flujo completo plan → tareas → tests por stack → revisión humana → PR está operativo.

### Alcance

**Entra en este plan**:

- 14 runtime templates: python-pytest, node-jest, node-vitest, node-playwright, php-phpunit, php-pest, go-test, java-maven, java-gradle, ruby-rspec, rust-cargo, dotnet-test, generic-shell, generic-http.
- Configuración Project.execution_runtimes con templates declarados por proyecto.
- Vinculación tarea ↔ runtime en acceptance_criteria.
- Servicios auxiliares: compose efímero por tarea con DB/Redis efímeros.
- Testcontainers opt-in con DinD proxy controlado.
- Caché de dependencias por hash de lock.
- TestReport canónico estructurado al agente revisor.
- Bare repos persistentes + git worktrees por tarea.
- Sincronización con remoto: fetch periódico + webhooks Git.
- Contenedor review-runtime persistente (vive durante revisión humana del plan).
- URL temporal firmada + terminal web + logs WebSocket + botón 're-ejecutar tests'.
- Ciclo de vida: 48h timeout, suspensión por inactividad 4h, cap por tenant.
- Doble Kanban con flujo real (no estático): tareas en done agregan progreso al plan.
- Rama git por plan, commits con trailers, push tras revisión del agente, PR automático al completar plan.
- Múltiples repos por plan = múltiples PRs.
- Política push_policy aplicada al merge.

**Queda fuera (otras fases)**:

- Documentación canónica /docs y visor (Fase 7).
- Evals continuos sobre el TestReport (Fase 14).

### Decisiones Clave

- Runtime templates como imágenes Docker mantenidas, no Dockerfiles ad-hoc por proyecto.
- Compose efímero por tarea por defecto (más limpio); Testcontainers opt-in con conciencia del trade-off de seguridad.
- Git worktrees nativos (no clones múltiples) para velocidad y eficiencia de objetos.
- Un PR por repo por plan (no PR único multi-repo).
- push_policy 'branch_only_pr_required' default: el sistema abre el PR, humano hace merge.

### Riesgos Identificados

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Catálogo de runtimes mal mantenido se queda obsoleto | Media | Alto | Job nocturno de update + CI que valida builds. Versionado semver. |
| Bug en git worktrees corrompe repo del proyecto | Baja | Crítico | Tests exhaustivos. Backup del bare repo antes de cada worktree de plan. |
| Cap de review-runtimes saturado bloquea equipos | Media | Medio | Notificaciones de cap alcanzado. Configuración generosa por defecto. |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Runtime Templates

#### `task_06_01` — Definición del schema de runtime template (docker_image, workspace_mount_path, dep_cache_mount, default_pre_install, default_resources, output_parsers, network_policy)

- [ ] **Título**: Definición del schema de runtime template (docker_image, workspace_mount_path, dep_cache_mount, default_pre_install, default_resources, output_parsers, network_policy)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_01_a
    description: "Definición del schema de runtime template (docker_image, workspace_mount_path, dep_cache_mount, default_pre_install, default_resources, output_parsers, network_policy)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_runtime_template_schema.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_02` — Dockerfiles + builds para los 14 runtime templates iniciales

- [ ] **Título**: Dockerfiles + builds para los 14 runtime templates iniciales
- **Tiempo estimado**: 20 h
- **Complejidad**: l
- **Rol sugerido**: devops
- **Dependencias**: `task_06_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_02_a
    description: "Dockerfiles + builds para los 14 runtime templates iniciales"
    check_type: automated
    runtime: generic-shell
    command: "for t in python-pytest node-jest node-vitest node-playwright php-phpunit php-pest go-test java-maven java-gradle ruby-rspec rust-cargo dotnet-test generic-shell generic-http; do docker build -t agent-runtime-${t}:v1 docker/agent-runtimes/${t}/ || exit 1; done"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_03` — Pipeline CI que builda y publica los runtime templates

- [ ] **Título**: Pipeline CI que builda y publica los runtime templates
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_06_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_03_a
    description: "Pipeline CI que builda y publica los runtime templates"
    check_type: automated
    runtime: generic-shell
    command: "actionlint .github/workflows/build-runtime-templates.yml"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Worker-test y Compose Efímero

#### `task_06_04` — Worker-test que lee tarea + criterios y agrupa por runtime

- [ ] **Título**: Worker-test que lee tarea + criterios y agrupa por runtime
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_04_a
    description: "Worker-test que lee tarea + criterios y agrupa por runtime"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_worker_test.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_05` — Lanzamiento de test-runtime con worktree montado y servicios auxiliares en mini-red Docker

- [ ] **Título**: Lanzamiento de test-runtime con worktree montado y servicios auxiliares en mini-red Docker
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + devops
- **Dependencias**: `task_06_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_05_a
    description: "Lanzamiento de test-runtime con worktree montado y servicios auxiliares en mini-red Docker"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_test_runtime_launch.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_06` — Servicios auxiliares: postgres-test, redis-test, parametrizables por proyecto

- [ ] **Título**: Servicios auxiliares: postgres-test, redis-test, parametrizables por proyecto
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_06_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_06_a
    description: "Servicios auxiliares: postgres-test, redis-test, parametrizables por proyecto"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_aux_services.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_07` — Testcontainers opt-in con DinD proxy controlado

- [ ] **Título**: Testcontainers opt-in con DinD proxy controlado
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_06_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_07_a
    description: "Testcontainers opt-in con DinD proxy controlado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_testcontainers_mode.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Caché de Dependencias

#### `task_06_08` — Cálculo de hash de lock files (package-lock.json, requirements.txt, composer.lock, etc.)

- [ ] **Título**: Cálculo de hash de lock files (package-lock.json, requirements.txt, composer.lock, etc.)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_08_a
    description: "Cálculo de hash de lock files (package-lock.json, requirements.txt, composer.lock, etc.)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_lock_hashing.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_09` — Persistencia de dep-cache en /data/.../dep-cache/{type}-{hash}/

- [ ] **Título**: Persistencia de dep-cache en /data/.../dep-cache/{type}-{hash}/
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_09_a
    description: "Persistencia de dep-cache en /data/.../dep-cache/{type}-{hash}/"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dep_cache_persist.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_10` — Montaje del dep-cache en test-runtime si existe (skip pre_install)

- [ ] **Título**: Montaje del dep-cache en test-runtime si existe (skip pre_install)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_10_a
    description: "Montaje del dep-cache en test-runtime si existe (skip pre_install)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dep_cache_mount.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_11` — TTL de 14 días sin uso → purga automática

- [ ] **Título**: TTL de 14 días sin uso → purga automática
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_06_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_11_a
    description: "TTL de 14 días sin uso → purga automática"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dep_cache_ttl.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_12` — Botón 'Invalidar caché' en UI del proyecto

- [ ] **Título**: Botón 'Invalidar caché' en UI del proyecto
- **Tiempo estimado**: 3 h
- **Complejidad**: xs
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_06_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_12_a
    description: "Botón 'Invalidar caché' en UI del proyecto"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/invalidate-dep-cache.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase D — TestReport Canónico

#### `task_06_13` — Schema TestReport canónico (status, summary, failures, logs_excerpt, artifacts)

- [ ] **Título**: Schema TestReport canónico (status, summary, failures, logs_excerpt, artifacts)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_13_a
    description: "Schema TestReport canónico (status, summary, failures, logs_excerpt, artifacts)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_testreport_schema.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_14` — Parsers para junit_xml, jest_json, playwright_json, surefire_xml, tap, trx

- [ ] **Título**: Parsers para junit_xml, jest_json, playwright_json, surefire_xml, tap, trx
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_14_a
    description: "Parsers para junit_xml, jest_json, playwright_json, surefire_xml, tap, trx"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_output_parsers.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_15` — Entrega del TestReport al agente revisor como input estructurado

- [ ] **Título**: Entrega del TestReport al agente revisor como input estructurado
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_06_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_15_a
    description: "Entrega del TestReport al agente revisor como input estructurado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_reviewer_with_testreport.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Git Worktrees y Bare Repos

#### `task_06_16` — Estructura /data/agent-platform/projects/{tenant}/{project}/repos/ con bare repos

- [ ] **Título**: Estructura /data/agent-platform/projects/{tenant}/{project}/repos/ con bare repos
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + devops
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_16_a
    description: "Estructura /data/agent-platform/projects/{tenant}/{project}/repos/ con bare repos"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_bare_repos.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_17` — Fetch periódico + webhook Git para detectar push externos

- [ ] **Título**: Fetch periódico + webhook Git para detectar push externos
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_17_a
    description: "Fetch periódico + webhook Git para detectar push externos"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_remote_sync.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_18` — Creación de worktree por tarea (git worktree add) en arranque

- [ ] **Título**: Creación de worktree por tarea (git worktree add) en arranque
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_18_a
    description: "Creación de worktree por tarea (git worktree add) en arranque"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_worktree_create.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_19` — Montaje del worktree (no jerarquía) en agent-runtime y test-runtime

- [ ] **Título**: Montaje del worktree (no jerarquía) en agent-runtime y test-runtime
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_06_18`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_19_a
    description: "Montaje del worktree (no jerarquía) en agent-runtime y test-runtime"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_worktree_mount.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_20` — Cleanup de worktrees a los 30 días sin actividad

- [ ] **Título**: Cleanup de worktrees a los 30 días sin actividad
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_06_19`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_20_a
    description: "Cleanup de worktrees a los 30 días sin actividad"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_worktree_cleanup.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase F — Integración Git con el Plan

#### `task_06_21` — Creación de rama plan/{plan_id_short}-{slug} al sincronizar plan al Kanban

- [ ] **Título**: Creación de rama plan/{plan_id_short}-{slug} al sincronizar plan al Kanban
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_21_a
    description: "Creación de rama plan/{plan_id_short}-{slug} al sincronizar plan al Kanban"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_branch_create.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_22` — Commits con trailers Plan-Id, Task-Id, Execution-Id, Generated-By

- [ ] **Título**: Commits con trailers Plan-Id, Task-Id, Execution-Id, Generated-By
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_21`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_22_a
    description: "Commits con trailers Plan-Id, Task-Id, Execution-Id, Generated-By"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_commit_trailers.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_23` — Push de la rama al remoto tras tests automáticos + revisión del agente

- [ ] **Título**: Push de la rama al remoto tras tests automáticos + revisión del agente
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_23_a
    description: "Push de la rama al remoto tras tests automáticos + revisión del agente"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_push_after_review.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_24` — Apertura automática de PR al finalizar plan (un PR por repo afectado)

- [ ] **Título**: Apertura automática de PR al finalizar plan (un PR por repo afectado)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_23`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_24_a
    description: "Apertura automática de PR al finalizar plan (un PR por repo afectado)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pr_creation.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_25` — Política push_policy aplicada al merge (manual humano vs automático tras CI)

- [ ] **Título**: Política push_policy aplicada al merge (manual humano vs automático tras CI)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_24`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_25_a
    description: "Política push_policy aplicada al merge (manual humano vs automático tras CI)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_push_policy.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase G — Contenedor Review-Runtime

#### `task_06_26` — Composición del review-runtime: worktree con commits del plan + servicios auxiliares persistentes + servicio principal levantado

- [ ] **Título**: Composición del review-runtime: worktree con commits del plan + servicios auxiliares persistentes + servicio principal levantado
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: backend-dev + devops
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_26_a
    description: "Composición del review-runtime: worktree con commits del plan + servicios auxiliares persistentes + servicio principal levantado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_runtime_compose.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_27` — URL temporal firmada con caducidad = timeout de la revisión

- [ ] **Título**: URL temporal firmada con caducidad = timeout de la revisión
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_06_26`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_27_a
    description: "URL temporal firmada con caducidad = timeout de la revisión"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_url.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_28` — Terminal web embebida (ttyd o xterm.js) scoped a /workspace del contenedor

- [ ] **Título**: Terminal web embebida (ttyd o xterm.js) scoped a /workspace del contenedor
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + devops
- **Dependencias**: `task_06_27`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_28_a
    description: "Terminal web embebida (ttyd o xterm.js) scoped a /workspace del contenedor"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/review-terminal.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_29` — Logs en tiempo real vía WebSocket

- [ ] **Título**: Logs en tiempo real vía WebSocket
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_28`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_29_a
    description: "Logs en tiempo real vía WebSocket"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/review-logs.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_30` — Botón 're-ejecutar tests automáticos' que invoca worker-test sobre todo el plan

- [ ] **Título**: Botón 're-ejecutar tests automáticos' que invoca worker-test sobre todo el plan
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_06_29`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_30_a
    description: "Botón 're-ejecutar tests automáticos' que invoca worker-test sobre todo el plan"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/review-rerun-tests.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_31` — Vista de checklist con los tests humanos definidos en cabecera del plan

- [ ] **Título**: Vista de checklist con los tests humanos definidos en cabecera del plan
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_06_30`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_31_a
    description: "Vista de checklist con los tests humanos definidos en cabecera del plan"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/review-checklist.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_32` — Suspensión por inactividad: 4h sin actividad → docker pause

- [ ] **Título**: Suspensión por inactividad: 4h sin actividad → docker pause
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_31`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_32_a
    description: "Suspensión por inactividad: 4h sin actividad → docker pause"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_suspension.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_33` — Timeout 48h sin verdict → plan a blocked + notificación

- [ ] **Título**: Timeout 48h sin verdict → plan a blocked + notificación
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_32`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_33_a
    description: "Timeout 48h sin verdict → plan a blocked + notificación"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_timeout.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_34` — Cap por tenant configurable + cola cuando se llega al cap

- [ ] **Título**: Cap por tenant configurable + cola cuando se llega al cap
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_33`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_34_a
    description: "Cap por tenant configurable + cola cuando se llega al cap"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_cap.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase H — Doble Kanban Real y Cierre

#### `task_06_35` — Actualizar Kanban de Planes con progreso real (X/Y tareas done) y coste acumulado

- [ ] **Título**: Actualizar Kanban de Planes con progreso real (X/Y tareas done) y coste acumulado
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_06_35_a
    description: "Actualizar Kanban de Planes con progreso real (X/Y tareas done) y coste acumulado"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/kanban-plans-progress.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_36` — Transición automática plan → pending_human_validation al completar todas las tareas

- [ ] **Título**: Transición automática plan → pending_human_validation al completar todas las tareas
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_35`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_36_a
    description: "Transición automática plan → pending_human_validation al completar todas las tareas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_transition_to_review.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_37` — Transición automática plan → completed tras verdict humano approved + PR mergeado

- [ ] **Título**: Transición automática plan → completed tras verdict humano approved + PR mergeado
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_36`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_37_a
    description: "Transición automática plan → completed tras verdict humano approved + PR mergeado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_completion.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_38` — Documentación: ADRs, guías de runtime templates, runbook de review-runtime, changelog

- [ ] **Título**: Documentación: ADRs, guías de runtime templates, runbook de review-runtime, changelog
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_06_37`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_38_a
    description: "Documentación: ADRs, guías de runtime templates, runbook de review-runtime, changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/06-testing-revision-git.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_06_01
  description: "Ciclo completo end-to-end de un plan con repo Git"
  hint: "Ejecutar un plan real que toca un proyecto Laravel con tests phpunit"
  checklist:
    - "Al sincronizar, se crea rama plan/{id}-{slug} en el repo"
    - "Cada tarea hace su commit con trailers correctos"
    - "Los tests phpunit se ejecutan en test-runtime php-phpunit"
    - "TestReport canónico se entrega al agente revisor con failures parseados"
    - "Tras éxito de cada tarea, el commit se pushea al remoto"
    - "Al completar todas las tareas, plan pasa a pending_human_validation"
    - "review-runtime se levanta con Laravel servido + MySQL/Postgres efímero"
    - "Humano accede vía URL temporal y prueba la app"
    - "Humano marca tests humanos del plan como pass"
    - "Plan pasa a completed, sistema abre PR contra main"

- id: human_06_02
  description: "Caché de dependencias funciona"
  hint: "Ejecutar el mismo proyecto dos veces seguidas"
  checklist:
    - "La primera vez, npm ci / composer install / pip install tarda lo normal"
    - "La segunda vez, el dep-cache se monta y los tests arrancan en segundos"
    - "Cambiar el lock file invalida el caché y reinstala"

- id: human_06_03
  description: "Aislamiento de servicios auxiliares"
  hint: "Dos tareas paralelas usando el mismo proyecto Postgres"
  checklist:
    - "Cada tarea recibe su propio postgres-test efímero"
    - "Una tarea no ve datos de la otra"
    - "Al terminar cada tarea, los servicios se destruyen sin dejar rastro"

- id: human_06_04
  description: "Validación humana del plan funciona"
  hint: "Plan con política Producción + tests humanos definidos en cabecera"
  checklist:
    - "review-runtime se levanta y URL accesible al revisor"
    - "Terminal web permite hacer comandos dentro del contenedor"
    - "Re-ejecutar tests funciona desde el botón"
    - "Tras checklist completo, verdict approved abre PR"
    - "Si rejected, contenedor sigue 4h y plan vuelve a in_progress"

- id: human_06_05
  description: "Múltiples repos por plan"
  hint: "Plan que toca backend (repo A) + frontend (repo B)"
  checklist:
    - "Se crea rama plan/... en AMBOS repos"
    - "Los commits van al repo correcto según la tarea"
    - "Al completar, se abren DOS PRs (uno por repo)"
    - "El plan no pasa a completed hasta que ambos PRs están en estado válido"

- id: human_06_06
  description: "Git worktrees no corrompen el bare repo"
  hint: "Test de stress: 10 tareas paralelas sobre 3 repos distintos"
  checklist:
    - "Los worktrees se crean y destruyen sin errores"
    - "git fsck en cada bare repo al final pasa sin warnings"
    - "Ningún worktree huérfano queda tras los 30 días"

```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Generada entrada en `/docs/07-changelog/{plan_id}.md`.
6. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el siguiente es **Plan 07** (`07-documentacion-visor.md`).
