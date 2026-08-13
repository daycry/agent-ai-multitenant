---
title: Un `flush()` fallido mata la transacción EXTERIOR, aunque vaya dentro de `begin_nested()`
area: sqlalchemy
encountered: 2026-08-13
stack: SQLAlchemy 2.0.49 async, asyncpg, FastAPI (sesión por request con `async with session.begin()`)
---

## Síntoma

Quieres capturar una `IntegrityError` y **seguir consultando** en la misma
petición (por ejemplo, para proponer un nombre libre después de un choque de
unicidad). Envuelves el flush en un SAVEPOINT, que es el idioma documentado:

```python
try:
    async with session.begin_nested():
        await session.flush()
except IntegrityError:
    ...
    await session.execute(select(Agent.name).where(...))   # ← revienta aquí
```

y la consulta siguiente no llega a la BD:

```
sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside
context manager.  Please complete the context manager before emitting further
commands.
```

## Causa raíz

El SAVEPOINT sí se deshace, pero **la transacción exterior queda `DEACTIVE`**.
Comprobado a mano contra PostgreSQL con SQLAlchemy 2.0.49: tras el flush fallido,
`sync_session._transaction._state` es `SessionTransactionState.DEACTIVE` y
`_trans_context_manager._transaction_is_active()` es `False`. `_trans_ctx_check`
—que corre en `_connection_for_bind`, es decir en CUALQUIER `execute` posterior—
levanta el `InvalidRequestError`.

El idioma de la documentación de SQLAlchemy («recorre registros, mete cada uno en
su `begin_nested()` y sigue») funciona cuando la sesión está en autobegin, sin
transacción exterior abierta **como context manager**. Aquí sí la hay:
`auth/deps.open_tenant_session` abre `async with session.begin()` para toda la
petición, y ese CM es justo el que `_trans_ctx_check` mira.

Y la salida obvia —`await session.rollback()` completo para desatascar la
sesión— tiene una trampa peor en este proyecto: se lleva por delante el
`set_config('app.tenant_id', …, is_local := true)` que instala la RLS, porque es
de ámbito TRANSACCIÓN. La consulta siguiente corre **sin tenant**, la RLS no
devuelve NADA y el código no falla: devuelve un resultado vacío que parece
legítimo. Del rollback sales con una respuesta silenciosamente equivocada.

## Fix

**Preguntar ANTES del flush, no después.** El `except IntegrityError` se queda
—es el único que cierra la carrera entre dos peticiones simultáneas, porque la
autoridad es el índice—, pero cualquier consulta que necesites para construir la
respuesta se hace mientras la transacción sigue viva:

```python
taken = await taken_agent_names(session, tenant_id=..., project_id=..., prefix=name)
if name in taken:
    raise agent_name_conflict(...)          # camino normal, con buen mensaje
try:
    await session.flush()
except IntegrityError as exc:
    await session.rollback()                 # aquí ya no se consulta nada más
    raise agent_name_conflict(...) from exc  # carrera: best-effort con lo leído
```

Ver `apps/api-server/src/api_server/routers/_agent_names.py` (el módulo entero
existe por esto) y `routers/_integrity.flush_or_conflict` para el caso simple, en
el que basta con `rollback()` + 409 porque no hay que consultar nada después.

Dos detalles que van con el fix:

- La consulta previa va bajo `with session.no_autoflush:`. El objeto que va a
  chocar YA está `session.add`-eado, y un autoflush lo insertaría dentro de la
  propia consulta: la misma `IntegrityError` que venías a evitar, ahora desde el
  sitio que venía a explicarla.
- Un pre-check NO sustituye al `except`: dos peticiones simultáneas lo pasan las
  dos. Sin el `except`, la carrera vuelve a salir como 500.

## Cómo verificar el fix

```
TEST_PG_DB_NAME=agentic_fork409 .venv/Scripts/python.exe -m pytest \
  tests/integration/test_fork_duplicate_name_409.py -q -p no:randomly --timeout=900
```

Los seis tests en verde. Con la variante de SAVEPOINT, cuatro de ellos fallan con
`InvalidRequestError: Can't operate on closed transaction inside context manager`
en lugar de devolver el 409.
