---
title: DR completo — restauración total desde un backup
docs_language: es
audience: operador, system admin
updated: 2026-05-30
---

# Runbook — DR completo (restauración total desde un backup)

Recuperación ante desastre **completa**: reconstruir todo el stack
(PostgreSQL + volúmenes de datos) a partir de un bundle de backup de
Fase A, usando el restore completo de Fase C
(`workers.restore.run_full_restore`, `task_12_10`).

Es una operación **destructiva y global**: reemplaza la base de datos y
los volúmenes de TODOS los tenants por el contenido del backup elegido.
Para recuperar un solo tenant sin tocar a los demás, usa
[dr-tenant-restore.md](./dr-tenant-restore.md) en su lugar.

## Propósito

- Levantar la plataforma en una máquina virgen tras una pérdida total
  (disco corrupto, host destruido, ransomware).
- Volver el stack a un punto en el tiempo conocido y verificado.

## Precondiciones

- Tienes un **bundle de backup** accesible en disco con el layout de
  Fase A: `<id>/postgres/` (dump lógico en formato directorio),
  `<id>/<volumen>.tar.gz` por cada volumen, y `<id>/manifest.json`
  (artefactos + tamaños + checksums SHA-256). Si solo está en un destino
  remoto, descárgalo primero (ver
  [dr-manual-backup.md](./dr-manual-backup.md), sección «Recuperar un
  bundle del destino remoto»).
- Si el bundle está **cifrado** (sufijo `.enc`), Vault está accesible y
  contiene la clave de cifrado (`WORKERS_BACKUP_ENCRYPTION_VAULT_KEY`);
  `run_full_restore` la resuelve por el mismo seam que el backup.
- Docker + Docker Compose instalados; el repo desplegado en la máquina
  destino; `docker/.env` con las variables `WORKERS_*` correctas
  (`WORKERS_BACKUP_DATABASE_URL`, `WORKERS_BACKUP_VOLUMES`,
  `WORKERS_BACKUP_VOLUMES_MOUNT_ROOT`).
- El stack puede estar **parado**: el restore completo detiene los
  servicios que poseen los volúmenes antes de sobrescribirlos.

> Verificación primero, fail-closed: el motor verifica el manifest
> (checksums + estructura) ANTES de tocar nada. Un bundle corrupto
> aborta sin escribir un solo byte.

## Pasos

### 1. Identifica el bundle a restaurar

Lista los bundles disponibles en el directorio de backups
(`WORKERS_BACKUP_ROOT`) y elige el `<backup_id>` más reciente que sepas
bueno:

```bash
ls -1 "${WORKERS_BACKUP_ROOT:-/data/agent-platform/backups}"
```

Cada subdirectorio es un bundle. Inspecciona su `manifest.json` para ver
fecha, artefactos y checksums.

### 2. Arranca la infraestructura mínima

Para que el restore pueda recrear la base de datos necesita PostgreSQL
arriba (los volúmenes se restauran con el resto de servicios parados):

```bash
docker compose \
  -f docker/docker-compose.yml \
  up -d postgres
```

### 3. Lanza el restore completo DESDE EL HOST

```bash
./scripts/restore.sh --list        # los bundles disponibles
./scripts/restore.sh <backup_id>   # pide el token de doble confirmación
```

El script pide un **token de confirmación** igual al `backup_id` (doble
confirmación: evita restaurar el bundle equivocado).

> **Nunca `docker compose exec`.** Este runbook mandó durante meses
> `exec -T worker python -c ...`, y no funcionaba por dos razones
> independientes: (a) el servicio se llama `workers`, no `worker`, así que el
> comando fallaba antes de empezar; (b) aunque existiera, el restore PARA la
> aplicación y `workers` está entre los servicios que para — el proceso se
> mataría a sí mismo a mitad de una operación destructiva. Por eso el motor
> corre en el host, con acceso al socket de Docker y a los volúmenes. El plano
> que ejecuta el restore nunca puede estar en la lista de servicios a parar.

Requisitos del host: `docker`, `pg_restore` y `psql` en el `PATH`. El script los
comprueba antes de tocar nada.

El motor, en orden: localiza el bundle → lo descifra si procede → lo
**verifica** (fail-closed: un bundle corrupto aborta sin escribir un byte) →
**preflight de servicios** (todos los que va a parar tienen que estar declarados
en el compose) → para la aplicación → `pg_restore --clean --exit-on-error` →
**re-concede los GRANTs** de `app_user` → para los servicios de los volúmenes y
re-extrae volúmenes, **repos de proyectos** y binds declarados → arranca el
stack.

### 4. Desella Vault y reconcilia

Si `vault_data` estaba en el bundle, Vault arranca **sellado**:

```bash
docker compose --file /data/agent-platform/docker-compose.yml \
  exec vault vault operator unseal   # repite hasta el threshold
```

Y antes de dar el restore por bueno, comprueba que los cuatro almacenes cuentan
la misma historia:

```bash
python -m workers.restore_reconcile
```

## Verificación

- Ejecuta [health-check.md](./health-check.md): `docker compose ps`
  todos `healthy`, `GET /healthz` → 200, `GET /admin/system-health` →
  `status: ok` con `postgres: ok`.
- Confirma que un usuario de un tenant puede **hacer login** con sus
  credenciales previas y que sus proyectos / planes / conversaciones
  aparecen intactos.
- Confirma que los volúmenes restauran: subir/leer un objeto en MinIO,
  Vault desellado (ver siguiente punto), sesión en Redis.
- Si Vault estaba en el backup como snapshot de volumen, tras restaurar
  hay que **desellarlo** de nuevo (ver
  [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md), sección
  «Desellar tras un restore»).

Este es el test humano `human_12_02` del plan («Restore completo en
máquina virgen»).

## Rollback / aborto

- **Antes de lanzar el paso 3**: no se ha escrito nada; basta con no
  ejecutarlo.
- **Si el token de confirmación no coincide**: el motor rechaza la
  operación sin tocar la base de datos ni los volúmenes. Reintenta con el
  `backup_id` correcto.
- **Si el bundle no verifica** (`RestoreVerificationError`): el restore
  aborta antes de cualquier escritura. El bundle está corrupto — usa un
  bundle anterior o recupera una copia íntegra del destino remoto.
- **Si el restore falla a mitad** (raro: fallo de disco durante la
  re-extracción de un volumen): el stack queda en estado inconsistente.
  No lo arranques en producción; repite el restore completo desde el
  mismo bundle (la operación es idempotente: vuelve a hacer `--clean` y a
  vaciar los volúmenes) o desde uno anterior. Mientras tanto, mantén el
  stack **parado** para no servir datos parciales.

## A quién avisar

- **System Admin** de la plataforma: es el aprobador de un DR completo y
  quien custodia las claves de Vault necesarias para descifrar el bundle.
- Si el bundle está cifrado y la clave de Vault no está disponible, el
  restore es imposible: escala al **responsable de seguridad** que
  custodia las unseal keys ([dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)).

## Notas y limitaciones conocidas

- **Sincronización a destino remoto aún no auto-cableada**: en esta fase,
  `workers.backup.run_full_backup()` NO sube el bundle a los destinos
  remotos (S3/B2/SFTP/rclone) automáticamente — los adaptadores de
  `workers.backup_destinations` existen y se prueban, pero la llamada de
  subida desde el flujo de backup queda pendiente de cableado (ver
  [dr-manual-backup.md](./dr-manual-backup.md)). Para un DR real,
  asegúrate de que el bundle que vas a restaurar está físicamente en la
  máquina destino.
