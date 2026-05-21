---
title: asyncpg no acepta múltiples sentencias en una prepared statement
area: postgres
encountered: 2026-05-20
stack: asyncpg, alembic
---

## Síntoma

```
asyncpg.exceptions.PostgresSyntaxError:
  cannot insert multiple commands into a prepared statement
```

Pasaste un bloque SQL multilinea con varios `;` a `op.execute(...)`,
o a `session.execute(text(...))`.

## Causa raíz

asyncpg envía cada `execute()` como prepared statement, y el
protocolo extendido de Postgres prohíbe múltiples sentencias por
prepared statement. (psycopg2 sí lo permitía via protocolo simple,
de ahí la sorpresa al migrar a asyncpg.)

## Fix

Trocear el script en sentencias individuales. Para migraciones
Alembic:

```python
_RLS_POLICIES_UP: tuple[str, ...] = (
    "ALTER TABLE organizations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE organizations FORCE ROW LEVEL SECURITY",
    "CREATE POLICY org_self_only ON organizations ...",
    # una por línea, sin punto y coma final
)

def upgrade() -> None:
    ...
    for stmt in _RLS_POLICIES_UP:
        op.execute(stmt)
```

## Cómo verificar el fix

`alembic upgrade head` corre sin el `PostgresSyntaxError`. Si
quieres recuperar el ergonomic de un solo string, podrías usar el
driver psycopg para esa migración concreta, pero perderías la
homogeneidad del stack.
