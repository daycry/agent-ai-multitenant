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

### 2-bis. Readiness de la API (`/readyz`) — no es lo mismo que liveness

`/healthz` responde `200` en cuanto el proceso está en pie. Para la otra
pregunta —«¿puede atender tráfico AHORA?»— está `/readyz`, que prueba las
dependencias **críticas** (PostgreSQL y Redis, con deadline por check) y
devuelve `503` diciendo cuál falla:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/readyz   # 200
curl -s http://localhost:8001/readyz | python -m json.tool              # detalle
```

Vault, Ollama y Docling **no** entran a propósito: son opcionales, y meterlos
convertiría un auxiliar caído en un flapping de readiness de la api-server
entera.

Quién lo consulta, que es lo que le da sentido:

| Consumidor                       | Qué hace con el 503                                          |
| -------------------------------- | ------------------------------------------------------------ |
| **Caddy** (`health_uri /readyz`) | deja de enrutar a la api-server; la repone al volver el 200  |
| **Smoke post-despliegue**        | `tests/smoke/test_smoke.py::test_readyz` falla el despliegue |

Lo que **no** lo consulta, y es deliberado: el `healthcheck` del contenedor de
la api-server, que sigue en `/healthz`. Docker sólo admite uno por contenedor y
el watchdog reinicia lo que sale `unhealthy`; apuntarlo a readiness haría que
una BD caída reiniciase la api-server en bucle sin arreglar nada.

Síntoma típico: `docker compose ps` dice `healthy` y el navegador recibe **503
con `Server: Caddy`**. No es el proxy: es la api-server declarándose no lista.
Mira el cuerpo de `/readyz` para saber si es PostgreSQL o Redis.

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
- `GET /readyz` devuelve `200` con los dos checks (`postgresql`, `redis`) en
  `ok`. Si da `503`, el stack está arrancado pero **no** sirviendo tráfico.
- `GET /admin/system-health` devuelve `status: ok` y ningún servicio
  en `down`.

Si algún servicio sigue caído, continúa con
[restart-services.md](./restart-services.md).
