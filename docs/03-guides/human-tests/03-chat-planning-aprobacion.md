# Plan 03 — tests humanos

Esta guía cubre los **5 tests humanos** del Plan 03 (Chat, Planning
Multi-Agente y Aprobación). Validan el flujo completo de conversación
con el equipo → plan estructurado → revisión → sincronización al Kanban
respetando el DAG → doble firma sobre umbral.

> **Estado del plan**: `completed` (mergeado a `master`,
> `completed_at: 2026-05-25`). Esta guía es el **registro histórico**
> de los tests humanos con los que se cerró el plan; queda para
> regresión cuando se toque el chat de planning, la generación de
> planes, la sincronización al Kanban o el flujo de aprobación.

## TL;DR

El Plan 03 **no tiene** `scripts/demos/setup_demo_03.py` ni launcher
dedicado: los tests son una conversación real con el equipo desde la
UI. Setup manual:

```powershell
.\scripts\dev\up.ps1                 # api-server :8001 + admin-panel :3000 + postgres + redis + workers
# luego: crea un proyecto con un equipo (PM + Arquitecto al menos) y abre su chat
```

> Requiere un **proveedor LLM configurado** (Claude SDK / Copilot /
> Azure Foundry / Ollama) para que los agentes respondan. Sin
> proveedor, el chat de planning no avanza. Puedes apoyarte en
> `scripts/demos/setup_demo_project.py` para tener un proyecto + agente base
> de partida.

## Pre-requisitos

| Requisito                             | Por qué                                                      |
| ------------------------------------- | ------------------------------------------------------------ |
| Stack dev arriba (`up.ps1`)           | api-server + admin-panel + postgres + redis + workers        |
| Proveedor LLM configurado             | Los agentes PM / Arquitecto deben poder responder en el chat |
| Proyecto con equipo (PM + Arquitecto) | Para la conversación de planning multi-agente                |
| Dos usuarios con permiso de aprobar   | Para `human_03_05` (doble firma)                             |

---

## `human_03_01` — conversación de planning produce un plan utilizable

**Qué prueba**: una conversación con el equipo desemboca en un plan
estructurado persistido con tareas, dependencias y costes.

**Precondiciones**: proyecto con equipo (PM + Arquitecto), proveedor
LLM activo, chat en modo **Planning**.

**Pasos**:

1. Abre el chat del proyecto en modo Planning.
2. Escribe: _"Necesito construir una API de gestión de inventario con
   autenticación JWT"_.
3. Conversa 3-5 turnos respondiendo a las preguntas del equipo.
4. Cuando aparezca el botón **"Generar Plan"**, púlsalo.
5. Abre el plan generado y revisa que persistió completo.

**Resultado esperado**:

- El PM hace preguntas de descubrimiento relevantes.
- El Arquitecto interviene en las decisiones técnicas.
- Tras 3-5 turnos el equipo presenta un plan estructurado en el chat.
- Aparece el botón **"Generar Plan"**.
- Al pulsar, el plan persiste con todas las tareas, dependencias y
  costes.

**Checklist**:

- [ ] El PM agente hace preguntas de descubrimiento relevantes.
- [ ] El Arquitecto interviene cuando hay decisiones técnicas.
- [ ] Tras 3-5 turnos el equipo presenta un plan estructurado en el
      chat.
- [ ] Aparece el botón "Generar Plan".
- [ ] Al pulsar, el plan persiste con todas las tareas, dependencias y
      costes.

**Pitfalls conocidos**:

- Si los agentes no responden, revisa que el proveedor LLM esté
  configurado y con credenciales en Vault.
- Si "Generar Plan" no aparece, el equipo aún no considera la propuesta
  lo bastante madura: sigue conversando o sé más concreto.

---

## `human_03_02` — cambio de modos sin pérdida de contexto

**Qué prueba**: alternar entre Planning y Discusión conserva el
historial y el contexto conversacional.

**Precondiciones**: una conversación de planning ya iniciada
(`human_03_01`).

**Pasos**:

1. Estando en **Planning**, cambia a **Discusión**.
2. Pregunta algo que dependa de lo conversado en Planning.
3. Vuelve a **Planning**.
4. Revisa el historial y los marcadores de hito.

**Resultado esperado**:

- El historial completo sigue visible tras cada cambio.
- El equipo en Discusión recuerda lo conversado en Planning.
- Al volver a Planning, retoma la propuesta donde la dejó.
- Los cambios de modo aparecen marcados como hito en el historial.

**Checklist**:

- [ ] El historial completo sigue visible tras cada cambio.
- [ ] El equipo en Discusión recuerda lo conversado en Planning.
- [ ] Al volver a Planning, el equipo retoma la propuesta donde la
      dejó.
- [ ] Los cambios de modo aparecen marcados como hito en el historial.

**Pitfalls conocidos**:

- Si el contexto se pierde al cambiar de modo, revisa que la
  conversación sea la misma (no se haya creado una nueva sesión de
  chat).

---

## `human_03_03` — detalle del plan es revisable

**Qué prueba**: la pantalla de detalle de un plan en
`pending_approval` muestra toda la información necesaria para decidir:
coste, alcance, DAG, Gantt, desglose y comentarios in-line.

**Precondiciones**: un plan en estado `pending_approval` (generado en
`human_03_01`).

**Pasos**:

1. Abre el plan en `pending_approval`.
2. Revisa cada bloque: cabecera de coste, descripción detallada, grafo
   DAG de tareas, vista Gantt, desglose de coste por tarea.
3. Añade un comentario in-line en una tarea antes de aprobar.

**Resultado esperado**:

- Cabecera con coste humano vs IA y ahorro estimado.
- Descripción detallada con alcance, supuestos, decisiones y riesgos.
- Tareas con dependencias visibles en grafo DAG.
- Vista Gantt con línea crítica.
- Desglose de coste por tarea con totales.
- Comentarios in-line antes de aprobar.

**Checklist**:

- [ ] Cabecera con coste humano vs IA y ahorro estimado.
- [ ] Descripción detallada con alcance, supuestos, decisiones y
      riesgos.
- [ ] Tareas con sus dependencias visibles en grafo DAG.
- [ ] Vista Gantt muestra línea crítica.
- [ ] Desglose de coste por tarea con totales.
- [ ] Posibilidad de añadir comentarios in-line antes de aprobar.

**Pitfalls conocidos**:

- Si el DAG o el Gantt aparecen vacíos, el plan se generó sin
  dependencias entre tareas: re-genera con un prompt que las requiera.

---

## `human_03_04` — sincronización al Kanban respeta DAG

**Qué prueba**: al aprobar y sincronizar un plan con dependencias, las
tareas entran al Kanban respetando el DAG (sin dependencias → ready;
con dependencias → backlog hasta completar las predecesoras).

**Precondiciones**: un plan aprobado con tareas que tienen dependencias.

**Pasos**:

1. Aprueba el plan y sincronízalo al Kanban.
2. Observa el estado inicial de las tareas.
3. Completa una tarea que es dependencia de otra y observa la sucesora.
4. Intenta mover a `in_progress` una tarea con dependencias pendientes.

**Resultado esperado**:

- Las tareas se crean en el Kanban en `backlog`.
- Las tareas sin dependencias pasan automáticamente a `ready`.
- Las tareas con dependencias quedan en `backlog`.
- Al completar una dependencia, la sucesora pasa a `ready`
  automáticamente.
- Mover una tarea con dependencias pendientes a `in_progress` devuelve
  **422**.

**Checklist**:

- [ ] Las tareas se crean en el Kanban en estado backlog.
- [ ] Tareas sin dependencias pasan automáticamente a ready.
- [ ] Tareas con dependencias quedan en backlog.
- [ ] Al completar una dependencia, la sucesora pasa a ready
      automáticamente.
- [ ] Intentar mover una tarea con dependencias pendientes a
      in_progress devuelve error 422.

**Pitfalls conocidos**:

- El 422 es por diseño: el guard del DAG lo impone en el backend, no
  solo en la UI.

---

## `human_03_05` — doble firma sobre umbral funciona

**Qué prueba**: cuando el coste estimado supera el umbral configurado,
el plan exige dos aprobaciones de usuarios distintos.

**Precondiciones**: umbral de doble firma configurado (p. ej. 500 €) y
un plan con coste IA estimado superior a ese umbral. Dos usuarios con
permiso de aprobar.

**Pasos**:

1. Configura el umbral de doble firma a 500 €.
2. Crea/genera un plan con coste IA estimado > 500 €.
3. Aprueba con el **primer** usuario.
4. Intenta aprobar la segunda firma con el **mismo** usuario (no debe
   poder) y luego con un **segundo** usuario con permisos.

**Resultado esperado**:

- Tras la primera aprobación el plan pasa a `pending_second_approval`.
- Solo **otro** usuario con permisos puede dar la segunda firma.
- Tras la segunda firma el plan pasa a `approved`.

**Checklist**:

- [ ] Tras la primera aprobación el plan pasa a pending_second_approval.
- [ ] Solo otro usuario con permisos puede aprobar la segunda firma.
- [ ] Tras la segunda firma el plan pasa a approved.

**Pitfalls conocidos**:

- Si la doble firma no se dispara, revisa que el coste estimado del
  plan realmente supere el umbral configurado.
- El mismo usuario no puede firmar dos veces: es la invariante central
  de este test.

---

## Cierre del plan

El plan ya está `completed` (`2026-05-25`). Esta guía es el registro
histórico para regresión.

## Troubleshooting

Los errores transversales del stack dev (proveedor LLM sin
credenciales en Vault, workers caídos, JWT secret mismatch, asyncpg)
viven en `docs/03-guides/gotchas/`.
