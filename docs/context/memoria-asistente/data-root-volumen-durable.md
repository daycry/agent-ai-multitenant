---
name: data-root-volumen-durable
description: "2026-07-03: /data/agent-platform migrado al named volume EXTERNO agentic-platform-agent-data montado en su ruta daemon-side (identidad DooD) — sobrevive engine-restarts y down -v; + guarda repo_history_lost; + backup diario reparado (pg_dump 16, URL, volúmenes prefijados)."
metadata:
  node_type: memory
  type: project
  originSessionId: 75127a11-d792-4ccf-aaf9-63b6eb2823b6
---

2026-07-03 (rama `plan/runs-visor-trabajo`): el operador exigió que apagar el stack no pierda datos. Fix estructural del incidente 2026-07-02:

- **Data-root durable**: los bare repos/worktrees ya NO viven en el bind `/data/agent-platform` (rootfs efímero de la VM Docker Desktop). Ahora: named volume **externo** `agentic-platform-agent-data` montado en su ruta daemon-side `/var/lib/docker/volumes/agentic-platform-agent-data/_data` (así el bind DooD worker→daemon→agent-runtime resuelve el MISMO path). `WORKERS_DATA_ROOT` apunta ahí. Bootstrap en máquina nueva: `docker volume create agentic-platform-agent-data` ANTES de `compose up`. VERIFICADO e2e: marker sobrevivió a Stop Docker Desktop + `wsl --shutdown` + reinicio (el escenario que arrasó /data el 07-01 y 07-02).
- **Guarda `repo_history_lost`** (execution.py `_provision_worktree`): si el plan tiene tareas done/in_review y la rama del plan no existe en el bare (o su checkout está vacío), el run aborta fail-fast con ese abort_code en vez de re-seedear un repo vacío y churnear. + `WorktreeManager.add` ahora poda registraciones huérfanas (`git worktree prune`) antes de re-crear. Tests en test_workspace_failfast.py / test_worktree_create.py.
- **Backup diario reparado** (nunca había podido correr en dev): imagen workers ahora lleva `postgresql-client-17` nativo de trixie (Debian 13; pg*dump 17 dumpea PG16 — el mismatch fatal es solo cliente<servidor. OJO: PGDG `bookworm-pgdg` NO instala en esta base); `WORKERS_BACKUP_DATABASE_URL` → `postgres:5432` (default era localhost:15432, inalcanzable); `WORKERS_BACKUP_VOLUMES` con prefijo real `agentic-platform*\*`+ el agent-data; bundles a`/backups`→ bind Windows`repo/.backups/agent-platform/`(fuera del VHDX);`tar`sin`--create`desde Plan 12 (runner fake nunca lo vio) — arreglado. La cola`privileged`la consume el servicio dedicado **workers-backup** (root en contenedor,`WORKERS_RUN_AS_ROOT=1`en el entrypoint: redis/vault`\_data` son 0700 de uid 999/100 y el restore escribe); workers-aux quedó solo con test/review.

**Gotcha de build**: `agentic-platform/workers:ci` se construye con `--build-arg BASE_IMAGE=agentic-platform/api-server:manuals` (la base `:ci` está desfasada, 07-01 — sin `distil_execution_result` → ImportError al arrancar). Contexto = raíz del repo.

Relacionado: [[auditoria-runs-2026-07-02-remediacion]], [[gotcha-setpriv-home-y-visibility-timeout]].
