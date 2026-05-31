---
adr: "0036"
title: Backup con pg_dump LÓGICO, cifrado AES-256-GCM con clave del Vault, destinos remotos enchufables y restore selectivo por tenant vía BD staging
status: accepted
date: 2026-05-30
deciders: System Architect, DevOps, Security
phase: 12-backup-restore
---

# ADR 0036 — Backup con pg_dump LÓGICO, cifrado AES-256-GCM, destinos remotos enchufables y restore selectivo por tenant

> **Estado: `accepted`.** Recoge las decisiones arquitectónicas tomadas durante
> el Plan 12 (Backup, Restore y Continuidad) que no estaban registradas en un
> ADR previo: el uso de **`pg_dump` LÓGICO en formato directorio** (no
> `pg_basebackup`) como única captura de PostgreSQL; el **cifrado opcional
> AES-256-GCM del bundle con clave del Vault**; el **modelo `BackupDestination`
> enchufable** con credenciales resueltas por el seam de secretos; y el **restore
> selectivo por tenant mediante una base de datos staging desechable + copia
> filtrada por `tenant_id`**. La monitorización del host (node-exporter /
> cAdvisor / Prometheus / Alertmanager / Grafana) reusa el stack de
> observabilidad ya decidido por el tech stack y el patrón de notificación del
> Plan 10, por lo que no abre una decisión arquitectónica nueva; se documenta en
> el changelog y en `docs/04-reference/backup-restore.md`.

## Contexto

El backup manual con cron de la Fase 0 era suficiente para arrancar pero no
institucionalizaba la continuidad: ni UI, ni restore probado, ni destinos
remotos, ni verificación, ni monitorización. El Plan 12 lo formaliza. Varias
cuestiones de diseño no quedaban cerradas por ADRs previos:

1. **¿Cómo se captura PostgreSQL** de modo que un restore pueda recuperar el
   stack entero PERO también un solo tenant sin tocar a los demás (multi-tenancy
   con RLS desde el día uno, ADR 0001)?
2. **¿Cómo se protege el backup en reposo** sin meter una clave en código ni en
   la BD, respetando la regla "ningún secreto en claro" (CLAUDE.md) y el uso de
   Vault desde el día uno (ADR 0003)?
3. **¿Cómo se soportan destinos remotos heterogéneos** (S3, Backblaze B2,
   SFTP/NAS, rclone) sin acoplar el flujo de backup a cada SDK y sin volcar
   credenciales en config plano?
4. **¿Cómo se restaura un solo tenant** desde un dump que contiene a todos, sin
   poder usar un restore físico, y sin riesgo de tocar las filas de otro tenant?

## Decisión

### 1. `pg_dump` LÓGICO en formato directorio (no `pg_basebackup`)

PostgreSQL se captura con **`pg_dump --format=directory`** (dump LÓGICO), nunca
con `pg_basebackup` binario. El formato directorio es el único que:

- `pg_restore --list` puede introspeccionar (lo necesita la verificación
  post-backup, fail-closed);
- se puede restaurar en una **base de datos staging** y luego consultar +
  filtrar por `tenant_id` (la base del restore selectivo por tenant, decisión 4).

Los volúmenes Docker (MinIO, Redis RDB/AOF, Vault file backend) se capturan con
`tar + gzip` por volumen, y el bundle lleva un `manifest.json` con checksums
SHA-256. El contrato es **fail-closed**: cualquier sub-paso fallido borra el
bundle parcial — un medio-bundle que parece bueno es peor que no tener bundle.
Todo comando externo pasa por un seam `CommandRunner` inyectable (producción:
argv explícito, nunca `shell=True`; tests: un fake), de modo que CI prueba la
construcción de comandos y la orquestación sin un dump real del stack.

### 2. Cifrado opcional AES-256-GCM con clave del Vault

El cifrado en reposo es **OPCIONAL y OFF por defecto** (añade una dependencia de
Vault, así que el operador opta explícitamente). Al activarlo, el bundle se
colapsa en un único `bundle.tar` y se envuelve en un blob **AES-256-GCM**
(`bundle.tar.enc`) con cabecera auto-descriptiva `MAGIC|version|nonce|
ciphertext+tag`, pasando la cabecera como **associated data** (GCM autenticado:
un bit alterado en cabecera, nonce o ciphertext hace fallar el descifrado al
restaurar — fail loud, nunca basura silenciosa). La clave se resuelve del
**Vault** por el mismo seam `SecretsProvider` que usa el inyector de
credenciales del agent-runtime (fold SHA-256 del secreto → 32 bytes AES-256),
vive solo en memoria durante la operación, y **nunca** se escribe en disco ni se
loguea (se loguea el NOMBRE de la clave, no el valor). Los artefactos en claro se
borran tras envolver; solo sobrevive el blob.

### 3. Modelo `BackupDestination` enchufable, credenciales por el seam de secretos

Cada backend remoto implementa un único Protocol **`BackupDestination`**
(`upload` / `list_remote` / `download` / `test_connectivity`), con un adaptador
por backend registrado por `type` (`s3`, `b2`, `sftp`, `rclone`) y un factory
`build_destination(config, secrets=...)`:

- **S3** (boto3, `upload_file` con multipart automático) sirve cualquier
  proveedor S3-compatible vía `endpoint_url`;
- **B2** subclasa S3 (B2 ES S3) y solo añade sus quirks (endpoint derivado de la
  región, part-size multipart pineado, creds propias);
- **SFTP/NAS** (paramiko) con auth password o clave privada y política de
  host-key configurable (`reject` por defecto — el host debe estar en
  known_hosts; nunca se desactiva el check silenciosamente);
- **rclone** envuelve la CLI (~70 backends) tras el `CommandRunner`, abriendo el
  catálogo sin un adaptador a medida por proveedor.

Invariante: **una config de destino NUNCA lleva un secreto**. Los knobs
NO-secretos (bucket, endpoint, host, path, remote) pueden persistirse en
`platform_settings`; las credenciales (access key/secret, password/clave, blob
de rclone) se resuelven en tiempo de ejecución por el seam (Vault/env), nunca en
config plano, nunca en el manifest, nunca logueadas. La construcción de un
destino no hace red ni resuelve creds (lazy), así que el botón "test de
conectividad" de la UI puede invocarlo barato.

### 4. Restore selectivo por tenant vía BD staging + copia filtrada por `tenant_id`

Un restore que simplemente `pg_restore`-ara el dump clobberearía a todos los
tenants. En su lugar, el restore por tenant es una **copia staged y filtrada**:

1. localiza → (descifra) → **VERIFICA** (fail-closed, precede a todo);
2. `pg_restore` del dump LÓGICO en una **base de datos STAGING desechable** (la
   BD viva no se toca aún);
3. **preview**: `SELECT count(*) ... WHERE tenant_id = <target>` por tabla
   tenant-scoped en la staging (sin escribir nada; es lo que la UI muestra para
   la segunda confirmación, y todo lo que hace un `dry_run`);
4. **copia filtrada** a la BD viva, en orden FK, dentro de UNA transacción:
   `DELETE`/`INSERT ... SELECT dblink(...) WHERE tenant_id = <target>` en AMBOS
   lados, con `ON_ERROR_STOP`; corre con el rol **BYPASSRLS** (RLS ocultaría las
   filas de staging/otros) pero **SIEMPRE** acotado por el predicado `tenant_id`
   — BYPASSRLS quita la política, el predicado mantiene el radio de impacto en un
   solo tenant;
5. drop de la staging en un finally;
6. object storage: re-extrae SOLO el prefijo `<tenant_id>/` del volumen
   capturado, nunca el volumen entero.

Guardas: **doble confirmación** con token `<tenant_id>@<backup_id>`,
**verify-before-restore fail-closed**, **`tenant_id` validado como UUID** y
nombres de tabla validados contra un regex de identificador antes de
interpolarse en SQL. El restore por tenant **requiere la extensión `dblink`**
disponible para el rol admin (prerequisito de despliegue; ver Consecuencias).

## Alternativas consideradas

- **`pg_basebackup` (backup físico binario).** Más rápido y simple para un
  restore completo, pero NO permite restaurar en una staging ni filtrar por
  tenant, y es sensible a la versión exacta del binario PostgreSQL. Descartado: el
  restore selectivo por tenant es un requisito del plan.
- **Cifrado por GPG/openssl como paso de shell.** Otra dependencia de binario
  externo y un manejo de clave fuera del seam de secretos ya existente.
  Descartado a favor de `cryptography` (`AESGCM`, pip-clean, ya presente) con la
  clave del Vault por el seam.
- **Streaming chunked del cifrado.** Necesitaría un formato enmarcado propio; el
  bundle es un tar ya dimensionado por la retención del operador, así que la
  primera versión usa AEAD one-shot. Una mejora futura, no una decisión de fondo.
- **Un cliente/SDK por destino cableado directo en el flujo de backup.** Acoplaría
  el motor a boto3/paramiko/rclone y duplicaría upload/list/download por backend.
  Descartado a favor del Protocol `BackupDestination` único + registry por type.
- **Credenciales de destino en `platform_settings` (config).** Volcaría secretos
  en la BD. Descartado: la config lleva solo knobs no-secretos; las credenciales
  van por el seam (Vault/env).
- **Restore por tenant con `pg_restore` parcial (`--table`).** No puede filtrar
  por `tenant_id` (filtra por tabla, no por fila) ni borrar las filas vivas del
  tenant de forma segura. Descartado a favor de staging + copia filtrada por
  predicado.
- **Copia cross-DB con dump/COPY a fichero intermedio.** Más pasos y un fichero
  con datos de un tenant en disco. Descartado a favor de `dblink` en una sola
  transacción (asumiendo la extensión como prerequisito).

## Consecuencias

- El backup es **portable y selectivo**: un mismo bundle sirve para un DR
  completo y para un restore de un solo tenant. La verificación post-backup
  (checksums + `pg_restore --list`/`tar -tf`) es fail-closed y precede a todo
  restore.
- Un destino remoto nuevo es **una clase más** que implementa
  `BackupDestination`, registrada por `type`; el flujo de backup y el botón de
  test de conectividad no cambian. Las credenciales nunca tocan la config ni los
  logs.
- El restore por tenant **requiere la extensión `dblink`** disponible para el rol
  admin (`CREATE EXTENSION dblink;`). Es un **prerequisito de despliegue**: sin
  ella, la copia filtrada staging → BD viva falla. Se valida en el test humano
  `human_12_03`.
- **El cableado «backup → subida automática al destino remoto» NO está integrado
  todavía** en el beat task: los adaptadores + la UI existen y se prueban, pero
  `run_full_backup()` no los invoca tras un backup bueno. Hoy la subida es un paso
  manual (runbook `dr-manual-backup.md`). Cerrarlo es un follow-up; este ADR no lo
  decide más allá de fijar el modelo de destinos.
- **Todos los caminos reales (pg_dump/pg_restore/tar/S3/SFTP/rclone, y los `curl`
  a Prometheus/Grafana) están mock-verificados en CI** y necesitan los tests
  humanos `human_12_*` con un stack vivo para validación integral.
- Las imágenes del overlay de monitorización están **pineadas** (sin `:latest`) y
  endurecidas (`no-new-privileges`, `cap_drop: ALL` donde es viable), alineadas
  con la auditoría 06.14.
