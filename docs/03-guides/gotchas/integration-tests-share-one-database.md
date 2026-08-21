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
TEST_REDIS_URL="redis://:${P}@localhost:6379/14" \
  .venv/Scripts/python.exe -m pytest tests/integration/test_x.py -q -p no:randomly
```

(`P=$(grep '^REDIS_PASSWORD=' docker/.env | cut -d= -f2-)` antes, porque **esa
URL lleva contraseña**: desde prod-10 Redis arranca con `--requirepass` y una
URL pelada falla con `AuthenticationError` dentro de una fixture — ERROR y no
FAILED, con el traceback apuntando al parser de `redis-py`. Ver
[redis-con-contrasena-rompe-la-integracion.md](redis-con-contrasena-rompe-la-integracion.md).)

Redis trae 16 DBs (0-15). La 15 es el default de los tests, y **la 0, la 1 y la
2 son del stack de docker-compose**: streams de eventos, broker de Celery y
result backend. Reparte entre la 5 y la 14 — nunca por debajo. Apuntar el arnés
a la 1 no da un error de conexión: da un rojo mentiroso tres capas más allá,
porque el worker vivo drena la cola que el test acaba de llenar
([tests-de-integracion-en-la-redis-del-stack-vivo.md](tests-de-integracion-en-la-redis-del-stack-vivo.md)).
Si el choque fuese entre tests del mismo fichero, que cada uno mintee su sesión
**después** del flush, no antes.

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

## Y el cuarto caso, el que no se ve: las tres tablas SIN `tenant_id` (2026-08-20)

Los tres de arriba dan un error. Éste no: da **verde hasta que cambia el orden**.

CI reparte los ~547 ficheros de integración entre **cuatro shards por
round-robin** (`find | sort` + módulo, ver `tests/unit/test_ci_integration_shards.py`),
y los ~137 de cada shard corren en **un solo proceso** contra la misma BD. Casi
todo aguanta porque casi todo lleva `tenant_id` y cada fichero siembra su tenant.
Tres tablas no:

| Tabla               | Qué deja el fichero anterior                                               |
| ------------------- | -------------------------------------------------------------------------- |
| `platform_settings` | un ajuste con un valor no-default… y su copia en la caché Redis (TTL 30 s) |
| `llm_providers`     | una fila `ollama` **activa**, que gana al doble de LLM que el test inyecta |
| `model_prices`      | precios que hacen que un cálculo de coste dé otro número                   |

El síntoma es siempre el mismo y siempre engaña: **pasa en solitario, falla en
lote**, y el shard donde cae depende de cuántos ficheros haya en el árbol — o
sea, que **añadir un test en cualquier parte reordena los cuatro shards** y
enciende un rojo en un fichero que nadie tocó.

Dos casos reales, los dos del 2026-08-19:

- cuatro rojos de `test_memory_skip_reason`: `_select_distiller` elige destilador
  en tres escalones (provider del agente → fila ACTIVA del catálogo → factoría
  inyectada), así que el `ollama` que dejaban `test_cortex_model_settings` y
  `test_assistant_provider_teardown` ganaba al doble. El memorizer salía a
  `http://ollama:11434` y los cuatro tests morían en `llm_error`;
- seis rojos de memoria: el `TRUNCATE platform_settings` del seed borraba la
  fila pero no la **entrada cacheada**, así que la lectura seguía sirviendo lo
  del fichero anterior.

Y el modo de fallo de fondo es peor que cualquiera de los dos: **un `_seed` que
trunca doce tablas y se deja una es indistinguible de uno correcto** hasta que
cambia el reparto.

### El arreglo: el estado conocido lo garantiza el conftest, no el recuerdo de cada fichero

`tests/integration/conftest.py` monta dos fixtures automáticas, con dos
granularidades distintas a propósito:

- **`_global_tables_baseline`** (scope de **módulo**) — `TRUNCATE
platform_settings, model_prices, llm_providers` al empezar cada FICHERO. Vacías
  es exactamente el estado tras `alembic upgrade head`: ninguna migración ni seed
  de arranque las siembra, así que no se borra la semilla de nadie (lo ancla
  `tests/unit/test_integration_global_baseline.py`). Por módulo y no por test
  porque la fuga invisible es la que cruza ficheros —dentro de un fichero el
  orden es fijo y el rojo se reproduce en local—, y truncar por test rompería a
  quien siembre un proveedor en un test y lo lea en el siguiente.
- **`_platform_setting_cache_baseline`** (scope de **función**) — borra las
  claves `psetting:*` de la Redis del arnés al empezar cada TEST. Ésta sí muerde
  entre tests del mismo fichero, y purgar cuesta un `DEL`.

Los módulos que no tocan la BD (`test_egress_proxy`, `test_container_isolation`,
`test_no_docker_socket`… — Docker puro) quedan fuera y siguen corriendo sin
PostgreSQL: lo decide `_module_uses_the_database`, mirando si algún test del
módulo pide una fixture de BD.

**Lo que esto NO te ahorra**: escribir un ajuste por SQL crudo **y releerlo en el
mismo test** sigue leyendo lo que se cacheó al principio del test. Para eso, la
regla del otro gotcha sigue en pie — invalida como haría la escritura real
(`invalidate_platform_setting_cache`), ver
[arreglar-la-cache-rompe-tests-que-vivian-de-su-fallo.md](arreglar-la-cache-rompe-tests-que-vivian-de-su-fallo.md).

## Cómo verificar el fix

Dos sesiones simultáneas con nombres distintos terminan las dos en verde:

```bash
TEST_PG_DB_NAME=agentic_platform_test_a .venv/Scripts/python.exe -m pytest tests/integration/test_me_endpoint.py -q -p no:randomly &
TEST_PG_DB_NAME=agentic_platform_test_b .venv/Scripts/python.exe -m pytest tests/integration/test_me_endpoint.py -q -p no:randomly
```

Con el nombre por defecto en las dos, al menos una revienta.
