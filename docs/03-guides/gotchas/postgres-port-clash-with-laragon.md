---
title: Postgres host port 5432 lo ocupa Laragon (u otro postgres local)
area: postgres
encountered: 2026-05-20
stack: docker-compose v2.x, Windows 11 con Laragon
---

## Síntoma

Conectar al postgres del compose (asyncpg / psql / etc.) desde el
host devuelve `InvalidPasswordError: password authentication failed
for user "postgres"`, **no** `connection refused`. Eso significa que
hay un postgres respondiendo en 5432 pero con credenciales distintas.

## Causa raíz

Laragon en Windows (o cualquier postgres del sistema en Linux/macOS)
ya escucha en `0.0.0.0:5432`. Nuestro container expone 5432 al host
y el sistema operativo deja que el primero que llegó conserve el
puerto; el container queda en una IP interna no accesible.

## Fix

`docker-compose.dev.yml` mapea Postgres a `15432:5432` por defecto:

```yaml
services:
  postgres:
    ports:
      - "${POSTGRES_PORT:-15432}:5432"
```

Si tu host no tiene otro postgres, pon `POSTGRES_PORT=5432` en `.env`.

Tests (`tests/integration/conftest.py`) usan `TEST_PG_PORT=15432` por
defecto.

## Cómo verificar el fix

```bash
netstat -ano | grep ":15432"
# Debe aparecer "LISTENING" con el PID de Docker Desktop.

docker compose -f base -f dev exec -T postgres psql -U postgres -c "SELECT 1"
# Funciona con la password del .env.example.
```
