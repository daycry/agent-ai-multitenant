---
plan_id: prod-03-guardrails-validacion-humana
title: Guardrails cableados y validación humana operativa
started_at: 2026-07-04
completed_at: null
status: pending_human_validation
tasks_done: 16
tasks_total: 16
docs_language: es
---

> **Estado: entregado, pendiente de validación humana.** Las 16 casillas están
> marcadas desde el 2026-08-12 y el plan seguía etiquetado `pending_approval`
> («no empezado»). Esta entrada se escribe el 2026-09-29, en la consolidación
> del roadmap, tras volver a ejecutar sus tests declarados. Los tests humanos
> del plan siguen sin validar.

# Changelog — Plan prod-03 · Guardrails cableados y validación humana operativa

La auditoría de producción de 2026-06 encontró incumplidos en producción los
principios rectores 10 y 11, aunque el código que los implementaba existía y
pasaba sus tests:

- el motor de guardrails no se invocaba en ningún flujo real, así que las
  salidas de RAG, HTTP y MCP reentraban crudas al contexto del modelo;
- el gate de aprobación emitía cuatro categorías que no coincidían con ninguna
  de las trece de las plantillas, con la categoría no listada en `auto`: ni el
  preset «Cliente Externo» detenía una sola tool.

Este plan cierra los diez hallazgos `guardrails-1` a `guardrails-10`.

## Resultado por fases

- **Fase A — Validación humana reparada (tasks 01-03).** Las 13 categorías viven
  una sola vez en `shared_domain.approval_categories`, importable desde el
  sandbox. Todas las tools cableadas, incluidas las MCP y las custom, tienen
  categoría. Un proyecto sin política hereda el preset por defecto de
  plataforma (ADR 0104, `development`), en vez de no instanciar el gate.
- **Fase B — Ciclo de vida de aprobaciones (tasks 04-06).** La resolución es
  atómica, sin la carrera check-then-act. Un beat caduca las solicitudes no
  atendidas. Aprobar autoriza **esa acción exacta, una vez**: el canje es por
  huella de tool y argumentos verbatim (ADR 0135), lo que cierra el bucle
  aprobar, volver a backlog y re-aparcar del ADR 0020.
- **Fase C — Config en capas y política de fallo (tasks 07-10).** Tabla
  `guardrail_configs` con RLS y baseline de plataforma con candados que las
  capas inferiores no pueden relajar. Cada check declara `on_error: block|warn`
  (ADR 0102 D5): una excepción del check deja de pasar en silencio. El pipeline
  corre fuera del event loop y con tope de tamaño de entrada.
- **Fase D — Cableado en los flujos reales (tasks 11-14).** El worker resuelve la
  config efectiva de las tres capas y la transporta en el spec. El runtime la
  aplica en los cuatro hooks (`pre_llm`, `post_llm`, `pre_tool`, `post_tool`).
  Los eventos se persisten desde el worker, porque el sandbox no tiene BD. El
  chat de planning y «Generar Plan» pasan por el motor.
- **Fase E — Verificación y documentación (tasks 15-16).** El e2e con el preset
  `customer-external` aparca la primera tool sensible, aborta al rechazar, no
  re-aparca al aprobar y caduca lo no atendido. Referencia en
  `docs/04-reference/guardrails.md`.

## Decisiones que se tomaron por el camino

- **Política de fallo del motor**: ADR 0102, opción (c), `on_error` por check con
  `block` para los guardrails bloqueados de plataforma.
- **Categoría no listada**: ADR 0153, clave `unlisted_category` en la política y
  plantillas completadas sobre las 13 canónicas; decidido por el operador el
  2026-08-02.
- **Qué autoriza una aprobación**: ADR 0135, acción exacta y un solo canje.

## Lo que NO entra

- **Reanudar desde el punto exacto tras una aprobación.** Hoy la tarea se
  redespacha con la aprobación canjeable en el spec. La reanudación durable es
  `harness-02` (`task_h02_07`).
- **Guardrails fail-closed cuando la política configurada no se puede resolver.**
  El worker todavía degrada a baseline con un warning. Lo recoge `harness-01`
  (`task_h01_05`), con el ADR 0168.

## Verificación (2026-09-29)

Los ficheros de test que declara el plan, ejecutados contra el Postgres de
desarrollo:

| Árbol                                        | Ficheros | Resultado  |
| -------------------------------------------- | -------: | ---------- |
| `tests/` (unit + integration)                |       24 | 302 passed |
| `docker/agent-runtimes/agent-runtime/tests/` |        5 | 31 passed  |
