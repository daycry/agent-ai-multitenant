---
title: Refactorización por partes del pipeline de runs (+revisión de prompts de sistema)
date: 2026-07-07
status: in_progress
owner: operador (jmano)
branch: plan/runs-visor-trabajo
---

# Refactorización por partes — plan y estado

Petición del operador (2026-07-07): _"refactorización del código por partes, para no hacer un
refactor inmenso; de paso, revisa la implementación y los prompts de sistema"_.

Método por parte: **caracterizar → mover → verde → commit** (un commit por parte, sin big-bang,
comportamiento intacto salvo donde se indique). Los cuatro hotspots por tamaño×churn son los del
pipeline de runs:

| Módulo                                                       | Líneas | Problema                                                                  |
| ------------------------------------------------------------ | ------ | ------------------------------------------------------------------------- |
| `apps/workers/src/workers/execution.py`                      | 1768   | god-function `conduct_execution` (517 L) + 11 clusters mezclados          |
| `docker/agent-runtimes/agent-runtime/agent_runtime/graph.py` | 1639   | bucle del grafo + clasificación de tools + nudges + harvest en un fichero |
| `apps/workers/src/workers/maintenance.py`                    | 1636   | 10 tareas beat sin relación en un cajón de sastre                         |
| `apps/orchestrator/src/orchestrator/dispatch.py`             | 1458   | payload del implementador y del reviewer duplicado (~150 L)               |

Riesgos transversales que TODAS las partes deben respetar (verificados por mapeo):

1. **Nombres Celery = contrato de wire**: `beat_schedule`/orchestrator/api-server encolan por
   string (`workers.run_execution`, `workers.compose_review_runtime`, …). Mover un `@app.task`
   exige mantener el `name=` idéntico Y que su módulo siga importándose en el boot
   (`celery_app.imports` / façade de paquete).
2. **Monkeypatches de tests = lookup site**: los tests parchean atributos de módulo
   (`workers.execution._provision_worktree`, `workers.tasks._run_test_runtime`, `m.app`…). Un
   símbolo movido debe seguir siendo global del módulo donde su caller lo busca, o el test debe
   actualizarse en el mismo commit.
3. **Gate mypy de pre-commit excluye por path**: cualquier módulo nuevo que importe `api_server.*`
   debe añadirse al regex `exclude` de `.pre-commit-config.yaml` en el mismo commit (el venv del
   hook no ve paquetes hermanos). Los módulos extraídos PUROS (sin `api_server`) entran al gate
   gratis — es una ganancia buscada.
4. **Sin desplegar**: todo queda en `plan/runs-visor-trabajo`; el rebuild de workers espera la
   ventana del operador (re-entrega ~7 h con runs en vuelo).

## Partes

### P1 — `workers/maintenance.py` → paquete de submódulos enfocados ✅ HECHA

Split en `workers/maintenance/` (9 submódulos: `cleanup`, `review_runtimes`, `memory_backfill`,
`dag_promotion_beat`, `stale_sweeper`, `budget_sweep`, `queue_sampler`, `reconciler`,
`worktree_backfill`) + façade `__init__.py` que preserva la superficie importable y el registro
Celery. Test de caracterización nuevo (`tests/unit/test_maintenance_package_surface.py`) fija los
dos contratos. 54 tests (31 unit + 23 integración) verdes sin tocar aserciones.

### P2 — `workers/execution.py`: extraer los clusters puros ✅ HECHA

Extraídos `workers/run_contract.py` (CrossTenantExecutionError + ExecutionRequest/Outcome, el
contrato de wire), `workers/run_result.py` (`_parse_line`, `_scan_logs_for_terminal`,
`_assemble_result`, `_RuntimeResult`, `_EMPTY_USAGE`) y `workers/run_spec.py` (`_agent_spec`,
`_resolve_tool_spec_images`, `_SDK_BASE_SHELL_COMMANDS`). Los tres son puros y **entran al gate
mypy** (antes nada de esto se type-checkeaba). `execution.py` queda en 1273 líneas y re-exporta
todo (superficie intacta); `_build_runtime_env` se queda (mintea el token interno, api_server);
`_load_project`/`_persist_guardrail_events`/`_provision_worktree` no se mueven (lookup sites de
monkeypatch). 2047 unit + 109 integración verdes.

De paso se arregló un rojo PREEXISTENTE de la rama: 2 tests de `test_run_tools_by_stack.py`
afirmaban el contrato viejo de `tool_wiring` (raise) que `602a24b` cambió a skip+warning; se
re-afirmaron al contrato vigente + test nuevo del raise dispatch-side.

### P3 — `workers/execution.py`: trocear `conduct_execution` (517 L) en fases nombradas

Extraer métodos/funciones por fase (provisión → lanzamiento → streaming → finalize →
post-proceso: commit/tests/budgets/memorize) manteniendo `conduct_execution` como orquestador
fino. La suite de integración existente es la red; añadir tests de fase solo donde falte
cobertura. Es la parte más delicada — hacerla sola, sin mezclar con P2.

### P4 — `orchestrator/dispatch.py`: builder común del payload implementador/reviewer

La rama reviewer (`_build_review_request`) **duplica inline la cadena de herencia de modelo** en
vez de llamar a `_resolve_model_spec` (riesgo real de divergencia), y repite budgets, dict base,
claves opcionales (`allowed_tools`/`tool_specs`/`skill_prompt_fragments`) y threading de proyecto
del implementador (`_route_ai`). Extraer un constructor compartido; primero que el reviewer llame
a `_resolve_model_spec` (quick win). Los tests de integración no parchean internos → seguro
mientras se preserven `TaskDispatcher(...)`, `.handle()`, nombres de tarea y shape del request.
Los alias re-importados por unit tests (`_COMPOSE_REVIEW_RUNTIME_TASK`, `_REVIEW_QUEUE`,
`_DISPATCH_EVENT_TASK`, `publish_task_status_changed`) deben seguir en `dispatch`.

### P5 — agent-runtime `graph.py`: extraer módulos puros

- `tool_classification.py`: `_RESEARCH_TOOLS`/`_PRODUCING_TOOLS`/`_READONLY_TOOLS`,
  `_base_tool_name`, `_read_target`, predicados, `_is_platform_error`.
- `nudges.py`: los 5 nudges + `_research_exhausted` + umbrales.
- `review_harvest.py`: `_workspace_root`, `_harvest_worktree_files`, `_referenced_paths`,
  `_deliverable_summary`.
- `graph.py` re-exporta (los tests del runtime importan `_AgentLoop`, `_loop_trip_outcome`,
  umbrales y nudges directamente). El estado mutable por-run de `_AgentLoop` (read_targets,
  has_produced, safeguard_stats…) NO se toca en esta parte.
- Requiere rebuild de la imagen agent-runtime para desplegar (receta WITH_CLAUDE=1) — solo
  cuando el operador abra ventana.

### P6 — agent-runtime `providers.py`: consolidar `decide()`/`review()`

`_ProviderModelClient` y `ClaudeSDKModelClient` duplican los cuerpos de `decide`/`review`
(mismo patrón retry+mensajes+parseo). Parametrizar la base con «advertir `submit_result`» y
«call kwargs» y dejar en la subclase claude_sdk solo credencial dual, `max_turns`, effort y el
no-tool-choice. Los 4 providers quedan como ya están (duplicación baja).

### P7 — prompts de sistema: única fuente del contrato `<verdict>` + fencing del review_context

Cambia comportamiento (texto de prompts) — validar con e2e de review, no solo unit:

- El formato del tag `<verdict>` vive en **5 sitios** (`_REVIEW_VERDICT_INSTRUCTION`,
  `_REVIEW_RUN_SYSTEM` y 3 nudges de review) → constante única compartida.
- **Fencing del `review_context`** (hallazgo de seguridad, ver §Hallazgos): delimitar
  `implementer_output`/`test_report`/criterios como DATOS en el preámbulo del reviewer.
- Números mágicos del texto: `12` de `_progress_summary` a constante; `output_override` de
  `_loop_trip_outcome` en español → inglés (consistencia con el resto de summaries).

### P8 — `db/domain.py` vs `db/models.py` — NO abordar ahora (documentado)

El reparto es histórico (fase 0 vs fase 1), con agregados partidos (Task/`TaskAuditEvent`,
Plan/`ReviewSession`) y 170+103 ficheros importadores. Beneficio moderado, radio de impacto
enorme. Si algún día se unifica: shim de re-export y de-precación gradual, nunca big-bang.

## Hallazgos de la revisión (implementación + prompts de sistema)

Revisión hecha con 3 agentes de mapeo (2026-07-07) sobre `execution/tasks`, `graph/providers/
__main__` del agent-runtime y `dispatch/domain/models`.

1. **[seguridad, media] Prompt injection en el preámbulo del reviewer** —
   `agent_runtime/__main__.py:build_review_preamble` inserta `implementer_output`,
   `acceptance_criteria` y `test_report` SIN fencing en el system prompt del reviewer. El output
   del implementador es influenciable por el contenido del repo/tarea → texto atacante en la
   posición de mayor privilegio. Igual patrón (más débil) en `_review_messages` (volcado de
   ficheros del worktree) y `_decide_messages` (título/descripción de la task). Los guardrails
   post_tool no escanean el review_context. → **P7**.
2. **[divergencia, media] El reviewer re-deriva el modelo inline** en `_build_review_request`
   en vez de llamar a `_resolve_model_spec` — un cambio futuro en la cadena de herencia
   (plataforma→proyecto→equipo→agente) solo se aplicaría al implementador. → **P4**.
3. **[duplicación] Contrato `<verdict>` en 5 sitios**; dos canales de veredicto conviven
   (tag en prosa para el run reviewer vs tool `submit_verdict` en la self-review) — coherentes
   hoy, pero cualquier cambio exige tocar todos a la vez. → **P7** (unificar la constante;
   fusionar canales es decisión aparte).
4. **[duplicación] decide/review duplicados** entre las dos jerarquías de model-client del
   runtime. → **P6**.
5. **[menor] Números mágicos**: `12` (ficheros listados en PROGRESS) sin constante;
   `output_override` de `_loop_trip_outcome` en español (resto de summaries en inglés). → **P7**.
6. **[deuda] Doble estado por-run** en el runtime: parte en `AgentState` (TypedDict compartido
   por claves string entre `graph.py` y `providers.py`) y parte en la instancia `_AgentLoop`.
   Renombrar una clave rompe en silencio. Mitigación barata: comentario-contrato + constantes de
   clave; solución real (dataclass de estado) queda fuera de este plan.
7. **[ok] Prompts del run**: tras F35/ADR 0087-0095 el contrato implementador/reviewer es
   coherente entre providers (submit_result para HTTP, prosa+`<finish>` para claude_sdk);
   idioma consistente EN para el modelo. Los seeds de agentes (ES+EN) y los prompts del córtex
   no presentan problemas nuevos.

## Estado

| Parte | Alcance                     | Riesgo                 | Estado                   |
| ----- | --------------------------- | ---------------------- | ------------------------ |
| P1    | maintenance → paquete       | bajo                   | ✅ hecha (2026-07-07)    |
| P2    | execution: clusters puros   | bajo                   | ✅ hecha (2026-07-07)    |
| P3    | conduct_execution por fases | alto                   | pendiente                |
| P4    | dispatch: builder común     | medio                  | pendiente                |
| P5    | graph: módulos puros        | medio                  | pendiente                |
| P6    | providers: decide/review    | medio                  | pendiente                |
| P7    | prompts: verdict + fencing  | medio (cambia prompts) | pendiente                |
| P8    | domain vs models            | —                      | no abordar (documentado) |
