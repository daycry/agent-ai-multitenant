---
title: Refactorización por partes del pipeline de runs (+revisión de prompts de sistema)
date: 2026-07-07
status: completed
completed_at: 2026-07-08
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

### P3 — `workers/execution.py`: trocear `conduct_execution` en fases nombradas ✅ HECHA

(2026-07-08, 1535ab5) La god-function de 518 líneas es un orquestador fino de 5 fases en el
MISMO módulo (los monkeypatch por string siguen resolviendo): `_prepare_run` (txn inicial) →
`_provision_workspace` (git fuera de txn) → `_launch_and_stream` (docker+Redis+cancel) →
`_finalize_and_transition` (txn atómica P0.5) → `_implementer_post_process` (commit/tests +
publicación diferida prod-18). Dataclasses `_PreparedRun`/`_Workspace` como salida de fase;
fail-fasts y guarda F12 en el orquestador. 2050 unit + 72 integración verdes sin tocar aserciones.

### P4 — `orchestrator/dispatch.py`: builder común del payload implementador/reviewer

✅ **HECHA** en dos tramos: quick win H2 (2026-07-08, cb96ad0 — la rama reviewer llama a
`_resolve_model_spec`, con test de caracterización de la herencia) y el builder común
(2026-07-08, 944997f — `_assemble_run_request` concentra tools/skills/budgets/dict
base/claves opcionales/threading de proyecto; cada rama añade solo sus claves específicas).
El `model_spec` llega al builder ya resuelto (el implementador lo valida ANTES del claim
atómico — C3 F07). 114 tests de dispatch verdes sin tocar aserciones.

### P5 — agent-runtime `graph.py`: extraer módulos puros ✅ HECHA

(2026-07-08, bf68379) Extraídos `tool_classification.py` (clasificación research/producing/
read-only + `_is_platform_error`), `nudges.py` (5 nudges + `_research_exhausted` + umbrales) y
`review_harvest.py` (harvest acotado + `_referenced_paths`). `graph.py` 1639→1242 líneas, queda
con el bucle (`_AgentLoop` + nodos + wiring) y re-exporta lo movido (los tests siguen importando
de graph). El estado mutable por-run de `_AgentLoop` NO se tocó (deuda H6). Composición
byte-idéntica de los textos. 244 tests del runtime verdes. Requiere rebuild de la imagen
agent-runtime al desplegar (receta WITH_CLAUDE=1).

### P6 — agent-runtime `providers.py`: consolidar `decide()`/`review()` ✅ HECHA

(2026-07-08, 78ca74d) `ClaudeSDKModelClient` hereda de `_ProviderModelClient`; dos flags de
clase (`_advertises_submit_result`, `_forces_verdict_choice`) parametrizan la única diferencia
real de protocolo. Tests de caracterización del protocolo del SDK añadidos ANTES de consolidar.

### P7 — prompts de sistema: contrato `<verdict>` + fencing ✅ HECHA

(2026-07-08, 3595b9a + 4f4fe80 + e436a9a) — CAMBIA los prompts de los runs; **pide QA e2e de un
ciclo review al desplegar** (rebuild agent-runtime WITH_CLAUDE=1):

- `agent_runtime/review_contract.py` = fuente única del tag `<verdict>` (los 5 sitios interpolan;
  composición byte-idéntica). El contrato cruzado runtime↔worker lo ata
  `tests/unit/test_review_verdict_wire_contract.py` (lo anunciado == lo que parsea
  `parse_reviewer_output`).
- **Fencing anti-injection**: review_context/feedback/comentarios viajan en un fence
  `<<<UNTRUSTED_DATA … UNTRUSTED_DATA>>>` con aviso datos-no-órdenes y neutralización de
  marcadores embebidos; el volcado del workspace en `_review_messages` se marca como datos.
- Menores: caps de PROGRESS nombrados (`_PROGRESS_FILES_MAX`/`_PROGRESS_DIGESTS_MAX`),
  `output_override` del stalemate en inglés, contrato de claves de estado documentado en
  `state.py` (mitigación H6; la dataclass de estado queda como deuda).

### P8 — `db/domain.py` vs `db/models.py` — NO abordar ahora (documentado)

El reparto es histórico (fase 0 vs fase 1), con agregados partidos (Task/`TaskAuditEvent`,
Plan/`ReviewSession`) y 170+103 ficheros importadores. Beneficio moderado, radio de impacto
enorme. Si algún día se unifica: shim de re-export y de-precación gradual, nunca big-bang.

## Hallazgos de la revisión (implementación + prompts de sistema)

Revisión hecha con 3 agentes de mapeo (2026-07-07) sobre `execution/tasks`, `graph/providers/
__main__` del agent-runtime y `dispatch/domain/models`. **Corregidos el 2026-07-08** (orden del
operador «revisa y corrige los hallazgos»): H1→`4f4fe80`, H2→`cb96ad0`, H3→`3595b9a`,
H4→`78ca74d`, H5+H6→`e436a9a`. Queda H6-real (dataclass de estado) como deuda anotada y la
fusión de los dos canales de veredicto como decisión aparte.

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
| P3    | conduct_execution por fases | alto                   | ✅ hecha (2026-07-08)    |
| P4    | dispatch: builder común     | medio                  | ✅ hecha (2026-07-08)    |
| P5    | graph: módulos puros        | medio                  | ✅ hecha (2026-07-08)    |
| P6    | providers: decide/review    | medio                  | ✅ hecha (2026-07-08)    |
| P7    | prompts: verdict + fencing  | medio (cambia prompts) | ✅ hecha (2026-07-08)    |
| P8    | domain vs models            | —                      | no abordar (documentado) |
