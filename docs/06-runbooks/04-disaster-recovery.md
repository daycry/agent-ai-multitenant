---
title: Disaster recovery — restore completo y selectivo por tenant
docs_language: es
audience: operador, system admin, responsable de seguridad
updated: 2026-05-31
---

# Runbook — Disaster recovery (restore completo y por tenant)

Punto de entrada **canónico** para recuperar la plataforma desde un backup:
reconstruir todo el stack tras una pérdida total, o devolver un solo tenant a
un punto en el tiempo sin tocar a los demás. Este runbook **orquesta** la
decisión y los prerequisitos comunes; el detalle paso a paso de cada motor de
restore vive en los runbooks de Fase 12, que se enlazan en cada sección — no
se duplican aquí.

> Alcance: **Docker Compose en una sola máquina** (CLAUDE.md). Ambos restores
> se apoyan en los bundles del motor de backup de Fase 12 y en su seam de
> cifrado/verificación. Para producir o subir un bundle (incluido el backup
> pre-upgrade), ver [dr-manual-backup.md](./dr-manual-backup.md).

## Cuándo y qué camino elegir

| Situación                                                                  | Camino                                                                                                   |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Pérdida total (disco corrupto, host destruido, ransomware); máquina virgen | **Restore completo** → [dr-full-restore.md](./dr-full-restore.md) (detalle de Fase 12)                   |
| Volver TODO el stack a un punto en el tiempo conocido                      | **Restore completo** → [dr-full-restore.md](./dr-full-restore.md)                                        |
| Un solo tenant borró/corrompió sus datos; el resto debe seguir operando    | **Restore selectivo por tenant** → [dr-tenant-restore.md](./dr-tenant-restore.md) (Fase 12)              |
| Rollback de un upgrade fallido / migración no reversible                   | **Restore completo** al backup pre-upgrade — ver [03-system-upgrade.md](./03-system-upgrade.md#rollback) |

La diferencia operativa clave:

- El **restore completo** es **destructivo y global**: reemplaza la base de
  datos y los volúmenes de TODOS los tenants. Requiere parar el stack.
- El **restore selectivo** es **quirúrgico**: solo toca las filas del tenant
  objetivo (`WHERE tenant_id = '<target>'`) y su prefijo en MinIO, con el
  stack arriba y cero impacto sobre los demás tenants.

## Invariantes comunes a ambos restores

Antes de entrar en cualquiera de los dos runbooks de detalle, estos
prerequisitos son comunes y conviene tenerlos resueltos.

### 1. Verificación primero (fail-closed)

Los dos motores **verifican el bundle ANTES de escribir un solo byte**:
recomputan los checksums SHA-256 del `manifest.json` y validan la estructura
(`pg_restore --list`, `tar -tf`). Un bundle corrupto **aborta** el restore sin
tocar nada (`RestoreVerificationError` / `PerTenantRestoreVerificationError`).

Si quieres reverificar un bundle a mano antes de comprometerte a restaurar,
usa el corruption check de Fase 12 descrito en
[dr-manual-backup.md](./dr-manual-backup.md) (sección «Verifica el bundle»):

```bash
docker compose \
  -f docker/docker-compose.yml \
  exec -T worker \
  python -c '
from workers.backup_verification import verify_bundle
print("verify:", verify_bundle("<backup_id>"))
'
```

Nunca restaures desde un bundle que no verifica: usa uno anterior o recupera
una copia íntegra del destino remoto.

### 2. Descifrado con la clave de Vault

Si el bundle está **cifrado** (sufijo `.enc`), Vault debe estar accesible y
**desellado**, y contener la clave de cifrado
(`WORKERS_BACKUP_ENCRYPTION_VAULT_KEY`). Ambos motores resuelven la clave por
el **mismo seam** que usó el backup para cifrar; no hay que descifrar el
bundle a mano. Sin Vault desellado o sin la clave, el restore de un bundle
cifrado es **imposible**: escala al responsable de seguridad que custodia las
unseal keys (ver [05-key-rotation.md](./05-key-rotation.md)).

En un DR sobre máquina virgen, Vault arranca **sellado**; deséllalo antes del
restore si el bundle está cifrado (ver §«Desellar Vault tras un restore» más
abajo).

### 3. Disponibilidad física del bundle (gap conocido)

El motor de backup de Fase 12 **NO sube** el bundle a los destinos remotos
(S3/B2/SFTP/rclone) automáticamente — el cableado «backup → subida» está
pendiente (ver [dr-manual-backup.md](./dr-manual-backup.md)). Para un DR real,
asegúrate de que el bundle a restaurar está **físicamente** en la máquina
destino, bajo `WORKERS_BACKUP_ROOT`. Si solo está en remoto, descárgalo antes
con la herramienta nativa del destino (`aws s3 cp --recursive`, `b2`, `sftp`,
`rclone copy`).

### 4. dblink (solo para el restore selectivo por tenant)

El restore selectivo copia las filas del tenant desde una base de datos de
staging a la base viva con `dblink(...)`. La extensión `dblink` **NO se
aprovisiona** hoy en ninguna migración Alembic ni en el init de PostgreSQL:
hay que crearla **una sola vez por despliegue** antes del primer restore
selectivo:

```bash
docker compose \
  -f docker/docker-compose.yml \
  exec -T postgres \
  psql -U postgres -d agentic_platform \
  -c 'CREATE EXTENSION IF NOT EXISTS dblink;'
```

Es idempotente; reejecutarlo es inofensivo. Sin esta extensión, el copiado
filtrado falla. (Gap de despliegue: debería formalizarse como migración
idempotente en un plan posterior — ver
[dr-tenant-restore.md](./dr-tenant-restore.md), «Notas y limitaciones».)

## Restore completo (todo el stack)

Operación destructiva y global. El procedimiento detallado —identificar el
bundle, arrancar PostgreSQL, lanzar `run_full_restore` con el token de
confirmación igual al `backup_id`, y arrancar el stack— está en
**[dr-full-restore.md](./dr-full-restore.md)**.

Resumen del flujo (el detalle, comandos y rollback están en ese runbook):

1. Identifica el `<backup_id>` bueno bajo `WORKERS_BACKUP_ROOT` (inspecciona
   su `manifest.json`).
2. Arranca la infraestructura mínima (`docker compose up -d postgres`).
3. Lanza el restore completo con **doble confirmación**
   (`confirm="<backup_id>"`): localiza, descifra (clave de Vault) y
   **verifica** el bundle; `pg_restore --clean`; detiene los servicios dueños
   de los volúmenes; re-extrae cada `<volumen>.tar.gz`.
4. Arranca el stack completo (`docker compose up -d`) y **desella Vault**.

Este es el test humano `human_12_02` del Plan 12 («Restore completo en máquina
virgen»).

## Restore selectivo por tenant

Operación quirúrgica, con el stack arriba y cero impacto sobre los demás
tenants. El procedimiento detallado —dry-run de vista previa, token
`f"{tenant_id}@{backup_id}"`, copiado en UNA transacción vía `dblink`— está en
**[dr-tenant-restore.md](./dr-tenant-restore.md)**.

Resumen del flujo (detalle, comandos y rollback en ese runbook):

1. Identifica el `<backup_id>` y el `<tenant_id>` (UUID) afectado.
2. Asegura el prerequisito **dblink** (§4 de este runbook): una sola vez por
   despliegue.
3. **Dry-run** (`dry_run=True`): calcula tablas afectadas + recuento de filas
   sin escribir nada; revisa que cuadra antes de continuar.
4. Ejecuta el restore selectivo (`dry_run=False`) con el token de
   confirmación: verifica el bundle; restaura a un **staging** desechable; en
   UNA transacción borra y re-inserta las filas del tenant
   (`WHERE tenant_id = '<target>'`) vía `dblink`; elimina el staging en un
   `finally`; re-extrae solo el prefijo `<tenant_id>/` de MinIO. Si algo
   falla a mitad, el `ON_ERROR_STOP=1` fuerza **ROLLBACK** y la base viva
   vuelve a su estado previo.

Este es el test humano `human_12_03` del Plan 12 («Restore selectivo de un
tenant sin afectar a los demás» + audit log de la operación).

## Desellar Vault tras un restore

Si el volumen `vault_data` se restauró (restore completo en máquina virgen, o
arranque tras restaurar), Vault arranca **sellado**. Deséllalo con el umbral
de unseal keys vigentes en el momento del backup (threshold = 3 de 5 por
defecto):

```bash
docker compose -f docker/docker-compose.yml \
  exec vault vault operator unseal   # repite hasta alcanzar el threshold
```

Detalle, custodia de shares y rotación de claves en
[05-key-rotation.md](./05-key-rotation.md). Hasta que Vault no esté desellado,
la API y los workers no leen secretos y fallan.

## Verificación post-restore

Tras cualquiera de los dos restores:

1. **Salud del stack** — [health-check.md](./health-check.md):
   `docker compose ps` todos `Up (healthy)`, `GET /healthz` → 200,
   `GET /admin/system-health` → `status: ok` con `postgres: ok` (y `vault: ok`
   si estaba en el bundle).
2. **Login y datos** — un usuario del tenant restaurado hace login con sus
   credenciales previas y ve sus proyectos / planes / conversaciones intactos
   al punto del backup elegido.
3. **Smoke tests post-deploy** (`task_15_26`) — ejercitan los caminos
   críticos de extremo a extremo sobre el stack recuperado:

   ```bash
   pytest tests/smoke/ -v
   ```

   Se **autoexcluyen** (skip-guard) cuando no hay stack vivo, así que en CI
   quedan en verde; contra el stack recuperado **sí** corren y validan la
   recuperación.

4. **Aislamiento (solo restore selectivo)** — confirma que un usuario de
   **otro** tenant sigue viendo sus datos actuales **sin cambios**, y que el
   audit log refleja quién hizo el restore y sobre qué tenant.

## Rollback / aborto

- **Antes de lanzar el restore**: nada escrito; basta con no ejecutarlo.
- **Token de confirmación incorrecto**: el motor rechaza la operación sin
  tocar la base ni los volúmenes. Reintenta con el `backup_id` (y `tenant_id`)
  correcto.
- **Bundle que no verifica**: el restore aborta antes de cualquier escritura;
  usa un bundle anterior o recupera una copia íntegra del remoto.
- **Fallo a mitad — restore completo**: el stack queda inconsistente; mantenlo
  **parado**, no sirvas datos parciales, y repite el restore (es idempotente)
  desde el mismo bundle o uno anterior. Detalle en
  [dr-full-restore.md](./dr-full-restore.md).
- **Fallo a mitad — restore selectivo**: la transacción única hace **ROLLBACK**
  y la base viva queda como estaba; corrige la causa (típicamente `dblink` no
  habilitado) y reintenta. Detalle en [dr-tenant-restore.md](./dr-tenant-restore.md).

## A quién avisar

- **System Admin**: aprobador de cualquier restore (acción sensible y
  destructiva) y quien programa la ventana; custodia las unseal keys
  necesarias para descifrar el bundle y desellar Vault.
- **Responsable del tenant** afectado (restore selectivo): confirma el punto
  de restauración deseado y valida los datos recuperados.
- **Responsable de seguridad**: si el bundle está cifrado y la clave de Vault
  no está disponible, o si hay que coordinar las unseal keys
  ([05-key-rotation.md](./05-key-rotation.md)).
- **DBA / DevOps** (restore selectivo): si `dblink` no está habilitado o la
  transacción de copiado falla por integridad referencial.

## Enlaces

- Detalle del restore completo: [dr-full-restore.md](./dr-full-restore.md).
- Detalle del restore selectivo por tenant: [dr-tenant-restore.md](./dr-tenant-restore.md).
- Producir / verificar / subir un bundle: [dr-manual-backup.md](./dr-manual-backup.md).
- Backup manual a nivel de volumen (procedimiento básico): [backups.md](./backups.md).
- Desellar y rotar claves de Vault: [05-key-rotation.md](./05-key-rotation.md).
- Rollback de un upgrade fallido: [03-system-upgrade.md](./03-system-upgrade.md#rollback).
- Salud del stack: [health-check.md](./health-check.md).
  </content>
  </invoke>
