---
title: Fixture que omite API_SERVER_ADMIN_DATABASE_URL → verde en local, rojo solo-en-CI
area: tests/integration, api-server, CI
encountered: 2026-06-17
stack: pytest + FastAPI + SQLAlchemy async (dos engines: app_user + admin/migrations_user)
---

## Síntoma

Tres tests de `tests/integration/test_auth.py` (login / me / logout) fallan
**solo en CI** con:

```
asyncpg.exceptions.UndefinedTableError: relation "user_mfa_totp" does not exist
```

La suite COMPLETA en local pasa (test_auth verde). El diag prueba que un
`alembic upgrade head` limpio crea `user_mfa_totp` en CI, la cadena de
migraciones es lineal y ninguna la dropea. Desconcertante: parece que la tabla
"desaparece" solo en el runner.

## Causa raíz

El api-server usa **dos engines** (`api_server/db/session.py`):

- `get_engine()` → `API_SERVER_DATABASE_URL` (rol `app_user`, NOBYPASSRLS).
- `get_admin_engine()` → `API_SERVER_ADMIN_DATABASE_URL` (rol `migrations_user`,
  BYPASSRLS), que usan los endpoints `/admin/*` **y** la probe de MFA del login
  (`auth/mfa/store.py::user_mfa_methods`, que corre sin contexto de tenant).

El fixture `configured_app` **duplicado** en `test_auth.py` (y antes en
`test_isolation.py`) seteaba `API_SERVER_DATABASE_URL` pero **olvidaba**
`API_SERVER_ADMIN_DATABASE_URL`. Sin ese override, el admin engine cae al
**DSN admin por defecto** (`config.py`), que apunta a la BD `agentic_platform`
(no a la throwaway `agentic_platform_test`).

- **En local pasa por casualidad:** esa BD por defecto es la **dev**, que está
  migrada y tiene `user_mfa_totp`. El test "funciona" consultando la BD
  equivocada.
- **En CI falla:** el compose solo migra `agentic_platform_test`; la BD por
  defecto (`agentic_platform`) está **vacía** → `relation ... does not exist`.

Solo fallan los tests que llegan a la probe MFA (login OK, /me, logout);
login con password mala o email desconocido dan 401 antes y pasan.

Forense decisivo: una sonda con conexión fresca al DSN admin por defecto
devolvió `relation "alembic_version" does not exist` → la BD que consultaba
estaba **vacía**, confirmando que no era un problema de la tabla sino del DSN.

## Fix

Todo fixture `configured_app` que arranque la app debe pinear **ambos** DSN al
DB de test, igual que hace el canónico `tests/integration/conftest.py`:

```python
monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)  # ← imprescindible
```

(añade `admin_database_url: str` a los parámetros del fixture).

**Regla general:** prefiere usar el `configured_app` de `conftest.py` en vez de
duplicarlo por módulo. Si lo duplicas, copia **todos** los `setenv`, no solo el
de la BD de app. Un override de BD incompleto es invisible en local (la BD dev
tapa el agujero) y solo estalla en CI, donde no existe BD dev.

Síntoma reutilizable: `relation X does not exist` **solo en CI** para una query
que va por el admin engine ⇒ sospecha de `API_SERVER_ADMIN_DATABASE_URL` sin
override apuntando a una BD vacía.
