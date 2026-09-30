---
plan_id: ui-reestructuracion-2026-09-09
title: Reestructuración del panel — áreas, dashboard, proyecto como hub y recursos con patrón común
started_at: 2026-09-09
completed_at: null
status: blocked
tasks_done: 11
tasks_total: 13
docs_language: es
---

> **Estado: entregado y bloqueado por dos decisiones del operador.** Once de las trece
> casillas están cerradas con su test en verde. Las dos abiertas no dependen de más código:
> la mitad «URL» de `task_ui_21` espera al
> [ADR 0170](../05-architecture-decisions/0170-ingesta-de-conocimiento-por-url.md), y
> `task_ui_40`, la piel visual, espera a que el operador la apruebe tras ver el panel, como
> fija el plan. Cuando se decidan, el plan pasa a `pending_human_validation` para sus tests
> humanos `human_ui_01..04`.

# Changelog — ui-reestructuracion · El panel ordenado por lo que hace cada persona

El panel crecía por acumulación: cada plan añadía su pantalla al menú y el tenant acababa
con decenas de entradas planas. Este plan lo ordena en tres áreas (Trabajo, Proyecto y
Sistema), convierte el proyecto en el sitio desde el que se trabaja y da un mismo patrón a
las pantallas de recursos. Ninguna ruta anterior deja de responder: la guarda
`tests/unit/test_admin_panel_routes_preserved.py` lo comprueba.

## Qué cambió

**Tres áreas y un dashboard que dice qué espera de ti** (`task_ui_01`, `task_ui_02`, `task_ui_04`).

- El shell se partió en ocho piezas y elige la barra por ruta y por rol. El área Sistema
  sólo existe para el System Admin.
- `GET /tenant/dashboard` reúne en una respuesta lo que espera a un humano, el trabajo por
  proyecto, el gasto, la salud cuando duele, la actividad y los MCP. Tiene caché de 30 s,
  y el gasto aparte, de 300 s.
- El mapa de pantallas (`docs/04-reference/mapa-de-pantallas-del-panel.md`) dice en qué
  área y para qué rol está cada ruta, y un test falla si mapa y árbol divergen.

**Una sola bandeja humana** (`task_ui_03`). `/admin/inbox` reúne Aprobaciones, Preguntas,
Revisiones, Notificaciones y Asignadas a mí, cada una con su contador. Un contador que
carga o falla ya no se pinta como cero. Las rutas antiguas redirigen a su pestaña, y el
menú de Inicio queda en Dashboard y Bandeja humana.

**El proyecto como hub** (`task_ui_10` a `task_ui_12`, `task_ui_20`, `task_ui_21`).

- La barra del proyecto tiene ocho pestañas: Resumen, Tablero, Runs, Equipo, Capacidades,
  Conocimiento, Costes y Ajustes.
- `GET /projects/{id}/overview` calcula seis avisos: MCP sin conectar o sin tools, Vault
  no disponible, egress bloqueado, repo sin sincronizar, pausa por presupuesto y rol sin
  agente (ADR 0091). Mira 90 días de `executions` para podar particiones y cachea 30 s.
  El gasto y las pausas sólo los ve el `tenant_admin`.
- Tablero con el doble Kanban acotado al proyecto, y `/admin/board` como portfolio de
  planes de todos los proyectos.
- Runs con filtros, Equipo con los huecos de rol (una sola definición, compartida con el
  aviso) y Costes por plan, agente y modelo.
- Capacidades amplía `GET /projects/{id}/capabilities` sin romper a agentes ni equipos:
  procedencia de cada MCP, OAuth en tres estados, tools importadas y egress. La lectura de
  Vault sale del event loop, con caché de 30 s que se invalida al conectar.
- Conocimiento tiene un asistente de tres pasos, cifras exactas calculadas en SQL,
  «activar una base existente» sin subir nada y el vocabulario de ingesta dentro del
  detalle del fallo. El índice nuevo en `documents` (migración 0150) se crea con
  `CREATE INDEX CONCURRENTLY`.

**Recursos del tenant con un patrón** (`task_ui_30` a `task_ui_32`).

- `components/resource/` aplicado a Agentes, Equipos, Agentes humanos y Skills y tools,
  que ahora tiene dos pestañas. Los agentes muestran su última actividad, calculada de
  `executions`.
- Memorias por ámbito con contador, procedencia resuelta en lote, buscador híbrido y la
  regla de privadas del listado aplicada a los tres endpoints. «Cuántas veces se recuperó»
  espera a `task_mem_02` del plan `memoria-agentes-2026-09-09`.
- Ficha del agente en cinco pestañas, y marketplace en Catálogo, Instaladas y Publicar, con
  el nombre de cada proyecto donde está desplegada cada instalación.

## Endpoints nuevos o ampliados

`GET /tenant/dashboard`, `GET /projects/{id}/overview`, `/team`, `/costs`,
`/capabilities` (ampliado), `GET /projects/{id}/knowledge-bases` (con categoría y cifras),
`GET /knowledge-bases` (con cifras y proyectos concedidos),
`GET /knowledge-bases/{id}/documents?ids=`, `GET /runs?project_id=`, `GET /memories`
(ampliado), `/memories/counts`, `/memories/search`, `GET /marketplace/installations` (con
`deployments`) y `GET /agents` (con `last_activity_at`). Todos filtran por `tenant_id`
además de la RLS, y todos tienen test de integración con caso cross-tenant.

## Cómo se verificó

- **Método:** cada tarea la implementó el agente `implementer` del plugin `custom-agents`
  con la skill `tdd`. Hubo dos intentos de la skill `adversarial-review` con cuatro lentes
  (conformidad, robustez, seguridad y rendimiento). Todos los gaps Critical e Important se
  corrigieron o se llevaron a decisión del operador. La traza completa está en el plan,
  §«Revisión adversarial de las olas 2 y 3».
- **Suites:** unit 6677 passed; vitest 1792 passed (209 ficheros); integración de los
  endpoints de este plan 90 passed; `tsc`, eslint, `check-i18n`, `check-component-size`,
  `ruff` y `mypy_gate` (802 ficheros) limpios.
- **E2E:** subset mockeado de Playwright contra `next dev`. Las cifras finales están en la
  nota de cierre del plan.

## Lo que decide el operador

1. **ADR 0170**: si el asistente acepta una URL. La recomendación es no ofrecerla ahora y,
   si se quiere, hacerla con allowlist y sin red interna.
2. **Coste por run visible a miembros**: un miembro puede sumar el gasto de un proyecto con
   `/runs?project_id=`, que `/costs` le niega. Pasa desde `5211c8ac`.
3. **Agentes built-in en el dispatch**: Equipo los enseña, pero el dispatch del ADR 0091 no
   los cuenta para cubrir un rol.
4. **`task_ui_40`**, la piel visual: sólo si la aprueba tras ver el panel.

## Deuda aceptada

- «Conectar» desde Capacidades vuelve a la pantalla de MCP, porque el callback OAuth es un
  contrato del ADR 0127.
- Memorias precarga `/similar` fila a fila, algo que viene de antes del plan.
- La caché de la sonda OAuth es por proceso: la invalidación al conectar afecta al proceso
  que atiende el callback, y el resto la renueva a los 30 s.
