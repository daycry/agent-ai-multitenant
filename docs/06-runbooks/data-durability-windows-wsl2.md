---
title: Durabilidad de los datos en Windows/Docker Desktop (WSL2)
docs_language: es
audience: operador, system admin
updated: 2026-07-03
related: ["restart-services.md", "backups.md", "dr-manual-backup.md"]
---

# Runbook — Durabilidad de los datos en Windows/Docker Desktop (WSL2)

## TL;DR

Desde el 2026-07-03 **TODO el estado del stack dev vive en named volumes durables**,
incluido el data-root de agentes (bare repos + worktrees + dep-cache), que antes era un
bind al rootfs **efímero** de la VM de Docker Desktop y se perdía en cada reinicio del
engine (incidente 2026-07-02: repos de 8 tareas done arrasados). El data-root es ahora el
volumen **externo** `agentic-platform-agent-data`, montado en su ruta daemon-side
(`/var/lib/docker/volumes/agentic-platform-agent-data/_data`) para conservar la identidad
de rutas que exige el bind DooD hacia los `agent-runtime` (ver
[gotcha worktree-bind-dood](../03-guides/gotchas/worktree-bind-dood-empty-vs-named-volume.md)).

Bootstrap en una máquina nueva (una única vez, ANTES de `compose up`):

```powershell
docker volume create agentic-platform-agent-data
```

Al ser `external: true`, **ni siquiera `docker compose down -v` lo elimina**.

Qué destruye datos todavía:

- **`docker compose down -v`** — borra los named volumes DECLARADOS (Postgres, Redis,
  MinIO, Vault…). El agent-data sobrevive (external), pero la BD no: sin BD las tareas
  "done" y el resto del estado se pierden igualmente. No lo uses sin backup.
- **`docker volume rm agentic-platform-agent-data`** — borrado explícito del volumen.
- **Docker Desktop → _Troubleshoot → Clean / Purge data_** — se pierde TODO el VHDX
  (volúmenes incluidos).

Qué ya NO destruye datos:

- **Reinicio del engine / arranque del host / `wsl --shutdown`** — los volúmenes viven en
  el VHDX persistente de docker-desktop-data (igual que `postgres_data`, que sobrevivió a
  los incidentes de 2026-07-01/02).

Si en los logs del worker ves `bare_repo.init` + `bare_repo.seed_initial_commit` para un
proyecto que ya tenía código, **hubo pérdida de datos**, no un redeploy normal — y desde
2026-07-03 el run NO seguirá a ciegas: aborta con `abort_code=repo_history_lost` (guarda
en `_provision_worktree`) si el plan tenía tareas completadas.

## Defensas activas

1. **Volumen durable** (arriba): la causa raíz del incidente 2026-07-02 está eliminada.
2. **Self-heal de permisos**: el entrypoint del worker (`apps/workers/docker-entrypoint.sh`)
   repara la propiedad del data-root (uid 1000) en CADA arranque; el one-shot
   `worktrees-init` queda como red de seguridad.
3. **Fail-fast**: provisión de worktree fallida → `workspace_unavailable` en segundos;
   historial del plan desaparecido (repo re-seedeado vacío o rama sin ficheros con tareas
   done) → `repo_history_lost`; `stack_exec` valida el bind-source antes de
   `containers/create`.
4. **Backup diario** (Plan 12 + F0.4): corre en la cola `privileged`, consumida por el
   servicio dedicado `workers-backup` (root dentro de su contenedor — los `_data` de
   redis/vault son 0700 de otros uids y un restore además escribe en ellos; este pool no
   ejecuta runs de agentes). Captura pg_dump + los volúmenes `agentic-platform_minio_data`,
   `agentic-platform_redis_data`, `agentic-platform_vault_data` y
   `agentic-platform-agent-data` (`WORKERS_BACKUP_VOLUMES`). Los bundles se escriben en
   `repo/.backups/agent-platform/` — **FS de Windows**, fuera del VHDX: sobreviven incluso
   a un Clean/Purge de Docker Desktop.

## Matriz de pérdida por acción

| Acción                                     | Postgres / Redis / MinIO / Vault | Bare repos & worktrees (agent-data)    |
| ------------------------------------------ | -------------------------------- | -------------------------------------- |
| `docker compose restart`                   | ✅ persiste                      | ✅ persiste                            |
| `stop` + `start`                           | ✅ persiste                      | ✅ persiste                            |
| `down` (sin `-v`) + `up`                   | ✅ persiste                      | ✅ persiste                            |
| `down -v` + `up`                           | ❌ **se pierde** (volúmenes)     | ✅ persiste (external)                 |
| Reinicio de Docker Desktop / arranque host | ✅ persiste                      | ✅ persiste (volumen, fix 2026-07-03)  |
| `wsl --shutdown` + reinicio                | ✅ persiste                      | ✅ persiste                            |
| `docker volume rm` explícito / Clean-Purge | ❌ **se pierde**                 | ❌ **se pierde** (restaura del backup) |

`scripts/dev/down.ps1 -Docker` y `down.sh --docker` usan `docker compose down --remove-orphans`
(**sin `-v`**) → seguros. NUNCA añadas `-v` ni hagas Clean/Purge sin un backup previo.

## Redeploy seguro (aplicar imágenes nuevas)

```powershell
# Reconstruir + recrear sin tocar datos (recomendado). NO usa -v.
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml `
  -f docker/docker-compose.manuals.yml up -d --force-recreate <servicio>
```

Para recrear el stack completo, omite `<servicio>`. `--force-recreate` reaplica imágenes/env sin borrar
volúmenes. Verifica luego con [health-check.md](./health-check.md).

## Backup / restore manual del data-root

Además del backup diario, puedes volcar el volumen a mano a un path durable de Windows:

```powershell
# Backup → C:\AgentData\backups\agent-platform-<fecha>.tar.gz
.\scripts\backup-data.ps1 -Destination C:\AgentData\backups
```

```bash
# Equivalente en WSL/bash
./scripts/backup-data.sh /mnt/c/AgentData/backups
```

Ambos usan un contenedor `alpine` efímero que monta el volumen
`agentic-platform-agent-data:ro` y el directorio destino, y producen un `.tar.gz`. Para
restaurar (con el stack parado):

```powershell
docker run --rm -v agentic-platform-agent-data:/data -v C:\AgentData\backups:/backup:ro `
  alpine sh -c "cd /data && tar xzf /backup/<archivo>.tar.gz && chown -R 1000:1000 /data"
```
