---
adr: "0016"
title: Motor de validación humana
status: accepted
date: 2026-05-22
deciders: System Architect, Backend Dev
phase: 02-ejecucion-agentes
---

# ADR 0016 — Motor de validación humana

## Contexto

Plan 02 Fase F aplica de verdad la `human_approval_policy` que el Plan
01 dejó configurable: cuando un agente intenta una acción de una de las
13 categorías sensibles, la plataforma debe poder **pausar** la
ejecución y esperar a un humano. Hay que decidir:

1. Cómo se evalúa una acción contra la política y dónde se persiste.
2. Qué estado toma una ejecución pausada y qué pasa si nadie responde.
3. Cómo se "notifica" la solicitud en la aplicación.

## Decisión

### El motor: política → solicitud → ejecución aparcada

`db/approval_repo.py` es el motor. `requires_human_approval(policy,
category)` lee la `human_approval_policy` del proyecto (JSONB
`{"categories": {<categoría>: "auto" | "human_required"}}`; una
categoría no listada cuenta como `auto`).

`request_approval_if_needed`:

- `auto` → devuelve `None`, la acción procede, no se persiste nada.
- `human_required` → persiste un `ApprovalRequest` (`pending`) y aparca
  la ejecución en el estado `awaiting_human_approval`.

`resolve_approval` aprueba o rechaza y devuelve la ejecución a
`running` — en un rechazo, el `reason` del revisor es el feedback que
recibe el agente.

La tabla `approval_requests` (migración 0012) guarda categoría, la
acción propuesta (JSONB), estado, motivo, quién y cuándo resolvió. Va
con RLS de tenant como el resto del dominio.

### `awaiting_human_approval` es estado de **ejecución**

Lo que se pausa es la _ejecución_, no la tarea: la tarea sigue
`in_progress` mientras su ejecución espera. `awaiting_human_approval`
se añade a `ExecutionStatus`; obligó a ampliar `executions.status` de
`VARCHAR(16)` a `VARCHAR(32)` (migración 0012).

### Una decisión que nadie toma no puede colgar la ejecución

`expire_stale_requests` caduca toda solicitud `pending` más antigua que
una ventana **configurable** (por defecto 24 h): la marca `timed_out`,
**aborta** su ejecución (`abort_code = approval_timeout_exceeded`) y
**bloquea** su tarea. Un agente esperando para siempre consume un slot
y nunca termina; el timeout lo cierra.

### La "notificación in-app" es el feed de pendientes

`GET /approvals` devuelve las solicitudes `pending`; la página
`/admin/approvals` las muestra como tarjetas con su contador. **No** se
crea una tabla `notifications` todavía: la notificación in-app _es_ la
solicitud pendiente surfaceada en la UI. Una tabla de notificaciones
con tipos y canales (email, Slack…) es trabajo del Plan 10 — el roadmap
ya sitúa ahí los canales externos.

## Alternativas descartadas

1. **Tabla `notifications` propia en Fase F.** Sobreingeniería para un
   solo tipo de notificación y un solo canal. El feed de
   `approval_requests` pendientes cumple; la tabla general llega con
   los canales externos (Plan 10).
2. **`awaiting_human_approval` como estado de la tarea.** La tarea es
   la unidad de trabajo; puede tener varias ejecuciones (reintentos).
   Lo que se pausa es una ejecución concreta — el estado vive ahí.
3. **Timeout fijo de 24 h.** Se deja como parámetro
   (`timeout_hours`); 24 h es sólo el valor por defecto.
4. **El agent loop consultando la BD para pedir aprobación.** El
   contenedor del agente no tiene acceso a la plataforma (ADR 0012). El
   motor corre server-side; la integración con el loop en ejecución
   (el agente emite la intención, el worker corre el motor) es wiring
   de una fase posterior — Fase F entrega y prueba el motor en sí.

## Consecuencias

Positivas:

- La `human_approval_policy` del Plan 01 deja de ser declarativa: se
  aplica, con solicitud persistida, UI de resolución y caducidad.
- El timeout garantiza que ninguna ejecución queda colgada para
  siempre esperando una decisión.

Negativas / cuidados:

- La caducidad la dispara `expire_stale_requests`; falta el job
  periódico (Celery beat) que la invoque — es wiring de despliegue.
- La integración motor ↔ agent loop en ejecución no está cableada:
  Fase F entrega el motor; el agente que lo invoca a mitad de bucle es
  trabajo posterior.
- La notificación es sólo in-app; los canales externos son Plan 10.

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — Fase F, task_02_24..28.
- Motor: `apps/api-server/.../db/approval_repo.py`, `routers/approvals.py`,
  migración `0012_approval_requests`.
- UI: `apps/admin-panel/app/admin/approvals/page.tsx`.
- Tests: `tests/integration/test_human_approval_motor.py`,
  `test_approval_timeout.py`; `apps/admin-panel/e2e/approval-request.spec.ts`,
  `approval-ui.spec.ts`.
- ADR 0012 (aislamiento de contenedores) y ADR 0013 (agent loop).
- Documento maestro, secciones 7.7-7.8 (validación humana).
