---
adr: "0020"
title: Estado `awaiting_human_approval` en la tarea — agente libre, vuelta a backlog
status: accepted
date: 2026-05-23
deciders: System Admin
phase: 02-ejecucion-agentes
---

# ADR 0020 — Estado `awaiting_human_approval` en la tarea (agente libre, vuelta a backlog)

> **Estado: `accepted`.** El System Admin eligió la **Opción A** el
> 2026-05-23. La **Opción B (rechazar con feedback que reintroduce la
> tarea al pipeline)** queda documentada abajo como alternativa por si
> en el futuro hace falta el bucle de feedback humano → agente.

## Contexto

Las Fases F (`task_02_24`/`task_02_27`) y G (`task_02_33`) implementaron
el motor de aprobación: cuando un agente intenta una acción sensible,
la **`Execution`** queda en `awaiting_human_approval` y se persiste una
`ApprovalRequest` pendiente. Funciona, pero deja una UX raquítica:

- La **`Task` se queda en `in_progress`** — el board no enseña en
  ningún sitio que esa tarea está esperando aprobación, queda como una
  ejecución más en curso.
- El agente queda virtualmente "ocupado" con esa tarea aunque su
  contenedor ya terminó — el dispatcher debería poder darle otra cosa.
- El checklist de `human_02_04` dice literalmente _"la tarea pasa a
  `awaiting_human_approval`"_ — y `TaskStatus` no tiene ese valor.

## Decisión — Opción A

Introducir **`TaskStatus.AWAITING_HUMAN_APPROVAL`** con un ciclo de
vida explícito:

1. **Aparcar** (acción sensible detectada) →
   - `Task.status = awaiting_human_approval`
   - `Task.assigned_agent_id = NULL` — el agente queda libre. Las
     consultas de carga del dispatcher (`active_task_count` filtrando
     `status == in_progress`) ya no lo cuentan, así que puede aceptar
     otra tarea sin más cambios.
   - `Execution.status = awaiting_human_approval` (sin cambios).
   - `ApprovalRequest(status=pending)` (sin cambios).
   - Se publica `task.status_changed` para que el board reaccione en
     vivo.
2. **Aprobar** →
   - `Task.status = backlog`, `Task.assigned_agent_id = NULL`.
   - La tarea entra de nuevo al pipeline normal (`backlog → ready →
in_progress`); el dispatcher la asigna libremente (al mismo
     agente o a otro según política y carga).
   - La `Execution` original se cierra como terminal (`done`, con el
     motivo de cierre y el resolver registrados en la
     `ApprovalRequest`).
3. **Rechazar** →
   - `Task.status = blocked`. Decisión humana firme: la acción no se
     hace; alguien debe intervenir manualmente.
   - La `Execution` original se cierra como `aborted` con
     `abort_code = approval_rejected`.
4. **Timeout** → ya implementado en `expire_stale_requests`:
   `Task.status = blocked`, `Execution.status = aborted`. Sin cambios.

Para acomodar la cadena `"awaiting_human_approval"` (23 caracteres) en
`Task.status` (hoy `String(16)`), nace la **migración `0013`** que
amplía la columna a `String(32)` —reversible, espejo de lo que la
migración `0012` hizo con `Execution.status`.

En el board del admin-panel, **se añade una columna "Pendiente de
aprobación"** entre "En curso" y "Revisión".

## Opción B (documentada como alternativa futura)

**Rechazar → `backlog` con el motivo del rechazo como contexto para el
agente en la siguiente ejecución** — implementaría literalmente el
checklist actual de `human_02_04` ("al rechazar vuelve a in_progress y
el agente recibe feedback").

Esto requiere que el agent loop pueda **leer ese feedback al
rearrancar** — una _resumption con contexto_ que hoy no existe. El
sustrato ya está parcialmente: `ApprovalRequest.reason` recoge el
motivo del revisor (aprobando o rechazando). Lo que falta es:

- Mecanismo para pasar el `reason` al agent-runtime en la siguiente
  ejecución de la tarea (parte de la `task_spec`, o un fragmento de
  contexto inicial).
- Idealmente, mantener el contexto previo del agente (no empezar de
  cero) — eso encaja con la **memoria** del Plan 04 y con el **pool
  elástico por plan** + worktrees compartidos del Plan 06.

Discartada por ahora: complejidad alta para el valor incremental sobre
A en esta fase. Cuando interese implementarla, este ADR es el punto de
partida.

## Limitación conocida

Con un modelo determinista, aprobar → backlog → re-ejecutar volverá a
proponer la misma acción sensible y volverá a aparcarse — bucle. Con un
LLM real puede ser distinto pero no garantizado. Una resumption
verdadera (el agente continúa desde el punto donde se aparcó, con la
decisión aprobada ya tomada) es Plan 06. Para esta iteración: la
aprobación es "borrón y cuenta nueva" y se documenta.

## Consecuencias

Cambios que entran con la aceptación:

- Migración `0013` — `Task.status` ampliado a `String(32)`, reversible.
- `TaskStatus.AWAITING_HUMAN_APPROVAL` en `api_server.db.domain`.
- `workers.execution.conduct_execution` — al aparcar, además de crear
  la `ApprovalRequest`, mueve la `Task` a `awaiting_human_approval`,
  pone `assigned_agent_id = NULL` y publica `task.status_changed`.
- `api_server.db.approval_repo.resolve_approval` — aprobar / rechazar
  mueven la `Task` según A; el router de aprobaciones publica el
  evento del board.
- `apps/admin-panel/app/admin/board/page.tsx` — columna "Pendiente de
  aprobación".
- Tests `test_live_approval_safeguards`, `test_human_approval_motor` y
  `test_approval_timeout` — actualizar expectativas.
- `scripts/demo_human_02_04.py` — reflejar el nuevo estado de la tarea
  y dirigir al revisor al board (donde ya se ve aparcada).
- Changelog del Plan 02 — anotar el refinamiento + la deuda explícita.

## Referencias

- ADR 0016 — Motor de validación humana (es este ADR su refinamiento de
  ciclo de vida y UX).
- ADR 0017 — Fase G (donde el motor se aplicó sobre el run en vivo).
- `task_02_24` / `task_02_27` (motor + timeout), `task_02_33`
  (aprobación + salvaguardas sobre el run en vivo).
- Checklist `human_02_04` en `docs/roadmap/02-ejecucion-agentes.md`.
- CLAUDE.md §11 (validación humana configurable por proyecto).
