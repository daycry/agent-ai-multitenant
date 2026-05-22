---
adr: "0017"
title: Fase de integración end-to-end del Plan 02
status: proposed
date: 2026-05-22
deciders: System Admin (pendiente de aprobación)
phase: 02-ejecucion-agentes
---

# ADR 0017 — Fase de integración end-to-end del Plan 02

> **Estado: `proposed`.** Este ADR documenta un hueco del roadmap y
> propone cerrarlo. Requiere aprobación humana antes de implementarse
> (CLAUDE.md: una desviación del roadmap no la decide Claude solo).

## Contexto

Plan 02 ("Ejecución de Agentes") cerró sus **28 tareas** con todos los
tests automáticos en verde — 181 pytest + 13 Playwright. Pero al
intentar ejecutar los **5 tests humanos** (`human_02_01`..`human_02_05`)
aparece un hueco:

Las 28 tareas entregaron los **componentes**, cada uno probado de forma
aislada — orchestrator, worker, imagen `agent-runtime`, agent loop,
tools, captura, WebSockets, motor de aprobación. Pero la descomposición
del roadmap **no incluyó ninguna tarea de "integrar los componentes en
un pipeline vivo"**. En concreto, hoy:

- El `agent-runtime` arranca un _self-check_, **no el agent loop**.
- Nada encola la tarea Celery del worker; el orchestrator no despacha.
- `publish_execution_event` no se llama nunca — no fluyen eventos.
- El motor de aprobación no lo invoca ningún loop en ejecución.
- No hay un `ModelClient` real: el loop corre con `ScriptedModelClient`.

Los tests humanos asumen un **sistema vivo** ("un agente ejecuta una
tarea end-to-end"). Con los componentes sin cablear, `human_02_01`,
`_03`, `_04` y casi todo `_05` **no son ejecutables**.

Marcar los 28 checkboxes fue correcto ("checkbox = su test automático
pasa"), pero **el plan como conjunto no cumple su intención**: ningún
agente ha ejecutado una tarea de principio a fin. El plan se llama
"Ejecución de Agentes".

## Decisión propuesta

Añadir una **Fase G — Integración end-to-end** al Plan 02 (no un plan
nuevo: es la compleción de Plan 02). Tareas propuestas:

| Tarea        | Alcance                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `task_02_29` | El `agent-runtime` ejecuta el agent loop: lee la especificación de tarea, corre `run_agent`, emite cada step como línea JSON en stdout, escribe el resultado final.                                                                                                                                                                                                                     |
| `task_02_30` | El worker conduce una ejecución real: lanza el contenedor para una tarea, streamea su stdout, publica los step events al stream Redis por-ejecución (`publish_execution_event`) y persiste la fila `Execution` (`record_execution`).                                                                                                                                                    |
| `task_02_31` | El orchestrator despacha: ante un evento de tarea, elige agente (políticas de asignación), mueve la tarea a `in_progress` y encola la tarea Celery del worker.                                                                                                                                                                                                                          |
| `task_02_32` | `ModelClient`(s) reales detrás del protocolo, cubriendo los **tres caminos de proveedor** de CLAUDE.md §9 / ADR 0009: el **gateway LiteLLM** (Anthropic, OpenAI, Gemini, Ollama, Bedrock…), el **Claude Agent SDK** (suscripción Pro/Max) y **GitHub Copilot** (OAuth Device Flow). Es la tarea más pesada; al detallar la Fase G probablemente se parta en una subtarea por proveedor. |
| `task_02_33` | El motor de aprobación y las salvaguardas operan sobre el run en vivo: una acción sensible dispara `request_approval_if_needed`; una salvaguarda rota se refleja en la ejecución.                                                                                                                                                                                                       |
| `task_02_34` | Test de humo end-to-end + ejecución de los 5 tests humanos del plan.                                                                                                                                                                                                                                                                                                                    |

### Tres proveedores LLM, un mismo protocolo

El `ModelClient` ya es un protocolo enchufable (ADR 0013) — y ése es
exactamente el sitio donde encajan los tres caminos de proveedor: cada
uno es **una implementación** del mismo protocolo (gateway LiteLLM,
Claude Agent SDK, Copilot OAuth Device Flow). El agent loop no cambia;
sólo cambia qué `ModelClient` se inyecta. Cada camino trae su propio
modelo de credenciales (API key, suscripción Pro/Max, OAuth Device
Flow), lo que justifica tratar `task_02_32` como la tarea más grande de
la fase.

### El LLM real solo lo necesita un test

La Fase G cablea **la fontanería** — `task_02_29..31` y `task_02_33`
funcionan igual con el modelo determinista que con uno real:

- `task_02_29..31`, `task_02_33` y los tests humanos **`human_02_02`,
  `_03`, `_04`, `_05`** quedan ejecutables con el `ScriptedModelClient`
  — no dependen de un LLM real.
- **`human_02_01`** ("el agente escribe un poema") es el único que
  necesita un LLM real → requiere que el operador configure **uno** de
  los tres caminos de proveedor: una API key para el gateway LiteLLM,
  una suscripción Claude Pro/Max para el Claude Agent SDK, o el OAuth
  Device Flow de GitHub Copilot — con el coste que aplique.

Por eso `task_02_32` no bloquea al resto: 4 de 5 tests humanos no
necesitan ningún proveedor LLM real.

## Alternativas descartadas

1. **Cerrar Plan 02 a nivel de componentes** y re-escribir los tests
   humanos a "nivel componente". Rechazada: declara `completed` un
   sistema que nunca ha ejecutado una tarea, y traslada deuda
   **estructural** a los planes 03+ (todos asumen que la ejecución de
   agentes funciona). Sería un roadmap verde engañoso.
2. **Un plan nuevo `02b` separado.** El wiring es la compleción de Plan
   02, no un plan aparte; renumerar añade ruido. Fase G dentro de
   `docs/roadmap/02-ejecucion-agentes.md` mantiene la trazabilidad.
3. **Integrar un LLM real como prerrequisito de toda la fase.**
   Innecesario: el protocolo `ModelClient` permite cablear y validar
   4/5 tests humanos con el modelo determinista; los proveedores reales
   son una tarea más, no la puerta de entrada.
4. **Soportar solo LiteLLM y dejar Claude Agent SDK y Copilot para
   después.** Rechazada: CLAUDE.md §9 los lista como soportados en esta
   versión; el protocolo `ModelClient` los acomoda sin coste de diseño,
   así que el recorte no estaría justificado. (Sí es razonable
   priorizar un proveedor para desbloquear `human_02_01` y añadir los
   otros dos en paralelo — decisión de detalle de la Fase G.)

## Consecuencias

Si se aprueba:

- Plan 02 pasa de `pending_human_validation` a `in_progress` mientras
  se ejecuta la Fase G; al terminarla y pasar los 5 tests humanos,
  cierra como `completed` honestamente.
- Los planes 03+ se construyen sobre un sistema que de verdad ejecuta
  agentes.
- `human_02_01` queda condicionado a que el operador configure al menos
  uno de los tres caminos de proveedor LLM.

Si NO se aprueba (se elige la alternativa 1):

- Plan 02 cierra a nivel de componentes; los tests humanos se
  re-escriben; la deuda de integración se asume explícitamente para un
  plan futuro, que debe crearse antes que Plan 03.

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — tareas 01-28 y tests humanos.
- `docs/07-changelog/02-ejecucion-agentes.md` — sección "Deuda y notas".
- CLAUDE.md §9 — LLM providers desacoplados (LiteLLM + Claude Agent SDK
  - GitHub Copilot OAuth Device Flow).
- ADR 0009 (gateway LLM), 0011-0016 (componentes del Plan 02).
- Documento maestro, sección 12 (ejecución de agentes).
