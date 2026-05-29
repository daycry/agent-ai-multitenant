---
title: Comprobar la salud del stack
docs_language: es
audience: operador, system admin
updated: 2026-05-29
---

# Runbook — Comprobar la salud del stack

## Cuándo

- Tras levantar el stack, para confirmar que todo arrancó bien.
- Cuando el panel o la API responden con errores intermitentes.
- Como primer paso de cualquier incidencia: saber **qué servicio**
  está caído antes de tocar nada.

## Comprobación previa

El stack de infraestructura está definido en
`docker/docker-compose.yml` (+ `docker/docker-compose.dev.yml` en dev)
y arranca estos servicios: **postgres** (PostgreSQL 16 + pgvector +
pg_trgm), **redis**, **minio**, **vault**, **clamav**, **docling-serve**
y **egress-proxy**.

## Pasos

### 1. Estado de los contenedores

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  ps
```

Todos deben aparecer como `Up X (healthy)`. Un servicio en
`Restarting` indica un fallo de arranque; mira sus logs
(`docker compose logs <servicio> --tail 50`). Para Vault atascado en
`Restarting`, revisa
[`docs/03-guides/gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md).

### 2. Healthcheck de la API

El api-server expone un endpoint público de liveness:

```bash
curl -fsS http://localhost:8001/healthz
```

Debe devolver `200` con un JSON de estado.

### 3. Salud agregada del stack (System Admin)

El endpoint `GET /admin/system-health` (requiere JWT con `sys: true`)
consulta en paralelo cada dependencia y devuelve un estado por
servicio. Cubre: **postgres**, **redis**, **vault** (`/v1/sys/health`),
**minio** (`/minio/health/live`), **clamav** (TCP), **docling-serve**
(`/health`), **ollama** (`/api/version`) y **egress-proxy** (TCP).

```bash
TOKEN="<access_token de un System Admin>"
curl -fsS http://localhost:8001/admin/system-health \
  -H "Authorization: Bearer $TOKEN"
```

El campo `status` global es `ok` solo si PostgreSQL responde; cada
servicio reporta su propio `status` en la lista `services`. La misma
respuesta alimenta el dashboard de salud del admin-panel.

## Verificación

El runbook se considera superado cuando:

- `docker compose ps` muestra todos los servicios `healthy`.
- `GET /healthz` devuelve `200`.
- `GET /admin/system-health` devuelve `status: ok` y ningún servicio
  en `down`.

Si algún servicio sigue caído, continúa con
[restart-services.md](./restart-services.md).
