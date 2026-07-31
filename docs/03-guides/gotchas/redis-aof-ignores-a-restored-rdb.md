---
title: Un Redis con `appendonly yes` IGNORA el `dump.rdb` que le restauras — y arranca vacío sin dar error
area: redis
encountered: 2026-07-31
stack: redis 7.4-alpine (7.4.8 medido), docker compose v2
---

# Redis con AOF activado ignora un `dump.rdb` restaurado

## Síntoma

Restauras un backup de Redis copiando `dump.rdb` al data dir, arrancas el
contenedor, y Redis levanta **sano, healthy y VACÍO**:

```
$ docker exec redis redis-cli DBSIZE
(integer) 0
$ docker exec redis redis-cli GET alguna:clave:que:existía
(nil)
```

En los logs, en lugar de un `DB loaded from …`:

```
1:M 31 Jul 2026 19:54:51.858 * Creating AOF base file appendonly.aof.1.base.rdb on server start
1:M 31 Jul 2026 19:54:51.867 * Creating AOF incr file appendonly.aof.1.incr.aof on server start
```

Ni un error, ni un warning, ni un exit code ≠ 0. El healthcheck pasa. El restore
«funcionó».

## Causa raíz

Cuando `appendonly yes` está activo, **el AOF es la única fuente de verdad al
arrancar**; el RDB no se consulta. Si el `appendonlydir/` no existe, Redis 7 no
cae al `dump.rdb`: crea un AOF nuevo y vacío y sirve una base vacía.

El compose de esta plataforma arranca Redis con `--appendonly yes`
(`--appendfsync everysec`), así que **cualquier procedimiento de backup/restore
basado en `BGSAVE` + `dump.rdb` produce una restauración silenciosamente vacía**.
Y Redis aquí no es una caché: aloja las **sesiones de servidor**, el **broker de
Celery** (o sea, la capacidad de encolar trabajo) y los contadores de rate limit.

Medido, no inferido (`redis:7-alpine`, 2026-07-31):

| Data dir al arrancar          | `appendonly` | `DBSIZE` |
| ----------------------------- | ------------ | -------- |
| solo `dump.rdb`               | `yes`        | **0**    |
| solo `dump.rdb`               | `no`         | 1 ✔      |
| `appendonlydir/` + `dump.rdb` | `yes`        | 4 ✔      |

## Fix

**Capturar el `appendonlydir`, no el RDB**, y precederlo de un `BGREWRITEAOF`
completado en vez de un `BGSAVE`:

1. `BGREWRITEAOF` → esperar a `aof_rewrite_in_progress:0` **y comprobar
   `aof_last_bgrewrite_status:ok`** (un rewrite puede terminar habiendo fallado, y
   entonces lo que capturarías es el AOF anterior sin saberlo).
2. `tar` del data dir entero (`appendonlydir` + `dump.rdb` si está).
3. Al restaurar, **vaciar el destino** antes de extraer: un `appendonlydir`
   residual con una secuencia MÁS ALTA que la capturada le gana al restaurado,
   porque Redis lee el manifest que encuentra. Extraer «por encima» deja ficheros
   de dos generaciones y un manifest apuntando a la vieja.

Implementado en `workers.backup_consistency.RedisAofRewriter` +
`BackupEngine._tar_redis` (artefacto `redis_tar`) y en
`RestoreEngine._restore_data_artifacts` (extracción con `wipe=True`).

Si lo que tienes es solo un `dump.rdb` de un backup antiguo, la vía manual es:
arrancar con `--appendonly no` (carga el RDB), luego
`redis-cli CONFIG SET appendonly yes` (reescribe el AOF **desde memoria**;
verificado: `aof_last_bgrewrite_status:ok`), y volver a la config normal.

## Cómo verificar el fix

Tras cualquier restore, dos comprobaciones — la segunda es la que distingue
«cargó el backup» de «arrancó de cero»:

```bash
docker compose -f docker/docker-compose.yml exec redis \
  redis-cli -a "$REDIS_PASSWORD" DBSIZE          # > 0

docker compose -f docker/docker-compose.yml logs redis | grep -E "DB loaded|Creating AOF base"
# Esperado:  * DB loaded from base file appendonly.aof.<n>.base.rdb
# MAL:       * Creating AOF base file … on server start
```

Automatizado: `tests/integration/test_backup_consistency.py`
(`test_the_redis_capture_takes_the_aof_not_a_lone_rdb`,
`test_the_redis_artifact_is_restored_and_not_just_captured`).

## Relacionado

- [ADR 0149 — consistencia del bundle de backup](../../05-architecture-decisions/0149-consistencia-del-bundle-de-backup.md)
  (`proposed`): el skew residual que queda tras esto.
- [`docs/06-runbooks/04-disaster-recovery.md`](../../06-runbooks/04-disaster-recovery.md),
  sección «Skew residual del bundle» y paso 3 de la verificación post-restore.
