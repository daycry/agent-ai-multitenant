---
plan_id: prod-14-tenancy-defensa-profundidad
title: Multi-tenancy — defensa en profundidad (junctions, service_user, meta-test)
started_at: 2026-07-30
completed_at: null
status: pending_human_validation
tasks_done: 11
tasks_total: 11
docs_language: es
---

> **Estado: entregado, pendiente de validación humana.** Las 11 casillas están
> marcadas desde el 2026-08-19 y el plan seguía etiquetado `pending_approval`
> («no empezado»). Esta entrada se escribe el 2026-09-29, en la consolidación
> del roadmap, tras volver a ejecutar sus tests declarados. Los tests humanos
> del plan siguen sin validar.

# Changelog — Plan prod-14 · Multi-tenancy, defensa en profundidad

La auditoría de producción de 2026-06 calificó el aislamiento multi-tenant como
bueno y **no encontró fugas explotables**. Este plan no corrige una brecha: añade
las capas que faltaban para que el aislamiento no dependa de la disciplina de cada
query futura. Cierra `tenancy-1`, `-2`, `-3`, `-5`, `db-9` y `quality-8`.

## Resultado por fases

- **Fase A — Junctions (tasks 01-03).** `agent_skills`, `agent_tools`,
  `team_members` y `task_dependencies` ganan `tenant_id` y RLS (migración `0124`),
  sus caminos de escritura lo pueblan, y hay tests `cross_tenant` de denegación.
  Era una desviación literal del principio rector 1.
- **Fase B — Rol `service_user` (tasks 04-06).** BYPASSRLS y DML, sin DDL ni
  ownership. Workers, orchestrator, notification-dispatcher y el engine admin del
  api-server dejan de conectar como `migrations_user`, que es el owner: un servicio
  comprometido ya no puede hacer `ALTER TABLE … DISABLE ROW LEVEL SECURITY`.
  `migrations_user` queda sólo para Alembic. Un test demuestra que `service_user`
  no puede tocar DDL ni RLS.
- **Fase C — Meta-test de cobertura RLS (task 07).** Toda tabla con `tenant_id`
  debe tener RLS habilitada, forzada y con política; toda tabla sin `tenant_id`
  debe estar en una allowlist documentada. Cierra la clase de regresión «migración
  nueva que olvida la RLS y pasa CI en verde».
- **Fase D — Tabla `users` global (task 08).** Test de no-fuga del directorio
  sobre las dos superficies que devuelven personas en contexto de tenant.
- **Fase E — Coherencia (task 11).** Las cuatro copias de `_verify_project_visible`
  se reducen a un módulo común con sus 17 llamadas.

## Cerradas en negativo

- **ADR de endurecimiento estructural de `users` (task 09).** El ADR 0137 descarta
  dos de las tres opciones por imposibilidad técnica, no por preferencia, así que
  no había decisión de producto que firmar.
- **Unicidad `(tenant_id, name)` (task 10).** No quedaba migración que escribir:
  la hizo prod-13 en la `0126`.

## Verificación (2026-09-29)

Los ficheros de test que declara el plan, ejecutados contra el Postgres de
desarrollo:

| Árbol                         | Ficheros | Resultado  |
| ----------------------------- | -------: | ---------- |
| `tests/` (unit + integration) |       13 | 118 passed |
