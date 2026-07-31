---
title: "Dos pytest de integración a la vez se destruyen la base de datos: `TEST_PG_DB_NAME` por proceso"
area: tests, workflows
encountered: 2026-07-29
stack: pytest, asyncpg, PostgreSQL, Workflow `parallel()`
---

## Síntoma

Dos o más procesos de pytest corren `tests/integration/` a la vez (varios agentes
en paralelo, o una suite en background mientras verificas otra cosa a mano). Los
fallos que salen no tienen nada que ver con lo que se está tocando:

- `asyncpg.exceptions.InvalidCatalogNameError: database "agentic_platform_test" does not exist`
- `relation "tenants" does not exist` en un test que ayer pasaba
- `UndefinedTableError` a mitad de una sesión que empezó bien
- fallos que no se reproducen al re-correrlos solos

## Causa raíz

`tests/integration/conftest.py` monta su base de datos con una fixture
**session-scoped** que hace, literalmente, `DROP DATABASE IF EXISTS` +
`CREATE DATABASE` al abrir la sesión de pytest, y `DROP DATABASE` al cerrarla —
previo `pg_terminate_backend` de **todo el que esté conectado**.

El nombre es uno solo para todo el repo: `agentic_platform_test`. Así que el
segundo pytest que arranca le tira la base de datos al primero por debajo, y
encima le corta las conexiones. Al terminar, cualquiera de los dos la dropea
mientras el otro sigue trabajando.

El propio docstring de la fixture ya advertía de la mitad del problema («DO NOT
run this suite under pytest-xdist»), pero la advertencia habla de `-n`, y es fácil
leerla como «no uses xdist» en vez de como lo que de verdad dice: **una sola BD
compartida, un solo proceso a la vez**. Con agentes en paralelo no hay ningún `-n`
por ninguna parte y el problema es idéntico.

## Fix

`conftest.py` lee el nombre de la BD del entorno, así que dar a cada proceso el
suyo es todo lo que hace falta:

```bash
TEST_PG_DB_NAME=agentic_platform_test_mi_carril \
  .venv/Scripts/python.exe -m pytest tests/integration/test_x.py -q -p no:randomly
```

Reglas prácticas:

1. **Un proceso de integración a la vez**, o un `TEST_PG_DB_NAME` distinto por
   proceso. En un `parallel()` de N agentes, sufija el nombre con el id del carril.
2. Las BDs quedan dropeadas al terminar la sesión. Si un proceso muere a lo bruto,
   la suya sobrevive: `DROP DATABASE` a mano, o déjala, que la próxima sesión con
   ese nombre la recrea.
3. **Al ver un error de «la base/tabla no existe», comprueba primero si hay otro
   pytest corriendo** antes de tocar el test. Es el mismo modo de fallo que
   [un «flaky» reportado por revisores en paralelo](workflow-parallel-review-source-contamination.md):
   lo caro no es el fallo fantasma, es «arreglar» un test que estaba bien.

## Y su hermano gemelo en Redis: `401 session has been revoked`

Redis tiene el mismo problema y **su síntoma no se parece a su causa**. El conftest
apunta todo a la DB 15 (`redis://localhost:6379/15`) y varios fixtures la flushean.
Como las sesiones de autenticación viven en Redis, un test que flushea le borra la
sesión al de al lado, y ese falla en el **middleware de auth** con

```
401 {"detail":"session has been revoked"}
```

es decir, **sin llegar nunca al código bajo prueba**. Parece un fallo de la feature
y es un fallo de vecindad. La firma para reconocerlo:

- en aislamiento pasa;
- invirtiendo el orden de los dos tests, pasan los dos;
- el `assert` que falla no es el de la aserción, es un `401`/`403` inesperado.

El mecanismo, en tres líneas del conftest: `TEST_REDIS_URL` sale de
`os.environ.get(..., "redis://localhost:6379/15")` (:33) — o sea, es
parametrizable pero **el default es único para todo el repo** —, y la fixture de
app llama a `_flush_redis()` → `flushdb()` en **cada** setup (:228). Con dos
procesos, el `flushdb` de uno cae entre el minteo de sesión y la petición HTTP
del otro.

Mismo fix, y hay que poner **las dos** variables, no solo la de Postgres:

```bash
TEST_PG_DB_NAME=agentic_platform_test_mi_carril \
TEST_REDIS_URL=redis://localhost:6379/14 \
  .venv/Scripts/python.exe -m pytest tests/integration/test_x.py -q -p no:randomly
```

Redis trae 16 DBs (0-15); la 0 es la de desarrollo y la 15 el default de los
tests, así que reparte entre la 1 y la 14. Si el choque fuese entre tests del
mismo fichero, que cada uno mintee su sesión **después** del flush, no antes.

## Y el tercer caso: `test_migrations.py` no puede ir en un lote

`tests/integration/test_migrations.py` hace **downgrade hasta base** sobre la BD
compartida de la sesión. Si en ese lote hay otros ficheros que ya crearon filas,
la cadena de bajada revienta a media altura:

```
FAILED test_migrations.py::test_downgrade_base_drops_all_tables
FAILED test_migrations.py::test_upgrade_downgrade_upgrade_is_idempotent
FAILED test_migrations.py::test_fk_cleanup_migration_is_reversible
```

Y en solitario pasa. **No es una migración rota: es la invocación.** Corre ese
fichero SIEMPRE en su propia sesión:

```bash
# el lote de lo que has tocado, SIN test_migrations
TEST_PG_DB_NAME=..._lote  TEST_REDIS_URL=...  pytest tests/integration/test_a.py test_b.py -q -p no:randomly
# y las migraciones aparte
TEST_PG_DB_NAME=..._migr  TEST_REDIS_URL=...  pytest tests/integration/test_migrations.py -q -p no:randomly
```

El aviso ya estaba en el docstring de la fixture `test_database_url` («some tests
depend on execution order, e.g. `test_migrations.py`»), y aun así se cayó en la
trampa al armar un lote ordenado alfabéticamente. De ahí que quede escrito aquí,
donde se busca cuando algo falla, y no solo donde se explica el diseño.

## Cómo verificar el fix

Dos sesiones simultáneas con nombres distintos terminan las dos en verde:

```bash
TEST_PG_DB_NAME=agentic_platform_test_a .venv/Scripts/python.exe -m pytest tests/integration/test_me_endpoint.py -q -p no:randomly &
TEST_PG_DB_NAME=agentic_platform_test_b .venv/Scripts/python.exe -m pytest tests/integration/test_me_endpoint.py -q -p no:randomly
```

Con el nombre por defecto en las dos, al menos una revienta.
