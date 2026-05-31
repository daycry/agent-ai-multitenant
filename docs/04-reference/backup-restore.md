---
title: Backup, Restore y Continuidad — Referencia del bundle, destinos, modos de restore, monitorización y knobs
audience: backend-dev, devops, architect
phase: 12-backup-restore
updated: 2026-05-30
---

# Backup, Restore y Continuidad — Referencia

Esta página documenta lo construido en el Plan 12: el **layout del bundle de
backup**, los **tipos de destino remoto**, los **modos de restore** (completo y
selectivo por tenant) con sus guardas de seguridad, la **monitorización del
host** con alertas, y los **knobs de configuración**. Para los procedimientos
paso a paso ver los runbooks de DR; para las decisiones de fondo ver
[ADR 0036](../05-architecture-decisions/0036-backup-pgdump-logico-cifrado-aesgcm-destinos-enchufables-restore-por-tenant.md).
El cifrado reusa el Vault de [ADR 0003](../05-architecture-decisions/0003-vault-from-day-one.md);
el aislamiento por tenant del restore selectivo se apoya en la RLS de
[ADR 0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md).

> **⚠ Gaps conocidos.** (1) La **subida automática al destino remoto NO está
> cableada** en el flujo de backup todavía — hoy es un paso manual. (2) El
> **restore por tenant requiere la extensión `dblink`** disponible para el rol
> admin (prerequisito de despliegue). (3) Todos los caminos reales están
> **mock-verificados en CI**: validar con los tests humanos `human_12_*` y un
> stack vivo. Detalle en el [changelog del Plan 12](../07-changelog/12-backup-restore.md),
> sección Pendiente.

## Layout del bundle de backup

Cada corrida del motor (`workers.backup.run_full_backup`) escribe un
**directorio timestamped** bajo `WORKERS_BACKUP_ROOT` (id = UTC sortable, p. ej.
`20260530T031500Z`):

```
<backup_id>/
├── postgres/            # pg_dump LÓGICO, formato directorio (--format=directory)
├── <volumen>.tar.gz     # uno por volumen Docker (minio_data, redis_data, vault_data)
└── manifest.json        # version, backup_id, created_at, status, encrypted,
                         #   artifacts[] (name/kind/path/size_bytes/sha256/source),
                         #   total_size_bytes; la URL de la BD va SANEADA (sin password)
```

Cuando el cifrado está activado, el directorio contiene en su lugar un único
**`bundle.tar.enc`** (blob AES-256-GCM) y el manifest marca `encrypted: true`.

| Propiedad             | Valor                                                                                   |
| --------------------- | --------------------------------------------------------------------------------------- |
| Captura de PostgreSQL | `pg_dump --format=directory` (LÓGICO; permite `pg_restore --list` + restore por tenant) |
| Captura de volúmenes  | `tar + gzip` por volumen (`WORKERS_BACKUP_VOLUMES`)                                     |
| Integridad            | checksums SHA-256 por artefacto en el manifest                                          |
| Fail-closed           | cualquier sub-fallo borra el bundle parcial y eleva `BackupError`                       |
| Retención local       | poda los bundles más antiguos que `WORKERS_BACKUP_RETENTION_DAYS` (default 7)           |

## Cifrado en reposo (opcional)

| Aspecto   | Detalle                                                                                            |
| --------- | -------------------------------------------------------------------------------------------------- |
| Primitiva | AES-256-GCM (`cryptography` `AESGCM`), AEAD autenticado                                            |
| Formato   | cabecera `MAGIC(8) + version(1) + nonce(12) + ciphertext+tag`; la cabecera va como associated data |
| Clave     | resuelta del **Vault** por el seam `SecretsProvider` (fold SHA-256 → 32 bytes)                     |
| Manejo    | solo en memoria; NUNCA en disco ni log (se loguea el NOMBRE de la clave)                           |
| Tamper    | un bit alterado en blob/nonce/cabecera → `BackupEncryptionError` al restaurar (fail loud)          |
| Default   | OFF (`WORKERS_BACKUP_ENCRYPTION_ENABLED=false`)                                                    |

## Verificación post-backup (corruption check)

`workers.backup_verification.verify_bundle` recomputa los checksums SHA-256 del
manifest y comprueba la estructura (`pg_restore --list` del dump, `tar -tf` de
cada archivo). Devuelve un `VerificationReport` tipado (válido + fallos por
artefacto). Es el gate **verify-before-restore**, fail-closed, reusado por las
dos rutas de restore: un bundle que no verifica ABORTA el restore antes de
cualquier comando destructivo.

## Tipos de destino remoto

Todos implementan el Protocol `BackupDestination` (`upload` / `list_remote` /
`download` / `test_connectivity`), registrados por `type` y construidos por
`build_destination(config, secrets=...)`. **La config NUNCA lleva un secreto**;
las credenciales se resuelven por el seam de secretos (Vault/env).

| Tipo     | Backend                       | Knobs NO-secretos (config)                                                       | Secretos (seam)                         |
| -------- | ----------------------------- | -------------------------------------------------------------------------------- | --------------------------------------- |
| `s3`     | boto3 (cualquier S3-compat.)  | `bucket`, `prefix`, `endpoint_url`, `region`                                     | access key id + secret access key       |
| `b2`     | Backblaze B2 (subclase de S3) | `bucket`, `prefix`, `region` (endpoint derivado)                                 | application keyId + applicationKey      |
| `sftp`   | paramiko (SSH)                | `host`, `port`, `username`, `remote_path`, `host_key_policy`, `known_hosts_path` | password O clave privada (+ passphrase) |
| `rclone` | CLI rclone (~70 backends)     | `remote`, `path`                                                                 | blob `rclone.conf` (creds obscurecidas) |

Notas: S3 usa `upload_file` (multipart automático); B2 deriva el endpoint de la
región y pinea el part-size multipart; SFTP usa `host_key_policy: reject` por
defecto (el host debe estar en known_hosts — nunca se desactiva el check
silenciosamente); rclone escribe el blob a un temp `rclone.conf` (0600) pasado
con `--config` (creds en el FICHERO, nunca en argv/log) y lo borra en un finally.
El botón "test de conectividad" de la UI (task_12_09) construye el destino (lazy,
sin red) y hace una sonda barata (`head_bucket` / `stat` / `lsd`).

## Modos de restore + seguridad

| Modo           | Entrypoint                                          | Qué toca                                                 | Confirmación                       |
| -------------- | --------------------------------------------------- | -------------------------------------------------------- | ---------------------------------- |
| **Completo**   | `workers.restore.run_full_restore`                  | TODA la BD (`pg_restore --clean`) + todos los volúmenes  | token == `<backup_id>`             |
| **Por tenant** | `workers.restore_per_tenant.run_per_tenant_restore` | SOLO las filas + el prefijo de object storage del tenant | token == `<tenant_id>@<backup_id>` |

**Restore completo** (`restore.py`): localiza → (descifra) → **VERIFICA**
(fail-closed) → para los servicios de app dejando PostgreSQL accesible →
`pg_restore --clean --if-exists` → restaura cada volumen (para servicio dueño,
vacía + re-extrae `_data`) → levanta el stack. Orden: verify → stop →
pg_restore → volúmenes → start. Conduce `docker compose` contra el proyecto +
fichero configurados, nunca uno implícito.

**Restore por tenant** (`restore_per_tenant.py`): `pg_restore` del dump LÓGICO en
una **BD STAGING desechable** → **preview** (`count(*) WHERE tenant_id` por
tabla, sin escribir) → **copia filtrada** a la BD viva en orden FK dentro de UNA
transacción (`DELETE`/`INSERT ... SELECT dblink(...) WHERE tenant_id = <target>`,
`session_replication_role = replica`, `ON_ERROR_STOP`) → drop de la staging en un
finally → re-extrae solo el prefijo `<tenant_id>/` del object store. Corre con el
rol **BYPASSRLS** pero SIEMPRE acotado por el predicado `tenant_id`: BYPASSRLS
quita la política RLS, el predicado mantiene el radio de impacto en un solo
tenant. **Requiere la extensión `dblink`** (prerequisito de despliegue).

Guardas comunes: **doble confirmación**, **verify-before-restore fail-closed**,
**`tenant_id` validado como UUID** y nombres de tabla validados contra un regex
de identificador antes de interpolarse en SQL. Un `dry_run` calcula el preview
sin escribir nada en la BD viva.

## Monitorización del host + alertas

Overlay de compose `docker/docker-compose.monitoring.yml` (imágenes pineadas,
endurecidas). Se monta con:

```bash
docker compose -f docker/docker-compose.yml \
  -f docker/docker-compose.monitoring.yml up -d
```

| Servicio        | Rol                                                                     |
| --------------- | ----------------------------------------------------------------------- |
| `node-exporter` | host: CPU/RAM/disco/swap/red + textfile collector del backup            |
| `cadvisor`      | métricas por contenedor                                                 |
| `prometheus`    | scrape (`prometheus`/`node-exporter`/`cadvisor`) + evaluación de reglas |
| `alertmanager`  | dedup/group/throttle + entrega al notificador de plataforma             |
| `grafana`       | dashboard `host-overview` provisionado (read-only desde fichero)        |

**Reglas de alerta** (`monitoring/prometheus/rules/host_alerts.yml`):

| Alerta                | Expresión / umbral                                                     | `for:` | Severidad |
| --------------------- | ---------------------------------------------------------------------- | ------ | --------- |
| `HostDiskUsageHigh`   | `1 - avail/size > 0.80`                                                | 10m    | warning   |
| `HostMemoryUsageHigh` | `1 - MemAvailable/MemTotal > 0.90`                                     | 5m     | warning   |
| `HostSwapActive`      | `SwapTotal - SwapFree > 0`                                             | 5m     | warning   |
| `HostOOMKills`        | `increase(node_vmstat_oom_kill[5m]) > 0`                               | 0m     | critical  |
| `ContainerOOMKilled`  | `increase(container_oom_events_total[5m]) > 0`                         | 0m     | critical  |
| `BackupLastRunFailed` | `agentic_backup_last_success == 0`                                     | 2m     | critical  |
| `BackupTooOld`        | `time() - agentic_backup_last_success_timestamp_seconds > 93600` (26h) | 0m     | critical  |

El motor de backup emite `agentic_backup_last_success` (1/0) y
`agentic_backup_last_success_timestamp_seconds` por el **textfile collector** de
node-exporter (`workers.backup_metrics`, escritura atómica). Alertmanager enruta
al **notificador de plataforma** (webhook al ingest del api-server →
notificación Plan 10 al System Admin), con repeat-interval más corto para
`critical` e inhibición de los `warning` de host durante un OOM.

> Los checks `curl prometheus` / `curl grafana` del plan necesitan un stack VIVO
> ⇒ son verificación humana/CI-con-stack. La validez de las reglas se prueba en
> CI con `tests/integration/test_host_alerts.py` (parsea el YAML + asevera las
> reglas/umbrales).

## Knobs de configuración

Todos vía env `WORKERS_*` (Pydantic `Settings`) salvo donde se indica. Resumen;
detalle de defaults en `apps/workers/src/workers/config.py`.

| Knob                                                                               | Para qué                                                    |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `WORKERS_BACKUP_ROOT`                                                              | raíz host de los bundles                                    |
| `WORKERS_BACKUP_DATABASE_URL`                                                      | URL libpq de `pg_dump`/`pg_restore` (rol BYPASSRLS)         |
| `WORKERS_BACKUP_RETENTION_DAYS`                                                    | retención local (default 7)                                 |
| `WORKERS_BACKUP_VOLUMES` / `_VOLUMES_MOUNT_ROOT`                                   | volúmenes capturados + su mount root                        |
| `WORKERS_BACKUP_CRON` + setting `backup_enabled`                                   | cadencia diaria (`0 3 * * *`) + palanca live (System Admin) |
| `WORKERS_BACKUP_METRICS_TEXTFILE_PATH`                                             | fichero `.prom` de salud del backup                         |
| `WORKERS_BACKUP_ENCRYPTION_ENABLED` / `_ENCRYPTION_VAULT_KEY`                      | cifrado AES-256 opcional + nombre de la clave del Vault     |
| `WORKERS_BACKUP_S3_*`                                                              | destino S3 (knobs no-secretos)                              |
| `WORKERS_BACKUP_B2_*`                                                              | destino Backblaze B2                                        |
| `WORKERS_BACKUP_SFTP_*`                                                            | destino SFTP/NAS                                            |
| `WORKERS_BACKUP_RCLONE_*`                                                          | destino rclone genérico                                     |
| `WORKERS_RESTORE_COMPOSE_PROJECT` / `_FILE` / `_APP_SERVICES` / `_VOLUME_SERVICES` | control del stack en el restore completo                    |
| `WORKERS_RESTORE_TENANT_SCOPED_TABLES` / `_OBJECT_STORE_VOLUME`                    | tablas tenant-scoped (orden FK) + volumen de object store   |
| `ALERTMANAGER_NOTIFIER_WEBHOOK_URL`                                                | seam (envsubst) del receptor del notificador                |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`                                    | credenciales de Grafana (override en prod/Vault)            |

## Runbooks relacionados

- [`dr-full-restore.md`](../06-runbooks/dr-full-restore.md) — DR completo desde un bundle.
- [`dr-tenant-restore.md`](../06-runbooks/dr-tenant-restore.md) — restore selectivo de un tenant.
- [`dr-manual-backup.md`](../06-runbooks/dr-manual-backup.md) — backup manual + verificación + subida a remoto (paso manual hoy).
- [`dr-vault-unseal-rotation.md`](../06-runbooks/dr-vault-unseal-rotation.md) — rotación de unseal keys de Vault.
- [`backups.md`](../06-runbooks/backups.md) — copia manual a nivel de volumen (procedimiento histórico, sin el motor).
