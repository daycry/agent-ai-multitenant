---
adr: "0019"
title: Acceso de red del sandbox agent-runtime a los proveedores LLM
status: accepted
date: 2026-05-22
deciders: System Admin
phase: 02-ejecucion-agentes
---

# ADR 0019 — Acceso de red del sandbox agent-runtime a los proveedores LLM

> **Estado: `accepted`.** El System Admin eligió la **Opción 1 — egress
> controlado** el 2026-05-22, por ser la más sencilla y porque el tool
> `http_request` necesita ese mismo mecanismo de todas formas. La
> **Opción 2 (llamada intermediada por el worker)** queda documentada
> abajo como alternativa, por si en el futuro hubiera que revisitar la
> decisión. Este ADR no implementa nada: el cableado del egress es una
> tarea de implementación aparte.

> **Nota (2026-05-25, ADR 0021).** La **estructura del egress**
> (sandbox `internal: true` + tinyproxy con `FilterDefaultDeny`) **sigue
> vigente**. Lo que ha cambiado es **la allowlist concreta**: LiteLLM
> ya no aparece (se retiró del catálogo), Azure AI Foundry vía APIM
> entra con el patrón `^[a-z0-9-]+\.azure-api\.net$`, y Ollama cloud
> añade `^ollama\.com$` (un Ollama local en el host o en otro
> contenedor del compose no sale por el proxy: alcanza al sandbox por
> la red interna `agentic-agents`). El fichero `docker/egress-proxy/filter.txt`
> es la fuente de verdad del listado actual.

## Contexto

`task_02_32` (Fase G) implementó tres `ModelClient` reales —gateway
LiteLLM, GitHub Copilot, Claude Agent SDK— probados con transports
mockeados, que era su alcance definido (su test: "conforman el
protocolo y parsean respuestas, transports mockeados, sin credenciales
reales").

El agent loop, incluido el `ModelClient`, corre **dentro del contenedor
`agent-runtime`**. Ese contenedor se lanza con su red en modo `internal`
(ADR 0012): sin salida al host ni a internet. Por tanto **desde dentro
del contenedor ningún proveedor LLM real es alcanzable** — los tres
caminos necesitan red. Hoy solo funciona el `ScriptedModelClient`
(determinista, sin red): es lo que usan todos los tests automáticos y el
script de demostración.

El mismo hueco afecta al tool `http_request` (Fase D): su allowlist de
dominios se validó en el código del tool, pero una petición real saldría
a una red `internal`. `workers/config.py` ya lo anota — _"controlled
egress for the http_request tool arrives in Fase D"_: la intención
estaba, el cableado de red nunca se hizo.

`task_02_32` entregó el código correcto según su test. Lo que falta es
**decidir y cablear cómo el sandbox alcanza la red que legítimamente
necesita**. Eso toca el aislamiento de ADR 0012, así que es una decisión
de arquitectura y no un detalle de implementación.

## La tensión

Aislamiento del contenedor (ADR 0012: sin egress, sin socket Docker,
cap-drop ALL, seccomp default-deny, FS read-only) ↔ el agent loop
necesita llamar a un LLM y el tool `http_request` necesita alcanzar
dominios externos.

## Opciones consideradas

### Opción 1 — Egress controlado desde el contenedor

El contenedor deja de ser `internal` y pasa a una red con **salida
restringida**: solo a una allowlist de destinos (el servicio gateway
LiteLLM, `api.githubcopilot.com`, el endpoint de Anthropic, y los
dominios del `http_request` por proyecto). Se implementa con un **proxy
de egress** —un servicio del compose que aplica la allowlist— o con
reglas de firewall. Las credenciales del proveedor entran por
`/run/secrets` (ya cableado, Fase B / `task_02_08`). El `ModelClient`
dentro del contenedor hace la llamada HTTP él mismo, que es lo que el
código de `task_02_32` ya hace.

- A favor:
  - El contenedor **necesita egress controlado de todas formas** para el
    tool `http_request` (Fase D). Construir ese mecanismo una vez sirve
    para los dos casos — no se duplica nada.
  - Cambio mínimo de código: los `ModelClient` ya hacen HTTP directo.
  - Es lo que la inyección de secretos por `/run/secrets` ya presupone:
    credenciales accesibles dentro del contenedor.
  - El aislamiento que más importa se conserva intacto: sin socket
    Docker, cap-drop ALL, seccomp, FS read-only, sin acceso al host. Un
    egress hacia una allowlist es un agujero estrecho y auditado, no
    abrir las compuertas.
- En contra:
  - Las credenciales del LLM viven dentro del sandbox. Un escape de
    contenedor o un agente malicioso podría exfiltrarlas o abusar del
    egress permitido.
  - Mitigación: tokens efímeros / de alcance reducido, allowlist
    estricta, el contenedor es efímero (uno por tarea).

### Opción 2 — Llamada al LLM intermediada por el worker

El `ModelClient` dentro del contenedor **no llama al LLM**: pide al
worker —de confianza, con red y credenciales— que haga la llamada, por
un canal local (un socket, o extendiendo el protocolo stdout actual a
petición/respuesta). El worker hace el HTTP real y devuelve la
respuesta. El contenedor sigue 100 % `internal`.

- A favor:
  - Las credenciales y la red **nunca** entran al sandbox. Es el
    aislamiento más fuerte y el más fiel al principio de CLAUDE.md.
- En contra:
  - Más trabajo: hoy el contenedor solo _emite_ por stdout (una vía);
    haría falta un canal de ida y vuelta.
  - **No resuelve `http_request`** —ese tool sí necesita egress real del
    contenedor—, así que habría que mantener DOS mecanismos: el
    intermediado para el LLM y el egress controlado para `http_request`.
  - Latencia del round-trip contenedor↔worker en cada `decide()`.

### Opción 3 (descartada) — Partir el loop

Los nodos `plan` / `self_review` (las llamadas al LLM) correrían
worker-side y solo `act` dentro del contenedor. Rediseña dónde vive el
agent loop (ADR 0013) y rompe el streaming unificado de `steps` de
`task_02_29/30`. Demasiado invasivo para el problema que resuelve.

## Recomendación

**Opción 1 — egress controlado.** El argumento decisivo: el contenedor
necesita egress controlado **igualmente** para `http_request`. Una vez
existe ese mecanismo (proxy de egress + allowlist), los `ModelClient` lo
reutilizan sin código nuevo. La Opción 2 obliga a construir y mantener
dos mecanismos distintos. El aislamiento crítico de ADR 0012 se conserva
entero; lo que se añade es un agujero de red estrecho, explícito y
auditado.

Detalles a fijar si se aprueba (decisiones de implementación, no de este
ADR):

- Una allowlist de egress **a nivel de plataforma** (gateway LiteLLM,
  Copilot, Anthropic) separada de la allowlist de `http_request` **por
  proyecto**.
- Mecanismo concreto: proxy de egress como servicio del compose, o red
  Docker no-`internal` con reglas de firewall.
- Credenciales de proveedor siempre por `/run/secrets`, nunca por
  variable de entorno.

## Consecuencias

Si se aprueba la Opción 1:

- Nace una tarea de implementación —a numerar por el System Admin: una
  `task_02_35` en la Fase G, o trabajo de un plan posterior— que cablea
  el egress controlado y mete los endpoints LLM en la allowlist de
  plataforma.
- Hasta que esa tarea exista, `human_02_01` con un proveedor real solo
  es ejecutable por la vía _host-side_ (correr el agent loop fuera del
  contenedor, en una máquina con red). Es un atajo de validación, no la
  arquitectura de producción.
- `workers/config.py:agent_network_internal` deja de ser siempre `True`.
- ADR 0012 queda complementado: el aislamiento admite un egress
  explícito hacia una allowlist.

Si NO se aprueba / se elige la Opción 2:

- Hay que diseñar el canal petición/respuesta contenedor↔worker, y aun
  así resolver por separado el egress de `http_request`.

## Referencias

- ADR 0012 — aislamiento de contenedores agent-runtime.
- ADR 0013 — agent loop LangGraph y el protocolo `ModelClient`.
- ADR 0017 — Fase G de integración end-to-end; ADR 0018 — el Claude SDK
  como `ModelClient`.
- `task_02_32` (ModelClients reales), `task_02_08` (secretos por
  `/run/secrets`), `task_02_17` (tool `http_request`).
- CLAUDE.md §2 (aislamiento por contenedor), §9 (proveedores LLM).
