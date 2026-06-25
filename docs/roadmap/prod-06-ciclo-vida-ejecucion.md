---
plan_id: prod-06-ciclo-vida-ejecucion
title: Ciclo de vida de ejecución robusto — DAG, zombis, cancelación y budgets
status: in_progress
blocking_plan: null
started_at: 2026-06-25
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 20
estimated_cost_human_eur: 9.000 € – 12.000 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-06 — Ciclo de vida de ejecución robusto: DAG, zombis, cancelación y budgets

## Cabecera

| Campo                              | Valor                               |
| ---------------------------------- | ----------------------------------- |
| **ID del Plan**                    | `prod-06-ciclo-vida-ejecucion`      |
| **Estado**                         | `pending_approval`                  |
| **Prioridad**                      | P1                                  |
| **Bloqueado por**                  | — (coordinar con prod-01 y prod-07) |
| **Tiempo estimado (calendario)**   | 4-5 semanas                         |
| **Tiempo estimado (persona-días)** | 20 (suma de tareas: 19,5)           |
| **Rama git sugerida**              | `plan/prod-06-ciclo-vida-ejecucion` |

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que el "happy path autónomo" del
producto **no se sostiene sin intervención manual**: nada promueve tareas de un plan
desde `backlog` a `ready` cuando sus dependencias terminan, una ejecución `done` o
`failed` deja su tarea en `in_progress` para siempre, y el bridge del reviewer
(`apply_reviewer_verdict`) no tiene ningún caller productivo (workers-1). Además, los
modos de fallo de larga duración no están cubiertos: un SIGKILL del child de Celery
(hard time limit u OOM) deja `executions.running` y contenedores huérfanos sin ningún
sweeper (workers-2); el broker Redis no tiene `visibility_timeout` configurado mientras
el hard limit es subible desde la UI hasta 24 h, permitiendo redelivery de runs vivos y
ejecuciones duplicadas (workers-3); la publicación de eventos es best-effort sin
reconciliación de la PEL ni barrido de tareas `ready` varadas (workers-4); y no existe
cancelación real de ejecuciones en curso (workers-5). Completan el cuadro: colas
heavy/gpu que ningún productor usa (workers-7), `_parse_cron` que degrada en silencio
cualquier cron malformado a "diario 04:00" (workers-9), budgets de run siempre `None`
(workers-10), re-encolado de ingestas bajo backlog (workers-11), la auto-pausa y
alertas de presupuesto sin ningún caller productivo (db-1) y el dispatch de tareas de
proyectos soft-borrados (db-5).

Este plan cierra el ciclo de vida completo: **promoción DAG → dispatch → ejecución →
transición de la tarea → review → sweeps de recuperación → cancelación → control de
presupuesto**, de modo que un plan aprobado se ejecute de principio a fin sin humanos
empujando estados, y que todo fallo (crash, OOM, evento perdido, presupuesto agotado)
converja a un estado terminal visible.

## Alcance

**Entra**:

- Promotor de DAG en el orchestrator (backlog → ready cuando las dependencias están done).
- Transición de la tarea al finalizar la ejecución (done/failed) + cableo del reviewer bridge.
- Sweeper de ejecuciones zombi + reaper de contenedores huérfanos + `task_reject_on_worker_lost`.
- `broker_transport_options.visibility_timeout` coherente con el hard limit + validación cruzada en el registry.
- Reconciliación de eventos: XAUTOCLAIM al arrancar el consumer + beat de re-emisión para tareas `ready` varadas.
- Cancelación cooperativa de ejecuciones en curso (flag en BD + chequeo en el worker + kill del contenedor + revoke).
- Decisión sobre colas heavy/gpu: routing real por complejidad/runtime o recorte del contrato (ADR).
- `_parse_cron` ruidoso con fallback por entrada.
- Budgets por proyecto/tenant threadeados al dispatch (`ExecutionRequest.budgets`).
- Claim/lease en el sweep de ingestión para no duplicar trabajos bajo backlog.
- Cableo productivo de `refresh_budget_pause_flags` + `maybe_alert_budgets` (beat + hook post-ejecución).
- Filtro `Project.deleted_at IS NULL` en el camino caliente del dispatch + cancelación de tareas al soft-borrar proyecto.

**Queda fuera**:

- El servicio `workers` del installer (envs `WORKERS_*`, socket Docker, lanes) — hallazgo
  workers-6, cubierto por **prod-01-despliegue-ejecutable**. Este plan asume que el worker
  arranca; prod-01 garantiza que el compose generado lo arranca bien.
- La degradación silenciosa de Vault en la resolución de credenciales (workers-8) y la
  contabilidad de costes LLM — cubiertos por **prod-07-fiabilidad-llm-costes**. La
  coordinación necesaria: el hook post-ejecución de budgets de este plan (Fase E) debe
  ejecutarse DESPUÉS de registrar el coste del run, cuyo registro exacto afina prod-07.
- Tuning del pool de conexiones y retención/purga de datos (db-2, db-4) — **prod-13-rendimiento-y-datos**.
  Nota de coordinación: el sweep periódico de budgets que cablea este plan hará más visible
  la consulta no-sargable de consumo (db-7, prod-13); no la optimizamos aquí.
- Métricas/alertas de profundidad de cola — la métrica se emite aquí (task_prod06_dag_03),
  la regla de alerta vive en **prod-08-observabilidad-alertas**.

## Decisiones clave

1. **`task_reject_on_worker_lost` (workers-2)** — Opciones: (a) activarlo: un OOM/SIGKILL
   reentrega el mensaje y el guard `supersede_running_executions` ya existente absorbe el
   duplicado; (b) no activarlo y confiar solo en el sweeper. **Recomendación: (a) + sweeper**
   — defensa en profundidad; el supersede ya está testeado como guard de redelivery.
2. **visibility_timeout vs. hard limit (workers-3)** — Opciones: (a) fijar
   `visibility_timeout = 90000s` (> 24 h, el máximo del registry) y mantener el rango actual;
   (b) acotar `execution_hard_time_limit_s.max_value` a 21600s (6 h) y fijar
   `visibility_timeout = 25200s` (7 h). **Recomendación: (b)** — un run de >6 h en un
   single-host es un olor; además se añade validación cruzada en el registry para que el
   operador no pueda romper el invariante desde la UI.
3. **Colas heavy/gpu (workers-7)** — Decisión de producto: (a) routing real
   (`estimated_complexity` l/xl → heavy; requisito GPU del runtime template → gpu con
   fallback a default si no hay worker); (b) recortar la topología a default+especializadas
   y actualizar runbook/ADR 0027. **No se decide aquí: se redacta ADR con ambas opciones**
   (task_prod06_colas_01) y la implementación sigue lo aprobado. La estimación de la fase
   asume la opción (a), la más cara.
4. **Transición post-ejecución (workers-1)** — al terminar `done`: la tarea pasa a
   `in_review` si el proyecto tiene reviewer configurado, a `done` si no; al terminar
   `failed`: la tarea pasa a `blocked` con el error visible (no a `backlog`, para no crear
   bucles de reintento silenciosos). Es política por defecto, configurable después; se
   documenta en el ADR de la decisión 3 si el humano quiere otra semántica.
5. **Origen de los budgets por run (workers-10)** — columna JSONB `execution_budgets` en
   `projects` (override) + platform setting como default de plataforma, con los defaults
   actuales del dataclass como techo (clamp en el dispatcher). Alternativa descartada:
   por-tarea (demasiada granularidad para el valor que aporta hoy).

## Tareas

### Fase A — Cierre del ciclo plan→tarea (workers-1)

#### `task_prod06_dag_01` — Transición de la tarea al finalizar la ejecución

- [x] **Título**: En `conduct_execution` (apps/workers/src/workers/execution.py:571-598),
      tras `finalize_execution`, transicionar SIEMPRE la tarea: `done` → `in_review`/`done`
      según política (decisión 4), `failed` → `blocked` con motivo; publicar el evento de
      cambio de estado. Hoy solo existe la rama `_AWAITING_APPROVAL` (execution.py:578) y
      una tarea terminada queda `in_progress` inflando el contador de carga del agente
      (dispatch.py:550-560).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_dag_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_task_transition.py -v"
  ```

#### `task_prod06_dag_02` — Promotor de DAG: backlog → ready en el orchestrator

- [x] **Título**: Implementar en apps/orchestrator el promotor que sync_to_kanban.py:215-216
      promete y no existe: al recibir el evento de tarea `done` (y en un beat de respaldo),
      mover a `ready` las tareas `backlog` del mismo plan cuyas dependencias estén todas
      `done`, publicando el evento `ready` que el dispatcher ya consume (dispatch.py:85-91).
      Idempotente y con lock por plan para evitar promociones dobles.
- **Tiempo**: 2 días · **Complejidad**: l
- **Depende de**: task_prod06_dag_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_dag_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_dag_promotion.py -v"
  - id: auto_prod06_dag_02_b
    runtime: python-pytest
    command: "pytest tests/e2e/test_plan_autonomous_lifecycle.py -v"
  ```

#### `task_prod06_dag_03` — Cablear el reviewer bridge al flujo post-ejecución

- [ ] **Título** ⏸️ **DIFERIDO (depende de ADR 0063 — ejecución del reviewer)**: Dar caller productivo a `apply_reviewer_verdict`
      (apps/api-server/src/api_server/reviewer_bridge.py:95, hoy 0 callers fuera de tests):
      cuando una tarea entra en `in_review`, el flujo post-test-runtime invoca
      `parse_reviewer_output` + `apply_reviewer_verdict` y la tarea avanza a `done` o vuelve
      con feedback según el veredicto, conforme al bucle descrito en ADR 0027:106-118.
      Emitir métrica de profundidad por cola/estado para prod-08.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: task_prod06_dag_01, task_prod06_dag_02
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_dag_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_reviewer_bridge_wiring.py -v"
  ```

### Fase B — Recuperación de fallos: zombis, redelivery y eventos perdidos

#### `task_prod06_zombi_01` — Sweeper de ejecuciones zombi + reaper de contenedores

- [x] **Título**: Nuevo beat task en apps/workers/src/workers/maintenance.py (registrado en
      beat_schedule.py): cerrar como `failed` (motivo `stale_after_worker_loss`) las
      `executions.running` cuya antigüedad supere `hard_time_limit + margen`, transicionar su
      tarea (reusa task_prod06_dag_01) y hacer `docker rm -f` de los contenedores con label
      `com.agentic-platform.execution-id` sin ejecución viva (container.py:31-36 ya etiqueta).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_zombi_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_stale_execution_sweeper.py -v"
  ```

#### `task_prod06_zombi_02` — `task_reject_on_worker_lost` + test de redelivery

- [x] **Título**: Añadir `task_reject_on_worker_lost=True` en
      apps/workers/src/workers/celery_app.py (decisión 1) y verificar con test que un
      worker-lost reentrega y `supersede_running_executions` (execution_repo.py:194-225)
      absorbe el duplicado sin doble contenedor.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_zombi_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_worker_lost_redelivery.py -v"
  ```

#### `task_prod06_zombi_03` — visibility_timeout coherente con el hard limit

- [x] **Título**: Fijar `broker_transport_options={"visibility_timeout": ...}` en
      celery_app.py de workers Y de notification-dispatcher (mismo patrón acks_late,
      notification-dispatcher/celery_app.py:45-50), acotar
      `execution_hard_time_limit_s.max_value` (platform_settings_registry.py:103-110, hoy 86400) según la decisión 2, y añadir validación cruzada en el registry que rechace
      valores de hard limit >= visibility_timeout.
- **Tiempo**: 1 día · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_zombi_03_a
    runtime: python-pytest
    command: "pytest tests/unit/test_celery_broker_options.py tests/unit/test_hard_limit_registry_validation.py -v"
  ```

#### `task_prod06_evento_01` — Reconciliación de la PEL + barrido de tareas ready varadas

- [x] **Título**: (a) En el arranque del consumer del orchestrator
      (consumer.py:120-145), XAUTOCLAIM de entradas pendientes envejecidas antes de leer con
      `>`; (b) nuevo beat tipo `sweep_pending_documents` que re-emita el trigger para tareas
      en `ready` sin dispatch en N minutos — el dispatcher ya es idempotente
      (dispatch.py:283-285). Cubre el hueco de events.py:38-50 (publicación best-effort). > Hecho: (a) `reclaim_stale_pending` (XAUTOCLAIM) llamado tras `ensure_group` en > app.py. (b) YA lo cubre el beat de dag_02 `promote_ready_plans`, que re-anuncia las > tareas `ready` sin fila de execution (no despachadas) cada 30s — no se duplica.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_evento_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_consumer_pel_reclaim.py tests/integration/test_ready_task_resweep.py -v"
  ```

### Fase C — Cancelación real de ejecuciones (workers-5)

#### `task_prod06_cancel_01` — Cancelación cooperativa end-to-end

- [ ] **Título**: (a) Columna/flag `cancel_requested_at` en executions + endpoint que lo
      marca al cancelar la tarea (routers/task_lifecycle.py:140-148 y la transición
      `in_progress → cancelled` de task_state_machine.py:68-77); (b) el bucle de drenado de
      `conduct_execution` (execution.py:514-569) consulta el flag periódicamente y mata el
      contenedor por label `com.agentic-platform.execution-id`; (c) `celery revoke` para
      mensajes aún encolados; (d) `finalize_execution` respeta el estado `cancelled` y NO
      sobrescribe la tarea cancelada.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_cancel_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_cancellation.py -v"
  ```

#### `task_prod06_cancel_02` — Cascada de cancelación a nivel plan

- [ ] **Título**: Al cancelar un plan, cancelar sus tareas no terminales y solicitar la
      cancelación de las ejecuciones en vuelo (reusa task_prod06_cancel_01). Al soft-borrar
      un proyecto (projects.py:270-279), cancelar en la misma transacción sus tareas no
      terminales — segunda mitad de la recomendación de db-5.
- **Tiempo**: 1 día · **Complejidad**: m
- **Depende de**: task_prod06_cancel_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_cancel_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_cancellation_cascade.py -v"
  ```

### Fase D — Contrato de colas y robustez de beat (workers-7, workers-9, workers-11)

#### `task_prod06_colas_01` — ADR: colas heavy/gpu reales o recorte del contrato

- [ ] **Título**: Redactar ADR en docs/05-architecture-decisions/ con las dos opciones de la
      decisión 3 (routing por `estimated_complexity`/runtime-GPU vs. recorte de topología y
      runbook 06-capacity-management.md) y someterlo a aprobación humana.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**: no aplica (documento); el gate es la aprobación del ADR.

#### `task_prod06_colas_02` — Implementar la opción aprobada del ADR

- [ ] **Título**: Si (a): routing en dispatch.py:247-258 usando `Task.estimated_complexity`
      (persistido en sync_to_kanban.py:242 y hoy sin uso) y el requisito GPU del runtime
      template, con fallback documentado a `default` si la lane no tiene workers. Si (b):
      eliminar heavy/gpu de celery_app.py:32-40, runbook y ADR 0027. En ambos casos: detectar
      y loguear cola sin consumidores al despachar (alerta de cola huérfana → prod-08).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: task_prod06_colas_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_colas_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_dispatch_queue_routing.py -v"
  ```

#### `task_prod06_beat_01` — `_parse_cron` ruidoso con fallback por entrada

- [ ] **Título**: En beat_schedule.py:100-116, ante cron malformado: log ERROR con la
      variable de entorno afectada y fallback al default documentado DE ESA entrada (no al
      04:00 global); en entorno staging/prod, rechazar el boot de beat. Hoy un typo en
      `WORKERS_HUMAN_ESCALATION_CRON` convierte el barrido de 10 min en diario sin aviso.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_beat_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_parse_cron_loud_failure.py -v"
  ```

#### `task_prod06_beat_02` — Claim/lease en el sweep de ingestión

- [ ] **Título**: En ingestion.py:179-215, marcar el documento al encolar (campo
      `enqueued_at` o SETNX en Redis con TTL) y filtrar `sweep_pending_documents` por esa
      marca, para que un backlog >5 min de la cola `ingestion` no re-encole documentos que
      siguen legítimamente en cola.
- **Tiempo**: 1 día · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_beat_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ingestion_sweep_lease.py -v"
  ```

### Fase E — Presupuestos operativos y proyectos borrados (workers-10, db-1, db-5)

#### `task_prod06_budget_01` — Cablear auto-pausa y alertas de presupuesto

- [ ] **Título**: Dar caller productivo a `refresh_budget_pause_flags` (budgets/pause.py:124)
      y `maybe_alert_budgets` (budgets/consumption.py:665), hoy solo invocados por tests:
      (a) entrada en beat_schedule.py (sweep periódico por tenant); (b) hook tras
      `finalize_execution` en el worker, después de registrar el coste (coordinación con
      prod-07). El guard lector (dispatch.py:298) ya existe; faltan los escritores.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_budget_01_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_budget_pause_end_to_end.py -v"
  ```

#### `task_prod06_budget_02` — Budgets de run configurables por proyecto/tenant

- [ ] **Título**: Sustituir el `"budgets": None` incondicional de dispatch.py:403-416 por un
      envelope resuelto proyecto → plataforma (decisión 5: columna JSONB en projects +
      platform setting), con clamp a los defaults actuales del dataclass
      (agent-runtime/safeguards.py:38-47) como techo. Migración Alembic reversible.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_budget_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_dispatch_budget_envelope.py -v"
  ```

#### `task_prod06_budget_03` — No despachar proyectos soft-borrados

- [ ] **Título**: Añadir `Project.deleted_at IS NULL` al cargar el proyecto en `_route_ai`
      (dispatch.py:338-340) y devolver None con log si está borrado. La cascada de
      cancelación al soft-borrar ya la cubre task_prod06_cancel_02.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod06_budget_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_dispatch_skips_deleted_projects.py -v"
  ```

## Hallazgos de auditoría cubiertos

| fid        | Severidad | Tarea(s) que lo cierran                                    |
| ---------- | --------- | ---------------------------------------------------------- |
| workers-1  | high      | task_prod06_dag_01, task_prod06_dag_02, task_prod06_dag_03 |
| workers-2  | high      | task_prod06_zombi_01, task_prod06_zombi_02                 |
| workers-3  | high      | task_prod06_zombi_03                                       |
| workers-4  | high      | task_prod06_evento_01                                      |
| workers-5  | medium    | task_prod06_cancel_01, task_prod06_cancel_02               |
| workers-7  | medium    | task_prod06_colas_01, task_prod06_colas_02                 |
| workers-9  | low       | task_prod06_beat_01                                        |
| workers-10 | low       | task_prod06_budget_02                                      |
| workers-11 | low       | task_prod06_beat_02                                        |
| db-1       | high      | task_prod06_budget_01                                      |
| db-5       | medium    | task_prod06_budget_03, task_prod06_cancel_02               |

## Riesgos

1. **Promoción DAG concurrente**: dos eventos `done` simultáneos del mismo plan pueden
   promover la misma tarea dos veces. Mitigación: lock por plan + dispatcher idempotente
   (ya re-chequea estado vivo); test de carrera explícito en auto_prod06_dag_02_a.
2. **`task_reject_on_worker_lost` puede reintroducir duplicados**: la reentrega tras OOM
   ejecuta de nuevo el run; el supersede absorbe la fila, pero los efectos laterales del
   run parcial (commits en worktree, coste LLM) no se deshacen. Aceptado y documentado;
   el sweeper acota la ventana.
3. **Cambiar la transición post-fallo a `blocked` altera la semántica del Kanban**: planes
   que hoy "parecen avanzar" mostrarán bloqueos reales. Es deseable, pero la UI y los
   tests e2e existentes pueden necesitar ajuste; presupuestado en task_prod06_dag_01.
4. **El ADR de colas puede tardar en aprobarse**: la Fase D queda gateada por humano.
   Mitigación: el resto de fases no dependen de ella; se puede mergear el plan por fases.
5. **El sweep de budgets agrava la consulta no-sargable de consumo (db-7)**: con muchos
   tenants el beat periódico escaneará executions completas. Coordinación explícita con
   prod-13 (índice (tenant_id, created_at) + predicado sargable); si prod-13 no llega
   antes, limitar la frecuencia del sweep.
6. **Matar contenedores por label desde el sweeper/cancelación puede afectar a runs vivos
   legítimos** si el criterio de antigüedad es agresivo. Mitigación: margen = hard limit
   - 25 % y doble verificación contra la fila de execution antes del `rm -f`.

## Tests humanos del Plan

```yaml
- id: human_prod06_01
  description: "Un plan se ejecuta de principio a fin sin intervención manual"
  hint: "Crear un plan de 3 tareas con dependencias A→B→C y agentes asignados"
  checklist:
    - "Aprobar el plan: la tarea A (sin dependencias) pasa sola a ready y se despacha"
    - "Al terminar A en done, B pasa sola a ready sin tocar nada"
    - "Cada tarea terminada queda en in_review o done (NUNCA in_progress)"
    - "El veredicto del reviewer mueve la tarea a done o la devuelve con feedback"
    - "Al terminar C, el plan completo queda en estado terminal coherente"

- id: human_prod06_02
  description: "Recuperación de zombis y cancelación"
  hint: "Usar docker kill sobre el contenedor de un run en curso y cronometrar"
  checklist:
    - "Matar el proceso worker durante un run: la ejecución acaba en failed/stale en < margen configurado y la tarea no queda in_progress"
    - "docker ps no muestra contenedores agent-runtime huérfanos tras el sweep"
    - "Cancelar desde la UI una tarea in_progress: el contenedor muere en < 30 s y la ejecución queda cancelled"
    - "Cancelar un plan: ninguna de sus tareas sigue ejecutándose"

- id: human_prod06_03
  description: "Presupuesto y proyectos borrados"
  hint: "Fijar un cap de presupuesto bajo en un proyecto sandbox"
  checklist:
    - "Superar el cap: llega la alerta y el siguiente dispatch del proyecto queda bloqueado por paused_by_budget sin tocar nada a mano"
    - "Configurar un budget de run por proyecto (p.ej. max_cost 1 USD) y verificar en los logs del runtime que el envelope aplicado no es el default"
    - "Borrar un proyecto con tareas ready: ninguna se despacha después y las no terminales quedan cancelled"

- id: human_prod06_04
  description: "Robustez operativa de beat y colas"
  hint: "Provocar configuraciones erróneas a propósito en un entorno dev"
  checklist:
    - "Arrancar beat con WORKERS_BACKUP_CRON malformado: error visible en logs (o boot rechazado en prod) y el resto de entradas conservan su frecuencia"
    - "Subir execution_hard_time_limit_s por encima del límite validado desde la UI: el registry lo rechaza con mensaje claro"
    - "Según el ADR aprobado: o una tarea xl aterriza en la cola heavy, o la cola heavy ya no existe en código ni runbook"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. El test e2e `test_plan_autonomous_lifecycle.py` pasa en CI: un plan multi-tarea con
   dependencias termina sin ninguna transición manual.
3. ADR de colas heavy/gpu aprobado por un humano e implementada la opción elegida.
4. Los 4 tests humanos del plan validados por un humano.
5. Runbook `docs/06-runbooks/06-capacity-management.md` actualizado si el contrato de
   colas cambia, y `docs/04-reference/` afectados al día.
6. Entrada de changelog en `docs/07-changelog/prod-06-ciclo-vida-ejecucion.md`.
7. PR del plan mergeado a `master`.

## Próximo Plan

**prod-07-fiabilidad-llm-costes** [P1] — Capa LLM fiable y contabilidad de costes
exacta. Coordinación directa con este plan: el hook post-ejecución que aquí cablea la
auto-pausa de presupuesto (task_prod06_budget_01) consume el coste por run cuya
exactitud y fiabilidad de registro endurece prod-07; y el hallazgo workers-8 (degradación
silenciosa de Vault en la resolución de credenciales) se cierra allí.
