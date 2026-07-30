---
title: Entrypoint root→setpriv sin HOME — asyncpg EACCES en /root/.postgresql
area: docker
encountered: 2026-07-02
stack: docker · setpriv · asyncpg · workers
---

## Síntoma

Tras introducir un entrypoint que arranca como root (self-heal de permisos de
`/data/agent-platform`) y degrada a uid 1000 con `setpriv`, TODO lo que toca la
BD en los workers falla en silencio (warnings, no crash):

```
maintenance.promote_ready_plans.error error=[Errno 13] Permission denied: '/root/.postgresql/postgresql.key'
```

El promotor del DAG deja de despachar (las tasks se quedan en `backlog`), los
sweeps no corren, y el healthcheck de celery sigue verde — el fallo solo se ve
en los logs.

## Causa raíz

`setpriv --reuid=1000` cambia el uid pero NO recalcula el entorno: el proceso
degradado hereda `HOME=/root` del root que ejecutó el entrypoint. `asyncpg`
(libpq-style) busca el certificado cliente en `$HOME/.postgresql/postgresql.key`
en CADA conexión; con `HOME=/root` ilegible para uid 1000, el `stat` devuelve
EACCES y asyncpg lo propaga como error de conexión.

Con el `USER 1000` clásico del Dockerfile no pasaba: Docker fijaba un HOME
inofensivo y el lookup terminaba en ENOENT (ignorado).

## Fix

En el entrypoint, exporta un HOME legible/escribible ANTES del drop:

```sh
export HOME=/tmp
exec setpriv --reuid=1000 --regid=1000 --clear-groups "$@"
```

(`apps/workers/docker-entrypoint.sh`.)

## Cómo detectarlo rápido

```powershell
docker logs agentic-platform-workers-1 --since 10m | Select-String "postgresql.key"
```

Cualquier `Permission denied: '/root/...'` en un proceso que debería correr como
uid 1000 = HOME heredado de root.
