---
title: "Readiness en el `healthcheck` del contenedor = bucle de reinicios (con watchdog)"
area: docker
encountered: 2026-08-19
stack: Docker Compose, Caddy 2.8, apps/watchdog, api-server /healthz + /readyz
---

## Síntoma

Aún no ha pasado en producción, y este fichero existe para que no pase: es la
«mejora» que cualquiera haría al ver que `/readyz` no lo consultaba nadie.

Si se cambia el healthcheck del contenedor de la api-server de `/healthz` a
`/readyz`, en cuanto PostgreSQL o Redis se caigan un rato se ve esto:

```
$ docker compose ps
api-server   Restarting (0) 4 seconds ago
```

y en bucle. Los logs del arranque anterior —los que dirían POR QUÉ— se los lleva
cada reinicio, así que el diagnóstico es más difícil justo cuando más falta hace.

## Causa raíz

Son tres piezas que por separado están bien:

1. **Docker admite UN healthcheck por contenedor.** No hay forma de declarar
   liveness y readiness por separado; lo que se ponga ahí es lo único que el
   runtime conoce.
2. **`/readyz` prueba dependencias externas** (PostgreSQL y Redis). Es su razón
   de ser: contesta 503 cuando el proceso está vivo pero no puede atender.
3. **El watchdog de esta plataforma reinicia lo que sale `unhealthy`**
   (`apps/watchdog/src/watchdog/service_monitor.py`: `status()` prefiere
   `State.Health.Status` y `restart()` entra con backoff).

Encadenadas: BD caída → `/readyz` 503 → contenedor `unhealthy` → el watchdog
reinicia la api-server. Reiniciar **no arregla la BD**, y además tira las
conexiones sanas que le quedaban y borra los logs. La sonda que debía informar
se convierte en la causa de un segundo incidente.

Es el mismo razonamiento que hay escrito en el módulo
`api_server.routers.health` para no meter PostgreSQL en `/healthz`, sólo que
por el otro lado.

## Fix

**Liveness al contenedor, readiness al proxy.** Cada pregunta a quien puede
actuar sobre ella:

| Pregunta            | Endpoint   | Consumidor               | Qué hace                                  |
| ------------------- | ---------- | ------------------------ | ----------------------------------------- |
| ¿hay que reiniciar? | `/healthz` | `healthcheck` + watchdog | reinicia el contenedor                    |
| ¿le mando tráfico?  | `/readyz`  | Caddy (`health_uri`)     | deja de enrutar y repone al volver el 200 |

En el Caddyfile (generado por `installer_backend.proxy_generator._API_UPSTREAM`
y en `docker/caddy-manuals/Caddyfile`):

```caddyfile
reverse_proxy api-server:8000 {
  health_uri /readyz
  health_interval 10s
  health_timeout 5s
  health_status 2xx
}
```

Caddy responde **503 con `Server: Caddy`** («no upstreams available») mientras
el backend no esté listo, y lo repone solo en el siguiente check tras el 200 —
sin reiniciar nada y sin intervención.

Comprobado con Caddy 2.8 real contra un upstream que devuelve 503 en `/readyz`
y 200 en todo lo demás: por el proxy sale 503 aunque el upstream sirva esa misma
ruta.

La asimetría la fija `tests/unit/test_readyz_has_a_consumer.py`, que afirma las
DOS mitades: que el proxy mira `/readyz` **y** que el healthcheck del contenedor
sigue en `/healthz`. Con sólo la primera, quien hiciera «la mejora» de arriba
seguiría en verde.

## Relacionado

- `apps/api-server/src/api_server/routers/health.py` — por qué la lista crítica
  es corta (Vault/Ollama/Docling quedan fuera a propósito).
- [`docs/06-runbooks/health-check.md`](../../06-runbooks/health-check.md) §2-bis
  — cómo se consulta y cómo se lee un 503.
- [`localhost-ipv6-primero-cuesta-dos-segundos.md`](./localhost-ipv6-primero-cuesta-dos-segundos.md)
  — el otro modo de fallo de `/readyz`, éste sí visto en el arnés.
