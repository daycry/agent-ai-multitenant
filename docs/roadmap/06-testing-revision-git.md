---
plan_id: 06-testing-revision-git
title: Testing Heterogéneo, Revisión y Ciclo Git del Plan
status: pending_human_validation
blocking_plan: [03-chat-planning-aprobacion, 05-mcp-tools-avanzadas]
started_at: 2026-05-27
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 85-105
estimated_cost_human_eur: 34.000 € – 42.000 €
estimated_cost_ai_eur: 200 € – 320 €
created_by: system_architect
spec_sections_referenced: [12.4, 12.5, 12.6, 14, 31.7, 31.12]
docs_language: es
---

# Plan 06 — Testing Heterogéneo, Revisión y Ciclo Git del Plan

## Cabecera

| Campo                              | Valor                                                   |
| ---------------------------------- | ------------------------------------------------------- |
| **ID del Plan**                    | `06-testing-revision-git`                               |
| **Estado**                         | `pending_approval`                                      |
| **Bloqueado por**                  | `03-chat-planning-aprobacion`, `05-mcp-tools-avanzadas` |
| **Tiempo estimado (calendario)**   | 4-5 semanas                                             |
| **Tiempo estimado (persona-días)** | 85-105                                                  |
| **Previsión de coste — humano**    | 34.000 € – 42.000 € (tarifa media 50 €/h)               |
| **Previsión de coste — IA**        | 200 € – 320 €                                           |
| **Aprobador propuesto**            | System Admin                                            |
| **Rama git**                       | `plan/06-testing-revision-git`                          |
| **Secciones del .docx**            | [12.4, 12.5, 12.6, 14, 31.7, 31.12]                     |

---

## Descripción Detallada

### Resumen Ejecutivo

Catálogo de runtime templates (Python/Node/PHP/Go/Java/Ruby/Rust/.NET) como imágenes Docker, contenedores test-runtime efímeros con servicios auxiliares, TestReport canónico al agente revisor, contenedor review-runtime persistente para validación humana del plan, pool elástico de runtime de propósito general por plan (sección 12.5 del .docx), integración Git completa con worktrees, flujo de cuatro transiciones de código con dos ejes de política (sección 12.6 del .docx), y PR automático al finalizar plan.

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
- Pool elástico de runtime de propósito general por plan: min/max/idle_ttl_seconds, lógica del orchestrator (libre / nuevo / cola), cambio de rol dentro del contenedor sin reiniciar proceso, limpieza entre pasos (sección 12.5 del .docx).
- Rama git por plan, commits con trailers, flujo de cuatro transiciones de código worktree → bare repo local → remoto rama del plan → rama default (sección 12.6 del .docx).
- Sincronización entre worktrees hermanos del plan: fetch + reset --hard al HEAD vigente de la rama antes de cada tarea; manejo de merges entre tareas paralelas con casos A/B/C.
- Dos ejes de política `branch_push_mode` (incremental / final_only) y `plan_validation_mode` (human_required / auto_approve), ortogonales a `push_policy`.
- PR automático al cierre del plan según la combinación de las tres políticas.
- Múltiples repos por plan = múltiples PRs.

**Queda fuera (otras fases)**:

- Documentación canónica /docs y visor (Fase 7).
- Evals continuos sobre el TestReport (Fase 14).

### Decisiones Clave

- Runtime templates como imágenes Docker mantenidas, no Dockerfiles ad-hoc por proyecto.
- Compose efímero por tarea por defecto (más limpio); Testcontainers opt-in con conciencia del trade-off de seguridad.
- Git worktrees nativos (no clones múltiples) para velocidad y eficiencia de objetos.
- Un PR por repo por plan (no PR único multi-repo).
- push_policy 'branch_only_pr_required' default: el sistema abre el PR, humano hace merge.
- Defaults razonables del flujo Git: branch_push_mode=incremental + plan_validation_mode=human_required + push_policy=branch_only_pr_required (sección 12.6.8 del .docx).
- Pool elástico de runtime de propósito general por plan, con defaults min=1 / max=5 / idle_ttl_seconds=300 (sección 12.5 del .docx). El contenedor del pool se reutiliza para distintos roles (implementador, Reviewer automático, Memorizer, Technical Writer, resumen del plan) sin reiniciar proceso Python ni conexiones HTTP.

### Riesgos Identificados

| Riesgo                                               | Probabilidad | Impacto | Mitigación                                                              |
| ---------------------------------------------------- | ------------ | ------- | ----------------------------------------------------------------------- |
| Catálogo de runtimes mal mantenido se queda obsoleto | Media        | Alto    | Job nocturno de update + CI que valida builds. Versionado semver.       |
| Bug en git worktrees corrompe repo del proyecto      | Baja         | Crítico | Tests exhaustivos. Backup del bare repo antes de cada worktree de plan. |
| Cap de review-runtimes saturado bloquea equipos      | Media        | Medio   | Notificaciones de cap alcanzado. Configuración generosa por defecto.    |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Runtime Templates

#### `task_06_01` — Definición del schema de runtime template (docker_image, workspace_mount_path, dep_cache_mount, default_pre_install, default_resources, output_parsers, network_policy)

- [x] **Título**: Definición del schema de runtime template (docker_image, workspace_mount_path, dep_cache_mount, default_pre_install, default_resources, output_parsers, network_policy)
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

- [x] **Título**: Dockerfiles + builds para los 14 runtime templates iniciales
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

- [x] **Título**: Pipeline CI que builda y publica los runtime templates
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

- [x] **Título**: Worker-test que lee tarea + criterios y agrupa por runtime
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

- [x] **Título**: Lanzamiento de test-runtime con worktree montado y servicios auxiliares en mini-red Docker
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

- [x] **Título**: Servicios auxiliares: postgres-test, redis-test, parametrizables por proyecto
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

- [x] **Título**: Testcontainers opt-in con DinD proxy controlado
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

- [x] **Título**: Cálculo de hash de lock files (package-lock.json, requirements.txt, composer.lock, etc.)
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

- [x] **Título**: Persistencia de dep-cache en /data/.../dep-cache/{type}-{hash}/
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

- [x] **Título**: Montaje del dep-cache en test-runtime si existe (skip pre_install)
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

- [x] **Título**: TTL de 14 días sin uso → purga automática
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

- [x] **Título**: Botón 'Invalidar caché' en UI del proyecto
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

- [x] **Título**: Schema TestReport canónico (status, summary, failures, logs_excerpt, artifacts)
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

- [x] **Título**: Parsers para junit_xml, jest_json, playwright_json, surefire_xml, tap, trx
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

- [x] **Título**: Entrega del TestReport al agente revisor como input estructurado
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

- [x] **Título**: Estructura /data/agent-platform/projects/{tenant}/{project}/repos/ con bare repos
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

- [x] **Título**: Fetch periódico + webhook Git para detectar push externos
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

- [x] **Título**: Creación de worktree por tarea (git worktree add) en arranque
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

#### `task_06_19` — Montaje del worktree (no jerarquía) en agent-runtime y test-runtime, con sync `fetch + reset --hard` antes de pasar control al agente

- [x] **Título**: Montaje del worktree (no jerarquía) en agent-runtime y test-runtime, con sync `fetch + reset --hard` antes de pasar control al agente
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_06_18`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_19_a
    description: "Montaje del worktree (no jerarquía) en agent-runtime y test-runtime, con sync al HEAD vigente de la rama del plan antes de arrancar el agente (sección 12.6.4 del .docx)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_worktree_mount.py tests/integration/test_worktree_sync.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_20` — Cleanup de worktrees a los 30 días sin actividad

- [x] **Título**: Cleanup de worktrees a los 30 días sin actividad
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

### Fase E2 — Pool Elástico de Runtime por Plan (sección 12.5 del .docx)

#### `task_06_20b1` — Modelo del pool elástico por plan con parámetros `min` / `max` / `idle_ttl_seconds`

- [x] **Título**: Modelo del pool elástico por plan: estructura de datos en orquestador, parámetros `min` (default 1) / `max` (default 5) / `idle_ttl_seconds` (default 300) heredados desde proyecto, con límite duro de plataforma `max_runtime_pool_size_per_tenant` (default 20)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_20`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_20b1_a
    description: "Pool por plan se crea con min contenedores al iniciar el plan y respeta max"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_runtime_pool_model.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_20b2` — Lógica del orchestrator: asignación de contenedores del pool (libre / nuevo / cola) y barrido de idle

- [x] **Título**: Lógica del orchestrator para asignar contenedores del pool: si hay libre lo toma, si no y `size < max` crea uno, si el pool está al máximo encola el paso. Barrido de contenedores idle cada 30s y eviction de los que llevan más de `idle_ttl_seconds` por encima de `min`.
- **Tiempo estimado**: 12 h
- **Complejidad**: l
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_20b1`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_20b2_a
    description: "Asignación a contenedor libre del pool funciona y reutiliza"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pool_assign.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_20b2_b
    description: "Pool al máximo encola pasos hasta que se libera un contenedor"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pool_queue.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_20b2_c
    description: "Eviction de contenedores idle por encima de min tras idle_ttl_seconds"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pool_idle_eviction.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_20b3` — Cambio de rol dentro de un mismo contenedor del pool sin reiniciar proceso Python

- [x] **Título**: Cambio de rol dentro de un mismo contenedor del pool: nuevo system_prompt + nuevo set de tools + nuevo contexto sin reiniciar proceso Python, sin reiniciar conexiones HTTP de los proveedores LLM (`shared-llm`, ADR 0021), sin recargar tokenizer. El contenedor pasa de Backend Senior a Reviewer a Memorizer manteniendo caliente proceso, conexiones, cliente MCP y tokenizer.
- **Tiempo estimado**: 10 h
- **Complejidad**: l
- **Rol sugerido**: ai-engineer + backend-dev
- **Dependencias**: `task_06_20b2`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_20b3_a
    description: "Un mismo contenedor del pool ejecuta sucesivamente implementador → reviewer → memorizer sin reiniciar proceso"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pool_role_switch.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_20b4` — Limpieza entre pasos: desmontar worktree, limpiar /tmp, unset vars, matar hijos huérfanos, reiniciar handlers de señales

- [x] **Título**: Rutina de limpieza al devolver un contenedor al pool: desmontar worktree del paso anterior (no borrarlo del FS), limpiar `/tmp`, unset de variables TASK_ID/EXECUTION_ID/secrets, matar procesos hijos huérfanos, reiniciar handlers de señales. Mantener caliente proceso Python, conexiones HTTP de los proveedores LLM (`shared-llm`, ADR 0021), cliente MCP, tokenizer, dep-cache.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_06_20b3`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_20b4_a
    description: "Tras soltar un contenedor al pool, /tmp está vacío, no quedan procesos huérfanos, las vars específicas del paso anterior no están presentes"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pool_cleanup.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_20b5` — Métricas Prometheus del pool: size / busy / idle / wait_seconds / evictions_total / role_executions_total

- [x] **Título**: Exposición de métricas Prometheus por plan: `runtime_pool_size{plan_id, project_id}`, `runtime_pool_busy{plan_id}`, `runtime_pool_idle{plan_id}`, `runtime_pool_wait_seconds{plan_id}`, `runtime_pool_evictions_total{plan_id, reason}`, `runtime_pool_role_executions_total{plan_id, role}`.
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev + devops
- **Dependencias**: `task_06_20b4`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_20b5_a
    description: "Las seis métricas del pool se exportan correctamente y reflejan la realidad"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_pool_metrics.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_20b6` — Migración desde el modelo simple de Fase 2 (un contenedor por tarea) al pool elástico por plan

- [x] **Título**: Sustituir el lanzamiento "un contenedor por tarea" de Fase 2 (`task_02_06`) por la lógica de solicitud al pool. El worker deja de lanzar contenedores directamente; pide al orchestrator un slot del pool del plan.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_20b5`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_20b6_a
    description: "El worker ya no lanza contenedores directamente; recibe un slot del pool y le monta el worktree"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_worker_uses_pool.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase F — Integración Git con el Plan

#### `task_06_21` — Creación de rama plan/{plan_id_short}-{slug} al sincronizar plan al Kanban

- [x] **Título**: Creación de rama plan/{plan_id_short}-{slug} al sincronizar plan al Kanban
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

- [x] **Título**: Commits con trailers Plan-Id, Task-Id, Execution-Id, Generated-By
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

#### `task_06_23` — Flujo de cuatro transiciones de código con los dos ejes `branch_push_mode` y `plan_validation_mode`

- [x] **Título**: Flujo de cuatro transiciones de código (worktree → bare repo local → remoto rama del plan → rama default) con los dos ejes `branch_push_mode` (incremental/final_only) y `plan_validation_mode` (human_required/auto_approve), ortogonales a `push_policy` (sección 12.6 del .docx)
- **Tiempo estimado**: 14 h
- **Complejidad**: l
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_23_a
    description: "Push worktree → bare repo local tras aprobación de revisión automática"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_push_worktree_to_bare.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_23_b
    description: "branch_push_mode=incremental pushea cada tarea aprobada al remoto"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_branch_push_incremental.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_23_c
    description: "branch_push_mode=final_only difiere el push al remoto al cierre del plan"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_branch_push_final_only.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_23_d
    description: "plan_validation_mode=auto_approve cierra el plan sin paso humano si todas las tareas pasaron revisión automática"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_validation_auto_approve.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_24` — Apertura automática de PR al finalizar plan (un PR por repo afectado)

- [x] **Título**: Apertura automática de PR al finalizar plan (un PR por repo afectado)
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

#### `task_06_25` — Política `push_policy` aplicada al merge, ortogonal a `branch_push_mode` y `plan_validation_mode`

- [x] **Título**: Política `push_policy` aplicada al merge (forbidden / branch_only_pr_required / direct_to_default_allowed), ortogonal a `branch_push_mode` y `plan_validation_mode` (las tres juntas definen el comportamiento Git completo del proyecto — sección 12.6 del .docx)
- **Tiempo estimado**: 8 h
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
  - id: auto_06_25_b
    description: "Matriz combinatoria de las tres políticas (branch_push_mode × plan_validation_mode × push_policy) produce el comportamiento esperado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_git_policies_matrix.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase G — Contenedor Review-Runtime

#### `task_06_26` — Composición del review-runtime: worktree con commits del plan + servicios auxiliares persistentes + servicio principal levantado

- [x] **Título**: Composición del review-runtime: worktree con commits del plan + servicios auxiliares persistentes + servicio principal levantado
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

- [x] **Título**: URL temporal firmada con caducidad = timeout de la revisión
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

- [x] **Título**: Terminal web embebida (ttyd o xterm.js) scoped a /workspace del contenedor
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

- [x] **Título**: Logs en tiempo real vía WebSocket
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

- [x] **Título**: Botón 're-ejecutar tests automáticos' que invoca worker-test sobre todo el plan
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

- [x] **Título**: Vista de checklist con los tests humanos definidos en cabecera del plan
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

- [x] **Título**: Suspensión por inactividad: 4h sin actividad → docker pause
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

- [x] **Título**: Timeout 48h sin verdict → plan a blocked + notificación
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

- [x] **Título**: Cap por tenant configurable + cola cuando se llega al cap
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

### Fase G2 — Flujo de Rechazo de Revisión y Tareas Plan-Scoped (secciones 6.5.3, 7.2, 7.9, 7.10 del .docx)

#### `task_06_34b1` — Rechazo de revisión automática envía la tarea a `backlog` con comentario estructurado del revisor

- [x] **Título**: Cambiar la transición `in_review → in_progress` por `in_review → backlog` cuando el revisor automático rechaza la tarea. El comentario estructurado del revisor (criterio fallido, evidencia del TestReport, qué arreglar) se adjunta a la tarea como `task_comment` y queda visible en la siguiente recogida. `retry_count++`.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_34`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_34b1_a
    description: "Rechazo del revisor automático envía la tarea a backlog con comentario estructurado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_rejection_to_backlog.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_34b1_b
    description: "El revisor automático NO crea tareas nuevas; solo añade comentario y devuelve la tarea a backlog"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_no_new_tasks.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_34b2` — Escalado a `awaiting_human` tras `retry_count >= max_review_retries`

- [x] **Título**: Cuando `retry_count >= max_review_retries` (default 3, límite global de plataforma), la tarea NO vuelve a backlog. Pasa a `awaiting_human`. Se envía notificación al humano por los canales habilitados con histórico completo de intentos y comentarios.
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_34b1`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_34b2_a
    description: "Tras max_review_retries rechazos, la tarea transiciona a awaiting_human y se dispara notificación"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_review_escalation.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_34b3` — Panel "Tareas Escaladas" en la UI con las cuatro acciones humanas

- [x] **Título**: Panel de "Tareas Escaladas" en la UI del plan que muestra todo el histórico de intentos, outputs y comentarios. Cuatro botones de acción: aprobar manualmente (`→ done` con `manual_approval=true`), reasignar con nueva guía (`→ backlog`, `retry_count=0`, instrucciones humanas inyectadas como contexto), bloquear por causa externa (`→ blocked` con razón), cancelar (`→ cancelled`).
- **Tiempo estimado**: 12 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_06_34b2`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_34b3_a
    description: "Las cuatro acciones del panel de Tareas Escaladas funcionan y registran en audit_log"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/escalated-tasks-panel.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_34b4` — Creación automática de tareas desde checkboxes humanos fallidos en validación del plan

- [x] **Título**: Cuando el humano marca como fail un checkbox de tests humanos durante la validación del plan, el sistema crea automáticamente una tarea nueva en el plan: `title` = texto del checkbox, `description` = comentario humano, `plan_id` = plan en revisión, estado `backlog`. El plan vuelve a `in_progress`. Se recalcula el DAG y se ajusta el coste estimado del plan.
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_34b3`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_34b4_a
    description: "Por cada checkbox fail se crea una tarea nueva en el plan con título y descripción correctos"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_checkbox_to_task.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_34b4_b
    description: "Las tareas nuevas son plan-scoped (plan_id != null) y se ven en el Kanban filtrado del plan"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_checkbox_task_scope.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_34b4_c
    description: "El plan no puede pasar a completed mientras las tareas generadas no estén done"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_completion_with_new_tasks.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_34b5` — Botón "Añadir tarea libre al plan" en review-runtime y panel de validación humana

- [x] **Título**: Botón opcional en la UI de validación humana que permite al humano crear una tarea no asociada a ningún checkbox (bug colateral, mejora, refactor observado). Misma semántica que las tareas generadas desde checkbox: plan-scoped, estado backlog, suma al coste estimado del plan.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_06_34b4`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_34b5_a
    description: "Botón Añadir tarea libre crea tarea plan-scoped en backlog"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/add-free-task.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_06_34b6` — Registro auditable completo de la tarea (sección 13.6 del .docx)

- [x] **Título**: Garantizar que la tarea es el punto de agregación único de su histórico: todas sus Executions, todas sus Reviews, todos los comentarios (agente y humano), todas las transiciones de estado, las decisiones humanas si pasó por awaiting_human, y el outcome final. Endpoint `GET /api/v1/tasks/{task_id}/history` que devuelve todo en JSON cronológico. Validación en el Servicio de Dominio que impide cerrar una tarea sin el registro completo.
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_06_34b5`
- **Tests automáticos**:
  ```yaml
  - id: auto_06_34b6_a
    description: "GET /api/v1/tasks/{id}/history devuelve histórico completo en orden cronológico"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_task_history_endpoint.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_34b6_b
    description: "Una tarea con N reintentos expone las N executions, las N reviews y los N comentarios desde su histórico"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_task_history_aggregation.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_34b6_c
    description: "El sistema rechaza con 422 cerrar una tarea (transición a done) si faltan campos obligatorios del registro auditable"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_task_close_audit_enforcement.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_06_34b6_d
    description: "El registro es append-only: intentar modificar un comentario o transición anterior se rechaza"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_task_history_append_only.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase H — Doble Kanban Real y Cierre

#### `task_06_35` — Actualizar Kanban de Planes con progreso real (X/Y tareas done) y coste acumulado

- [x] **Título**: Actualizar Kanban de Planes con progreso real (X/Y tareas done) y coste acumulado
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

- [x] **Título**: Transición automática plan → pending_human_validation al completar todas las tareas
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

- [x] **Título**: Transición automática plan → completed tras verdict humano approved + PR mergeado
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

- [x] **Título**: Documentación: ADRs, guías de runtime templates, runbook de review-runtime, changelog
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
  description: "Ciclo completo end-to-end de un plan con repo Git (defaults: branch_push_mode=incremental + plan_validation_mode=human_required + push_policy=branch_only_pr_required)"
  hint: "Ejecutar un plan real que toca un proyecto Laravel con tests phpunit"
  checklist:
    - "Al sincronizar, se crea rama plan/{id}-{slug} en el repo"
    - "Cada tarea hace su commit con trailers correctos"
    - "Los tests phpunit se ejecutan en test-runtime php-phpunit"
    - "TestReport canónico se entrega al agente revisor con failures parseados"
    - "Tras aprobación de revisión automática de cada tarea, el commit se pushea al bare repo local"
    - "Con branch_push_mode=incremental, cada tarea aprobada se pushea también al remoto y el PR del plan se actualiza en vivo"
    - "Antes de arrancar cada tarea posterior, el sistema hace fetch + reset --hard al HEAD vigente de la rama (la tarea ve el trabajo de las anteriores)"
    - "Al completar todas las tareas, plan pasa a pending_human_validation"
    - "review-runtime se levanta con Laravel servido + MySQL/Postgres efímero, montando un worktree con TODO el plan integrado"
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

- id: human_06_07
  description: "Pool elástico de runtime por plan se comporta como se espera (sección 12.5 del .docx)"
  hint: "Plan grande con DAG ancho ejecutado completo"
  checklist:
    - "Al iniciar el plan el pool arranca con min contenedores (default 1)"
    - "Al ejecutarse pasos paralelos, el pool crece hasta max contenedores"
    - "Cuando un paso de implementación termina y empieza la revisión automática, se reutiliza el mismo contenedor (cambio de rol sin reiniciar proceso Python)"
    - "Tras periodos de inactividad superiores a idle_ttl_seconds, contenedores por encima de min se destruyen"
    - "Las métricas runtime_pool_* exportadas a Prometheus reflejan el comportamiento observado"
    - "Al cerrar el plan, todos los contenedores del pool se destruyen limpiamente"

- id: human_06_08
  description: "Las cuatro combinaciones de branch_push_mode × plan_validation_mode funcionan (sección 12.6.7 del .docx)"
  hint: "Crear 4 proyectos sandbox con cada combinación posible y ejecutar un plan simple en cada uno"
  checklist:
    - "incremental + human_required (default): rama del plan visible en remoto desde la primera tarea, humano valida al final"
    - "incremental + auto_approve: rama visible en vivo, plan se cierra y abre PR sin paso humano cuando todas las tareas pasan revisión automática"
    - "final_only + human_required: rama no aparece en remoto hasta el cierre del plan, humano valida"
    - "final_only + auto_approve: rama aparece de golpe en remoto al cierre, sin paso humano"
    - "push_policy aplica correctamente en cada caso (PR abierto vs auto-mergeado)"

- id: human_06_09
  description: "Merge entre tareas paralelas con conflicto real (sección 12.6.5 del .docx)"
  hint: "Diseñar un plan con dos tareas paralelas que tocan deliberadamente la misma línea del mismo archivo"
  checklist:
    - "La primera tarea pushea limpio al bare repo"
    - "La segunda tarea recibe conflicto al pushear"
    - "El sistema aplica la política configurada (C1 = vuelve a backlog con feedback / C2 = plan a blocked)"
    - "Si C1: el agente recibe el feedback de conflicto estructurado y reintenta tras resolución"

- id: human_06_10
  description: "Escalado a humano tras max_review_retries rechazos (sección 7.9 del .docx)"
  hint: "Forzar una tarea que el implementador no consigue resolver (ej. acceptance_criteria contradictorios) y dejarla fallar 3 veces"
  checklist:
    - "Tras cada rechazo del revisor automático, la tarea vuelve a backlog con comentario estructurado adjunto"
    - "El retry_count se incrementa correctamente en cada rechazo"
    - "Tras max_review_retries (3), la tarea transiciona a awaiting_human"
    - "Llega notificación al humano por el asistente personal con histórico completo"
    - "El panel de Tareas Escaladas en la UI muestra los 3 intentos con sus outputs y comentarios"
    - "Las cuatro acciones humanas funcionan: aprobar manual, reasignar con guía (retry_count=0), bloquear con razón, cancelar"
    - "Cada acción queda en audit_log con timestamp, usuario, justificación"

- id: human_06_11
  description: "Validación humana del plan: checkbox fail genera tarea nueva (sección 7.10 del .docx)"
  hint: "Plan en pending_human_validation con varios checkboxes; marcar uno como fail con comentario"
  checklist:
    - "Por cada checkbox marcado fail se crea automáticamente una tarea nueva en el plan"
    - "El título de la tarea es el texto del checkbox; la descripción es el comentario humano"
    - "Las tareas nuevas son plan-scoped (plan_id correcto) y aparecen en el Kanban filtrado del plan"
    - "El plan vuelve a in_progress y las tareas nuevas a backlog"
    - "El coste estimado del plan se ajusta y la diferencia es visible al humano"
    - "Tras completar las tareas nuevas, el plan vuelve a pending_human_validation y se puede revalidar"
    - "El botón 'Añadir tarea libre al plan' permite crear tareas no asociadas a checkboxes y también son plan-scoped"

- id: human_06_12
  description: "Registro auditable completo de la tarea (sección 13.6 del .docx)"
  hint: "Abrir una tarea que haya pasado por al menos 2 rechazos del revisor y luego haya sido aprobada"
  checklist:
    - "La vista de detalle muestra una línea de tiempo con todas las executions, reviews y comentarios en orden cronológico"
    - "Cada entrada es expandible y muestra el detalle completo (steps_log, tool_calls, feedback_text, etc.)"
    - "El histórico incluye las transiciones de estado con el actor que las causó"
    - "El outcome final (commit hash si aplica, manual_approval=true si aplica) está visible en la cabecera de la tarea"
    - "Exportar la tarea como bundle JSON produce un fichero con todo el registro auditable"
    - "La API GET /api/v1/tasks/{id}/history devuelve la misma información que ve la UI"
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
