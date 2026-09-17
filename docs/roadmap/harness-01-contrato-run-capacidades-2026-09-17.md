---
plan_id: harness-01-contrato-run-capacidades-2026-09-17
title: Contrato de ejecución versionado, dependencias declaradas y capacidades verificadas
status: pending_approval
blocking_plan: [harness-00-programa-profesionalizacion-2026-09-17]
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 8-10
estimated_cost_human_eur: 3.500 € – 6.000 €
estimated_cost_ai_eur: 35 € – 80 €
created_by: claude-fable-5-1-verificacion-auditoria-harness-2026-09-17
spec_sections_referenced: []
docs_language: es
priority: P0
source_audit: auditoria-harness-ciclo-kb-memoria-2026-09-17.md
---

# Plan harness-01 — Contrato de ejecución versionado, dependencias y capacidades

## Cabecera

| Campo                 | Valor                                                      |
| --------------------- | ---------------------------------------------------------- |
| **ID del Plan**       | `harness-01-contrato-run-capacidades-2026-09-17`           |
| **Prioridad**         | P0                                                         |
| **Bloqueado por**     | `harness-00` (ADR 0168 aceptado y `task_h00_02` entregada) |
| **Rama git sugerida** | `plan/harness-01-contrato-run`                             |

> **Estado**: mantener `pending_human_validation` tras implementar hasta probar la
> compatibilidad orchestrator↔worker↔runtime en una instalación limpia.

## Resumen

Hoy el contrato entre orchestrator y worker es la dataclass `ExecutionRequest`
(`apps/workers/src/workers/run_contract.py`): sin `contract_version`, 20 campos
opcionales leídos con `raw.get`, y un orchestrator que **no usa el serializador**
sino que construye el dict a mano en `dispatch.py` y lo enriquece campo a campo.
Un campo desconocido pasa sin ruido; un campo crítico ausente relaja un control.
El proveedor «configurado» tampoco implica que el modelo sepa hacer tool-calling:
lo descubre el primer run que falla.

Este plan introduce `ExecutionEnvelopeV1` (pydantic, `extra="forbid"`, sellado con
digest), un contrato explícito de dependencias (`REQUIRED / OPTIONAL / FORBIDDEN`
→ `AVAILABLE / EMPTY / UNAVAILABLE / DEGRADED / STALE / FORBIDDEN`), un registro de
capacidades **observadas** con probes reales y una migración dual-read reversible.

## Hallazgos que cierra

| ID     |  Sev. | Hallazgo                                                                          | Evidencia                                                                      |
| ------ | ----: | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| H01-01 |  alta | Sin `contract_version` ni rechazo de campos desconocidos                          | `run_contract.py::from_dict`                                                   |
| H01-02 |  alta | Campos críticos opcionales degradan controles (allowlists, guardrails, política)  | `run_contract.py`; `dispatch.py` emite claves sólo «si no es None»             |
| H01-03 |  alta | Dos definiciones del contrato: el dataclass y el dict a mano del orchestrator     | `dispatch.py::_build_run_request` (lo cierra `task_h00_02`; aquí se consolida) |
| H01-04 |  alta | Proveedor configurado ≠ capacidad agéntica verificada                             | no hay probe de tool-calling; `remediacion-instalador` lo verificó a mano      |
| H01-05 | media | Sin semántica común REQUIRED/OPTIONAL/FORBIDDEN para KB, memoria, MCP, guardrails | `__main__.py` degrada todo a `[]`; `guardrails.py` a baseline                  |
| H01-06 | media | Sin digest integral del contexto de ejecución                                     | `executions.prompt_version` sólo sella prompt (gov-03)                         |

## Decisiones (fijadas por el ADR 0168; aquí se implementan)

1. El envelope es **estricto e inmutable** una vez sellado (`seal()` → digest SHA-256 del JSON canónico).
2. `extra="forbid"`: campo desconocido o mal tipado → el worker rechaza el mensaje con `skipped/invalid_envelope` y métrica.
3. `allowed_tools` obligatorio en V1: `[]` = ninguna, `['*']` = acceso total explícito. La migración de agentes sin allowlist la fija el ADR.
4. Requisito por dependencia: `REQUIRED | OPTIONAL | FORBIDDEN`; default `OPTIONAL` (mantiene el comportamiento actual salvo que el proyecto lo cambie).
5. Resultado por dependencia: `AVAILABLE | EMPTY | UNAVAILABLE | DEGRADED | STALE | FORBIDDEN`, persistido en `executions.dependency_report` (JSONB).
6. El Capability Registry registra **evidencia observada** (`verified`) separada de lo `declared`.
7. Dual-read en el worker durante una ventana; **un solo writer** (el orchestrator) por feature flag.

## Contrato mínimo (`ExecutionEnvelopeV1`)

```yaml
contract_version: 1
execution_id: uuid          # intención lógica (estable entre attempts)
attempt_id: uuid            # cada arranque del runtime (harness-02 lo usa)
claim_id: str               # ya existe; aquí es obligatorio
idempotency_key: str
traceparent: str|null       # harness-06 lo propaga
tenant_id / project_id / plan_id|null / task_id / agent_id|null
runtime:  {template_id, image_digest, sandbox_profile}
model:    {provider_id, model_id, required_capabilities: [tool_calling, …], credential_ref}
prompts:  {runtime_prompt_seal, agent_prompt_version: {prompt_hash, version}|null, skills_digest|null}
policy:   {guardrail_version|null, approval_policy_digest, failure_mode: fail_closed|baseline}
tools:    {catalog_digest, allowed_tools: [str], allowed_commands: [str], allowed_domains: [str], tool_specs: [..], mcp_servers: [..]}
knowledge:{requirement: REQUIRED|OPTIONAL|FORBIDDEN}
memory:   {requirement: REQUIRED|OPTIONAL|FORBIDDEN, write_mode: proposal_only}
budgets:  {wall_clock_s, iterations, input_tokens|null, output_tokens|null, cost_minor_units|null}
context:  {task, acceptance_criteria, predecessors, human_answers, prior_review_feedback, task_comments, prior_failure, agent_persona, review, review_context}
```

Los secretos **nunca** viajan dentro: sólo `credential_ref` (Vault) — coherente con
§«Dónde vive un secreto» de CLAUDE.md.

## Tareas

### Fase A — Inventario y schema

#### `task_h01_01` — Matriz de campos, productores y consumidores

- [ ] **Título**: inventariar cada campo de `ExecutionRequest` (quién lo produce en
      `dispatch.py`, quién lo consume en `execution.py` / `_agent_spec` /
      `__main__.run_task`) y su semántica `missing / null / []`. Incluir los campos
      que hoy sólo existen en el dict a mano del orchestrator y en los payloads de
      tests y CLI. Guardar en `docs/04-reference/execution-envelope-field-matrix.md`.
      **Test**: `tests/unit/test_execution_request_roundtrip_all_fields.py` (de
      `task_h00_02`) amplía su aserción: cada campo de la matriz existe en el
      dataclass y viceversa. **Coste**: 1 d.

#### `task_h01_02` — `ExecutionEnvelopeV1` en `packages/shared-domain`

- [ ] **Título**: modelo pydantic estricto en `packages/shared-domain/src/shared_domain/execution_envelope.py`
      (sin dependencias de BD ni Docker: lo importan orchestrator, worker y tests
      del runtime). `seal()` produce JSON canónico (claves ordenadas, sin
      espacios, UTF-8) y `envelope_digest` SHA-256; tras `seal()` el modelo es
      `frozen`. Secciones tipadas como en el contrato mínimo. Un campo fuera del
      schema → `ValidationError`.
      **Test**: `tests/unit/test_execution_envelope_v1.py` (rechaza extra, rechaza
      tipo erróneo, digest determinista, mutación tras seal falla, secretos no
      caben — sólo `credential_ref`). **Coste**: 2 d. **Depende de**: task_h01_01.

#### `task_h01_03` — Ausencia, `null` y vacío significan cosas distintas y declaradas

- [ ] **Título**: en V1, `allowed_tools`, `allowed_commands`, `allowed_domains` y
      `policy` son obligatorios; `predecessors`, `human_answers`,
      `prior_review_feedback`, `task_comments` son listas (vacías si no hay);
      `agent_persona`, `prior_failure`, `review_context` son `X | None` con `None`
      documentado. `ExecutionRequest.from_envelope()` y `to_legacy_dict()` para la
      ventana de compatibilidad.
      **Test**: `tests/unit/test_execution_envelope_rejects_ambiguous_defaults.py`.
      **Coste**: 1 d. **Depende de**: task_h01_02.

### Fase B — Dependencias y degradación

#### `task_h01_04` — Contrato de dependencias y `dependency_report`

- [ ] **Título**: enums `DependencyRequirement` y `DependencyState` en shared-domain.
      El worker resuelve **antes de lanzar el runtime**: guardrails
      (`_resolve_effective_guardrails`), modelo (provider habilitado y credencial
      resoluble), KB (backend responde a un ping barato), memoria, MCP declarados y
      política de aprobación. Cada uno produce un `DependencyState`. Una
      `REQUIRED` en `UNAVAILABLE` aborta con `abort_code=required_dependency_unavailable`
      sin crear contenedor; las `OPTIONAL` fallidas van a
      `executions.dependency_report` (migración aditiva, JSONB) y al preámbulo del
      agente («la KB no está disponible en este run»). Un timeout o 5xx **nunca**
      se representa como lista vacía: el runtime recibe el estado y sólo si es
      `AVAILABLE` llama al backend.
      **Test**: `tests/unit/test_dependency_contract.py`;
      `tests/integration/test_required_dependency_aborts_before_launch.py`;
      `tests/integration/test_optional_dependency_is_reported_not_hidden.py`.
      **Coste**: 1,5 d. **Depende de**: task_h01_02.

#### `task_h01_05` — Guardrails: fail-closed cuando hay política y no se resuelve

- [ ] **Título**: `_resolve_effective_guardrails` deja de degradar a `None` en
      silencio. Si el proyecto o el tenant **tienen** política configurada y la
      resolución falla → `DependencyState.UNAVAILABLE` sobre una dependencia
      `REQUIRED` → aborta (decisión 3 del ADR 0168). Si **no hay** política, baseline
      como hoy, pero declarado en `policy.failure_mode: baseline` del envelope. En
      el runtime, `build_pipeline` deja de correr «UNSCREENED»: si el envelope dice
      `fail_closed` y el motor no carga, el run termina con error tipado.
      **Test**: `tests/integration/test_guardrail_resolution_fail_closed.py`;
      `docker/agent-runtimes/agent-runtime/tests/test_guardrails_fail_closed.py`.
      **Coste**: 1 d. **Depende de**: task_h01_04.

### Fase C — Capability Registry y probes

#### `task_h01_06` — Registro de capacidades observadas

- [ ] **Título**: tabla `capability_observations` (tenant-aware; `subject_kind`:
      provider_model | runtime_template | knowledge_backend | mcp_server;
      `subject_ref`, `capability`, `declared: bool`, `verified: bool`,
      `observed_at`, `ttl_s`, `evidence` JSONB redacted, `config_digest`). Se
      invalida cuando cambia `config_digest` (imagen, modelo, base_url). Capacidades
      mínimas: `tool_calling`, `structured_output`, `streaming`, `context_window`,
      `embeddings`, `mcp_list_tools`, `runtime_available`.
      **Test**: `tests/integration/test_capability_registry.py` (RLS, TTL,
      invalidación por digest). **Coste**: 1,5 d.

#### `task_h01_07` — Probes reales y `python -m api_server.cli.doctor --capabilities`

- [ ] **Título**: probe autenticado por proveedor que, para modelos de agente,
      exige **una tool-call estructurada real sin efectos** (tool `noop_probe` con
      schema) y verifica structured output. Corre en instalación (`init_tenant`),
      en el boot del api-server (best-effort, en background) y antes de asignar
      una tarea que exige una capacidad **vencida**. Comando `doctor` con salida
      humana y `--format json`, reutilizando el patrón de `api_server.cli`. El
      dispatch rechaza asignar una tarea que exige `tool_calling` a un modelo cuya
      observación es `verified: false`, con motivo legible en la tarea.
      **Test**: `tests/integration/test_provider_capability_probe.py` (con
      `ScriptedModelClient`); `tests/unit/test_doctor_capabilities_output.py`.
      **Coste**: 2 d. **Depende de**: task_h01_06.

### Fase D — Migración y compatibilidad

#### `task_h01_08` — Dual-read, writer V1 por flag y retirada del dict a mano

- [ ] **Título**: `ORCHESTRATOR_EXECUTION_CONTRACT_VERSION` (`legacy` | `v1`,
      default `legacy` hasta validar). El orchestrator construye el envelope con
      un builder único (`build_execution_envelope`) y ya no muta el dict tras
      construirlo. El worker acepta V1 y legacy; métrica
      `worker_execution_payload_total{contract="legacy|v1"}` para saber cuándo
      retirar legacy. El runtime recibe **sólo** el spec derivado del envelope
      validado. Rollback = volver el flag a `legacy`; no hay columnas que perder
      (`dependency_report` es nullable).
      **Test**: `tests/integration/test_dispatch_emits_envelope_v1.py`;
      `tests/integration/test_worker_accepts_both_contracts.py`;
      `tests/unit/test_orchestrator_does_not_mutate_after_build.py`.
      **Coste**: 1,5 d. **Depende de**: task_h01_03, task_h01_04.

#### `task_h01_09` — Documentación, referencia y changelog

- [ ] **Título**: `docs/04-reference/execution-envelope-v1.md` (schema, semántica de
      cada estado, matriz producer/consumer, fecha de retirada de legacy,
      rollback); changelog en `docs/07-changelog/harness-01.md`; ADR 0168 enlaza.
      **Test**: `tests/unit/test_docs_governance.py` (estructura canónica).
      **Coste**: 0,5 d.

## Criterios de cierre

- [ ] Todo payload productivo lleva `contract_version` y `envelope_digest`.
- [ ] `claim_id` está en el serializador y en el round-trip (task_h00_02 + task_h01_01).
- [ ] Un campo extra o mal tipado rechaza el mensaje con código y métrica.
- [ ] Guardrail configurado y no resoluble bloquea el run; sin política, baseline declarado.
- [ ] Dependencias opcionales fallidas aparecen en `dependency_report` y en el preámbulo.
- [ ] Un modelo no verificado para `tool_calling` no recibe tareas que lo exigen.
- [ ] El probe hace una tool-call real.
- [ ] El uso de legacy es medible y el rollback está probado.

## Riesgos

1. **Romper workers antiguos**: dual-read y flag.
2. **Probes lentos o caros**: TTL y probe mínimo (una llamada, una tool sin efectos).
3. **Schema demasiado grande**: secciones tipadas y referencias por digest.
4. **Falsos negativos transitorios**: distinguir `UNAVAILABLE` (transitorio) de `verified: false` (estable).
5. **Secreto en el envelope**: test que rechaza cualquier valor con forma de credencial.

## Pruebas humanas

```yaml
- id: human_h01_01
  title: Inspección del envelope sellado
  steps:
    - Crear una tarea con KB, memoria, tools, feedback y predecessors.
    - Capturar el envelope antes de Celery y tras parsearlo en el worker.
    - Comparar digest y campos críticos.
  expected: Sin mutación ni pérdida; los secretos aparecen sólo como referencias.

- id: human_h01_02
  title: Capability probe con Ollama y proveedor remoto
  steps:
    - Configurar un modelo con tool-calling y otro sin.
    - Ejecutar `python -m api_server.cli.doctor --capabilities`.
    - Intentar asignar una tarea que exige tools a ambos.
  expected: Sólo el verificado la recibe; el rechazo explica la capacidad ausente.
```
