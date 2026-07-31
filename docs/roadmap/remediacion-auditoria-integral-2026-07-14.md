---
plan_id: remediacion-auditoria-integral-2026-07-14
title: Remediación delta de auditoría integral — seguridad, contratos y rendimiento
status: pending_approval
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 13
estimated_cost_human_eur: 5.850 € – 7.800 €
estimated_cost_ai_eur: 40 € – 90 €
created_by: github-copilot-audit-2026-07-14
spec_sections_referenced: []
docs_language: es
priority: P0
source_audit: auditoria-integral-2026-07-14
---

# Plan de remediación delta — Auditoría integral 2026-07-14

## Cabecera

| Campo                 | Valor                                              |
| --------------------- | -------------------------------------------------- |
| **ID del Plan**       | `remediacion-auditoria-integral-2026-07-14`        |
| **Prioridad**         | P0 para restaurar CI; P1/P2 para el resto          |
| **Bloqueado por**     | Ninguno; coordina con prod-02/07/08/09/13/14/15/16 |
| **Duración estimada** | 2-3 semanas                                        |
| **Esfuerzo estimado** | 13 persona-días                                    |
| **Rama sugerida**     | `plan/remediacion-auditoria-integral-2026-07-14`   |

## Resumen

Este plan implementa únicamente el delta confirmado por la auditoría del 14 de
julio. No duplica los planes de producción existentes: restaura los cuatro gates
de seguridad hoy rojos, resuelve dos contratos arquitectónicos incoherentes
(córtex/RLS y modelo de embeddings), consolida el acceso a BD de workers y añade
límites operativos para WebSocket/readiness.

El estado es `pending_approval`. No se activa ninguna fase ni se cambia el estado
de los cuatro documentos actualmente `in_progress`.

## Alcance

**Entra**:

- Hardening de `textfile-init` y meta-tests conscientes de overlays Compose.
- ADR y aplicación de la decisión sobre `cortex_conversations.tenant_id`/RLS.
- ADR y aplicación del contrato real de embeddings de KB.
- Factoría única de engines/sesiones Celery con `NullPool`.
- Deadline/backpressure en WebSockets.
- Separación `/healthz` (liveness) y `/readyz` (readiness).
- Alineación mínima del toolchain frontend y warnings actuales de hooks.
- Tests automáticos y documentación de referencia afectados.

**Queda fuera**:

- Hardening global `/admin/*`, tickets WS/cookies/headers (`prod-09`).
- Junctions tenant sin RLS (`prod-14`).
- Exporters y alertas Prometheus (`prod-08`).
- Guardrails completos (`prod-03`/ADR 0102).
- Campaña de validación humana y normalización global del roadmap (`prod-15`).
- Subida completa de cobertura a 70/80 (`prod-02`, por ratchet).
- Refactor i18n/general del frontend (`prod-16`).

## Decisiones clave

1. **No silenciar gates con excepciones implícitas.** Los overlays se evalúan como
   se despliegan y las excepciones de seguridad se documentan por clase de servicio.
2. **Córtex requiere ADR.** Opciones: (A) tenant-scoped con RLS real; (B)
   owner-scoped con política estructural propia y renombrado/eliminación del falso
   `tenant_id`; (C) excepción explícita al principio 1. Recomendación: B si el
   producto confirma que el córtex es singleton del System Owner; C no aporta
   defensa y debe ser la última opción.
3. **Embeddings requieren un contrato único.** Opciones: (A) un único modelo y
   dimensión de plataforma, retirando el selector por KB; (B) registro por modelo,
   dimensión compatible, reindexado y búsqueda agrupada. Recomendación: A para el
   alcance single-host actual; B solo si existe un caso real multi-modelo.
4. **Celery usa conexiones cortas.** Una factoría común con `NullPool` evita pools
   efímeros y concentra settings/observabilidad. Los tests pueden inyectar
   sessionmakers sin red.
5. **Liveness no es readiness.** `/healthz` no depende de servicios externos;
   `/readyz` prueba solo dependencias necesarias para aceptar tráfico, con timeout.

## Tareas

### Fase A — Restaurar el gate de seguridad

#### `task_audit14_01` — Compose monitoring endurecido y tests overlay-aware

- [x] **Título**: Corregir `textfile-init` y evaluar `workers` tras fusionar base+overlay
- **Descripción**: Añadir a `textfile-init` el baseline compatible con BusyBox
  (`no-new-privileges`, `cap_drop: [ALL]`, AppArmor validado). Refactorizar helpers
  de `tests/security` para renderizar `docker-compose.yml` +
  `docker-compose.monitoring.yml` como despliegue real; conservar asserts separados
  para servicios definidos solo en el overlay y documentar one-shot/host-agent.
- ℹ️ **Comando `auto_audit14_01_b` obsoleto (verificado 2026-07-31):**
  `docker compose -f docker-compose.yml -f docker-compose.monitoring.yml config`
  sale con código 1 («service "workers" has neither an image nor a build
  context»), y no por esta tarea: el compose base ya solo trae infraestructura —
  los servicios de aplicación los genera el instalador y viven en
  `docker-compose.manuals.yml`. El render del despliegue real, añadiendo ese
  tercer fichero, sale 0.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_01_a
    runtime: python-pytest
    command: "pytest tests/security/test_apparmor.py tests/security/test_seccomp_profiles.py tests/security/test_pentest_findings.py -v"
  - id: auto_audit14_01_b
    runtime: python-pytest
    command: "docker compose -f docker/docker-compose.yml -f docker/docker-compose.monitoring.yml config --quiet"
  ```

#### `task_audit14_02` — ADR de aislamiento owner/tenant del córtex

- [ ] **Título**: Decidir el contrato estructural de `cortex_conversations`
- **Descripción**: Redactar ADR `proposed` con las opciones A/B/C de la decisión 2,
  inventario de todas las tablas córtex, rutas BYPASSRLS y efecto sobre login,
  memoria, backups y tests cross-owner. La decisión es humana; no modificar el
  meta-test para hacerlo verde antes de ratificarla.
- ⏳ **Pendiente (2026-07-31):** el ADR no existe — ningún fichero de
  `docs/05-architecture-decisions/` menciona `cortex_conversations`, así que la
  opción A/B/C sigue sin plantearse ni ratificarse.
- **Tiempo**: 0,5 días · **Complejidad**: m
- **Tests automáticos**: lint/frontmatter de ADR; gate humano para la decisión.

#### `task_audit14_03` — Aplicar aislamiento del córtex ratificado

- [ ] **Título**: Migración reversible, repositorios y meta-test coherentes con el ADR
- **Descripción**: Implementar la opción ratificada sin perder el filtro
  `owner_user_id`; añadir defensa estructural, actualizar modelos/repositorios y
  hacer que el invariante de RLS exprese la regla elegida sin falsos negativos.
- ⏳ **Pendiente (2026-07-31):** la implementación parece entregada —migración
  `0125_cortex_conv_rls` (RLS + FORCE + política por `owner_user_id`) con
  `tests/integration/test_cortex_conversations_rls.py` y round-trip—, pero se
  hizo SIN el ADR de `task_audit14_02`: no se marca hasta que el operador
  ratifique cuál de las opciones A/B/C es la elegida y el ADR lo recoja.
- **Tiempo**: 2 días · **Complejidad**: l · **Depende de**: `task_audit14_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_03_a
    runtime: python-pytest
    command: "pytest tests/security/test_pentest_findings.py::test_every_tenant_owned_table_has_rls_enabled -v"
  - id: auto_audit14_03_b
    runtime: python-pytest
    command: "pytest tests/integration/test_cortex_threads_migration.py tests/integration/test_cortex_cross_owner.py -v -m cross_tenant"
  - id: auto_audit14_03_c
    runtime: python-pytest
    command: "pytest tests/migrations -v"
  ```

### Fase B — Contrato ejecutable de embeddings

#### `task_audit14_04` — ADR del modelo de embeddings de KB

- [ ] **Título**: Elegir modelo único de plataforma o multi-modelo real
- **Descripción**: Documentar las opciones de la decisión 3 con coste de migración,
  dimensión pgvector, query sobre varias KB, reindexado, rollback y UX. Medir el
  inventario real de modelos configurados antes de recomendar la migración final.
- ⏳ **Pendiente (2026-07-31):** el ADR no existe y la última sesión lo declaró
  explícitamente fuera de alcance («modelo de embeddings único vs multi-modelo»
  en el «NO ENTREGADO, Y A PROPÓSITO» del commit `4d1c3590`).
- **Tiempo**: 0,5 días · **Complejidad**: m
- **Tests automáticos**: lint/frontmatter de ADR; script read-only de inventario.

#### `task_audit14_05` — Implementar y probar el contrato de embeddings

- [ ] **Título**: La configuración mostrada por API/UI gobierna ingesta y retrieval
- **Descripción**: Para opción A, normalizar datos al modelo canónico y retirar el
  selector engañoso conservando una migración compatible; para B, resolver modelo
  antes de construir embedder, validar dimensión, agrupar query por modelo y añadir
  reindexado explícito. En ambos casos, error visible y métrica ante mismatch.
- ⏳ **Pendiente (2026-07-31):** bloqueada por `task_audit14_04` — sin ADR
  ratificado no hay opción que implementar, y
  `tests/integration/test_kb_embedding_model_contract.py` no existe.
- **Tiempo**: 2 días · **Complejidad**: l · **Depende de**: `task_audit14_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ingestion_worker.py tests/integration/test_embeddings.py tests/integration/test_vector_search.py -v"
  - id: auto_audit14_05_b
    runtime: python-pytest
    command: "pytest tests/integration/test_kb_embedding_model_contract.py -v"
  - id: auto_audit14_05_c
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run test -- app/admin/knowledge-bases/page.test.tsx"
  ```

### Fase C — Recursos y límites operativos

#### `task_audit14_06` — Factoría única de sesiones de worker con NullPool

- [x] **Título**: Sustituir las 36 creaciones directas de engine en workers
- **Descripción**: Crear módulo de infraestructura de BD para workers que devuelva
  engine/sessionmaker owner-aware, `poolclass=NullPool`, `pool_pre_ping` donde
  proceda y cierre garantizado. Migrar los 30 módulos por lotes pequeños sin cambiar
  semántica tenant. Prohibir imports directos de `create_async_engine` fuera del
  módulo y tests.
- ℹ️ **Desviación aceptada (2026-07-31):** la guarda de la prohibición vive en
  `tests/unit/test_worker_engines_nullpool.py` (hoy es un muro: `PENDING_MIGRATION`
  está vacía) y no en `tests/docs/test_worker_db_factory_contract.py`, que nunca
  se creó. `pool_pre_ping` se dejó DESACTIVADO a propósito: con `NullPool` cada
  checkout abre conexión nueva y el ping sería un `SELECT 1` por sesión a cambio
  de nada — decisión fijada en un test para que no parezca un olvido.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_worker_db_factory.py tests/unit/test_worker_engines_nullpool.py -v"
  - id: auto_audit14_06_b
    runtime: python-pytest
    command: "pytest tests/integration/test_worker_tenant_boundary.py tests/integration/test_autonomous_cycle.py -v"
  - id: auto_audit14_06_c
    runtime: python-pytest
    command: "pytest tests/docs/test_worker_db_factory_contract.py -v"
  ```

#### `task_audit14_07` — Backpressure y timeout de envío WebSocket

- [ ] **Título**: Cerrar clientes lentos sin dejar coroutines colgadas
- **Descripción**: Añadir setting de timeout de envío, envolver `send_json`, cerrar
  con código documentado y cancelar/await de `reader` y `xread` en todos los caminos.
  No cambiar todavía el transporte de credencial, que pertenece a `prod-09`.
- ⏳ **Pendiente (2026-07-31):** sin empezar — `routers/ws.py:290` sigue con un
  `await ws.send_json(event)` sin timeout, no hay setting de deadline de envío y
  `tests/unit/test_ws_pump_backpressure.py` no existe.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_ws_pump_backpressure.py -v"
  - id: auto_audit14_07_b
    runtime: python-pytest
    command: "pytest tests/integration/test_ws_streaming.py tests/integration/test_ws_tenant_isolation.py -v"
  ```

#### `task_audit14_08` — Readiness separada de liveness

- [ ] **Título**: Añadir `/readyz` con checks críticos y deadlines
- **Descripción**: Mantener `/healthz` como liveness de proceso. Añadir `/readyz`
  con PostgreSQL y Redis, timeout por check, respuesta 503 estructurada sin secretos
  y test de degradación parcial. Cablear el consumidor correcto (proxy/compose) sin
  crear restart loops por dependencias opcionales como Vault/Ollama/Docling.
- ⏳ **Pendiente (2026-07-31):** `/readyz` existe y está bien probado (PostgreSQL
  - Redis, deadline por check, 503 estructurado, saneado de credenciales,
    degradación parcial y recuperación sin reinicio: 6 tests verdes en
    `tests/integration/test_health_readiness.py` + `tests/unit/test_readiness_scrub.py`);
    falta el último tramo, cablear al consumidor — ningún `healthcheck` de
    `docker/`, del generador de compose del instalador ni del proxy consulta
    `/readyz`, así que hoy es un mecanismo sin llamantes.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_08_a
    runtime: python-pytest
    command: "pytest tests/integration/test_health_readiness.py -v"
  - id: auto_audit14_08_b
    runtime: python-pytest
    command: "pytest tests/smoke -v -k 'healthz or readyz'"
  ```

### Fase D — Toolchain y documentación

#### `task_audit14_09` — Frontend sin warnings y matriz de versiones compatible

- [ ] **Título**: Corregir dependencias de hooks y alinear Next/ESLint/TypeScript
- **Descripción**: Eliminar los ocho warnings actuales sin introducir memoización
  innecesaria (mover defaults dentro del hook cuando sea suficiente). Elegir una
  combinación soportada y fijada de Next/ESLint/TypeScript; actualizar lockfile y
  convertir warnings de lint en gate. Coordinar el upgrade mayor con `prod-16`.
- ⏳ **Pendiente (2026-07-31):** sin empezar — `npm --prefix apps/admin-panel run
lint` sigue emitiendo los OCHO warnings `react-hooks/exhaustive-deps` (en
  `approval-policy`, `knowledge-bases`, `plans`, `tasks` y `users`), el lint no es
  gate y la matriz Next/ESLint/TypeScript no se ha fijado.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_09_a
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run lint"
  - id: auto_audit14_09_b
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run typecheck && npm --prefix apps/admin-panel run test && npm --prefix apps/admin-panel run build"
  ```

#### `task_audit14_10` — Referencias y runbooks sincronizados

- [ ] **Título**: Documentar decisiones y retirar afirmaciones obsoletas verificadas
- **Descripción**: Actualizar referencias de multi-tenancy, KB/embeddings,
  healthchecks y workers; marcar el punto MCP de `analisis-diferidos-2026-07-12.md`
  como resuelto con evidencia del test, sin reescribir el resto del informe.
  Coordinar la normalización global de estados con `prod-15`.
- ⏳ **Pendiente (2026-07-31):** bloqueada por sus dependencias — de las cuatro
  (03, 05, 06, 08) solo 06 está cerrada, así que no hay decisiones firmes que
  documentar todavía.
- **Tiempo**: 0,5 días · **Complejidad**: s · **Depende de**: tareas 03, 05, 06, 08
- **Tests automáticos**:
  ```yaml
  - id: auto_audit14_10_a
    runtime: python-pytest
    command: "pytest tests/docs -v"
  ```

## Hallazgos cubiertos

| Hallazgo                           | Tareas       |
| ---------------------------------- | ------------ |
| AUD14-01 compose/gate de seguridad | 01           |
| AUD14-02 córtex/RLS                | 02, 03       |
| AUD14-03 embedding model por KB    | 04, 05       |
| AUD14-04 engines Celery dispersos  | 06           |
| AUD14-05 WebSocket sin deadline    | 07           |
| AUD14-06 readiness ausente         | 08           |
| AUD14-07 warnings/toolchain        | 09           |
| AUD14-08 documentación obsoleta    | 10 + prod-15 |

## Riesgos

1. Una política RLS incorrecta puede bloquear el córtex pre-tenant o dar falsa
   seguridad si el engine sigue con BYPASSRLS. El test cross-owner es obligatorio.
2. Cambiar el contrato de embeddings puede exigir reindexado de todos los chunks;
   debe existir dry-run, backup y rollback antes de mutar datos.
3. Migrar 30 módulos de workers de una vez aumenta el blast radius. Hacer lotes por
   familia y ejecutar sus suites antes del siguiente lote.
4. Timeouts WebSocket demasiado bajos expulsan clientes sanos bajo carga. El valor
   debe ser configurable y validado con un cliente lento controlado.
5. Readiness mal cableada puede causar flapping. Solo dependencias críticas y
   deadlines cortos; liveness permanece independiente.

## Tests humanos del plan

```yaml
- id: human_audit14_01
  description: "Hardening y composición reales en host Linux"
  checklist:
    - "docker compose base+monitoring config muestra el baseline en workers y textfile-init"
    - "textfile-init completa chmod y sale 0 con AppArmor cargado"
    - "tests/security queda 100% verde"

- id: human_audit14_02
  description: "Contrato de embeddings visible y honesto"
  checklist:
    - "Crear/editar una KB solo ofrece opciones que la ingesta ejecuta realmente"
    - "Ingestar, buscar y reindexar conserva resultados vectoriales"
    - "Un mismatch de modelo/dimensión produce error visible, no BM25-only silencioso"

- id: human_audit14_03
  description: "Degradación y recuperación operativa"
  checklist:
    - "Con PostgreSQL o Redis caído, /healthz sigue vivo y /readyz devuelve 503"
    - "Al recuperar la dependencia, /readyz vuelve a 200 sin reinicio"
    - "Un cliente WebSocket lento se cierra sin crecimiento sostenido de tasks/memoria"
```

## Criterios de cierre

1. Todos los checkboxes están `[x]` tras ejecutar sus tests automáticos.
2. `pytest tests/docs tests/security -q` está verde.
3. Suite unitaria, ruff, mypy, Vitest, lint y build siguen verdes.
4. ADRs de córtex y embeddings están ratificados por humano antes de implementar.
5. Tests humanos del plan validados.
6. Entrada en `docs/07-changelog/remediacion-auditoria-integral-2026-07-14.md`.
7. PR del plan mergeado a la rama por defecto conforme al protocolo vigente.
