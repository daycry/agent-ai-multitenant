---
plan_id: harness-02-checkpoints-reanudacion-2026-09-17
title: Durabilidad del runtime — checkpoints LangGraph remotos y reanudación del loop agéntico
status: pending_approval
blocking_plan: [harness-01-contrato-run-capacidades-2026-09-17]
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 9-12
estimated_cost_human_eur: 4.000 € – 7.000 €
estimated_cost_ai_eur: 40 € – 100 €
created_by: claude-fable-5-1-verificacion-auditoria-harness-2026-09-17
spec_sections_referenced: []
docs_language: es
priority: P0
source_audit: auditoria-harness-ciclo-kb-memoria-2026-09-17.md
---

# Plan harness-02 — Checkpoints durables y reanudación del loop agéntico

## Cabecera

| Campo                 | Valor                                           |
| --------------------- | ----------------------------------------------- |
| **ID del Plan**       | `harness-02-checkpoints-reanudacion-2026-09-17` |
| **Prioridad**         | P0 (condicionada al spike `task_h02_00`)        |
| **Bloqueado por**     | `harness-01`                                    |
| **Rama git sugerida** | `plan/harness-02-checkpoints`                   |

> **Estado**: mantener `pending_human_validation` hasta matar contenedores reales
> entre nodos y demostrar continuidad de presupuestos, detector de bucles y contexto.
> **La fase B en adelante sólo arranca si el spike de la fase A lo justifica.**

## Resumen

El grafo LangGraph se compila sin checkpointer (`graph.py::build_agent_graph` →
`graph.compile()`) y `run_agent` crea un `SafeguardTracker` y un `LoopDetector`
nuevos por proceso. Si el contenedor muere entre nodos, el worker lo detecta
(supersede + redelivery, ya implementado) y **relanza desde cero**: se pierden el
plan interno, las reflexiones, la conversación con el proveedor y los contadores
de presupuesto. El agente vuelve a pagar tokens ya gastados y puede repetir tools.

Este plan añade un checkpointer remoto (el sandbox no toca PostgreSQL; escribe
por la API interna con un token acotado a **una ejecución**), separa
`execution_id` (estable) de `attempt_id` (por arranque) y reanuda el estado
completo. Pero **antes mide** cuánto ocurre: si menos del 2 % de los runs mueren
a mitad, el ADR 0168 puede recortar el alcance a la fase A + D.

## Objetivos

- Reanudar desde el último límite durable, con nuevo `attempt_id` y el mismo `execution_id`.
- Mantener `AgentState`, presupuestos consumidos, deadline real, feedback y detector.
- Impedir que un runtime lea checkpoints de otra ejecución o tenant.
- Eventos append-only suficientes para recovery y para el replay de harness-06.

## No objetivos

- Evitar por sí solo que una tool externa se repita (harness-03).
- Persistir secretos o prompts sin redacción.
- Dar al runtime acceso directo a la BD.

## Decisiones (fijadas por el ADR 0168)

1. `execution_id` = intención lógica; `attempt_id` = cada arranque.
2. Store en PostgreSQL, expuesto por `/internal/agent/checkpoints/*` con token JWT que
   añade el claim `exe` (execution_id) al `task` que ya emite `auth/internal_agent.py`.
3. `sequence` monotónica por ejecución y compare-and-swap (`expected_sequence`).
4. Se guarda tras cada nodo del grafo y antes/después de un límite con efectos.
5. Safeguards, detector y conversación del proveedor forman parte del estado durable.
6. Checkpoints inmutables; la compactación crea snapshots nuevos.
7. Un checkpoint incompatible o corrupto **bloquea** el run con error tipado; nunca se
   reinicia desde cero sin una decisión explícita.

## Modelo mínimo de datos

```text
execution_checkpoints   (particionada como executions si el volumen lo pide)
  id, tenant_id, execution_id, attempt_id, sequence, graph_version,
  state_schema_version, node_name, state_blob (bytea, Fernet), state_digest,
  created_at, supersedes_checkpoint_id
  UNIQUE (execution_id, sequence)

execution_events        (append-only, familia de retención del ADR 0151)
  id, tenant_id, execution_id, attempt_id, sequence, event_type,
  payload_redacted JSONB, payload_digest, trace_id, created_at
```

## Tareas

### Fase A — Medir y contratar el estado

#### `task_h02_00` — Spike: cuántos runs mueren a mitad y qué pierden

- [ ] **Título**: consulta sobre `executions` de los últimos 60 días
      (`abort_code` en superseded, zombie o hard_timeout, y filas `failed` sin
      `finished_at` coherente) para medir la tasa de muertes mid-run y el coste
      medio (tokens, minutos) del trabajo perdido. Resultado en una sección
      «Medición» de este plan con la consulta usada y la fecha. Si la tasa es
      < 2 % y el coste medio < 1 € por muerte, se propone al ADR 0168 recortar el
      plan a `task_h02_01`, `task_h02_08` y `task_h02_10` (kill tests documentando
      el comportamiento actual).
      **Test**: no aplica (medición). **Coste**: 0,5 d.

#### `task_h02_01` — Inventario del estado durable y efímero

- [ ] **Título**: enumerar cada clave de `AgentState` (`agent_runtime/state.py`) y
      el estado que hoy vive **fuera** del TypedDict: `SafeguardTracker` (contadores,
      deadline, coste), `LoopDetector` (historial), conversación del proveedor
      (`providers.py`), uso de modelo y `pending_interrupt`. Clasificar cada valor
      como durable / recomputable / sensible / prohibido. Definir
      `STATE_SCHEMA_VERSION = 1` y la política de migración (un migrador por salto).
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_agent_state_contract.py`
      (toda clave del TypedDict está clasificada; ninguna clave sin clasificar).
      **Coste**: 1 d.

#### `task_h02_02` — Serializador canónico, redactado y cifrado

- [ ] **Título**: `agent_runtime/checkpoint_codec.py`: JSON canónico con tope de
      tamaño (configurable, default 2 MiB), exclusión de handles no serializables y
      de cualquier valor marcado sensible; digest del contenido lógico **antes** de
      cifrar; cifrado Fernet con clave que el worker inyecta por variable de entorno
      del contenedor (la familia `*_ENCRYPTION_KEYS` de prod-05, rotación incluida).
      Estado desconocido → error, no descarte silencioso.
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_checkpoint_codec.py`;
      `tests/security/test_checkpoint_secret_redaction.py`. **Coste**: 1,5 d.
      **Depende de**: task_h02_01.

### Fase B — Store durable y API interna

#### `task_h02_03` — Migración de checkpoints y eventos

- [ ] **Título**: tablas del modelo mínimo con RLS por `tenant_id`, restricción única
      por ejecución y secuencia, índices por `(execution_id, sequence DESC)`,
      retención configurable (`API_SERVER_CHECKPOINT_RETENTION_DAYS`) y estado de
      compactación. Downgrade reversible que **no borra datos** (renombra a
      `_dropped_*` como hacen las migraciones de particionado del repo).
      **Test**: `tests/integration/test_checkpoint_repository.py`;
      `tests/integration/test_checkpoint_tenant_isolation.py` (cross-tenant en la
      suite de seguridad). **Coste**: 1 d.

#### `task_h02_04` — API interna acotada a una ejecución

- [ ] **Título**: `GET /internal/agent/checkpoints/latest`,
      `POST /internal/agent/checkpoints` (con `expected_sequence`; conflicto → 409
      `checkpoint_sequence_conflict`), `POST /internal/agent/events`. El token de
      `mint_agent_token` gana el claim `exe`; el endpoint rechaza cualquier
      `execution_id` que no coincida. Límite de payload (`413`), rate limit y audit
      log de rechazos (`kind=checkpoint_access_denied`).
      **Test**: `tests/integration/test_internal_checkpoint_api.py`;
      `tests/security/test_checkpoint_token_scope.py`. **Coste**: 1,5 d.
      **Depende de**: task_h02_03.

### Fase C — Integración LangGraph

#### `task_h02_05` — `RemoteCheckpointSaver`

- [ ] **Título**: implementación de `BaseCheckpointSaver` (LangGraph `>=1.2,<2`, la
      versión pineada en el `pyproject.toml` del runtime) en
      `agent_runtime/remote_checkpointer.py`, sobre `InternalAPIClient`. `thread_id`
      = `execution_id`; propaga `attempt_id`, `graph_version` y
      `state_schema_version`. Al arrancar carga `latest`; crea el checkpoint inicial
      sólo si no existe. Latencia y errores instrumentados (`steps` del run).
      `build_agent_graph(..., checkpointer=…)` lo recibe; sin API interna (bare run)
      se compila sin checkpointer como hoy.
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_remote_checkpointer.py`
      (contra un servidor HTTP fake); `tests/integration/test_langgraph_remote_checkpoint.py`.
      **Coste**: 2,5 d. **Depende de**: task_h02_02, task_h02_04.

#### `task_h02_06` — Reanudación del estado completo

- [ ] **Título**: `run_agent` acepta `resume_from: Checkpoint | None`. Restaura
      `AgentState`, `SafeguardTracker` (consumos acumulados y **deadline real**, no
      reiniciado), `LoopDetector`, conversación del proveedor y feedback de
      self-review. El resultado lleva `resumed_from_sequence` y `resume_reason`. El
      worker, al recibir un redelivery de una ejecución con checkpoints, pasa el
      **mismo** `execution_id` con nuevo `attempt_id` en vez de crear una ejecución
      nueva (hoy `supersede_running_executions` + fila nueva).
      **Test**: `tests/integration/test_runtime_resume_preserves_state.py`;
      `tests/integration/test_runtime_resume_preserves_budgets.py`;
      `tests/integration/test_runtime_resume_preserves_loop_detector.py`.
      **Coste**: 1,5 d. **Depende de**: task_h02_05.

#### `task_h02_07` — Interrupciones humanas y cancelación durables

- [ ] **Título**: `ask_human` (ADR 0114) y las aprobaciones (ADR 0135) se modelan como
      interrupt durable: el checkpoint guarda la pregunta o la huella de la acción
      pendiente; la respuesta humana reanuda **desde ese checkpoint**, no desde un
      run nuevo con `human_answers` en el preámbulo (que se mantiene como fallback).
      Cancelación → evento terminal `execution_cancelled` y rechazo de cualquier
      reanudación posterior. Respuesta repetida → idempotente.
      **Test**: `tests/integration/test_durable_human_interrupt_resume.py`;
      `tests/integration/test_cancelled_execution_cannot_resume.py`. **Coste**: 1 d.
      **Depende de**: task_h02_06.

### Fase D — Recovery, compatibilidad y retención

#### `task_h02_08` — Checkpoint corrupto o incompatible

- [ ] **Título**: verificar `state_digest` antes de descifrar y deserializar;
      `state_schema_version` mayor que la soportada → `abort_code=checkpoint_incompatible`;
      digest inválido → `checkpoint_corrupt`. Ambos bloquean el run, alertan
      (evento + notificación `execution_failed` con motivo) y **no** relanzan desde
      cero. Comando `python -m api_server.cli.checkpoints` con subcomandos `inspect`
      y `select` para que el operador vea la cadena y, con aprobación, elija un
      checkpoint anterior.
      **Test**: `tests/integration/test_checkpoint_corruption_handling.py`;
      `tests/integration/test_checkpoint_schema_incompatibility.py`. **Coste**: 1 d.

#### `task_h02_09` — Compactación, retención y borrado

- [ ] **Título**: conservar los N últimos checkpoints completos (default 5) y
      compactar los anteriores de ejecuciones **terminales** en un snapshot; respetar
      el borrado de tenant/proyecto (cascada) y la retención del ADR 0151; medir
      tamaño y crecimiento (`checkpoint_bytes_total`). Beat en
      `workers.maintenance`.
      **Test**: `tests/integration/test_checkpoint_retention_compaction.py`.
      **Coste**: 1 d.

#### `task_h02_10` — Matriz de kill por frontera de nodo

- [ ] **Título**: inyector de fallo determinista (`AGENT_FAULT_INJECT=after:reflect`
      y similares, sólo si `AGENT_FAULT_INJECT_ENABLED=1`) que mata el proceso
      antes/después de cada nodo del grafo; el camino productivo (supersede +
      redelivery del worker) lo redespacha. Se afirma que no se pierden feedback,
      presupuesto, detector ni resultados de tools ya observados. Matriz de puntos
      cubiertos en `docs/04-reference/harness-kill-matrix.md`.
      **Test**: `tests/e2e/test_runtime_kill_resume_matrix.py` (runner Docker; JUnit
      adjunto al PR). **Coste**: 1,5 d. **Depende de**: task_h02_06, task_h02_07, task_h02_08.

## Criterios de cierre

- [ ] El spike está documentado con consulta y fecha, y el alcance final coincide con lo que el ADR 0168 decidió.
- [ ] `graph.compile()` recibe checkpointer en runs productivos.
- [ ] El runtime nunca accede directamente a la BD.
- [ ] Reiniciar tras `reflect` continúa desde estado durable con el mismo `execution_id`.
- [ ] Presupuesto y detector no se reinician.
- [ ] Un checkpoint corrupto no desencadena un run limpio silencioso.
- [ ] Approval, `ask_human` y cancelación sobreviven a un reinicio.
- [ ] La matriz de kill cubre todos los nodos sin skips.
- [ ] Retención y borrado probados.

## Riesgos

1. **Conversación del proveedor no serializable** (sesiones del Claude SDK): clasificarla como recomputable y reconstruirla desde el historial de decisiones si el SDK no expone estado.
2. **Doble escritor**: CAS + un solo attempt activo por lease (harness-03 `task_h03_10`).
3. **Inflación de almacenamiento**: medir antes de optimizar; compactar.
4. **Datos sensibles**: redacción, cifrado, scopes mínimos, test de seguridad.
5. **Upgrade incompatible**: migradores de estado y bloqueo ruidoso.

## Pruebas humanas

```yaml
- id: human_h02_01
  title: Reanudar un run real tras matar el contenedor
  steps:
    - Lanzar una tarea de varios pasos.
    - Esperar a un checkpoint posterior a reflect.
    - docker kill del contenedor del runtime.
    - Observar redelivery y reanudación en el visor de la ejecución.
  expected: Nuevo attempt_id, mismo execution_id, continuidad desde el último checkpoint, presupuesto no reiniciado.

- id: human_h02_02
  title: Privacidad del checkpoint
  steps:
    - Ejecutar con credenciales de prueba reales.
    - Leer el blob persistido con privilegio operativo.
    - Buscar secretos y comparar el digest lógico.
  expected: Ningún secreto en claro; el estado útil se restaura.
```
