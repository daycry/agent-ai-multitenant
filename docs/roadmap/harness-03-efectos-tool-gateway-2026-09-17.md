---
plan_id: harness-03-efectos-tool-gateway-2026-09-17
title: Efectos gobernados — ToolResult tipado, Tool Gateway, Effect Ledger y outbox post-run
status: pending_approval
blocking_plan: [harness-02-checkpoints-reanudacion-2026-09-17]
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 9-11
estimated_cost_human_eur: 4.000 € – 7.000 €
estimated_cost_ai_eur: 45 € – 110 €
created_by: claude-fable-5-1-verificacion-auditoria-harness-2026-09-17
spec_sections_referenced: []
docs_language: es
priority: P0
source_audit: auditoria-harness-ciclo-kb-memoria-2026-09-17.md
---

# Plan harness-03 — Efectos gobernados: Tool Gateway, Effect Ledger y outbox

## Cabecera

| Campo                 | Valor                                                                                                           |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**       | `harness-03-efectos-tool-gateway-2026-09-17`                                                                    |
| **Prioridad**         | P0                                                                                                              |
| **Bloqueado por**     | `harness-02` (las tareas de la fase A y la outbox `task_h03_09` pueden adelantarse: no dependen de checkpoints) |
| **Coordinar con**     | `prod-06`, `prod-17`, `prod-18`, `06.18`, ADR 0135                                                              |
| **Rama git sugerida** | `plan/harness-03-efectos`                                                                                       |

> **Estado**: no reabre las transiciones de prod-06/17/18. Añade garantías alrededor
> de efectos, redelivery y callbacks post-run. **Recorte respecto al borrador**: la
> «aprobación ligada al efecto exacto» ya existe (ADR 0135, `approval.py::args_hash`);
> aquí sólo se enlaza al `effect_id`.

## Resumen

El ciclo externo ya tiene claims con identidad, supersede, sweeper, cancelación,
finalización transaccional, reviewer y worktree. Pero absorber un duplicado en la
BD no revierte una tool, un commit remoto, un ticket o un coste que ya ocurrió.
`ToolResult` es `{ok, output, error:str}`: no dice si el error es reintentable, si
fue de política, de timeout o si el efecto **pudo haberse producido**. Y los
callbacks post-run (memorización, notificaciones) se publican al broker con
`try/except` que traga el fallo.

Este plan introduce un `ToolDescriptorV1` con clase de efecto, un `ToolResult`
tipado, un Tool Gateway por el que pasan tools nativas y MCP, un Effect Ledger con
idempotencia y reconciliación, y una outbox transaccional para los callbacks.

## Invariantes

1. Toda operación con efectos obtiene `effect_id` antes de ejecutarse.
2. La clave de idempotencia es estable entre attempts del mismo paso lógico.
3. Una respuesta perdida no autoriza repetir el efecto sin reconciliar.
4. El allowlist es explícito (harness-01); ausencia no es acceso total.
5. La aprobación (ADR 0135) queda enlazada al `effect_id` cuyo `args_hash` canjeó.
6. Cancelar impide iniciar efectos nuevos, pero no falsifica el estado de uno ya enviado.
7. Tools nativas y MCP comparten política, ledger, redacción y resultado tipado.

## Taxonomía de efectos y estados

```text
effect_class: READ_ONLY | IDEMPOTENT_WRITE | REVERSIBLE_WRITE | NON_IDEMPOTENT_EXTERNAL | DESTRUCTIVE

PROPOSED → AWAITING_APPROVAL → PREPARED → EXECUTING
                                       ├→ SUCCEEDED
                                       ├→ FAILED
                                       └→ UNCERTAIN → RECONCILING → SUCCEEDED | FAILED | MANUAL_REVIEW
```

La clasificación arranca de lo que ya existe: `agent_runtime/tool_classification.py`
(research / producing / read-only) y las 13 `APPROVAL_CATEGORIES` de shared-domain.

## Tareas

### Fase A — Descriptor y resultado tipado (no dependen de harness-02)

#### `task_h03_01` — `ToolDescriptorV1` y `tool_catalog_digest`

- [ ] **Título**: en `packages/shared-domain/src/shared_domain/tool_descriptor.py`:
      `name`, `version`, `input_schema`, `output_schema|null`, `implementation_digest`,
      `effect_class`, `approval_category` (una de las 13), `scopes`, `data_classification`,
      `timeout_s`, `retry_policy`, `max_attempts`, `idempotency: native|derived|none`,
      `reconciler: str|null`, `redaction_policy`, `network: {domains}`, `provider`.
      Los `ToolSpec` de 06.18 y los `MCPServerConfig` se proyectan a descriptor. El
      catálogo efectivo del run produce `tool_catalog_digest` (viaja en el envelope
      de harness-01).
      **Test**: `tests/unit/test_tool_descriptor_v1.py`;
      `tests/unit/test_tool_catalog_digest.py`. **Coste**: 1,5 d.

#### `task_h03_02` — `ToolResult` tipado

- [ ] **Título**: `ToolResult` gana `error_code`, `error_class`
      (`validation | policy_denied | timeout | provider_error | execution_uncertain | internal`),
      `retryable: bool`, `uncertain_effect: bool`, `output_digest` y `metadata`
      redactada. Ninguna excepción se convierte en string opaco: el gateway mapea
      timeout, validación, denegación de política, error de proveedor y ejecución
      incierta. Adaptador de compatibilidad para tools legacy que devuelven el
      `ToolResult` de tres campos (`error_class=internal`, `retryable=False`).
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_typed_tool_result.py`.
      **Coste**: 1 d.

### Fase B — Ledger y gateway

#### `task_h03_03` — Effect Ledger

- [ ] **Título**: tabla `execution_effects` (tenant-aware, RLS): `effect_id`,
      `execution_id`, `attempt_id`, `checkpoint_sequence|null`, `tool_name`,
      `tool_version`, `descriptor_digest`, `effect_class`, `idempotency_key`
      (`UNIQUE (execution_id, idempotency_key)`), `input_digest`, `output_digest|null`,
      `state`, `approval_id|null`, timestamps. Tabla hermana append-only
      `execution_effect_transitions` (retención ADR 0151). Transición inválida →
      error. Migración reversible sin pérdida.
      **Test**: `tests/integration/test_effect_ledger_repository.py`;
      `tests/security/test_effect_ledger_tenant_isolation.py`. **Coste**: 1,5 d.

#### `task_h03_04` — Tool Gateway en el runtime

- [ ] **Título**: `agent_runtime/tool_gateway.py` sustituye la llamada directa
      `ToolRegistry.call`: resuelve el descriptor, valida schema, allowlist sellado,
      scope, presupuesto y aprobación; para `effect_class != READ_ONLY` crea o
      recupera el efecto por `idempotency_key` vía `/internal/agent/effects`
      (PROPOSED → PREPARED), guarda checkpoint (harness-02), ejecuta, y cierra
      (SUCCEEDED / FAILED / UNCERTAIN). Si el efecto ya está `SUCCEEDED` en el
      ledger (reanudación), devuelve el resultado grabado **sin ejecutar**. Redacción
      antes de logs y eventos. Un `READ_ONLY` no toca el ledger (coste).
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_tool_gateway.py`;
      `tests/integration/test_tool_gateway_effects_roundtrip.py`;
      `tests/security/test_tool_gateway_policy_enforcement.py`. **Coste**: 2 d.
      **Depende de**: task_h03_01, task_h03_02, task_h03_03.

#### `task_h03_05` — MCP por el gateway, sin ruta lateral

- [ ] **Título**: `mcp_tools.register_mcp_server` registra descriptores (clase por
      defecto `NON_IDEMPOTENT_EXTERNAL` salvo anotación del servidor o del
      marketplace, ADR 0166), y la llamada pasa por el gateway con los mismos
      timeouts, aprobación, ledger y redacción. Un MCP `REQUIRED` caído aborta según
      el contrato de dependencias; uno `OPTIONAL` degrada explícitamente. Test
      sentinela que falla si aparece una llamada a `MCPToolRunner.call_tool` fuera
      del gateway.
      **Test**: `tests/integration/test_mcp_through_tool_gateway.py`;
      `docker/agent-runtimes/agent-runtime/tests/test_no_mcp_gateway_bypass.py`.
      **Coste**: 1,5 d. **Depende de**: task_h03_04.

### Fase C — Idempotencia, reconciliación y aprobación

#### `task_h03_06` — Clave de efecto estable

- [ ] **Título**: `idempotency_key = sha256(execution_id, logical_step, descriptor_digest, normalized_input_digest)`
      donde `logical_step` es el índice de decisión del plan interno (sobrevive al
      cambio de `attempt_id`); dos iteraciones intencionalmente distintas con el
      mismo input producen claves distintas por `logical_step`. Si la tool declara
      `idempotency: native`, se usa su clave (p. ej. `Idempotency-Key` HTTP).
      **Test**: `tests/unit/test_effect_idempotency_key.py` (casos generados
      con `hypothesis` si está en las dev-deps; si no, tabla de casos). **Coste**: 1 d.

#### `task_h03_07` — Reconciliar efectos inciertos

- [ ] **Título**: cada descriptor puede declarar `reconciler` (p. ej. `http_request`
      con `GET` de comprobación, `git push` con `ls-remote`). Sweeper en
      `workers.maintenance` que recorre `UNCERTAIN` → llama al reconciler →
      `SUCCEEDED | FAILED`; sin reconciler → `MANUAL_REVIEW` con notificación a la
      bandeja humana (tanda 2 2026-07-19) y **prohibición de repetir**. Compensación
      sólo si la tool declara y prueba operación inversa. Alerta por antigüedad.
      **Test**: `tests/integration/test_uncertain_effect_reconciliation.py`;
      `tests/integration/test_opaque_effect_requires_human_review.py`. **Coste**: 1,5 d.
      **Depende de**: task_h03_04.

#### `task_h03_08` — Enlazar la aprobación (ADR 0135) al `effect_id`

- [ ] **Título**: la aprobación que hoy se canjea por `args_hash` (`approval.py`) queda
      registrada en `execution_effects.approval_id`; cambiar parámetros ya invalida
      la aprobación (ADR 0135), aquí sólo se garantiza que el canje y el efecto se
      vean juntos en el visor y que una aprobación consumida sea auditable por
      `effect_id`. Con harness-02, la espera de aprobación es un interrupt durable.
      **Test**: `tests/integration/test_effect_bound_approval.py`. **Coste**: 0,5 d.
      **Depende de**: task_h03_03.

### Fase D — Integración con el ciclo externo

#### `task_h03_09` — Outbox transaccional para callbacks post-run

- [ ] **Título**: tabla `execution_outbox` (`id`, `tenant_id`, `execution_id`,
      `kind: memorize | notify | webhook`, `payload`, `state: pending | processing | delivered | failed | dead`,
      `attempts`, `next_attempt_at`, `idempotency_key UNIQUE`). `finalize_execution`
      escribe la fila **en la misma transacción** de cierre; un publicador (beat en
      `workers.maintenance`, cada 10 s) la entrega al broker con backoff y
      dead-letter visible en la UI de mantenimiento. `trigger_memorize` y
      `_notify_execution_outcome` pasan a escribir en la outbox; el cierre del run
      deja de depender del broker. Coordinado con `task_h05_03`.
      **Test**: `tests/integration/test_execution_postrun_outbox.py`;
      `tests/e2e/test_broker_down_postrun_outbox.py` (runner Docker). **Coste**: 1,5 d.

#### `task_h03_10` — Redelivery, lease y attempt sucesor

- [ ] **Título**: antes de preparar cualquier efecto el gateway comprueba (vía API
      interna) que el `attempt_id` sigue siendo el vigente de la ejecución (lease
      renovado por el worker cada N s en `executions.attempt_lease_until`). Un
      attempt superseded no adquiere efectos nuevos; el sucesor carga checkpoint y
      ledger antes de actuar. Alinear `visibility_timeout`, hard limit y renovación
      del lease (hoy la re-entrega Celery es ~7 h por diseño: se documenta y se
      ajusta el lease a ese valor, no al revés). Registrar `resume_reason`.
      **Test**: `tests/e2e/test_redelivery_effectively_once.py` (runner Docker).
      **Coste**: 1 d. **Depende de**: task_h03_04.

#### `task_h03_11` — Matriz de crash alrededor de efectos

- [ ] **Título**: con el inyector de harness-02, matar antes de preparar, tras
      preparar, durante la llamada, tras la respuesta y antes del checkpoint, para
      `READ_ONLY`, `IDEMPOTENT_WRITE` y `NON_IDEMPOTENT_EXTERNAL`. Se demuestra no
      duplicación o escalado a `MANUAL_REVIEW`, por camino productivo. Matriz en
      `docs/04-reference/harness-effect-crash-matrix.md`.
      **Test**: `tests/e2e/test_effect_crash_matrix.py` (runner Docker). **Coste**: 1,5 d.
      **Depende de**: task_h03_07, task_h03_10.

## Criterios de cierre

- [ ] Ninguna ruta productiva de tool o MCP evita el gateway (sentinela verde).
- [ ] Toda escritura tiene `effect_id` y `effect_class`.
- [ ] Un redelivery no repite un efecto `SUCCEEDED`.
- [ ] Un resultado incierto se reconcilia o escala; nunca se repite a ciegas.
- [ ] La aprobación aparece enlazada al efecto en el visor.
- [ ] Broker caído no pierde memorización ni notificación; la outbox lo muestra.
- [ ] Matriz de crash sin skips.

## Riesgos

1. **Tool legacy sin reconciler**: clasificar como opaca y escalar.
2. **Clave demasiado amplia o estrecha**: incluir `logical_step` e input normalizado; tests de colisión.
3. **Ledger inconsistente con checkpoint**: protocolo prepare → checkpoint → execute → close; reconciliación.
4. **Latencia**: `READ_ONLY` no toca el ledger; eventos en batch.
5. **Doble publicación desde la outbox**: `idempotency_key` UNIQUE y consumidores idempotentes (el memorizer ya lo es).

## Pruebas humanas

```yaml
- id: human_h03_01
  title: Efecto externo con respuesta perdida
  steps:
    - Tool de prueba que crea un recurso consultable (http_request contra un mock).
    - Cortar la red tras crear el recurso y antes de la respuesta.
    - Dejar que el run se recupere.
  expected: El reconciler encuentra el recurso; el ledger termina SUCCEEDED sin duplicar.

- id: human_h03_02
  title: Tool opaca no idempotente
  steps:
    - Tool sin reconciler cuyo resultado queda incierto.
    - Reanudar el run.
  expected: Aparece en la bandeja humana como MANUAL_REVIEW; el sistema no repite la llamada.

- id: human_h03_03
  title: Memorización con el broker caído
  steps:
    - Finalizar un run memorizable con Redis detenido.
    - Restaurar Redis.
  expected: La fila de la outbox pasa de pending a delivered una sola vez; la memoria existe.
```
