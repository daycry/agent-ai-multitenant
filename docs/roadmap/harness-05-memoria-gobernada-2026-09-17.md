---
plan_id: harness-05-memoria-gobernada-2026-09-17
title: Memoria de agentes gobernada — procedencia, Memory Gate, memorización durable y estados de recall (delta sobre memoria-agentes-2026-09-09)
status: pending_approval
blocking_plan: [harness-04-kb-reproducible-2026-09-17, memoria-agentes-2026-09-09]
started_at: null
completed_at: null
estimated_duration_calendar: 1,5 semanas
estimated_effort_person_days: 5-7
estimated_cost_human_eur: 2.200 € – 4.200 €
estimated_cost_ai_eur: 25 € – 60 €
created_by: claude-fable-5-1-verificacion-auditoria-harness-2026-09-17
spec_sections_referenced: []
docs_language: es
priority: P1
source_audit: auditoria-harness-ciclo-kb-memoria-2026-09-17.md
---

# Plan harness-05 — Memoria gobernada: el delta sobre `memoria-agentes-2026-09-09`

## Cabecera

| Campo                 | Valor                                                           |
| --------------------- | --------------------------------------------------------------- |
| **ID del Plan**       | `harness-05-memoria-gobernada-2026-09-17`                       |
| **Prioridad**         | P1                                                              |
| **Bloqueado por**     | `harness-04` y **`memoria-agentes-2026-09-09`** (aprobado, 0/7) |
| **Coordinar con**     | `06.7` (dedup, cerrado), `task_h03_09` (outbox)                 |
| **Rama git sugerida** | `plan/harness-05-memoria`                                       |

> **Estado**: este plan nace **recortado**. El borrador externo traía 11 tareas;
> seis duplicaban `memoria-agentes-2026-09-09`, que ya está aprobado. Aquí quedan
> sólo las que ese plan no cubre. Si `memoria-agentes` cambia de alcance, se revisa
> esta tabla antes de tocar código.

## Reconciliación con `memoria-agentes-2026-09-09` (hecha el 2026-09-17)

| Tarea del borrador harness-05                 | Cubierta por                                          | Decisión                                                                                       |
| --------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Recall por capas con `memory_get`             | `task_mem_01`                                         | **Duplicada** — no se hace aquí                                                                |
| Snapshot, citas y registro de uso             | `task_mem_02` (`memory_recall_log`, citas `[mem:id]`) | **Duplicada**; aquí sólo se añade el `state` al log (`task_h05_05`)                            |
| Observaciones tipadas y resumen por ejecución | `task_mem_04`                                         | **Parcial**: aquí se añade `agent_claimed` vs `externally_verified` (`task_h05_04`)            |
| Ranking con utilidad verificable              | `task_mem_10`                                         | **Duplicada**; aquí sólo se exige que la fórmula pondere `verification_status` (`task_h05_04`) |
| Supersesión, TTL, borrado                     | `task_mem_11` (caducidad y compactación)              | **Parcial**: aquí se añade `supersedes` y la prohibición de editar en silencio (`task_h05_06`) |
| API/UI de procedencia                         | `task_mem_20`                                         | **Duplicada** — este plan aporta las columnas que esa UI mostrará                              |
| `MemoryRecordV1` y procedencia                | —                                                     | **Nueva** (`task_h05_01`)                                                                      |
| Memory Gate                                   | —                                                     | **Nueva** (`task_h05_02`)                                                                      |
| Outbox de memorización                        | —                                                     | **Nueva**, compartida con harness-03 (`task_h05_03`)                                           |
| Estados de recall y `REQUIRED/OPTIONAL`       | —                                                     | **Nueva** (`task_h05_05`)                                                                      |
| Prompt injection y memoria envenenada         | —                                                     | **Nueva** (`task_h05_07`)                                                                      |
| Aislamiento cross-tenant de las piezas nuevas | 06.9 cubre el recall                                  | **Nueva** sólo para gate, outbox y supersesión (`task_h05_08`)                                 |

## Principios

1. El agente **propone**; el Memory Gate decide.
2. Toda memoria conserva origen y nivel de confianza, separados del estado de verificación.
3. Una autoafirmación del agente no es evidencia.
4. La memorización post-run no se pierde por una caída del broker.
5. Los scopes siguen siendo los de CLAUDE.md: `private` (humano; un agente ni la lee ni la escribe), `team_shared`, `project_shared`, `global`. Este plan no añade scopes.

## Tareas

### Fase A — Modelo y gate

#### `task_h05_01` — Procedencia y verificación en `MemoryEntry`

- [ ] **Título**: migración aditiva sobre `memory_entries`: `source_kind`
      (execution, tool_result, human_input, document, reviewer, test_report o
      memorizer), `source_ref` (id del origen; `source_execution_id` ya existe),
      `confidence` (0–1, del memorizer), `verification_status` (agent_claimed,
      externally_verified, human_confirmed o disputed), `content_digest`, `version`,
      `supersedes_id` nullable, `valid_from`, `valid_to` nullable, `sensitivity`
      (normal, pii o secret_suspect). `MemoryRecordV1` en shared-domain como forma
      canónica que el memorizer produce y la API expone. Backfill: filas existentes
      → `source_kind=memorizer`, `verification_status=agent_claimed`, `version=1`.
      **Test**: `tests/unit/test_memory_record_v1.py`;
      `tests/integration/test_memory_entry_provenance_columns.py`. **Coste**: 1 d.

#### `task_h05_02` — Memory Gate

- [ ] **Título**: `api_server/memorizer/gate.py`: toda escritura del memorizer (y
      cualquier futura tool `memory_propose`) pasa por el gate, que valida schema,
      scope permitido para el origen (un agente jamás escribe `private`),
      procedencia obligatoria, tamaño, y rechaza: instrucciones normativas («a
      partir de ahora…», detector ES/EN), credenciales y patrones de secreto (reusa
      `shared_guardrails.checks.pii` y el detector de secretos), y afirmaciones sin
      `source_ref`. Dedup exacto y semántico delegado en 06.7 sin perder fuentes
      (se anexa `source_ref`). Promoción a `externally_verified` sólo con evidencia
      configurada (test report verde, veredicto del reviewer, aprobación humana).
      Decisión y motivo en `memory_gate_decisions` (append-only, ADR 0151).
      **Test**: `tests/integration/test_memory_gate.py`;
      `tests/security/test_memory_gate_rejects_secrets_and_instructions.py`.
      **Coste**: 1,5 d. **Depende de**: task_h05_01.

#### `task_h05_03` — La memorización viaja por la outbox

- [ ] **Título**: `trigger_memorize` deja de encolar con un `try/except` que traga el
      error: escribe `kind=memorize` en `execution_outbox` (`task_h03_09`) en la
      misma transacción de `finalize_execution`, con clave de idempotencia derivada
      de `execution_id` y `memory_policy_version`. El consumidor
      (`memorize_execution`) ya es idempotente; se añade el estado visible (pending,
      processing, succeeded, failed, dead) y reprocesado manual desde la UI de
      mantenimiento. Si harness-03 aún no está, esta tarea se implementa **con** la
      outbox mínima y harness-03 la generaliza (una sola tabla, no dos).
      **Test**: `tests/integration/test_durable_memorization_outbox.py`;
      `tests/e2e/test_memory_job_survives_broker_outage.py` (runner Docker).
      **Coste**: 1 d. **Coordinar con**: `task_h03_09`.

### Fase B — Extracción, supersesión y recall

#### `task_h05_04` — Lo que el agente dice vs lo que se comprobó

- [ ] **Título**: el memorizer extrae observaciones desde **eventos y resultados**
      (tests del test-runtime, veredicto del reviewer, aprobaciones, `ToolResult`
      tipados), no sólo del texto final; cada observación nace `agent_claimed` o
      `externally_verified` según su origen. Un run `failed` no consolida un patrón
      como exitoso (va como episódico con `outcome=failed`). La fórmula de ranking
      de `task_mem_10` recibe `verification_status` como feature obligatoria con
      peso superior a la autoevaluación.
      **Test**: `tests/integration/test_execution_memory_extraction.py`;
      `tests/unit/test_memory_ranking_weights_verification.py`. **Coste**: 1 d.
      **Depende de**: task_h05_01.

#### `task_h05_05` — El recall devuelve estado y respeta el requisito

- [ ] **Título**: `POST /internal/agent/memory-recall` responde `{state, hits, …}`
      con los mismos estados que la KB (`AVAILABLE_EMPTY`, `UNAVAILABLE` y
      `FORBIDDEN` son distintos). El auto-recall de `__main__.py` deja de tragar la
      excepción y devolver lista vacía, y propaga el estado al `dependency_report` y
      al preámbulo. Requisito de memoria del envelope (harness-01): `REQUIRED` +
      `UNAVAILABLE` aborta antes del modelo; `FORBIDDEN` no registra las tools
      `memory_*`. El `memory_recall_log` de `task_mem_02` gana la columna `state`.
      **Test**: `tests/integration/test_memory_dependency_states.py`;
      `docker/agent-runtimes/agent-runtime/tests/test_memory_prefetch_reports_state.py`.
      **Coste**: 1 d.

#### `task_h05_06` — Supersesión sin reescribir la historia

- [ ] **Título**: una memoria referenciada por `memory_recall_log` es inmutable:
      corregirla crea una fila nueva con `supersedes_id` y cierra `valid_to` de la
      anterior; el recall devuelve sólo vigentes salvo `include_superseded`. Dos
      memorias incompatibles con evidencia distinta → `verification_status=disputed`
      en ambas hasta que un humano o una evidencia posterior resuelva; el recall
      las muestra juntas con su procedencia. Borrado tenant-aware con propagación a
      embeddings y caches (extiende `task_mem_11`).
      **Test**: `tests/integration/test_memory_supersession.py`;
      `tests/integration/test_memory_deletion_propagation.py`. **Coste**: 1 d.
      **Depende de**: task_h05_01.

### Fase C — Seguridad

#### `task_h05_07` — Memoria envenenada y prompt injection

- [ ] **Título**: los hits de memoria entran al preámbulo delimitados (etiqueta
      `<mem id v verified>` … `</mem>`) como datos; el check `prompt_injection`
      corre por hit; patrones sospechosos ponen la memoria en
      `sensitivity=secret_suspect` o en cuarentena (`quarantined_at`) y la excluyen
      del recall hasta revisión. Corpus adversarial ES/EN compartido con `task_h04_05`.
      **Test**: `tests/security/test_memory_prompt_injection.py`. **Coste**: 0,5 d.

#### `task_h05_08` — Aislamiento de las piezas nuevas

- [ ] **Título**: gate, outbox, `memory_gate_decisions` y supersesión probados con dos
      tenants y con los cuatro scopes; un agente no puede proponer ni leer `private`;
      el reprocesado manual de la outbox respeta RLS.
      **Test**: `tests/security/test_memory_governance_cross_tenant.py`. **Coste**: 0,5 d.
      **Depende de**: task_h05_02, task_h05_03, task_h05_06.

## Criterios de cierre

- [ ] La tabla de reconciliación de arriba sigue siendo cierta al cerrar (se revisa contra el estado real de `memoria-agentes`).
- [ ] Ningún runtime ni memorizer escribe memoria definitiva sin pasar por el gate.
- [ ] Broker caído no pierde la solicitud de memorización.
- [ ] El recall distingue vacío de indisponible y respeta `REQUIRED/OPTIONAL/FORBIDDEN`.
- [ ] Cada memoria tiene `source_kind`, `verification_status`, `version` y digest.
- [ ] Una memoria citada por un run histórico no cambia.
- [ ] Instrucciones y secretos no se promocionan a memoria.
- [ ] Cross-tenant y poisoning en verde.

## Riesgos

1. **Divergir de `memoria-agentes`**: este plan se implementa **después** o **junto** a él; nunca antes.
2. **Gate demasiado estricto**: métrica de rechazos por motivo; el operador ajusta umbrales.
3. **Falsa consolidación**: `externally_verified` exige evidencia con `source_ref` resoluble.
4. **Derecho de borrado vs replay**: metadatos mínimos y replay `PARTIAL`.

## Pruebas humanas

```yaml
- id: human_h05_01
  title: Memorización con el broker caído
  steps:
    - Finalizar un run con una observación memorizable.
    - Detener Redis antes de que la outbox publique.
    - Restaurar Redis y esperar al publicador.
  expected: La memoria se crea una sola vez con el execution_id de origen; la outbox muestra delivered.

- id: human_h05_02
  title: Memoria conflictiva y supersesión
  steps:
    - Crear una memoria externally_verified.
    - Proponer otra incompatible con evidencia más reciente.
    - Ejecutar recall antes y después de resolver.
  expected: La historia no se sobrescribe; la UI de task_mem_20 muestra vigencia, procedencia y disputa.
```
