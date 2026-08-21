---
title: "El api-server usa DOS URLs de BD; la que olvidas apunta a la base del operador"
area: tests/integration, api-server, CI, arnés e2e
encountered: 2026-06-17
updated: 2026-08-20
stack: pytest + FastAPI + SQLAlchemy async (dos engines: app_user + service_user)
---

> Esta nota tiene DOS casos con la misma causa. El segundo (arnés e2e) es el
> peligroso: su síntoma es benigno y su consecuencia no lo es.

## Síntoma (caso 1) — verde en local, rojo solo-en-CI

Tres tests de `tests/integration/test_auth.py` (login / me / logout) fallan
**solo en CI** con:

```
asyncpg.exceptions.UndefinedTableError: relation "user_mfa_totp" does not exist
```

La suite COMPLETA en local pasa (test_auth verde). El diag prueba que un
`alembic upgrade head` limpio crea `user_mfa_totp` en CI, la cadena de
migraciones es lineal y ninguna la dropea. Desconcertante: parece que la tabla
"desaparece" solo en el runner.

## Causa raíz (común a los dos casos)

El api-server usa **dos engines** (`api_server/db/session.py`):

- `get_engine()` → `API_SERVER_DATABASE_URL` (rol `app_user`, NOBYPASSRLS).
- `get_admin_engine()` → `API_SERVER_ADMIN_DATABASE_URL` (BYPASSRLS), que usan
  los endpoints `/admin/*` **y** la probe de MFA del login
  (`auth/mfa/store.py::user_mfa_methods`, que corre sin contexto de tenant).

> Desde prod-14 (task_05, hallazgo tenancy-2) el segundo conecta como
> `service_user` —BYPASSRLS pero **sin DDL**— y no como `migrations_user`, que
> era el OWNER del esquema y ponía `ALTER TABLE … DISABLE ROW LEVEL SECURITY`
> dentro del radio de explosión de `/admin/*`. `migrations_user` hoy solo lo usa
> Alembic. El fallo de esta nota es idéntico con cualquiera de los dos roles.

Lo decisivo es **cuál es su valor por defecto**: `Settings.admin_database_url`
(`apps/api-server/src/api_server/config.py`) apunta a
`…@localhost:15432/agentic_platform`. O sea: **si no lo seteas, va a la base de
datos real de quien esté delante.**

En el caso 1, el fixture `configured_app` **duplicado** en `test_auth.py` seteaba
`API_SERVER_DATABASE_URL` pero **olvidaba** `API_SERVER_ADMIN_DATABASE_URL`:

- **En local pasaba por casualidad:** esa BD por defecto es la **dev**, que está
  migrada y tiene `user_mfa_totp`. El test "funcionaba" consultando la BD
  equivocada.
- **En CI fallaba:** el compose solo migra `agentic_platform_test`; la BD por
  defecto está **vacía** → `relation … does not exist`.

Solo fallan los tests que llegan a la probe MFA (login OK, /me, logout); login
con password mala o email desconocido dan 401 antes y pasan.

Forense decisivo: una sonda con conexión fresca al DSN admin por defecto devolvió
`relation "alembic_version" does not exist` → la BD que consultaba estaba
**vacía**, confirmando que no era un problema de la tabla sino del DSN.

## Síntoma (caso 2) — un 500 en el login del arnés, y de fondo una escritura en la BD del operador

Medido el 2026-08-20 levantando el arnés de los 12 specs de Playwright que
necesitan backend vivo. La receta creaba una BD desechable, la migraba, y
arrancaba `uvicorn` con **una sola** variable:

```bash
DATABASE_URL=…/e2e_vivo  uvicorn api_server.main:app --port 8001   # ← INCOMPLETO
```

El síntoma fue un **500 en el login**. Benigno, aburrido, de los que se depuran
mirando la query. Lo que había debajo no lo era: `admin_database_url` había caído
a su default, y su default es **`agentic_platform`, la base del stack vivo del
operador** — 110 tablas, revisión de Alembic real, datos reales.

**Aquel día se salvó por accidente**: el default trae la contraseña _placeholder_
de desarrollo y el stack tiene otra en `docker/.env`, así que la conexión fue
rechazada. Con la contraseña buena, el arnés habría leído y —por los caminos
`/admin/*`, que insertan en `audit_log`— **escrito** en la base de producción del
operador, mientras el test de al lado se ponía verde.

O sea que la lección no es «acuérdate de la segunda variable». Es que **el modo
de fallo por defecto de este sistema es escribir en la base equivocada**, y lo
único que lo delata es un error genérico.

## Fix

Todo arnés que arranque la app —fixture de pytest o `uvicorn` a mano— debe pinear
**los dos** DSN a la BD desechable:

```python
monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)  # ← imprescindible
```

```bash
# el equivalente en un arnés lanzado a mano; las contraseñas SIEMPRE del
# fichero (docker/.env está en .gitignore), nunca literales en un script
export API_SERVER_DATABASE_URL="postgresql+asyncpg://app_user:$APP_PASS@localhost:15432/e2e_vivo"
export API_SERVER_ADMIN_DATABASE_URL="postgresql+asyncpg://service_user:$SERVICE_PASS@localhost:15432/e2e_vivo"
```

Para el arnés de Playwright eso ya está hecho en
[`scripts/dev/e2e-live-harness.ps1`](../../../scripts/dev/e2e-live-harness.ps1),
que además imprime los dos DSN al arrancar — precisamente para que el que falta
se vea antes de correr un test, y no después.

**Regla general:** prefiere el `configured_app` de `tests/integration/conftest.py`
en vez de duplicarlo por módulo. Si lo duplicas, copia **todos** los `setenv`, no
solo el de la BD de app. Un override de BD incompleto es invisible en local (la
BD dev tapa el agujero) y solo estalla en CI, donde no existe BD dev.

## Cómo verificar el fix

Antes de correr un solo test, pregúntale al proceso a qué bases está conectado —
la lista de conexiones de PostgreSQL no miente y no depende de leer bien un
`.env`:

```sql
SELECT datname, usename, count(*)
  FROM pg_stat_activity
 WHERE usename IN ('app_user', 'service_user')
 GROUP BY 1, 2;
```

Si aparece **una fila con la BD del stack** (`agentic_platform`) mientras crees
estar corriendo contra la desechable, tienes este bug. Todas las filas deben
nombrar la BD del arnés.

Síntoma reutilizable: `relation X does not exist` o `permission denied`
**solo en CI**, o un 500 opaco en el login del arnés, para una query que va por
el admin engine ⇒ sospecha de `API_SERVER_ADMIN_DATABASE_URL` sin override.

## Relacionado

- [`postgres-alter-default-privileges-per-db.md`](./postgres-alter-default-privileges-per-db.md)
  — el otro fallo de la misma familia: la migración no trae los GRANT, y el login
  cae con `permission denied for table user_mfa_totp`.
- [`integration-tests-asyncpg-needs-the-plain-dsn.md`](./integration-tests-asyncpg-needs-the-plain-dsn.md)
  — `asyncpg.connect` no acepta el DSN de SQLAlchemy.
