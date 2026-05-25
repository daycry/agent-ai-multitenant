---
adr: "0013"
title: Agent loop con LangGraph y captura de ejecuciones
status: accepted
date: 2026-05-22
deciders: System Architect, AI Engineer
phase: 02-ejecucion-agentes
---

# ADR 0013 — Agent loop con LangGraph y captura de ejecuciones

## Contexto

Plan 02 Fase C da vida al agente: el bucle de razonamiento que percibe
una tarea, planifica, actúa con tools, observa, reflexiona y entrega un
resultado. Hay que decidir:

1. Qué motor mueve el bucle y cómo se estructura.
2. Cómo se prueba un bucle agéntico de forma **determinista** y sin
   depender de un LLM real.
3. Cómo se captura y persiste lo que hizo el agente.
4. Dónde viven las salvaguardas y cómo se acota `max_review_retries`.

El bucle corre **dentro** del contenedor `agent-runtime` (Fase B); el
worker lo lanza y la plataforma persiste el resultado.

## Decisión

### Motor: LangGraph, ocho nodos

El bucle es un `langgraph.StateGraph` (`agent_runtime/graph.py`) con
ocho nodos — `perceive → recall → plan → act → observe → reflect →
finalize → self_review` — sobre un estado `AgentState` (un `TypedDict`
con _reducers_ `operator.add` para las listas acumulativas: `context`,
`reflections`, `steps`).

`plan` es el nodo bisagra: comprueba las salvaguardas, pide al modelo la
siguiente acción y ejecuta la detección de loops. El modelo decide
cuándo terminar; `self_review` puede devolver la salida para otra
pasada, acotado por `max_review_retries`.

LangGraph (no un bucle a mano) por mandato del roadmap: madurez,
_checkpointing_ y ecosistema. Sobre LangGraph se construirá la
reutilización inter-paso del pool elástico (Plan 06).

### Inyección de dependencias y modelo determinista

El bucle nunca importa un SDK de LLM. Depende de `AgentDeps` —
`ModelClient`, `ToolRegistry`, función de `recall`— inyectado. Eso hace
el bucle **ejecutable offline y determinista**: los tests lo mueven con
`ScriptedModelClient`, que reproduce una secuencia fija de decisiones y
revisiones. Los clientes reales (Azure AI Foundry vía APIM, GitHub
Copilot, Claude Agent SDK, Ollama — catálogo cerrado de ADR 0021;
LiteLLM se retiró) se enchufan detrás del protocolo `ModelClient`
como adaptadores delgados sobre `packages/shared-llm`, sin tocar el
grafo.

Ningún test de Fase C llama a un LLM real: un bucle agéntico probado
contra un modelo no determinista no es un test, es una apuesta.

### Captura: `steps_log` JSONB

Cada nodo, llamada al modelo, llamada a tool y lectura de memoria añade
un _step_ al `AgentState["steps"]`. La lista se persiste tal cual en la
columna `executions.steps_log` (JSONB). Tipos de step: `node`,
`model_call` (con tokens y coste), `tool_call` (con args y resultado),
`memory_read` (placeholder hasta Plan 04). `agent_runtime/capture.py`
trocea el `steps_log` por tipo y recalcula el _roll-up_ de uso —
contraste independiente frente al `SafeguardTracker` del bucle.

La tabla `executions` (migración 0010) es **separada de `tasks`**: una
tarea puede tener varias ejecuciones (reintentos). Las columnas
`total_*` / `*_count` son _roll-ups_ denormalizados para que un panel no
tenga que escanear el `steps_log`.

`api-server` **no importa** `agent_runtime`: el `steps_log` es JSONB
opaco aquí y `record_execution` está _duck-typed_ sobre el resultado del
bucle. El runtime es un paquete _container-side_; mantenerlos
desacoplados es la misma filosofía del ADR 0011.

### Salvaguardas y `max_review_retries`

Cada ejecución corre contra un `Budgets`: `max_iterations`,
`max_tokens`, `max_cost`, `max_wall_clock`, `max_tool_calls`,
`max_review_retries`. `SafeguardTracker` acumula el uso; al romperse un
presupuesto el bucle aborta con un `SafeguardCode` específico, que se
persiste en `executions.abort_code`. El presupuesto de iteraciones se
comprueba **antes** de contar el turno, así una ejecución terminada
reporta un recuento honesto.

`max_review_retries` es un **límite duro de plataforma** (spec §7.9,
default 3): un tenant no puede aflojarlo. Vive en la tabla global
`platform_settings` (migración 0011, sin `tenant_id`, sin RLS) y solo un
System Admin puede escribirla (`db/platform_settings.py` lo verifica).

La detección de loops (`LoopDetector`) marca _fingerprint_ de cada
acción (tool + args, independiente del orden de claves) y aborta cuando
una se repite **más de** 3 veces.

### `agent_runtime` como paquete instalable

`docker/agent-runtimes/agent-runtime/` pasa a ser un paquete Python
instalable (`pyproject.toml`). La imagen `agent-runtime` lo instala con
`pip install` en vez de copiar el fuente; los tests lo instalan
`-e`. Una única fuente de verdad para sus dependencias.

## Alternativas descartadas

1. **Bucle agéntico a mano** en vez de LangGraph. Rechazado por
   mandato del roadmap; además LangGraph aporta _checkpointing_ y la
   base de la reutilización inter-paso (Plan 06).
2. **LLM real en los tests.** Rechazado: no determinista, lento, con
   coste y dependiente de red. `ScriptedModelClient` da cobertura
   completa del grafo, la captura y las salvaguardas, offline.
3. **`steps_log` como filas en una tabla `steps`.** Rechazado: una
   ejecución son decenas de pasos; JSONB es una escritura, evoluciona
   sin migración y encaja con la decisión "sin escrituras constantes en
   BD" (los logs en vivo van por Redis Streams, ADR 0011 / Fase E).
4. **`api-server` importando `agent_runtime`** para tipar el resultado
   del bucle. Rechazado: acopla el servicio al paquete _container-side_.
   El `steps_log` es JSONB opaco; `record_execution` es _duck-typed_.
5. **`max_review_retries` como ajuste por tenant.** Rechazado: es una
   salvaguarda de coste de plataforma; un tenant no debe poder
   desactivarla. De ahí `platform_settings` y el _gate_ a System Admin.

## Consecuencias

Positivas:

- Bucle agéntico determinista y verificable: 47 tests de Fase C en
  verde sin tocar un LLM.
- Captura completa y persistente de cada ejecución — base directa del
  Timeline de Ejecución (Fase E, task_02_22).
- Salvaguardas con código de aborto específico y presupuesto de
  revisión acotado por plataforma.
- `agent_runtime` desacoplado de `api-server`; el contrato entre ambos
  es JSONB opaco.

Negativas / cuidados:

- El nodo `recall` y los _steps_ `memory_read` son **placeholders**
  hasta Plan 04 (memoria + RAG).
- `self_review` usa el modelo de forma simple (pasa/no pasa); una
  rúbrica de revisión más rica queda para una iteración posterior.
- El `ScriptedModelClient` no ejercita el _prompting_ real; la calidad
  del bucle con un LLM de verdad se valida en los tests humanos del
  plan (`human_02_01`).
- `recursion_limit` de LangGraph se dimensiona a partir de los
  presupuestos; las propias salvaguardas terminan el grafo mucho antes.

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — Fase C, task_02_10..14.
- Bucle: `docker/agent-runtimes/agent-runtime/agent_runtime/`
  (`graph`, `state`, `steps`, `model`, `tools`, `safeguards`,
  `loop_detection`, `capture`).
- Persistencia: `apps/api-server/.../db/{domain,execution_repo,
platform_settings}.py`, migraciones 0010 y 0011.
- Tests: `tests/unit/test_agent_graph.py`, `test_loop_detection.py`;
  `tests/integration/test_steps_log.py`, `test_execution_capture.py`,
  `test_safeguards.py`, `test_max_review_retries_scope.py`.
- ADR 0011 (bus de eventos) y ADR 0012 (aislamiento de contenedores).
- Documento maestro, secciones 7.9, 12 y 13.
