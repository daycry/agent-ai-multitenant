---
plan_id: 12-backup-restore
title: Backup, Restore y Continuidad
completed_at: null
docs_language: es
---

# Plan 12 — Backup, Restore y Continuidad

## Resumen

Institucionaliza el backup manual con cron de Fase 0 en un **motor de backup
automatizado, verificado y opcionalmente cifrado**, con **destinos remotos**
enchufables, **restore completo y selectivo por tenant** con UI, y una capa de
**monitorización del host/contenedores + alertas + dashboards** sobre el stack
de observabilidad ya existente (OpenTelemetry + Prometheus + Grafana + Loki).
Cierra con runbooks de Disaster Recovery.

El **motor de backup** (`apps/workers/src/workers/backup.py`) produce un
**bundle timestamped** por corrida: un **`pg_dump` LÓGICO** en formato
directorio (`--format=directory`) — no `pg_basebackup` binario, **precisamente
para habilitar el restore selectivo por tenant** (Decisiones Clave del plan) —,
un **`tar + gzip` por volumen Docker** configurado (MinIO, Redis RDB/AOF, Vault
file backend) y un **`manifest.json`** con artefactos, tamaños, checksums
SHA-256 y estado. Contrato **fail-closed**: si cualquier sub-paso falla, el
bundle parcial se borra y se eleva `BackupError` — nunca un medio-bundle que dé
falsa confianza. Tras una corrida buena, poda los bundles locales más antiguos
que la ventana de retención (7 días por defecto). Todo comando externo
(`pg_dump`/`tar`) pasa por un **seam inyectable `CommandRunner`** (producción:
`SubprocessRunner` con argv explícito, nunca `shell=True`; tests: un fake que
registra el argv y fabrica artefactos), de modo que CI prueba **construcción de
comandos + orquestación + manifest/verificación**, jamás un dump real del stack
vivo.

El **cifrado en reposo** (`backup_encryption.py`) es OPCIONAL y OFF por defecto:
cuando se activa, el bundle se colapsa en un único `bundle.tar` y se envuelve en
un blob **AES-256-GCM** (`bundle.tar.enc`) con clave resuelta del **Vault** (vía
el seam `SecretsProvider`, fold SHA-256 → 32 bytes), con cabecera auto-descriptiva
`MAGIC|version|nonce|ciphertext+tag` y la cabecera como AAD; GCM es autenticado,
así que un bit alterado falla ruidosamente al restaurar. La clave vive solo en
memoria, nunca en disco ni en log (se loguea el NOMBRE de la clave, nunca el
valor). La **verificación post-backup** (`backup_verification.py`) recomputa los
checksums del manifest y comprueba la estructura (`pg_restore --list`, `tar -tf`)
— fail-closed, precede a todo restore.

Los **destinos remotos** (`backup_destinations.py`) implementan un único
Protocol **`BackupDestination`** (`upload` / `list_remote` / `download` /
`test_connectivity`) con un adaptador por backend registrado por `type`: **S3**
(boto3, `upload_file` multipart automático, sirve cualquier proveedor
S3-compatible vía `endpoint_url`), **Backblaze B2** (subclase de S3 con sus
quirks: endpoint derivado de la región, part-size multipart pineado, creds
propias), **SFTP/NAS** (paramiko, auth password o clave privada, política de
host-key configurable con `reject` por defecto) y **rclone genérico** (~70
backends vía la CLI tras el `CommandRunner`, con el blob de config escrito a un
temp `rclone.conf` 0600 y borrado en un finally). Las **credenciales son
secretos** resueltos por el seam (Vault/env), nunca en config plano, nunca en el
manifest, nunca logueadas. La **UI de configuración de destinos** (task_12_09)
guarda una lista de configs NO-secretas y ofrece un botón **test de
conectividad** que llama a `build_destination(...)` sin transferir datos.

El **restore completo** (`restore.py`) es la otra mitad destructiva: localiza →
(descifra) → **VERIFICA** (fail-closed) → para los servicios de app dejando
PostgreSQL accesible → `pg_restore --clean --if-exists` del dump lógico →
restaura cada volumen (para servicio dueño, vacía + re-extrae `_data`) →
levanta el stack. **Doble confirmación**: exige un token igual al `backup_id`.
El **restore selectivo por tenant** (`restore_per_tenant.py`) restaura SOLO las
filas de un tenant sin tocar a los demás: `pg_restore` del dump en una **base de
datos STAGING desechable**, **preview** (`count(*) WHERE tenant_id` por tabla),
y **copia filtrada** a la BD viva en orden FK dentro de UNA transacción
(`DELETE`/`INSERT ... WHERE tenant_id = <target>` en ambos lados, vía `dblink`),
ejecutada con el rol BYPASSRLS pero acotada SIEMPRE por el predicado `tenant_id`;
re-extrae solo el prefijo `<tenant_id>/` del volumen de object storage. Doble
confirmación con token `<tenant_id>@<backup_id>`. La **UI de restore**
(task_12_12) lista bundles, muestra preview, doble confirmación y log de
progreso.

La **monitorización del host** (Fase D) añade un overlay de compose
(`docker/docker-compose.monitoring.yml`): **node-exporter** (CPU/RAM/disco/swap/
red), **cAdvisor** (por contenedor), **Prometheus** (scrape + evaluación de
reglas), **Alertmanager** (enrutado al notificador de plataforma) y **Grafana**
con un dashboard host-overview provisionado. Las **reglas de alerta**
(`host_alerts.yml`) cubren las cinco del plan: disco >80%, RAM >90% sostenida
(5m), swap activo, OOM kills (host + contenedor) y último backup fallido /
demasiado antiguo. El motor de backup emite dos gauges
(`agentic_backup_last_success`, `..._timestamp_seconds`) por el **textfile
collector** de node-exporter (`backup_metrics.py`), que alimentan las alertas de
backup. Todas las imágenes están **pineadas** (sin `:latest`) y endurecidas
(`no-new-privileges`, `cap_drop: ALL` donde aplica) según la auditoría 06.14.

Las 17 tareas se desarrollaron en cuatro fases (A — motor; B — destinos
remotos; C — restore; D — monitorización del host y cierre).

> **⚠ Gaps conocidos que NO cierran en este plan.** El cableado «backup →
> subida automática al destino remoto» NO está integrado en el beat task; el
> restore por tenant requiere la extensión `dblink` de Postgres como
> prerequisito de despliegue; y todos los caminos reales (backup/restore/remoto/
> monitorización) están **mock-verificados** en CI y necesitan los tests humanos
> `human_12_*` con un stack vivo. Ver [Pendiente](#pendiente).

## Cambios por tarea

### Fase A — Motor de Backup

- ✅ **`task_12_01`** — **Backup full: `pg_dump` LÓGICO + `tar` de volúmenes +
  manifiesto** (`workers/backup.py`). `BackupEngine.run_full_backup()`: dump
  `--format=directory` (el ÚNICO formato que `pg_restore --list` introspecta +
  que permite el restore selectivo por tenant), un `<volumen>.tar.gz` por
  volumen configurado, y `manifest.json` con artefactos + tamaños + checksums
  SHA-256. **Fail-closed**: cualquier sub-fallo borra el bundle parcial y eleva
  `BackupError`. Poda por retención (`WORKERS_BACKUP_RETENTION_DAYS`, default 7)
  por la edad derivada del NOMBRE del bundle (no el mtime). Comandos tras el seam
  `CommandRunner` (argv explícito, nunca `shell=True`); tests sobre la
  construcción de comandos, la orquestación y el manifest.
- ✅ **`task_12_02`** — **Cifrado opcional AES-256-GCM con clave del Vault**
  (`workers/backup_encryption.py`). OFF por defecto. Al activarlo el bundle se
  colapsa en un `bundle.tar` y se envuelve en `bundle.tar.enc`
  (`MAGIC|version|nonce|ciphertext+tag`, cabecera como AAD). Clave resuelta del
  Vault por el seam `SecretsProvider` (fold SHA-256 → 32 bytes), solo en memoria,
  nunca en disco/log (se loguea el NOMBRE de la clave). GCM autenticado: un blob
  alterado/truncado/clave-equivocada eleva `BackupEncryptionError` al restaurar.
  Los artefactos en claro se borran; solo sobrevive el blob.
- ✅ **`task_12_03`** — **Verificación post-backup (corruption check)**
  (`workers/backup_verification.py`). Recomputa los checksums SHA-256 del
  manifest y comprueba la estructura del dump (`pg_restore --list`) + de cada tar
  (`tar -tf`). Devuelve un `VerificationReport` tipado (válido + fallos por
  artefacto). Es la base del gate **verify-before-restore**, fail-closed, reusado
  por las dos rutas de restore.
- ✅ **`task_12_04`** — **Cron + ventana horaria configurable desde el panel**
  (`workers/backup_task.py`, beat). Cadencia `WORKERS_BACKUP_CRON` (default
  `0 3 * * *`); la palanca live enable/disable es la PLATFORM setting
  `backup_enabled` (un System Admin la voltea sin reiniciar). Tras cada corrida
  el task escribe los metrics de salud por el textfile collector. E2E Playwright
  `backup-schedule.spec.ts` **escrito, no ejecutado**.

### Fase B — Destinos Remotos

- ✅ **`task_12_05`** — **Destino S3 con boto3 + el Protocol `BackupDestination`**
  (`workers/backup_destinations.py`). `S3Destination`: `upload_file` (multipart
  automático), `list_objects_v2` paginado, `download_file`, `head_bucket` para
  conectividad. `endpoint_url` hace funcionar CUALQUIER proveedor S3-compatible
  (MinIO, Wasabi, R2). Cliente boto3 lazy tras un `client_factory` (tests
  inyectan un mock). Creds resueltas por el seam de secretos, nunca en config/log.
- ✅ **`task_12_06`** — **Destino Backblaze B2** (S3-compatible con quirks).
  `B2Destination` subclasa `S3Destination` (B2 ES S3): endpoint derivado de la
  región (`s3.<region>.backblazeb2.com`), `TransferConfig` con part-size B2-friendly
  (100 MB) pineado, y creds propias (`keyId`/`applicationKey`) por sus field-names
  de secreto. URI `b2://`.
- ✅ **`task_12_07`** — **Destino SFTP/NAS** (`SftpDestination`, paramiko). Sesión
  SFTP lazy tras `transport_factory` (tests inyectan un mock). Auth password O
  clave privada, ambas por el seam (nunca plano, nunca log). Política de host-key
  configurable: `reject` (default, el host debe estar en known_hosts), `auto_add`,
  `warn` — nunca se desactiva el check silenciosamente. `put`/`listdir_attr`/`get`/
  `stat`-para-conectividad.
- ✅ **`task_12_08`** — **Destino rclone genérico** (`RcloneDestination`). Envuelve
  la CLI de rclone (~70 backends) tras el MISMO `CommandRunner` del motor. El blob
  de config (creds obscurecidas) es un SECRETO: se resuelve por el seam, se escribe
  a un temp `rclone.conf` (0600, `O_CREAT|O_EXCL`), se pasa con `--config` (creds en
  el FICHERO, nunca en argv/log) y se borra en un finally. `copy`/`lsjson`/`copy`/
  `lsd`-para-conectividad.
- ✅ **`task_12_09`** — **UI de configuración de destinos + test de conectividad**.
  El registry `build_destination(config, secrets=...)` mapea un dict de config
  NO-secreto `{type, name, ...}` (type ∈ `s3`/`b2`/`sftp`/`rclone`) a un
  `BackupDestination` vivo, sin resolución de red/creds al construir (lazy), apto
  para que el endpoint de "test de conectividad" lo invoque antes de probar. La UI
  guarda la lista de configs y ofrece el botón de test. E2E Playwright
  `backup-destinations.spec.ts` **escrito, no ejecutado**.

### Fase C — Restore

- ✅ **`task_12_10`** — **Restore completo** (`workers/restore.py`). Localiza →
  (descifra) → **VERIFICA** (fail-closed) → para los servicios de app dejando
  PostgreSQL accesible → `pg_restore --clean --if-exists` del dump → restaura cada
  volumen (para servicio dueño, vacía + re-extrae `_data`) → levanta el stack vía
  `docker compose` contra el proyecto + fichero configurados (nunca uno implícito).
  **Doble confirmación**: token igual al `backup_id`. Comandos tras el `CommandRunner`
  seam; tests sobre construcción + orden (verify → stop → pg_restore → volúmenes →
  start) + fallo tipado.
- ✅ **`task_12_11`** — **Restore selectivo por tenant** (`workers/restore_per_tenant.py`).
  `pg_restore` del dump LÓGICO en una **base STAGING desechable** → **preview**
  (`count(*) WHERE tenant_id` por tabla, sin escribir) → **copia filtrada** a la BD
  viva en orden FK dentro de UNA transacción (`DELETE` en orden FK inverso +
  `INSERT ... SELECT dblink(...) WHERE tenant_id = <target>` en orden FK), con
  `session_replication_role = replica` + `ON_ERROR_STOP`. Corre como BYPASSRLS pero
  SIEMPRE acotado por el predicado `tenant_id` (UUID validado; nombres de tabla
  validados contra un regex de identificador). Re-extrae solo el prefijo
  `<tenant_id>/` del object store. Drop de la staging en un finally. Doble
  confirmación con token `<tenant_id>@<backup_id>`.
- ✅ **`task_12_12`** — **UI de restore** (lista, preview, doble confirmación, log de
  progreso) + backend (`restore_task.py`, list/preview/trigger como job de fondo).
  E2E Playwright `restore-ui.spec.ts` **escrito, no ejecutado**.

### Fase D — Monitorización del Host y Cierre

- ✅ **`task_12_13`** — **node-exporter + cAdvisor + Prometheus** en el stack
  (`docker/docker-compose.monitoring.yml` + `monitoring/prometheus/prometheus.yml`).
  node-exporter (host: CPU/RAM/disco/swap/red, `node_load1`…) con el textfile
  collector apuntado al dir compartido con el worker; cAdvisor (por contenedor);
  Prometheus con scrape jobs `prometheus`/`node-exporter`/`cadvisor`,
  `rule_files: /etc/prometheus/rules/*.yml` y el target `alertmanager:9093`.
  Imágenes pineadas + endurecidas. **Check live `curl prometheus` ⇒ humano/CI con
  stack.**
- ✅ **`task_12_14`** — **Alertmanager + reglas** (`monitoring/prometheus/rules/
host_alerts.yml` + `monitoring/alertmanager/alertmanager.yml`). Las cinco
  alertas del plan: `HostDiskUsageHigh` (>80%, `for: 10m`), `HostMemoryUsageHigh`
  (>90% sostenida, `for: 5m`), `HostSwapActive`, `HostOOMKills` +
  `ContainerOOMKilled`, y `BackupLastRunFailed` (gauge `== 0`) + `BackupTooOld`
  (>26h sin éxito). El motor de backup emite las gauges por el textfile collector
  (`workers/backup_metrics.py`). Alertmanager enruta al **notificador de
  plataforma** (webhook al api-server → notificación Plan 10 al System Admin), con
  repeat-interval más corto para `critical` e inhibición de los `warning` de host
  durante un OOM. **Test pytest** `tests/integration/test_host_alerts.py` parsea el
  fichero de reglas y asevera reglas/umbrales (no hace `curl`).
- ✅ **`task_12_15`** — **Dashboards Grafana del host**
  (`monitoring/grafana/dashboards/host-overview.json` + el provisioning de
  datasource y dashboards). Dashboard `uid: host-overview` (CPU/RAM/disco/red y
  por contenedor) provisionado read-only desde fichero (la UI no persiste
  ediciones). Datasource Prometheus provisionado. **Check live `curl grafana` ⇒
  humano/CI con stack.**
- ✅ **`task_12_16`** — **Runbooks de DR** (`docs/06-runbooks/`): `dr-full-restore.md`
  (DR completo), `dr-tenant-restore.md` (restore selectivo por tenant),
  `dr-manual-backup.md` (backup manual + verificación + destino remoto) y
  `dr-vault-unseal-rotation.md` (rotación de unseal keys). El check generic-shell
  cuenta ≥4 ficheros en `docs/06-runbooks/`.
- ✅ **`task_12_17`** — **Documentación + ADRs + changelog** (esta entrada, la
  **ADR 0036**, y la referencia `docs/04-reference/backup-restore.md`). Documenta
  lo implementado y **flagea los gaps conocidos** (cableado de subida remota,
  prerequisito `dblink`, mock-verificación en CI, e2e escritos-no-ejecutados).

## Dependencias nuevas

| Item                   | Tipo           | Para qué                                                                                                                                         |
| ---------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `boto3>=1.34,<2`       | dep (workers)  | Destino S3/B2 (`upload_file` multipart, `list_objects_v2`, `head_bucket`). Cualquier proveedor S3-compatible                                     |
| `paramiko>=3.4,<4`     | dep (workers)  | Destino SFTP/NAS (sesión SSH+SFTP: `put`/`get`/`listdir_attr`/`stat`)                                                                            |
| `cryptography>=42,<49` | dep (workers)  | AES-256-GCM del bundle (`AESGCM`). El rango admite **cryptography 48**; la firma del marketplace se estrechó a Ed25519 por compatibilidad con 48 |
| rclone (binario)       | runtime (host) | Destino rclone genérico — CLI invocada por el `CommandRunner`, NO una dep Python (no entra en la imagen del worker)                              |

> Las imágenes del overlay de monitorización están **pineadas** (sin `:latest`):
> `prom/prometheus:v2.54.1`, `prom/node-exporter:v1.8.2`, `prom/alertmanager:v0.27.0`,
> `gcr.io/cadvisor/cadvisor:v0.49.1`, `grafana/grafana:11.2.0`.

## Configuración nueva (env / settings)

| Variable / setting                                                                                       | Para qué                                                                                                 |
| -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `WORKERS_BACKUP_ROOT`                                                                                    | Raíz host donde se escriben los bundles (uno por corrida)                                                |
| `WORKERS_BACKUP_DATABASE_URL`                                                                            | URL libpq que usa `pg_dump`/`pg_restore` (rol BYPASSRLS para capturar/escribir todos los tenants)        |
| `WORKERS_BACKUP_RETENTION_DAYS`                                                                          | Ventana de retención local (default 7)                                                                   |
| `WORKERS_BACKUP_VOLUMES` / `WORKERS_BACKUP_VOLUMES_MOUNT_ROOT`                                           | Volúmenes Docker capturados + su mount root host                                                         |
| `WORKERS_BACKUP_CRON` + setting `backup_enabled`                                                         | Cadencia del backup diario (default `0 3 * * *`) + palanca live (System Admin)                           |
| `WORKERS_BACKUP_METRICS_TEXTFILE_PATH`                                                                   | Fichero `.prom` del textfile collector con las gauges de salud del backup                                |
| `WORKERS_BACKUP_ENCRYPTION_ENABLED` / `WORKERS_BACKUP_ENCRYPTION_VAULT_KEY`                              | Cifrado AES-256 opcional + nombre de la clave del Vault                                                  |
| `WORKERS_BACKUP_S3_*` (`ENABLED`/`BUCKET`/`PREFIX`/`ENDPOINT_URL`/`REGION`)                              | Destino S3 (knobs NO-secretos; access key/secret por el seam)                                            |
| `WORKERS_BACKUP_B2_*` (`ENABLED`/`BUCKET`/`PREFIX`/`REGION`)                                             | Destino Backblaze B2 (endpoint derivado de la región)                                                    |
| `WORKERS_BACKUP_SFTP_*` (`ENABLED`/`HOST`/`PORT`/`USERNAME`/`PATH`/`HOST_KEY_POLICY`/`KNOWN_HOSTS_PATH`) | Destino SFTP/NAS (knobs NO-secretos; password/clave por el seam)                                         |
| `WORKERS_BACKUP_RCLONE_*` (`ENABLED`/`REMOTE`/`PATH`)                                                    | Destino rclone genérico (blob de config por el seam)                                                     |
| `WORKERS_RESTORE_COMPOSE_PROJECT` / `_FILE` / `_APP_SERVICES` / `_VOLUME_SERVICES`                       | Control del stack durante el restore completo (proyecto/fichero/servicios; Postgres ausente a propósito) |
| `WORKERS_RESTORE_TENANT_SCOPED_TABLES` / `WORKERS_RESTORE_OBJECT_STORE_VOLUME`                           | Tablas tenant-scoped (orden FK) + volumen de object store para el restore por tenant                     |
| `ALERTMANAGER_NOTIFIER_WEBHOOK_URL`                                                                      | Seam (envsubst) para repuntar el receptor del notificador (Alertmanager no expande `${ENV}`)             |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`                                                          | Credenciales de Grafana (default dev-only; override en prod/Vault)                                       |

## Servicios de monitorización nuevos

| Servicio        | Imagen (pineada)                   | Rol                                                                    |
| --------------- | ---------------------------------- | ---------------------------------------------------------------------- |
| `prometheus`    | `prom/prometheus:v2.54.1`          | TSDB + scrape + evaluación de reglas de alerta (retención 15d)         |
| `node-exporter` | `prom/node-exporter:v1.8.2`        | Métricas host (CPU/RAM/disco/swap/red) + textfile collector del backup |
| `cadvisor`      | `gcr.io/cadvisor/cadvisor:v0.49.1` | Métricas por contenedor                                                |
| `alertmanager`  | `prom/alertmanager:v0.27.0`        | Dedup/group/throttle + entrega al notificador de plataforma            |
| `grafana`       | `grafana/grafana:11.2.0`           | Dashboards host-overview provisionados (read-only desde fichero)       |

> Se montan con `docker compose -f docker/docker-compose.yml -f docker/docker-compose.monitoring.yml up -d`.
> El overlay `docker/docker-compose.monitoring.dev.yml` expone los puertos en dev.

## Decisiones

- **`pg_dump` LÓGICO (formato directorio), no `pg_basebackup` binario.** Es el
  único formato que `pg_restore --list` introspecta y que permite restaurar el
  dump en una BD staging y filtrar por tenant — la base del restore selectivo.
  Registrado en **ADR 0036**.
- **Cifrado de backup AES-256-GCM con clave del Vault.** Opcional, OFF por
  defecto; blob auto-descriptivo autenticado (cabecera como AAD) keyed por el
  seam de secretos; la clave nunca toca disco ni log. Registrado en **ADR 0036**.
- **Modelo `BackupDestination` enchufable + creds por el seam de secretos.** Un
  Protocol único (upload/list/download/test) con un adaptador por backend
  registrado por `type`; B2 reusa S3; rclone abre el catálogo a ~70 backends sin
  adaptador a medida. Las credenciales NUNCA viven en la config (que sí puede
  persistirse en `platform_settings`), solo en Vault/env. Registrado en **ADR 0036**.
- **Restore por tenant vía BD staging + copia filtrada por `tenant_id`.** El dump
  se restaura en una base desechable y solo las filas del tenant se copian a la BD
  viva (orden FK, una transacción, `dblink`), con el rol BYPASSRLS pero SIEMPRE
  acotado por el predicado `tenant_id`. Registrado en **ADR 0036**.
- **Reuso del notificador de plataforma para las alertas del host.** Alertmanager
  no abre un canal SMTP/Slack paralelo: hace webhook al ingest del api-server, que
  lo convierte en una notificación Plan 10 al System Admin. Registrado en **ADR 0036**.
- **Imágenes de monitorización pineadas + endurecidas.** Sin `:latest`,
  `no-new-privileges`, `cap_drop: ALL` donde es viable (cAdvisor necesita
  privilegios de host, con mounts read-only). Alinea con la auditoría 06.14.

## Verificación

- `pre-commit run --files <cambiados>` (black/ruff/mypy/prettier/yaml/markdown) ✅
  por tarea.
- Suite de Fase A/B/C en verde por tarea (pytest) con el `CommandRunner` /
  cliente boto3 / sesión paramiko / config rclone **MOCKEADOS** — CI nunca toca
  un `pg_dump`/S3/SFTP/rclone real.
- `test_host_alerts.py` (task_12_14) parsea `host_alerts.yml` y asevera las
  reglas + umbrales (disco 80%, RAM 90%, swap, OOM, último backup) sin `curl`.
- `docker compose -f docker/docker-compose.yml -f docker/docker-compose.monitoring.yml
config -q` parsea (el overlay extiende el stack sin romper el job de CI
  build-images). Los `curl prometheus`/`curl grafana` del plan necesitan un stack
  VIVO ⇒ marcados como check humano/CI-con-stack.
- Single head de migraciones intacto en **`0053_guardrail_alert_rules`** — este
  plan es infra/config/docs, sin migración nueva.

## Pendiente

### Gaps conocidos (reportados por las fases A–D)

1. **Subida a destino remoto NO auto-cableada en el flujo de backup.** Los
   adaptadores `BackupDestination` (S3/B2/SFTP/rclone) y su UI EXISTEN y están
   probados, pero `workers.backup.run_full_backup()` **NO** los invoca tras un
   backup bueno — el paso «backup → subida a los destinos configurados» todavía
   no está integrado en el beat task. Hoy la subida es un paso **manual** (ver
   [`dr-manual-backup.md`](../06-runbooks/dr-manual-backup.md), paso 3). Cerrar el
   cableado es un follow-up.
2. **El restore por tenant requiere la extensión `dblink` de Postgres** disponible
   para el rol admin. La copia filtrada staging → BD viva usa `dblink(...)`; sin
   la extensión instalada/permitida, el restore por tenant falla. Es un
   **prerequisito de despliegue** (`CREATE EXTENSION dblink;` con privilegio
   adecuado) que se valida en el test humano `human_12_03`.
3. **Todos los caminos reales están mock-verificados en CI.** El `CommandRunner`
   (pg_dump/pg_restore/tar/psql/createdb/rclone), el cliente boto3, la sesión
   paramiko y la config rclone se mockean; `curl prometheus`/`curl grafana`
   necesitan un stack vivo. Quedan **pendientes los tests humanos** `human_12_01`
   (backup automático sin intervención + sync a remoto), `human_12_02` (restore
   completo en máquina virgen), `human_12_03` (restore selectivo por tenant) y
   `human_12_04` (alertas del host) **con un stack en marcha**.
4. **E2E Playwright escritos-no-ejecutados.** `backup-schedule.spec.ts`,
   `backup-destinations.spec.ts` y `restore-ui.spec.ts` están **escritos pero
   PENDIENTES DE VERIFICACIÓN HUMANA**: el runtime node-playwright de este entorno
   no tiene navegador.

### Cierre del plan

El plan pasa a `pending_human_validation` (no `completed`): faltan los tests
humanos `human_12_*` con un stack vivo y el **PR a `main`**, ambos
**human-owned**. Las 17 tareas tienen su checkbox `[x]` y su test automático en
verde (o, para los checks live de stack, marcado como verificación humana/CI).

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan y cerrar — o aceptar explícitamente — los gaps de
arriba).
