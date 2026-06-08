---
title: "alembic: «Can't locate revision» tras verificar una migración de otra rama contra la DB de dev"
area: alembic / postgres / dev workflow
encountered: 2026-06-03
stack: Alembic, PostgreSQL, Windows, scripts/dev/up.ps1
---

## Síntoma

`scripts/dev/up.ps1` (o cualquier `alembic upgrade head`) falla al arrancar:

```
FAILED: Can't locate revision identified by '0077_tools_dedup_taxonomy'
alembic upgrade failed
```

El número de revisión (`0077_...`) corresponde a una migración que **no
existe en la rama actual** del working tree — `ls apps/api-server/migrations/versions/`
no la muestra, y `alembic current` contra la DB de dev también falla con el
mismo error.

## Causa raíz

La tabla `alembic_version` de la **DB de dev** (`agentic_platform` en `:15432`,
la `DATABASE_URL` que usa `up.ps1`) quedó apuntando a una revisión cuyo
**fichero solo vive en otra rama no mergeada** (p. ej. `plan/06.18-...`).

Ocurre cuando un proceso —típicamente un **agente/workflow** que verifica la
reversibilidad de una migración nueva— ejecuta `alembic upgrade head` **sin
aislar la base de datos**, usando la `DATABASE_URL` por defecto (= la DB de
dev) en vez de la DB de test. La migración se aplica a la DB de dev y deja su
`alembic_version` en esa revisión. Al volver a una rama que no contiene ese
fichero (master / otra feature), Alembic no puede localizar la revisión que la
DB dice tener → no puede ni `current` ni `upgrade`.

No es corrupción: es un **desajuste rama↔DB** (la DB está "más adelantada" que
los ficheros de migración del working tree).

## Fix

Revertir la DB de dev a la última revisión que **sí** existe en la rama actual.
Como Alembic necesita el `downgrade()` del fichero ausente, se trae el fichero
temporalmente desde la rama que lo tiene, se hace el downgrade y se borra:

```bash
# 1) localizar el fichero en la rama que lo contiene
F=$(git ls-tree --name-only plan/06.18-tools-overhaul -- apps/api-server/migrations/versions/ | grep 0077)

# 2) traerlo SOLO para el downgrade
git show plan/06.18-tools-overhaul:$F > $F

# 3) revertir la DB de dev a la revisión que SÍ está en esta rama (p. ej. 0076)
(cd apps/api-server && \
  DATABASE_URL="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@localhost:15432/agentic_platform" \
  ../../.venv/Scripts/python.exe -m alembic downgrade 0076_sso_global)

# 4) borrar el fichero temporal
rm -f $F

# 5) verificar: current = 0076 y upgrade head no-op
(cd apps/api-server && DATABASE_URL="...agentic_platform" ../../.venv/Scripts/python.exe -m alembic current)   # -> 0076_sso_global (head)
```

`alembic stamp` **no** sirve aquí: dejaría el `alembic_version` en 0076 pero el
esquema con los cambios de 0077 aplicados (estado incoherente).

## Prevención

- **Las migraciones se verifican SOLO contra la DB de test**, nunca la de dev.
  En este repo: prefija `TEST_PG_PORT=15432 TEST_REDIS_URL=...` y deja que el
  conftest use `agentic_platform_test` (que recrea). Nunca corras `alembic` ni
  `python -m api_server.seeds` con la `DATABASE_URL` por defecto desde un script
  o agente de verificación.
- Si verificas reversibilidad a mano (`upgrade head && downgrade -1 && upgrade
head`), **hazlo en la DB de test** y deja la de dev en paz.
- Los **prompts de workflows** que tocan migraciones/seeds deben llevar esta
  regla explícita (aislamiento de DB), porque un agente autónomo tenderá a usar
  la `DATABASE_URL` del entorno.
- La DB de dev solo debe avanzar de revisión vía `up.ps1` sobre una rama cuyo
  head de migraciones contenga esa revisión.

## Cómo verificar el fix

```bash
# up.ps1 vuelve a arrancar sin error; o directamente:
(cd apps/api-server && DATABASE_URL="...agentic_platform" \
  ../../.venv/Scripts/python.exe -m alembic upgrade head)   # -> sin "Can't locate revision"
```
