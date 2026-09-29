---
plan_id: prod-13-rendimiento-y-datos
title: Rendimiento y gestión de datos — event loop, pool, retención e índices
started_at: 2026-07-31
completed_at: null
status: pending_human_validation
tasks_done: 23
tasks_total: 23
docs_language: es
---

> **Estado: entregado, pendiente de validación humana.** Las 23 casillas están
> marcadas desde el 2026-08-19 y el plan seguía etiquetado `pending_approval`
> («no empezado»). Esta entrada se escribe el 2026-09-29, en la consolidación
> del roadmap, tras volver a ejecutar sus tests declarados. Los tests humanos
> del plan siguen sin validar.

# Changelog — Plan prod-13 · Rendimiento y gestión de datos

La auditoría de producción de 2026-06 dio por bien construidos los hot paths, pero
encontró tres frentes que en producción congelaban la plataforma entera:

- **event loop bloqueado** por llamadas síncronas dentro de handlers async: las
  puertas de análisis del marketplace, boto3 y paramiko en backup, Vault sin
  timeout en el chat, y el upload de documentos entero en RAM;
- **BD sin tuning ni retención**: pool por defecto de 15 conexiones con la
  transacción retenida durante el turno LLM completo, índice FTS que no casaba
  con la query, ninguna purga ni retención;
- **endpoints sin válvulas**: listados sin paginar, `/ws/kanban` releyendo el
  stream global por socket, `/assistant/chat` sin rate limit.

Este plan cierra los 23 hallazgos asignados (`perf-1..11`, `db-2/4/6/7/8/9`,
`api-2/3/4/5/6/10`).

## Resultado por fases

- **Fase A — Event loop (tasks 01-05).** Puertas de análisis y sandbox del
  marketplace fuera del loop. Backup con `to_thread` y plazo explícito. Vault con
  timeout y secreto cacheado por proveedor durante 30 s. Upload en streaming con
  rechazo temprano, y la lista de formatos se le pregunta a `docling-serve` con
  caché y respaldo fijo. Cliente httpx compartido en los hot paths.
- **Fase B — Pool y transacciones (tasks 06-09).** Pool configurable con su
  métrica. La transacción por request ya no abarca llamadas LLM ni embeddings: la
  suite del asistente pasó de 57 verdes a 57 verdes con el cambio. `NullPool` en
  los engines por tarea Celery. Una transacción por seed.
- **Fase C — Índices y búsqueda (tasks 10-13).** FTS de chunks sobre
  `es_unaccent` (migración `0107`). Índice `(tenant_id, created_at)` en
  `executions` con predicado sargable y corte en UTC explícito. Recall vectorial
  multitenant con `iterative_scan` y `ef_search` por transacción (ADR 0152).
  Unicidad por tenant en teams, skills y agents (migración `0126`).
- **Fase D — Retención y backfill (tasks 14-16).** Beat de purga de filas
  soft-deleted. Embeds por lotes de 64 y test propio del backfill de chunks sin
  vector.
- **Fase E — Endpoints (tasks 17-23).** Paginación de conversaciones, documentos
  y citas. Keyset en listados y en el export de runs, que antes perdía en silencio
  todo lo que pasaba de 5 000 filas. `/ws/kanban` por canal de proyecto. Rate
  limit por usuario y cap por tenant en el chat. Caché Redis de membership.
  `FOR UPDATE` en transiciones críticas, con test de interleave. Los 409 ya no
  exponen el error crudo de PostgreSQL.

## Cerrada en negativo

- **Retención de tablas append-only (task 15).** El ADR 0151 eligió particionado
  nativo por tiempo en vez del job de borrado que pedía la tarea. El particionado
  lo implementó el plan `part-01-particionado-append-only`.

## Verificación (2026-09-29)

Los ficheros de test que declara el plan, ejecutados contra el Postgres de
desarrollo:

| Árbol                         | Ficheros | Resultado  |
| ----------------------------- | -------: | ---------- |
| `tests/` (unit + integration) |       34 | 270 passed |
