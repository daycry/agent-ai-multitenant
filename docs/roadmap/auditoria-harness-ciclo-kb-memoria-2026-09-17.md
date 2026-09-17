---
status: informe
title: Auditoría del agent harness — ciclo de ejecución, KB y memoria (verificada contra el código)
date: 2026-09-17
branch_analyzed: plan/instalador-runs-de-serie-2026-09-09 (acbe24c8, 2026-09-10)
supersedes: docs/new_feature/00-AUDITORIA-BASE-CICLO-KB-MEMORIA.md (borrador externo del 2026-09-16)
docs_language: es
---

# Auditoría del agent harness — verificada contra el código (2026-09-17)

Este informe reemplaza el borrador externo `00-AUDITORIA-BASE-CICLO-KB-MEMORIA.md`
(2026-09-16). Se conserva su tesis —**el ciclo externo de tareas está defendido;
el ciclo interno del agente no es durable**— y se **corrigen** las afirmaciones que
no aguantaron la verificación fichero a fichero. Cada fila lleva la evidencia
para que quien abra un plan hijo pueda comprobarla en un minuto.

## Matriz de verificación

| #   | Afirmación del borrador                                                                    | Veredicto                                                                                                                                                                                                       | Evidencia (rama `plan/instalador-runs-de-serie-2026-09-09`)                                                       |
| --- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1   | El contrato no lleva versión, sus campos son opcionales y la compat se apoya en `dict.get` | **Cierto**                                                                                                                                                                                                      | `apps/workers/src/workers/run_contract.py` — dataclass `ExecutionRequest`, `from_dict` con `raw.get` en 20 campos |
| 2   | El payload se muta tras serializarse                                                       | **Cierto, y más**: el orchestrator no llama a `as_dict()`; construye el dict a mano y lo enriquece con `request["…"] = …`                                                                                       | `apps/orchestrator/src/orchestrator/dispatch.py` (`_build_run_request` y `request["claim_id"] = claim_id`)        |
| 3   | `claim_id` existe en el modelo y puede quedar fuera del serializador canónico              | **Cierto**: el campo existe, `from_dict` lo lee y `as_dict()` **no lo emite**. El test existente (`test_la_reclamacion_viaja_con_identidad.py`) sólo cubre la lectura                                           | `run_contract.py` campo `claim_id`; `as_dict()` sin la clave                                                      |
| 4   | `allowed_tools` ausente = sin restricción                                                  | **Cierto**, pero es diseño documentado (06.14), no descuido. Cambiarlo es una decisión, no un fix                                                                                                               | `run_contract.py` comentario del campo; `agent_runtime/tools.py::ToolRegistry`                                    |
| 5   | El grafo se compila sin checkpointer                                                       | **Cierto**                                                                                                                                                                                                      | `agent_runtime/graph.py` — `return graph.compile()` sin argumentos                                                |
| 6   | `SafeguardTracker` y `LoopDetector` nuevos por run                                         | **Cierto**                                                                                                                                                                                                      | `agent_runtime/graph.py::run_agent`                                                                               |
| 7   | Auto-RAG y auto-recall degradan cualquier excepción a `[]`                                 | **Cierto**                                                                                                                                                                                                      | `agent_runtime/__main__.py` — dos `except Exception: return []` con el comentario «nunca rompe el run»            |
| 8   | La memorización post-run es fire-and-forget y traga errores del broker                     | **Cierto**                                                                                                                                                                                                      | `apps/workers/src/workers/execution.py` (`trigger_memorize`), `workers/memorizer.py::trigger_memorize`            |
| 9   | Los guardrails tienen fallback silencioso                                                  | **Cierto en dos capas**: el worker degrada a `None` (`_resolve_effective_guardrails`) y el runtime cae al baseline LOG o corre «UNSCREENED» con un warning (`build_pipeline`)                                   | `execution.py`, `agent_runtime/guardrails.py`                                                                     |
| 10  | `ToolResult` no expresa clase de error ni incertidumbre                                    | **Cierto**: `ok / output / error:str`                                                                                                                                                                           | `agent_runtime/tools.py`                                                                                          |
| 11  | No hay outbox, checkpoints ni ledger de efectos                                            | **Cierto**                                                                                                                                                                                                      | ninguna migración en `apps/api-server/migrations/versions` los menciona                                           |
| 12  | No hay `traceparent` a través de Celery → worker → runtime                                 | **Cierto**: OpenTelemetry sólo instrumenta la api-server                                                                                                                                                        | `api_server/telemetry/setup.py`; cero `opentelemetry` en `apps/workers` y `apps/orchestrator`                     |
| 13  | Un redelivery puede repetir un efecto externo                                              | **Cierto**; el worker ya cierra la fila huérfana (`supersede_running_executions`), descarta claims obsoletos (`_claim_is_current`) y salta tareas no lanzables, pero nada de eso revierte una tool ya ejecutada | `execution.py`                                                                                                    |
| 14  | La aprobación humana no está ligada al efecto exacto                                       | **Falso — ya implementado**: ADR 0135 canjea aprobaciones por huella `tool + args` de un solo uso                                                                                                               | `agent_runtime/approval.py` (`args_hash`, `_redeem`, `action_fingerprint`)                                        |
| 15  | Falta deduplicación y supersesión de memoria                                               | **Parcial**: el dedup lo cerró 06.7 (`completed`). Faltan `supersedes`, `expires_at`, `confidence`, `verification_status` y `source_kind` en `MemoryEntry`                                                      | `api_server/db/memory.py`                                                                                         |
| 16  | La KB no versiona documento, chunker ni embedding                                          | **Parcial**: `Chunk` ya lleva `embedding_model_id`; faltan `chunker_version`, versión de documento y digest de fuente                                                                                           | `api_server/db/knowledge.py`                                                                                      |
| 17  | Los hits de RAG traen procedencia sólo parcial                                             | **Cierto**: `chunk_id`, `document_id`, `rrf_score`, `rerank_score`; y `hits=[]` (200) también cuando el agente no tiene proyecto                                                                                | `api_server/routers/internal_agent.py`                                                                            |
| 18  | Un eval en verde puede no haber evaluado nada                                              | **Parcial — gov-01 ya lo separó**: sin secreto corre `--dry-run`; con secreto el gate vivo sale 0/1/2 (INCONCLUSIVE ≠ verde). Queda que el dry-run sale 0 y el job aparece verde                                | `.github/workflows/eval-on-prompt-change.yml`                                                                     |
| 19  | No hay guardrail de prompt injection para KB                                               | **Parcial**: el check existe (`shared_guardrails/checks/prompt_injection.py`); falta delimitar los fragmentos recuperados como contenido no confiable                                                           | `packages/shared-guardrails`                                                                                      |
| 20  | El plan `memoria-agentes-2026-09-09.md` debe reconciliarse por delta                       | **Cierto y urgente**: existe, `approved`, 0/7 casillas; seis de las siete tareas del borrador harness-05 lo duplicaban                                                                                          | `docs/roadmap/memoria-agentes-2026-09-09.md`                                                                      |

## Lo que el borrador daba por existente y no existe

Las tareas del borrador nombraban infraestructura que no está en el repo. Los
planes corregidos o la crean como tarea explícita o la sustituyen por lo que sí hay:

| Asumido                                                                                        | Realidad                         | Qué hacen los planes corregidos                                                                                                         |
| ---------------------------------------------------------------------------------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/validate_roadmap.py`                                                                  | No existe                        | Los validadores son `tests/unit/test_docs_governance.py`, `test_declared_tests_exist.py`, `test_roadmap_frontmatter.py`                 |
| `docs/03-architecture/`                                                                        | No existe (7 carpetas canónicas) | Referencias en `docs/04-reference/`, decisiones en `docs/05-architecture-decisions/`                                                    |
| CLI `agentic doctor`                                                                           | No existe                        | Se crea como `python -m api_server.cli.doctor` (harness-01)                                                                             |
| `tests/property`, `tests/chaos`, `tests/contract`, `tests/ci`, `tests/observability`, `evals/` | No existen                       | Se usan `tests/unit`, `tests/integration`, `tests/security`, `tests/e2e` (runner Docker) y `docker/agent-runtimes/agent-runtime/tests/` |
| `blocking_plan: harness-01-…` (cadena)                                                         | CLAUDE.md exige **lista YAML**   | Corregido en todos los planes                                                                                                           |

## Decisiones de producto que el borrador presentaba como bugs

Tres cosas son **diseño deliberado y documentado en el código**, y cambiarlas
exige un ADR antes que un plan (cadena de precedencia de CLAUDE.md):

1. «La memoria nunca rompe el run» y «la KB nunca rompe el run» (`__main__.py`).
   Los planes harness-01/04/05 introducen `REQUIRED` / fail-closed. Es legítimo,
   pero es un cambio de política.
2. `allowed_tools=None` = sin restricción (06.14). Hacerlo obligatorio rompe la
   retrocompatibilidad con agentes sin allowlist.
3. Guardrails best-effort con baseline (ADR 0102 D3). Pasar a fail-closed cuando
   hay una política configurada y no resoluble es razonable; hacerlo siempre no.

Las tres se resuelven en el **ADR 0168** (task_h00_01 del programa), antes de
tocar código.

## Invariantes que el programa impone (sin cambios respecto al borrador)

1. Un mensaje Celery obsoleto no inicia efectos.
2. Un reinicio del runtime reanuda desde un checkpoint válido, no desde cero.
3. Presupuestos, detector de bucles y feedback sobreviven a la reanudación.
4. Un efecto confirmado no se repite por redelivery.
5. Un efecto de resultado incierto se reconcilia antes de volver a ejecutarse.
6. «No hay resultados» y «la dependencia está caída» son estados distintos.
7. Todo fragmento de KB o memoria que ve el modelo queda identificado y versionado.
8. Ninguna memoria se promociona a conocimiento confiable sólo porque la propuso un agente.
9. Toda escritura post-run relevante usa outbox durable e idempotencia.
10. Un replay reconstruye contrato, modelo, prompts, tools, checkpoints, conocimiento y efectos.
11. Un test verde de evaluación significa que el comportamiento fue evaluado de verdad.
12. Ningún cierre de plan se basa sólo en casillas: enlaza evidencia ejecutada.

## Límite de esta auditoría

Es una revisión estática. Antes de cambiar cualquier `status:` de los planes hijos
a `completed` hay que ejecutar, al menos: instalación limpia; run real con
tool-calling; kill del runtime en varios nodos; caída temporal de API interna,
Redis y backend de conocimiento; redelivery alrededor de una escritura externa;
prueba adversarial cross-tenant; replay de una ejecución terminada.
