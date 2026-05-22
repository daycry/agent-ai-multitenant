---
adr: "0018"
title: El Claude Agent SDK como ModelClient de un turno
status: accepted
date: 2026-05-22
deciders: System Admin
phase: 02-ejecucion-agentes
---

# ADR 0018 — El Claude Agent SDK como `ModelClient` de un turno

> **Estado: `accepted`.** El System Admin delegó la recomendación
> ("¿qué me recomiendas? no quiero complicarlo") y aceptó la opción A.

## Contexto

`task_02_32` (Fase G, ADR 0017) implementa los tres caminos de proveedor
LLM de CLAUDE.md §9 detrás del protocolo `ModelClient` (ADR 0013):
`decide()` devuelve **una decisión**, `review()` **un veredicto**.

Dos de los tres encajan sin fricción: el **gateway LiteLLM** y **GitHub
Copilot** son endpoints `/chat/completions` request/response — una
petición, una respuesta, una decisión.

El **Claude Agent SDK** no: el SDK **es en sí mismo un runtime
agéntico** — corre su propio loop, su propio `max_turns`, sus propias
tool calls. "El SDK corre el loop entero" no encaja de forma directa en
"un `ModelClient` devuelve una decisión". El documento de referencia
aportado por el operador (`docs/context/claude-agent-sdk-integration-example.md`)
y el propio ADR 0017 marcaron esta tensión como pendiente de un ADR.

## Opciones consideradas

1. **El SDK reemplaza el loop** cuando `provider=claude`: el agent-runtime
   entrega la tarea al loop nativo del SDK y nuestro grafo LangGraph se
   salta. Es lo más nativo al SDK, pero crea **dos runtimes** y dos
   formatos de `steps_log`, y contradice la definición de `task_02_32`
   ("los tres `ModelClient` reales detrás del protocolo") y su test.

2. **`ModelClient` de un turno (elegida).** El SDK se envuelve detrás de
   `ModelClient` igual que LiteLLM y Copilot: cada `decide()` ejecuta
   **un solo turno** del SDK (`max_turns=1`) y devuelve una decisión.
   Nuestro loop LangGraph (Fase C) sigue conduciendo las iteraciones.
   No se aprovecha el loop nativo del SDK; sí su autenticación por
   suscripción Pro/Max (sin API key).

3. **`ModelClient` que delega el loop al SDK**: el primer `decide()`
   corre el loop completo del SDK, cachea la trayectoria y la reproduce
   en las llamadas siguientes. Mantiene un único punto de entrada pero
   es **stateful** y casa mal con el contrato "una llamada → una
   decisión" (los errores del SDK afloran a mitad del replay).

## Decisión

**Opción A — `ModelClient` de un turno.** Los tres proveedores son
implementaciones de `ModelClient`; para `provider=claude` cada
`decide()` corre un turno del SDK con `max_turns=1`.

Motivos:

- **Un solo runtime.** El loop LangGraph (`agent_runtime`, ADR 0013)
  sigue siendo el único motor. `task_02_29` y `task_02_30` —
  entrypoint y worker — ya streamean los `steps` de **ese** loop; la
  opción 1 obligaría a un segundo camino de ejecución y a reconciliar
  dos formatos de `steps_log`.
- **Coherencia.** LiteLLM, Copilot y Claude se inyectan en el mismo
  punto; cambiar de proveedor es cambiar qué `ModelClient` se pasa, sin
  tocar el loop (es justo lo que ADR 0017 §"Tres proveedores, un mismo
  protocolo" anticipaba).
- **Conserva lo que el operador quería del SDK**: la autenticación por
  suscripción Claude Pro/Max, sin API key.
- **Es lo que `task_02_32` y su test ya presuponen** ("los tres
  `ModelClient` reales conforman el protocolo").
- **Simplicidad** — criterio explícito del System Admin.

Coste asumido: no se aprovecha el loop multi-turno nativo del SDK
(planificación + tool-use encadenado dentro de una sola llamada). Es un
coste aceptable: ese trabajo lo hace nuestro loop, que además es el que
aplica salvaguardas, captura y aprobación (`task_02_33`).

## Consecuencias

- `agent_runtime/providers.py` implementa `LiteLLMModelClient`,
  `CopilotModelClient` y `ClaudeSDKModelClient`, los tres bajo
  `ModelClient`. `model_from_spec` los construye por `kind`.
- `ClaudeSDKModelClient.decide()` ejecuta un turno (`max_turns=1`) vía
  `claude_agent_sdk.query`; la función `query` es inyectable, así los
  tests no necesitan ni el SDK ni credenciales.
- `claude-agent-sdk` es **dependencia opcional** de la imagen
  `agent-runtime` (extra `claude`): una ejecución scriptada / LiteLLM /
  Copilot no necesita ni el SDK ni el CLI de Node. Empaquetar el SDK +
  el CLI de Claude Code en la imagen es un paso de despliegue del
  operador, condicionado a elegir `provider=claude` (ADR 0017: sólo
  `human_02_01` necesita un proveedor real).
- Si en un plan futuro se quisiera explotar el loop nativo del SDK, este
  ADR habría que revisarlo (sería volver a la opción 1).

## Referencias

- ADR 0013 — el agent loop LangGraph y el protocolo `ModelClient`.
- ADR 0017 — Fase G; §"Tres proveedores, un mismo protocolo".
- `docs/context/claude-agent-sdk-integration-example.md` — la tensión
  arquitectónica, planteada por el operador.
- `docs/context/github-copilot-oauth-integration-example.md`.
- CLAUDE.md §9 — proveedores LLM desacoplados.
