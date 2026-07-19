---
title: "ADR 0120: Standup diario del PM agente"
status: accepted
date: 2026-07-19
---

# ADR 0120: Standup diario del PM agente

Aprobada por el operador el 2026-07-19 (tanda «adelante con todo»).

## Contexto

El sistema ya tiene todas las piezas de un parte diario — beat de Celery
(`beat_schedule`), pipeline de notificaciones multi-canal (inbox, WhatsApp
vía notification-dispatcher), agentes con rol `project_manager` y datos de
actividad (runs, tareas por estado, planes bloqueados, aprobaciones
pendientes, coste) — pero nadie las compone: el operador tiene que ir a
mirar. La información que más urge (¿qué espera a un humano? ¿qué se
bloqueó?) es exactamente la que menos se ve.

## Decisión

Una tarea beat diaria (hora operator-configurable por tenant, default 08:00)
que, por cada tenant activo, compone el **standup**: hecho ayer (tareas done,
planes cerrados), en curso, bloqueado/escalado (con enlaces), esperando
validación humana, y coste LLM del día. La redacción la hace el agente PM del
tenant (misma vía LLM que los runs, presupuesto acotado) sobre un resumen
estructurado calculado por SQL — el LLM redacta, NO decide los datos; si el
LLM falla, se envía la versión estructurada sin prosa (fail-open). Se entrega
por el pipeline de notificaciones existente (inbox siempre; WhatsApp/canales
según la config del tenant). Configuración: `platform_settings` +
override por tenant (`standup.enabled`, `standup.hour`).

## Consecuencias

- Visibilidad diaria proactiva sin abrir el panel; lo urgente llega solo.
- Composición de piezas existentes (beat + SQL + LLM + notificaciones): sin
  infraestructura nueva ni migraciones de dominio (solo settings).
- Coste LLM marginal (1 redacción/tenant/día, acotada); el fail-open evita
  que un proveedor caído silencie el parte.
