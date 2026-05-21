---
title: `SET LOCAL x = $1` no acepta bind params con asyncpg
area: postgres
encountered: 2026-05-20
stack: asyncpg, SQLAlchemy 2.x async
---

## Síntoma

```
asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "$1"
[SQL: SET LOCAL app.user_id = $1]
```

Quieres setear una GUC scoped a la transacción para que las RLS
policies de Postgres la consuman.

## Causa raíz

`SET LOCAL` es un _utility command_ en Postgres, no una sentencia
SQL normal. asyncpg lo envía como prepared statement, y los utility
commands **no admiten parámetros bindeados**: el `$1` queda como
texto literal y el parser lo rechaza.

## Fix

Usar la función `set_config(name, value, is_local)` que **sí**
acepta parámetros y aplica el mismo scope transaccional cuando
`is_local := true`:

```python
await session.execute(
    text("SELECT set_config('app.user_id', :uid, true)"),
    {"uid": str(principal.user_id)},
)
```

## Cómo verificar el fix

Dentro de una transacción del session:

```python
await session.execute(text("SELECT current_setting('app.user_id', true)"))
# -> el UUID que pasaste, no '' ni NULL.
```

Y `tests/integration/test_isolation.py::test_cross_tenant_isolation`
pasa (RLS aplica la GUC correctamente).
