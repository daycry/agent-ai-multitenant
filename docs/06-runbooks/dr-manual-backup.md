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
  - `WORKERS_BACKUP_VOLUMES` — lista JSON de volúmenes Docker. En un stack
    generado por el instalador es `[]` **a propósito**: no hay named volumes, los
    stores son binds bajo `{data_root}` (prod-04 task_prod_04_09).
  - `WORKERS_BACKUP_VOLUMES_MOUNT_ROOT` — dir host con esos volúmenes.
  - `WORKERS_BACKUP_BIND_PATHS` — lista JSON de rutas del host que se tarean
    (MinIO y el file backend de Vault). **Tienen que ser visibles dentro del
    contenedor de la lane `privileged` en su MISMA ruta**, o `tar` no las
    encuentra (o peor: las encuentra vacías en el rootfs efímero y el bundle sale
    «correcto» y vacío).
  - `WORKERS_BACKUP_REDIS_DIR` — data dir de Redis. Va aparte de los binds porque
    su captura pide un `BGREWRITEAOF` completado antes del `tar`.
  - `WORKERS_BACKUP_STABLE_SNAPSHOT_PATHS` — rutas cuya captura se verifica
    estable (el árbol de Vault). **No pongas MinIO aquí**: se escribe por diseño y
    el backup fallaría todas las noches.
  - `WORKERS_BACKUP_PROJECTS_ROOT` — raíz de los bare repos.
- Si el cifrado está activado, el **valor** de la clave en el entorno
  (`WORKERS_BACKUP_ENCRYPTION_KEY`) y su huella declarada en
  `WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT`. **La clave NO se resuelve de Vault**
  —`EnvSecretsProvider` lee `os.environ`— y **las unseal keys NO descifran el
  bundle**: son cosas distintas y ambas se custodian offsite por separado (ver
  [dr-drill.md](./dr-drill.md)). Si la huella no coincide con la de la clave
  activa, el backup FALLA a propósito: significa que alguien rotó la clave sin
  actualizar la custodia y los bundles nuevos no los podría abrir nadie.
- Para empujar a remoto: la entrada de `backup_destinations` configurada
  y sus credenciales en Vault/entorno (ver UI de destinos, `task_12_09`).

## Activar el cifrado en reposo: opt-in en DOS pasos

Un stack recién instalado sale con **`WORKERS_BACKUP_ENCRYPTION_ENABLED=false`**,
y no por descuido. El motor es fail-closed: con el cifrado encendido y sin huella
de custodia declarada, el backup **falla antes de empezar** — y con razón, porque
un bundle cifrado cuya clave no está custodiada es irrecuperable si el host
muere. Un instalador no puede depositar una clave en un sobre sellado, así que
encenderlo de fábrica solo produciría un stack cuyo backup falla cada noche.

El orden importa, y es este:

1. **Genera la clave y deposítala offsite.** 32 bytes de un CSPRNG, en el gestor
   corporativo o un sobre sellado, **junto a las unseal keys pero en un registro
   diferenciado** — no son lo mismo y las unseal keys NO descifran AES-GCM:

   ```bash
   python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
   ```

   Anota en el registro de custodia: quién la depositó, dónde, y la fecha.

2. **Cablea las tres variables y enciéndelo.** En `docker/.env`:
   `WORKERS_BACKUP_ENCRYPTION_KEY` (el valor),
   `WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT` (su huella SHA-256, que sale del log
   del primer backup o del campo `key_fingerprint` del manifest) y
   `WORKERS_BACKUP_ENCRYPTION_ENABLED=true`. Reinicia la lane `privileged` y lanza
   un backup manual para comprobar que la huella coincide.

Si el paso 1 no se ha hecho, **deja el cifrado apagado**: un bundle en claro en un
disco que controlas es peor que un bundle cifrado que puedes abrir, y muy mejor
que un backup que no existe porque falla cada noche.

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
- `<volumen>.tar.gz` — uno por volumen configurado (ninguno en un stack del
  instalador, que usa binds).
- `bind-<slug>.tar.gz` — uno por bind path (MinIO, el árbol de Vault).
- `redis.tar.gz` — el data dir de Redis tras un `BGREWRITEAOF` completado.
- `projects.tar.gz` — los bare repos de todos los proyectos.
- `manifest.json` — artefactos, tamaños y checksums SHA-256.

(El servicio de Celery se llama `workers`, no `worker`; y el backup diario ya lo
lanza el beat por sí solo. Este script es para el backup MANUAL desde el host,
p. ej. antes de un upgrade.)

### 2. Verifica el bundle (corruption check)

La verificación post-backup (`task_12_03`,
`workers.backup_verification`) recomputa los checksums del manifest y
comprueba la estructura (`pg_restore --list`, `tar -tf`). El motor ya la
ejecuta tras el backup; para reverificar un bundle existente a mano:

```bash
# Desde el HOST: el servicio se llama `workers`, no `worker`, y el motor no
# debe correr dentro de un contenedor que la propia operación puede parar.
python -c '
from workers.backup_verification import verify_bundle
report = verify_bundle("<backup_id>")
print("verify:", report)
'
```

Un bundle que no verifica NO debe usarse para restaurar: repite el backup.

### 3. Empuja el bundle al destino remoto

> **Ya no hace falta hacerlo a mano** (prod-04 task_prod_04_12). El backup
> diario sube el bundle **automáticamente**, y solo después de verificarlo, a
> todos los destinos habilitados; el resultado aparece en el resumen de la tarea
> (`uploaded` / `upload_failures`) y en la métrica
> `agentic_backup_offsite_uploaded`. Un bundle que NO verifica no se sube nunca:
> una copia remota corrupta es peor que ninguna, porque da confianza.
>
> El comando de abajo sigue siendo útil para **re-empujar** un bundle concreto
> (p. ej. tras arreglar un destino que estaba caído).

```bash
# Desde el HOST: el servicio se llama `workers`, no `worker`, y el motor no
# debe correr dentro de un contenedor que la propia operación puede parar.
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

- **Subida a destino remoto: automática desde prod-04.** El backup diario sube
  el bundle verificado a los destinos habilitados sin intervención. Es
  best-effort **por diseño**: un destino caído no invalida el backup local, pero
  deja de haber copia fuera de la máquina — vigila `upload_failures` y la
  métrica `agentic_backup_offsite_uploaded`. El paso 3 queda como re-empuje
  manual.
