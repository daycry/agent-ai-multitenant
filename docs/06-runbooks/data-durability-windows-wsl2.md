---
title: Durabilidad de los datos en Windows/Docker Desktop (WSL2)
docs_language: es
audience: operador, system admin
updated: 2026-07-02
related: ["restart-services.md", "backups.md", "dr-manual-backup.md"]
---

# Runbook — Durabilidad de los datos en Windows/Docker Desktop (WSL2)

## TL;DR

**Reiniciar el stack con compose NO pierde los named volumes.** `docker compose restart`,
`stop`+`start` y `down` (SIN `-v`) + `up` preservan la base de datos, MinIO y Vault. **PERO el bind
`/data/agent-platform` (bare repos + worktrees de los agentes) NO es fiable frente a reinicios del
ENGINE en este backend**: el incidente del 2026-07-02 lo demostró — al despertar el host y arrancar
Docker Desktop (07:32), el bind reapareció **vacío y root:root**, se perdieron los repos con el
trabajo de 8 tareas done, y los runs del día corrieron "a ciegas". Es la segunda recreación
observada (2026-07-01 y 2026-07-02). Trata `/data/agent-platform` como **efímero** salvo backup.

Qué destruye datos:

- **`docker compose down -v`** — la `-v` borra los **named volumes** (Postgres, Redis, MinIO, Vault).
- **Reinicio del engine / arranque del host** (evidencia 2026-07-02): puede recrear el bind
  `/data/agent-platform` vacío (y root:root, porque el daemon lo auto-crea al montar). Los named
  volumes sobrevivieron a ese mismo reinicio.
- **Reset de la VM**: `wsl --shutdown` con FS volátil, o Docker Desktop → _Troubleshoot → Clean /
  Purge data_ → **se pierde TODO**.

Si en los logs del worker ves `bare_repo.init` + `bare_repo.seed_initial_commit` para un proyecto que
ya tenía código, **hubo pérdida de datos**, no un redeploy normal.

## Mitigaciones activas (auditoría 2026-07-02)

1. **Self-heal de permisos**: el entrypoint del worker (`apps/workers/docker-entrypoint.sh`) repara
   la propiedad de `/data/agent-platform` (uid 1000) en CADA arranque del contenedor — el one-shot
   `worktrees-init` del compose queda como red de seguridad.
2. **Fail-fast**: si la provisión del worktree falla, el run implementador aborta en segundos con
   `abort_code=workspace_unavailable` en vez de quemar 50 iteraciones sobre un tmpfs vacío; y
   `stack_exec` valida el bind-source antes de `containers/create`.
3. **Backup**: el bundle programado (Plan 12) ahora incluye `/data/agent-platform`
   (`WORKERS_BACKUP_BIND_PATHS`, default activado). OJO: en el stack dev el backup diario corre en la
   cola `privileged`, que los workers dev NO consumen — usa `scripts/backup-data.ps1` (programable
   con el Task Scheduler de Windows) o levanta un worker de esa cola.

## Por qué `/data/agent-platform` es frágil en Windows

El worker monta `/data/agent-platform` como **bind mount** (no named volume) porque el daemon del host
debe resolver esa ruta para bindarla al contenedor `agent-runtime` efímero (DooD — ver
[gotcha worktree-bind-dood](../03-guides/gotchas/worktree-bind-dood-empty-vs-named-volume.md)). En
Windows + Docker Desktop con backend WSL2, esa ruta (un path Linux absoluto, no `C:\...`) vive **dentro
de la VM WSL2**, no en el FS de Windows. Por eso es resiliente a `down -v` pero vulnerable a un reset de
la VM.

## Matriz de pérdida por acción

| Acción                                      | Postgres / Redis / MinIO / Vault | Bare repos & worktrees (`/data`)        |
| ------------------------------------------- | -------------------------------- | --------------------------------------- |
| `docker compose restart`                    | ✅ persiste                      | ✅ persiste                             |
| `stop` + `start`                            | ✅ persiste                      | ✅ persiste                             |
| `down` (sin `-v`) + `up`                    | ✅ persiste                      | ✅ persiste                             |
| `down -v` + `up`                            | ❌ **se pierde** (volúmenes)     | ✅ persiste (bind)                      |
| Reinicio de Docker Desktop / arranque host  | ✅ persiste                      | ⚠️ **NO fiable** (evidencia 2026-07-02) |
| `wsl --shutdown` (FS volátil) / Clean-Purge | ❌ **se pierde**                 | ❌ **se pierde**                        |

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

## Backup / restore de `/data/agent-platform`

Los bare repos + worktrees NO los cubre el backup lógico de tenant (pg_dump, ADR 0036 /
[backups.md](./backups.md)). Respáldalos aparte a un path **durable de Windows** antes de cualquier
operación destructiva o reset de la VM:

```powershell
# Backup → C:\AgentData\backups\agent-platform-<fecha>.tar.gz
.\scripts\backup-data.ps1 -Destination C:\AgentData\backups
```

```bash
# Equivalente en WSL/bash
./scripts/backup-data.sh /mnt/c/AgentData/backups
```

Ambos usan un contenedor `alpine` efímero que monta `/data/agent-platform:ro` y el directorio destino,
y produce un `.tar.gz`. Para restaurar, desempaqueta el tar dentro de `/data/agent-platform` (con el
stack parado) y vuelve a levantar.

## Mejora a medio plazo (no aplicada)

Migrar el bind a un path **respaldado por Windows** (p.ej. `C:\AgentData\data\agent-platform` montado en
la VM) lo haría resiliente a resets de la VM, a costa de algo de I/O. Requiere config de Docker Desktop;
documentado aquí como follow-up, no implementado.
