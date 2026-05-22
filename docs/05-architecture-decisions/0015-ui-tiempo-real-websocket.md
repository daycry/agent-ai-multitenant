---
adr: "0015"
title: UI en tiempo real — WebSocket sobre Redis Streams
status: accepted
date: 2026-05-22
deciders: System Architect, Backend Dev, Frontend Dev
phase: 02-ejecucion-agentes
---

# ADR 0015 — UI en tiempo real: WebSocket sobre Redis Streams

## Contexto

Plan 02 Fase E hace visible la ejecución: el Timeline de una ejecución y
el Kanban deben actualizarse **en vivo**, sin que el usuario refresque.
Hay que decidir:

1. Qué transporte lleva los eventos al navegador.
2. Cómo se autentica una conexión WebSocket desde el navegador.
3. Cómo consume el frontend ese flujo sin acoplarse a una librería.
4. Cómo se prueban end-to-end un Timeline y un Kanban en tiempo real.

## Decisión

### Transporte: WebSocket que sigue un Redis Stream

Dos endpoints WebSocket en el api-server (`routers/ws.py`):

- `/ws/executions/{id}` — sigue el stream `exec:{id}` (un stream Redis
  por ejecución; los logs en vivo van por Redis, no por escrituras
  constantes en BD — ADR 0011).
- `/ws/kanban/{project_id}` — sigue el stream global `events:tasks` y
  filtra las entradas por `project_id`.

Cada socket lee su stream desde `0` (backlog + cola en vivo), así un
cliente que conecta a mitad de ejecución recibe lo ya ocurrido y luego
lo nuevo. El bucle hace `XREAD` con bloqueo y, **en paralelo**, un
`ws.receive()`: si el cliente cierra mientras el stream está inactivo,
se detecta de inmediato — no queda una tarea colgada en `XREAD`.

### Autenticación: el JWT como query param

La API WebSocket del navegador **no permite cabeceras**, así que el JWT
viaja como `?token=`. Un token ausente o inválido cierra el socket con
código 1008 (policy violation). Es la opción pragmática y compatible
con el navegador para Fase E. Una comprobación de propiedad por tenant
sobre la ejecución concreta (¿este tenant es dueño de este `exec:{id}`?)
queda como endurecimiento posterior — Fase E exige token válido.

### Frontend: WebSocket nativo, sin librería nueva

`lib/ws.ts` — `wsUrl` construye la URL `ws(s)://` con el token, y
`useWebSocket` es un hook que mantiene el socket vivo durante la vida
del componente, con el handler en una `ref` para no reconstruir el
socket cuando cambia la identidad del callback.

No se añade `socket.io` ni similar: el `WebSocket` nativo basta, y la
misma doctrina (no añadir dependencias para algo que la plataforma ya
puede hacer) guió el drag-and-drop del Kanban en Plan 01.

El Timeline carga `GET /executions/{id}` y sigue el socket; los pasos
en vivo se concatenan a los persistidos **deduplicados por `index`**.
El Kanban reusa la **misma clave de TanStack Query** que el camino
optimista del drag-and-drop, así una actualización por WebSocket y un
arrastre local quedan consistentes.

### Tests E2E autocontenidos

Los specs Playwright (`execution-streaming`, `execution-timeline`,
`kanban-realtime`, `kanban-live`) **mockean el WebSocket** con
`page.routeWebSocket` e inyectan el JWT directamente en `localStorage`
(la verja de auth del layout admin sólo lo comprueba en cliente). No
necesitan api-server ni stack: prueban el comportamiento del frontend
de forma aislada y determinista. La navegación a la ruta dinámica
`/admin/executions/[id]` usa `waitUntil: "domcontentloaded"` — en el
modo dev de Next el evento `load` de esa ruta no llega de forma fiable.

## Alternativas descartadas

1. **Server-Sent Events (SSE)** en vez de WebSocket. El roadmap pide
   WebSocket; además WS deja la puerta abierta a mensajes
   cliente→servidor (aún no usados, pero gratis).
2. **Una librería de WebSocket** (socket.io, …). El `WebSocket` nativo
   cubre el caso; una dependencia más que mantener no se justifica.
3. **Token por cookie o subprotocolo** WebSocket. La cookie httpOnly
   llega en Fase 15 (junto al cambio de almacenamiento del token); el
   subprotocolo es más enrevesado. El query param es lo pragmático hoy.
4. **Un stream Redis por tenant** para el Kanban. Se mantiene el stream
   global `events:tasks` (ADR 0011) y se filtra por `project_id` en el
   endpoint — decenas de tenants internos no justifican N streams.
5. **Specs E2E contra un api-server real.** Harían los tests
   dependientes de levantar el stack y sembrar un usuario. Mockear REST
   - WebSocket los hace deterministas y ejecutables en solitario.

## Consecuencias

Positivas:

- Timeline y Kanban se actualizan en vivo; el Timeline es la lectura
  directa del `steps_log` capturado en Fase C.
- Sin dependencias nuevas en el frontend; `useWebSocket` es reutilizable.
- 9 specs Playwright verdes, autocontenidos (sin api-server).

Negativas / cuidados:

- La autenticación del socket es "token válido"; falta la comprobación
  de propiedad por tenant de la ejecución concreta — pendiente.
- El escalado del WebSocket con muchas conexiones simultáneas necesita
  sticky sessions en nginx (riesgo anotado en el plan); es trabajo de
  despliegue (Fase 15).
- Detectar el cierre del cliente mientras el stream está inactivo
  funciona; un timeout/heartbeat explícito sería más robusto y queda
  como mejora.
- Los pasos de Fase C llevan `started_at == ended_at` (el bucle es
  instantáneo), así que la duración por paso es 0 ms con datos reales;
  el Timeline muestra lo que haya — distinto de cero cuando los pasos
  tengan duración real.

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — Fase E, task_02_20..23.
- Backend: `apps/api-server/.../routers/ws.py`, `routers/executions.py`,
  `events.py` (`publish_execution_event`).
- Frontend: `apps/admin-panel/lib/ws.ts`,
  `app/admin/executions/[id]/page.tsx`, `app/admin/board/page.tsx`.
- Tests: `tests/integration/test_ws_streaming.py`;
  `apps/admin-panel/e2e/{execution-streaming,execution-timeline,
kanban-realtime,kanban-live}.spec.ts`.
- ADR 0011 (bus de eventos Redis Streams).
- Documento maestro, sección 21 (tiempo real / WebSocket).
