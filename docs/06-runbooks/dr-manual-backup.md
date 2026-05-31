---
title: Backup manual y sincronización a destino remoto
docs_language: es
audience: operador, system admin
updated: 2026-05-30
---

# Runbook — Backup manual + verificación + destino remoto

Disparar un backup completo a mano (fuera del cron diario de las 03:00),
verificar que el bundle es íntegro, y empujarlo a un destino remoto. Caso
de uso típico: **backup pre-upgrade** antes de un `docker compose pull`, o
una copia bajo demanda antes de una operación arriesgada.

Para la copia manual a nivel de volumen Docker (procedimiento histórico,
sin el motor) ver [backups.md](./backups.md). Este runbook usa el motor de
Fase A (`scripts/backup.sh` → `workers.backup.run_full_backup`).

## Propósito

- Producir un bundle de backup completo, verificado, fuera de la ventana
  programada.
- Confirmar que el bundle es restaurable (verificación de corrupción).
- Llevar el bundle a un destino remoto (S3 / B2 / SFTP / rclone).

## Precondiciones

- El stack está **arriba y sano** ([health-check.md](./health-check.md)):
  el backup hace `pg_dump` contra PostgreSQL vivo y `tar` de los volúmenes.
- `docker/.env` con las variables del motor:
  - `WORKERS_BACKUP_ROOT` — dónde se escriben los bundles.
  - `WORKERS_BACKUP_DATABASE_URL` — URL libpq que usa `pg_dump`.
  - `WORKERS_BACKUP_RETENTION_DAYS` — retención local (por defecto 7).
  - `WORKERS_BACKUP_VOLUMES` — lista JSON de volúmenes Docker.
  - `WORKERS_BACKUP_VOLUMES_MOUNT_ROOT` — dir host con esos volúmenes.
- Si el cifrado está activado, Vault accesible con la clave
  (`WORKERS_BACKUP_ENCRYPTION_VAULT_KEY`).
- Para empujar a remoto: la entrada de `backup_destinations` configurada
  y sus credenciales en Vault/entorno (ver UI de destinos, `task_12_09`).

## Pasos

### 1. Lanza el backup manual

Desde la raíz del repo, el wrapper delega en el motor Python:

```bash
./scripts/backup.sh
```

Imprime el `backup_id`, el directorio del bundle, la ruta del manifest, el
número de artefactos y los bundles podados por retención. El bundle queda
en `WORKERS_BACKUP_ROOT/<backup_id>/` con:

- `postgres/` — dump lógico en formato directorio.
- `<volumen>.tar.gz` — uno por volumen configurado.
- `manifest.json` — artefactos, tamaños y checksums SHA-256.

(En un worker dentro del stack: `docker compose -f docker/docker-compose.yml
exec -T worker ./scripts/backup.sh`.)

### 2. Verifica el bundle (corruption check)

La verificación post-backup (`task_12_03`,
`workers.backup_verification`) recomputa los checksums del manifest y
comprueba la estructura (`pg_restore --list`, `tar -tf`). El motor ya la
ejecuta tras el backup; para reverificar un bundle existente a mano:

```bash
docker compose \
  -f docker/docker-compose.yml \
  exec -T worker \
  python -c '
from workers.backup_verification import verify_bundle
report = verify_bundle("<backup_id>")
print("verify:", report)
'
```

Un bundle que no verifica NO debe usarse para restaurar: repite el backup.

### 3. Empuja el bundle al destino remoto

> **Importante (gap conocido):** en esta fase el motor de backup
> (`run_full_backup`) **NO sube** el bundle a los destinos remotos
> automáticamente. Los adaptadores existen y están probados
> (`workers.backup_destinations`: S3, B2, SFTP, rclone), pero el cableado
> «backup → subida» todavía no está integrado en el flujo. La subida es,
> hoy, un paso **manual** invocando el adaptador.

```bash
docker compose \
  -f docker/docker-compose.yml \
  exec -T worker \
  python -c '
from pathlib import Path
from workers.backup_destinations import build_destination
from workers.backup_encryption import EnvSecretsProvider

dest = build_destination(
    {"type": "s3", "name": "primary", "bucket": "agentic-backups", "prefix": "prod/"},
    secrets=EnvSecretsProvider(),
)
res = dest.upload(Path("<WORKERS_BACKUP_ROOT>/<backup_id>"))
print("upload:", res)
'
```

Ajusta el dict de config al destino real (`type`: `s3` / `b2` / `sftp` /
`rclone` y sus knobs no-secretos). Las credenciales NO van en el dict: se
resuelven por el seam de secretos (`secrets=`). Antes de un upload real
puedes usar el **test de conectividad** de la UI de destinos
(`task_12_09`), que llama a `build_destination(...)` sin transferir datos.

### Recuperar un bundle del destino remoto

Para un DR ([dr-full-restore.md](./dr-full-restore.md)) en una máquina
virgen, descarga el bundle del remoto a `WORKERS_BACKUP_ROOT` con la
herramienta nativa del destino (`aws s3 cp --recursive`, `b2`, `sftp`,
`rclone copy`) antes de lanzar el restore.

## Verificación

- `scripts/backup.sh` termina con código 0 e imprime `backup ok: <id>`.
- El directorio `WORKERS_BACKUP_ROOT/<backup_id>/` existe con `postgres/`,
  los `.tar.gz` y `manifest.json`.
- La verificación del paso 2 reporta el bundle como íntegro.
- Tras el upload, el objeto aparece en el destino remoto (lista con la
  herramienta nativa) con el tamaño esperado.

## Rollback / aborto

- El backup es **no destructivo**: no toca datos vivos. Abortarlo
  (Ctrl-C) deja como mucho un bundle parcial en `WORKERS_BACKUP_ROOT`;
  bórralo a mano (`rm -rf <backup_id>/`) y reejecuta.
- Si el upload falla a mitad: reejecuta `dest.upload(...)`; los objetos
  S3/B2 se sobreescriben de forma idempotente por clave, y rclone/SFTP
  reintentan la transferencia. No hay estado que limpiar localmente.
- La poda por retención borra bundles **más antiguos** que
  `WORKERS_BACKUP_RETENTION_DAYS`; si necesitas conservar uno más tiempo,
  cópialo fuera de `WORKERS_BACKUP_ROOT` o súbelo a remoto antes de que la
  ventana lo elimine.

## A quién avisar

- **DevOps / operador**: ejecuta y vigila el backup manual.
- **System Admin**: si el backup falla repetidamente o el destino remoto
  rechaza las credenciales (revisa Vault).
- La alerta `BackupLastRunFailed` / `BackupTooOld`
  (`docker/monitoring/prometheus/rules/host_alerts.yml`) avisa al canal
  del System Admin si el último backup falló o es demasiado antiguo.

## Notas y limitaciones conocidas

- **Subida a destino remoto no auto-cableada (gap).** Hoy hay que invocar
  el adaptador a mano (paso 3). El cableado del flujo «backup → subida
  automática a los destinos configurados» queda pendiente; debería
  cerrarse en un plan posterior para que el cron diario sincronice solo.
