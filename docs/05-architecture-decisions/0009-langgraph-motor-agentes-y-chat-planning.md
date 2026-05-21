---
adr: "0009"
title: LangGraph como motor del agent loop y del supervisor multi-agente en chat Planning
status: accepted
date: 2026-05-21
deciders: System Architect
phase: 02-ejecucion-agentes
---

# ADR 0009 — LangGraph como motor del agent loop y del supervisor multi-agente en chat Planning

## Contexto

La plataforma necesita un motor de máquinas de estado con persistencia y
human-in-the-loop en **dos puntos** del sistema, ambos críticos:

1. **Agent loop interno** de cada agente (Plan 02, Fase C). Grafo de 8 nodos
   `perceive → recall → plan → act → observe → reflect → finalize → self_review`
   dentro del contenedor `agent-runtime`. Requiere checkpointing, salvaguardas,
   detección de loops repetitivos y captura completa de `steps_log`.
2. **Supervisor multi-agente** del modo Planning del chat (Plan 03). El PM
   agente actúa como portavoz único; otros agentes intervienen cuando aportan
   valor. El usuario pulsa "Generar Plan" cuando el equipo considera cerrada
   la propuesta.

`docs/context/tech-stack.md` y ambos planes de roadmap ya señalan LangGraph como
opción. Conviene formalizarla porque la decisión es **acumulativa** (afecta a
dos fases en cascada) y **de reversión costosa** (40–100 h según alternativa).
La pregunta no es solo "¿LangGraph?" sino también "¿en qué partes del chat?":
Discusión y Ejecución no necesitan orquestación multi-agente y meterlas en
LangGraph "por consistencia" sería sobreingeniería.

## Decisión

Adoptar **LangGraph** en los dos puntos descritos, con cuatro restricciones
duras:

1. **Solo el modo Planning del chat usa LangGraph.** Discusión y Ejecución
   se implementan con un agent loop ligero o routing directo sobre LiteLLM.
   El supervisor multi-agente se activa exclusivamente cuando
   `conversation.mode == "planning"`.
2. **Aislamiento tras fachada en dos paquetes** internos: `shared-agent-loop`
   (Plan 02) y `shared-multi-agent-supervisor` (Plan 03). Ningún otro paquete
   importa `langgraph.*` ni `langchain.*` directamente. Regla verificada por
   linter custom en CI.
3. **Versión pineada** (`langgraph==X.Y.Z`, sin rango). Subidas por PR
   explícito con justificación, no por dependabot automático.
4. **Captura de Executions agnóstica al motor**: los hooks que alimentan
   `steps_log`, `tool_calls`, `model_calls`, `memory_reads` viven en
   `shared-agent-loop` y no exponen tipos de LangGraph al modelo de dominio.

El checkpointing usa el `PostgresSaver` oficial envuelto en un wrapper propio
que prefija `thread_id` con `tenant_id` (`{tenant_id}:{execution_id}` para el
agent loop, `{tenant_id}:{conversation_id}` para el chat). Las tablas de
checkpointing van con RLS como el resto del esquema (ver
[ADR 0001](0001-postgres-rls-from-day-one.md)).

El reuso de proceso entre pasos previsto para Fase 6 (sección 12.5.5 del
documento maestro) exige limpieza explícita del estado de LangGraph entre
ejecuciones. Desde Fase 02 se introduce un test específico de "no fuga de
estado entre ejecuciones secuenciales en el mismo proceso" como condición de
cierre del plan.

## Alternativas descartadas

1. **Burr (DAGWorks).** Específico para máquinas de estado de agentes, sin
   acoplamiento LangChain. Para 8 nodos encajaría como un guante. Rechazado
   por comunidad pequeña y ecosistema de integraciones limitado. Se conserva
   como **plan B preferente** del agent loop si LangGraph se vuelve inviable.
2. **Implementación manual con asyncio + máquina de estados explícita.**
   Control total, cero dependencia. Rechazado por +40-60 h adicionales solo
   para checkpointing reanudable, HITL y streaming, más mantenimiento futuro.
   Plan C de respaldo.
3. **AutoGen / AG2** para el supervisor del chat. Patrones `GroupChatManager`
   muy maduros. Rechazado por HITL menos limpio, checkpointing menos pluggable
   y peor encaje con el agent loop que ya viviría en LangGraph (un agente
   AutoGen no se compone como subgraph de LangGraph sin pegamento). Plan B
   preferente del supervisor si LangGraph se queda corto.
4. **OpenAI Agents SDK / Swarm.** Ligero, handoff-based. Rechazado porque
   nuestro patrón de Planning es supervisor-con-portavoz, no handoff
   secuencial; además es OpenAI-céntrico.
5. **CrewAI.** Orientado a tasks fijas, sin patterns sólidos para chat libre
   con interrupciones humanas. Descartado pronto.
6. **LlamaIndex Workflows / Pydantic AI.** Más ligeros pero menos maduros en
   HITL y checkpointing (LlamaIndex) o con API de graphs demasiado reciente
   para un componente core (Pydantic AI). Reevaluables a 12-18 meses.

## Consecuencias

Positivas:

- Checkpointing por `thread_id` sobre PostgreSQL battle-tested y gratuito.
- HITL con `interrupt`/`resume` nativo, encaja con `human_approval_policy`
  y con el botón "Generar Plan" como punto de pausa.
- Streaming al WebSocket vía `astream_events`.
- Composición limpia: el supervisor del chat es un grafo y cada agente
  invocado reutiliza el agent loop como subgraph de LangGraph.
- Time-travel y replay útiles para debugging y para los tests humanos a
  nivel de plan.

Negativas / cuidados:

- **Acoplamiento al ecosistema LangChain**, históricamente inestable en APIs.
  Mitigado por la fachada en `shared-agent-loop` / `shared-multi-agent-supervisor`
  y por el pineado de versión.
- **El estado nativo es por grafo, no por tenant.** Mitigado por el wrapper
  de checkpointing con prefijado de `thread_id` y por tests que verifican
  que dos `thread_id` con mismo sufijo pero distinto `tenant_id` nunca
  devuelven el checkpoint del otro tenant.
- **La captura de Executions no es gratis** — hay que enganchar callbacks
  propios. Estimación: +8-10 h en `task_02_12`.
- **El reuso de proceso (Fase 6) exige limpieza meticulosa** del estado
  global de LangGraph (callbacks, tracers, contextos thread-local). Test
  específico bloqueante para cerrar Fase 02.
- **Riesgo acumulativo**: dos planes dependen del mismo motor. Mitigado por
  aislar el chat solo al modo Planning (Discusión y Ejecución quedan libres)
  y por las dos fachadas separadas, que permiten cambiar de motor en uno
  sin tocar el otro.

Si tras 3 meses en producción se observa degradación de latencia en
conversaciones largas de Planning, rotura de BC en upgrades sucesivos del
agent loop, o que >90% del tráfico de chat va a Discusión/Ejecución (que no
usan LangGraph), se reabre este ADR con un sucesor que migre uno u otro uso
a su plan B respectivo.

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — Fase 02, sección "Decisiones Clave".
- `docs/roadmap/03-chat-planning-aprobacion.md` — Fase 03, sección "Alcance".
- `docs/context/tech-stack.md` — fila "Orquestación agentes".
- [ADR 0001](0001-postgres-rls-from-day-one.md) — RLS también aplica a las
  tablas de checkpointing.
- Documento maestro, sección 8 (Chat y Planning) y sección 12.5 (pool elástico
  de runtimes).
- LangGraph: https://langchain-ai.github.io/langgraph/ (versión exacta a pinear
  según última estable a fecha de implementación de `task_02_05`).
