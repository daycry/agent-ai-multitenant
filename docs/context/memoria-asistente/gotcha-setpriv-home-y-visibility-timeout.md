---
name: gotcha-setpriv-home-y-visibility-timeout
description: Dos trampas operativas del stack — entrypoint root→setpriv hereda HOME=/root (asyncpg EACCES silencioso) y la re-entrega Celery tarda ~7h (visibility_timeout > hard-limit por diseño).
metadata:
  node_type: memory
  type: project
  originSessionId: 75127a11-d792-4ccf-aaf9-63b6eb2823b6
---

> **DOCUMENTADO EN EL REPO (2026-07-26)**: `docs/03-guides/gotchas/entrypoint-root-home-asyncpg-eacces.md + celery-visibility-timeout-redelivery-window.md`. La fuente de verdad es esa; esta nota queda como puntero.

**1. setpriv sin HOME** (2026-07-02): un entrypoint que arranca root y degrada con `setpriv --reuid=1000` deja `HOME=/root`; asyncpg busca `~/.postgresql/postgresql.key` en cada conexión → `Permission denied` SILENCIOSO (warnings, healthcheck verde) que paró el promotor del DAG y todos los sweeps.
**Why:** setpriv no recalcula el entorno; con `USER 1000` clásico Docker fijaba un HOME inofensivo.
**How to apply:** `export HOME=/tmp` antes del `exec setpriv` (ya en `apps/workers/docker-entrypoint.sh`); síntoma detectable con `docker logs workers-1 | grep postgresql.key`. Gotcha del repo: `docs/03-guides/gotchas/entrypoint-root-home-asyncpg-eacces.md`.

**2. Re-entrega lenta por diseño:** si un `docker compose --force-recreate workers` mata un run en vuelo, el mensaje unacked NO se re-entrega al reiniciar: `visibility_timeout` está clavado POR ENCIMA del hard-limit de 6h (prod-06 zombi_03) para no duplicar runs largos legítimos → la fila queda `running` zombi ~7h hasta el sweep/redelivery. No es un bug; no recrear workers con runs en vuelo si se puede evitar, o asumir la ventana.

Relacionado: [[auditoria-runs-2026-07-02-remediacion]].
