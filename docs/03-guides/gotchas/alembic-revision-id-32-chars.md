---
title: El revision id de Alembic debe ser ≤ 32 caracteres
area: postgres
encountered: 2026-06-02
stack: Alembic, SQLAlchemy 2.x async, PostgreSQL 16
---

## Síntoma

Una migración con un `revision` "descriptivo" largo (e.g.
`revision = "20260601_0074_project_budget_human_cost_snapshot"`) corre
bien al generar el script, pero **al aplicarla** revienta cuando
Alembic intenta escribir la fila de estado:

```
sqlalchemy.exc.DataError:
  (asyncpg.exceptions.StringDataRightTruncationError)
  value too long for type character varying(32)
[SQL: INSERT INTO alembic_version (version_num) VALUES ($1)]
```

El fallo aparece **en tiempo de ejecución** (`alembic upgrade head`),
no al crear el fichero — fácil de pasar por alto en local si nunca se
aplica esa revisión hasta CI o despliegue.

## Causa raíz

Alembic crea la tabla de estado con la columna
`alembic_version.version_num` como **`VARCHAR(32)`** (es un valor fijo
del propio Alembic, no configurable sin parchear). El `revision` que
pones en el script se inserta tal cual en esa columna; si supera 32
caracteres, Postgres lo rechaza por truncamiento (asyncpg no trunca
silenciosamente, lanza `StringDataRightTruncationError`).

Nuestra convención de nombrar las revisiones con prefijo de fecha +
secuencia + slug (`20260601_0074_<slug>`) consume ~14 chars antes del
slug, así que un slug largo se pasa de 32 con facilidad.

## Fix

Mantener el `revision` (el id que va en `version_num`) **≤ 32
caracteres**. El detalle descriptivo va en el **nombre del fichero** y
en el docstring, no en el id:

```python
# apps/api-server/migrations/versions/20260601_0074_project_budget_human_cost.py
"""project budget + human cost snapshot

Revision ID: 20260601_0074_proj_budget_hcost   # <= 32 chars
"""

revision = "20260601_0074_proj_budget_hcost"     # 31 chars — OK
down_revision = "20260531_0073_..."               # también <= 32
```

El nombre del fichero puede ser tan largo y descriptivo como quieras;
solo el `revision`/`down_revision` (lo que viaja a `version_num`) tiene
el límite duro.

## Cómo verificar el fix

```powershell
# Longitud del id antes de aplicar:
"20260601_0074_proj_budget_hcost".Length   # -> <= 32

# Y la aplicación real:
alembic upgrade head
# corre sin StringDataRightTruncationError.
```

## Notas

- Aplica igual a `down_revision` y a cualquier id referenciado en
  `depends_on` de migraciones con ramas.
- Si heredas una revisión con id demasiado largo y **ya está aplicada**
  en algún entorno, renombrar el id rompe el historial: hay que migrar
  también la fila de `alembic_version`. Mejor acertar el id ≤ 32 desde
  el principio.
- Relacionado: las migraciones que tocan RLS deben trocear el SQL
  multi-sentencia — ver
  [asyncpg-no-multistatement.md](./asyncpg-no-multistatement.md).
