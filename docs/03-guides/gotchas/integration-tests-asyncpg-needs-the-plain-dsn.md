---
title: "En tests de integración, asyncpg necesita `migrations_pg_dsn`, no `admin_database_url`"
area: tests/integration, postgres
encountered: 2026-07-25
stack: pytest, asyncpg, SQLAlchemy async
---

## Síntoma

Un test de integración nuevo revienta antes de tocar nada:

```
asyncpg.exceptions._base.ClientConfigurationError: invalid DSN: scheme is
expected to be either "postgresql" or "postgres", got 'postgresql+asyncpg'
```

## Causa raíz

Los fixtures exponen la misma base con **dos formas de DSN**, para dos clientes
distintos:

| Fixture                                   | Forma                    | Para                                           |
| ----------------------------------------- | ------------------------ | ---------------------------------------------- |
| `app_database_url` / `admin_database_url` | `postgresql+asyncpg://…` | **SQLAlchemy** (el `+driver` es sintaxis suya) |
| `migrations_pg_dsn`                       | `postgresql://…`         | **asyncpg directo** (`asyncpg.connect`)        |

Los helpers de siembra y truncado de los tests hablan asyncpg a pelo, así que
piden el DSN plano. Pasarles el de SQLAlchemy falla en el primer `connect`.

## Fix

En los helpers que usan `asyncpg.connect(...)`, pedir `migrations_pg_dsn`:

```python
def test_algo(configured_app, migrations_pg_dsn: str, test_redis_url: str) -> None:
    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug="t1")
```

`admin_database_url` sigue siendo el correcto para lo que consume la app
(`monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)`).

## Cómo verificar el fix

El test llega a hacer aserciones en vez de morir en el `connect`.
