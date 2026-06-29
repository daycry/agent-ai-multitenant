---
title: Durabilidad de los datos en Windows/Docker Desktop (WSL2)
docs_language: es
audience: operador, system admin
updated: 2026-06-29
related: ["restart-services.md", "backups.md", "dr-manual-backup.md"]
---

# Runbook — Durabilidad de los datos en Windows/Docker Desktop (WSL2)

## TL;DR

**Reiniciar el stack NO pierde trabajo.** `docker compose restart`, `stop`+`start` y
`down` (SIN `-v`) + `up` preservan TODO: la base de datos, MinIO, Vault y los **bare repos +
worktrees** de los agentes (`/data/agent-platform`). Lo único que destruye datos es:

- **`docker compose down -v`** — la `-v` borra los **named volumes** (Postgres, Redis, MinIO, Vault).
  El bind `/data/agent-platform` **sobrevive** (no es un named volume).
- **Reset de la VM**: `wsl --shutdown` con FS volátil, o Docker Desktop → _Troubleshoot → Clean / Purge
  data_. Esto borra la VM WSL2 entera → **se pierde TODO**, incluido `/data/agent-platform`.

Si en los logs del worker ves `bare_repo.init` + `bare_repo.seed_initial_commit` para un proyecto que
ya tenía código, **hubo pérdida de datos** (un `down -v` o un reset de la VM), no un redeploy normal.

## Por qué `/data/agent-platform` es frágil en Windows

El worker monta `/data/agent-platform` como **bind mount** (no named volume) porque el daemon del host
debe resolver esa ruta para bindarla al contenedor `agent-runtime` efímero (DooD — ver
[gotcha worktree-bind-dood](../03-guides/gotchas/worktree-bind-dood-empty-vs-named-volume.md)). En
Windows + Docker Desktop con backend WSL2, esa ruta (un path Linux absoluto, no `C:\...`) vive **dentro
de la VM WSL2**, no en el FS de Windows. Por eso es resiliente a `down -v` pero vulnerable a un reset de
la VM.

## Matriz de pérdida por acción

| Acción                                      | Postgres / Redis / MinIO / Vault | Bare repos & worktrees (`/data`) |
| ------------------------------------------- | -------------------------------- | -------------------------------- |
| `docker compose restart`                    | ✅ persiste                      | ✅ persiste                      |
| `stop` + `start`                            | ✅ persiste                      | ✅ persiste                      |
| `down` (sin `-v`) + `up`                    | ✅ persiste                      | ✅ persiste                      |
| `down -v` + `up`                            | ❌ **se pierde** (volúmenes)     | ✅ persiste (bind)               |
| Reinicio de Docker Desktop                  | ✅ persiste                      | ✅ persiste                      |
| `wsl --shutdown` (FS volátil) / Clean-Purge | ❌ **se pierde**                 | ❌ **se pierde**                 |

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
