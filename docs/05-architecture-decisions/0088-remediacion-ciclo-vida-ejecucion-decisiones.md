---
adr: "0088"
title: Remediación del ciclo de vida de ejecuciones — autoridad de finish_status y des-diferido del autostart del review-runtime
status: accepted
date: 2026-06-27
deciders: operador, System Architect (claude-opus)
phase: remediacion-ciclo-vida-ejecucion
related: ["0087", "0086", "0063", "0084", "0085", "0019"]
docs_language: es
---

# ADR 0088 — Decisiones de la remediación del ciclo de vida de ejecuciones

## Contexto

Una auditoría exhaustiva del pipeline de ejecución de tareas y reviews
(orchestrator → worker → contenedor agent-runtime → providers → review-runtime →
persistencia → UI) encontró **41 defectos confirmados** agrupados en 8 clusters de
causa raíz que hacían fallar los runs (tareas que rebotaban a `blocked`, atascos en
`in_progress`/`in_review`, fallos operacionales espurios, estado incoherente en la
UI). La remediación se ejecutó por fases con TDD. La mayoría de los fixes son
correcciones sin decisión de producto, pero **dos** tocan política y se documentan
aquí (el operador delegó "implementa hasta acabar eligiendo la mejor opción").

## Decisión D1 — `finish_status` es autoritativo sobre el cierre como `done` (addendum a ADR 0087)

El ADR 0087 definió el `finish_status` estructurado (`success`/`failed`/`partial`)
del `submit_result` como un **hint** para la UI y el reviewer, no como el veredicto.
La auditoría (F14/F29) mostró que esto permitía una combinación incoherente: un run
con `status=done` (self-review autoritativa pasó) pero `finish_status ∈ {failed,
partial}` (el propio agente declaró que NO completó) se cerraba como `done` y su
diff se comiteaba — ocultando el fallo autoreportado.

**Decidido:** cuando la self-review aprobaría el output pero `finish_status` es
`failed` o `partial`, el run NO se cierra como `done`: se **escala a
`needs_human_review`** (abort_code `agent_reported_failure`), que el worker mapea a
`blocked` + bandeja humana. Un agente que admite que no terminó no puede ser
certificado automáticamente por un reviewer indulgente. `success`/ausente mantienen
el camino `done` sin cambios.

**Consecuencia:** menos entregables falsamente "completados"; algún run honesto-
parcial más en la bandeja humana. Es el lado correcto del compromiso bajo un gate
autoritativo (CLAUDE.md ppio 7).

## Decisión D2 — Des-diferir el autostart del review-runtime (ADR 0063)

El ADR 0063 dejó **diferido** el auto-arranque del review-runtime de validación
humana al completar un plan. La auditoría (F39/F40/F41) confirmó que el diferido
dejaba el lifecycle incompleto: los planes quedaban atascados en
`pending_human_validation` sin sesión de review (URLs 404), la expiración no
transicionaba el plan ni notificaba, y los contenedores de review no se destruían
ni se aplicaba el tope por tenant en el camino de producción.

**Decidido:** des-diferir ADR 0063 e implementar el lifecycle completo:

1. **Autostart** — al ganar la transición a `pending_human_validation`, el
   orchestrator encola `compose_review_runtime` (resolviendo `main_image` + worktree
   del plan) y notifica con `build_review_urls`. Idempotente: no spawnea si ya hay
   sesión activa (necesario porque el nuevo reconciler también puede disparar la
   transición de plan).
2. **Expiración** — el sweep marca la sesión `expired` **y** transiciona el plan
   (`pending_human_validation → blocked`) idempotentemente + notifica al owner;
   `ReviewRuntimeManager.expire_overdue` deja de ser código muerto divergente.
3. **Teardown + cap** — toda sesión terminal (approved/rejected/expired/cancelled)
   destruye sus contenedores (`docker rm -f` por label); el `DEFAULT_TENANT_CAP` se
   aplica dentro de `compose_review_runtime` antes de spawnear.

**Consecuencia:** los planes completados arrancan su validación humana de forma
automática y el ciclo se cierra sin fugas de contenedores. El aislamiento por
contenedor (ppio 2) y el egress restringido (ADR 0019) se mantienen.

## Alternativas consideradas

- **D1 mantener `finish_status` como hint puro:** rechazada — perpetúa el cierre de
  fallos autoreportados como `done`, el síntoma "deliverables de baja calidad".
- **D2 seguir diferido / acción manual:** rechazada bajo la directiva del operador de
  cerrar el ciclo; se mantiene la idempotencia para no acoplarse a un único disparador.

## Trazabilidad

Hallazgos: F14, F29 (D1); F39, F40, F41 (D2). Plan de remediación y auditoría
completa en `docs/07-changelog/remediacion-ciclo-vida-ejecucion.md`.
