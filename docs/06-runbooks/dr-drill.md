---
title: Simulacro de DR — backup → máquina limpia → restore → ejecución de un plan
docs_language: es
audience: operador, system admin, responsable de seguridad
updated: 2026-07-31
---

# Runbook — Simulacro de recuperación ante desastre (DR drill)

Este es el **único** procedimiento que demuestra que la plataforma se puede
recuperar. Todo lo demás —los tests, la verificación del bundle, las alertas—
prueba que los mecanismos existen; solo el simulacro prueba que **funcionan
juntos y sin las cosas que solo hay en la máquina original**.

Es el test humano `human_prod_04_01` del plan prod-04, y su acta sirve además de
evidencia para `human_12_02` del Plan 12.

> **Regla de oro del simulacro**: durante todo el procedimiento, **nadie consulta
> la máquina origen**. Ni para copiar el `.env`, ni para «recordar» la clave, ni
> para mirar cómo estaba configurado algo. Si necesitas la máquina origen para
> recuperar, no tienes recuperación: tienes suerte.

## Qué demuestra (y qué NO demuestra un test automático)

| Pregunta                                                               | ¿La contesta la suite? |
| ---------------------------------------------------------------------- | ---------------------- |
| ¿El motor construye bien los argv de tar?                              | Sí                     |
| ¿Un bundle real se produce, verifica, cifra y descifra?                | Sí                     |
| ¿El restore extrae volúmenes, repos y binds?                           | Sí                     |
| **¿La clave de descifrado está de verdad en el sobre sellado?**        | **No — solo el drill** |
| **¿Alguien sabe desellar Vault sin la máquina origen?**                | **No — solo el drill** |
| **¿Cuánto se tarda de verdad (RTO real)?**                             | **No — solo el drill** |
| **¿La plataforma restaurada puede EJECUTAR un plan, no solo abrirse?** | **No — solo el drill** |

## Precondiciones

### Personas (dos, y no la misma)

- **Operador**: ejecuta el procedimiento. No debe tener acceso a la máquina
  origen durante el simulacro. Idealmente alguien que no la instaló.
- **Responsable de seguridad**: entrega el material de custodia (ver abajo) y
  registra la entrega. No ejecuta.

### Máquina limpia

- Host Linux con Docker + Docker Compose, **sin** ningún dato de la plataforma.
- Cliente de PostgreSQL instalado en el HOST (`pg_restore` y `psql`): el motor
  de restore corre en el host, no dentro de un contenedor (ver más abajo).
- Acceso de red al destino remoto donde vive el bundle.

### Material que sale de la custodia offsite (y de ningún otro sitio)

1. **El VALOR de `WORKERS_BACKUP_ENCRYPTION_KEY`**. Sin esto no hay nada que
   hacer: el bundle es AES-256-GCM y no existe forma de abrirlo.
2. **Las unseal keys de Vault** (threshold de 3 de 5 por defecto).
3. **Las credenciales de PostgreSQL** (`migrations_user`, `app_user`) o el `.env`
   de producción custodiado.
4. **Las credenciales del destino remoto** desde donde se descarga el bundle.

> **Las unseal keys y la clave del backup son cosas DISTINTAS.** Las unseal keys
> abren Vault; la clave del backup descifra el bundle. Y el backend de Vault
> viaja DENTRO del blob cifrado, así que las unseal keys por sí solas no sirven
> de nada: primero hay que descifrar. Los runbooks afirmaron durante meses que
> «Vault resuelve la clave del backup»; era falso —`EnvSecretsProvider` lee
> `WORKERS_BACKUP_ENCRYPTION_KEY` de `os.environ`— y esa creencia habría
> convertido el primer DR real en una pérdida total.

## Procedimiento

### 1. En la máquina ORIGEN: producir y verificar un bundle

```bash
./scripts/backup.sh
```

Comprobaciones antes de seguir (si alguna falla, el simulacro para aquí y se
arregla el backup, que es justo el objetivo):

- El bundle existe bajo `WORKERS_BACKUP_ROOT` con su `manifest.json`.
- El manifest incluye el artefacto `projects_tar` (los bare repos de los
  proyectos) además de los `volume_tar` y el `postgres/`.
- El manifest trae `key_fingerprint` y **coincide** con el que figura en el
  registro de custodia. Si no coincide, alguien rotó la clave sin actualizar la
  custodia (el backup debería haber fallado; si no lo hizo, revisa
  `WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT`).
- El log del backup dice `uploaded: [...]` con al menos un destino remoto.

Anota: `backup_id`, tamaño del bundle, hora de inicio y fin.

### 2. Cerrar la máquina origen

A partir de aquí, el operador **no vuelve a tocarla**. Si el simulacro es sobre
una plataforma viva, basta con que se comprometa a no consultarla; si se puede
apagar, mejor.

**Arranca el cronómetro del RTO.**

### 3. En la máquina LIMPIA: material de custodia

El responsable de seguridad entrega el material y lo registra en el acta. El
operador lo coloca en el entorno:

```bash
export WORKERS_BACKUP_ENCRYPTION_KEY='<valor recuperado de la custodia>'
```

Comprobación temprana (barata, y evita descubrir el problema tras descargar
40 GB): la huella de la clave que acabas de recibir tiene que coincidir con la
del manifest.

```bash
python -c '
from workers.backup_encryption import BackupEncryptor, EnvSecretsProvider
print(BackupEncryptor(provider=EnvSecretsProvider(),
                      vault_key_name="backup_encryption_key").key_fingerprint())
'
```

### 4. Traer el bundle del destino remoto

Con la herramienta nativa del destino (`aws s3 cp`, `b2`, `sftp`, `rclone copy`)
hasta `WORKERS_BACKUP_ROOT` de la máquina limpia. Descomprime el `.tar` a un
directorio con el nombre del `backup_id`.

### 5. Desplegar el stack y arrancar solo PostgreSQL

```bash
docker compose --file /data/agent-platform/docker-compose.yml up -d postgres
```

El resto de servicios se quedan parados: el restore los parará igualmente y
arrancarlos antes solo añade escrituras concurrentes.

### 6. Lanzar el restore desde el HOST

```bash
./scripts/restore.sh --list           # ver los bundles disponibles
./scripts/restore.sh <backup_id>      # pide el token de doble confirmación
```

**Por qué desde el host y no con `docker compose exec`**: el restore para el
stack, y `workers` está en la lista de servicios a parar. Un restore lanzado
dentro de un contenedor se mataría a sí mismo a mitad de una operación
destructiva. (El runbook anterior mandaba `exec -T worker`, un servicio que
además no existe en ningún compose: se llama `workers`.)

El motor hace, en orden: localizar → descifrar → **verificar** (fail-closed) →
preflight de servicios → parar la aplicación → `pg_restore --exit-on-error` →
re-conceder GRANTs a `app_user` → restaurar volúmenes, repos de proyectos y
binds → arrancar el stack.

**Si falla a mitad, el stack queda PARADO a propósito.** No lo arranques: lee el
`stage` del error, corrige, y re-ejecuta el restore completo (es idempotente).

### 7. Desellar Vault

```bash
docker compose --file /data/agent-platform/docker-compose.yml \
  exec vault vault operator unseal    # repite hasta el threshold
```

Con las unseal keys de la custodia. Hasta que Vault no esté desellado, la API y
los workers no leen secretos y fallan.

### 8. Comprobar permisos y RLS

El restore ya re-concede los GRANTs, pero hay que verificarlo con los ojos: el
dump se hace con `--no-owner --no-privileges`, así que sin esa re-concesión la
aplicación arranca y falla en la primera consulta.

```bash
# Como app_user (NOBYPASSRLS): leer y escribir con RLS activo.
psql "$APP_DATABASE_URL" -c "SET app.tenant_id = '<un tenant real>';" \
                         -c "SELECT count(*) FROM projects;"

# Como migrations_user: las migraciones tienen que poder correr.
alembic upgrade head
```

### 9. Reconciliar los cuatro almacenes

Un restore no está bien hasta que la base de datos, MinIO, Vault y los repos git
cuentan la misma historia. Un bundle es un conjunto de fotos tomadas en
instantes ligeramente distintos, y algo puede haberse quedado a medias.

```bash
python -m workers.restore_reconcile
```

Sale con código ≠ 0 si hay divergencias críticas. Registra el informe en el acta.

### 10. La prueba de verdad: usar la plataforma

Que el stack esté `healthy` no significa nada; hay que **usarlo**.

1. **Login** de un usuario de tenant con sus credenciales previas.
2. Sus proyectos, planes y conversaciones aparecen intactos.
3. **Abrir un proyecto y comprobar su repo**: los repos restaurados contienen
   las ramas `plan/*` de los planes activos (esto es lo que `restore_reconcile`
   comprueba en bloque; míralo también en la UI de un proyecto).
4. **Ejecutar un plan de punta a punta.** Es el paso que descubre lo que ningún
   otro descubre: credenciales de proveedor LLM que no sobrevivieron, imágenes
   de runtime que no están en el registry de la máquina nueva, un worktree que
   no se puede crear.

**Para el cronómetro. Ese es el RTO real.**

## Acta del simulacro

Plantilla — archívala junto al registro de custodia.

```
ACTA DE SIMULACRO DE DR
=======================
Fecha:                          ____________________
Operador:                       ____________________
Responsable de seguridad:       ____________________
Máquina limpia (host/specs):    ____________________

BUNDLE
  backup_id:                    ____________________
  Tamaño:                       ____________________
  Cifrado:                      sí / no
  key_fingerprint (manifest):   ____________________
  ¿Coincide con la custodia?:   sí / no
  Destino remoto usado:         ____________________

TIEMPOS
  Inicio del cronómetro:        ____________________
  Bundle descargado:            ____________________
  Restore terminado:            ____________________
  Vault desellado:              ____________________
  Login correcto:               ____________________
  Plan ejecutado:               ____________________
  RTO REAL:                     ____________________   (objetivo: <= 4 h)

PÉRDIDA DE DATOS
  Antigüedad del bundle:        ____________________
  RPO REAL:                     ____________________   (objetivo: <= 24 h)

MATERIAL DE CUSTODIA
  ¿Se consultó la máquina origen en algún momento?   sí / no
  (un "sí" invalida el simulacro; anota qué se consultó y por qué)
  Elementos que FALTABAN en la custodia:  ____________________

RECONCILIACIÓN (workers.restore_reconcile)
  Código de salida:             ____________________
  Divergencias BD <-> MinIO:    ____________________
  Divergencias BD <-> Vault:    ____________________
  Divergencias BD <-> git:      ____________________

CHECKLIST
  [ ] El bundle se verificó antes de restaurar
  [ ] scripts/restore.sh completó sin editar listas de servicios a mano
  [ ] app_user puede leer/escribir con RLS activo
  [ ] alembic upgrade head no falla
  [ ] Los repos traen las ramas plan/* de los planes activos
  [ ] Login de un usuario de tenant
  [ ] Ejecución de un plan de punta a punta

INCIDENCIAS Y ACCIONES
  ____________________________________________________________
  ____________________________________________________________
```

## Cuándo repetirlo

- Tras el primer despliegue en producción (antes de aceptar datos reales).
- Tras cada rotación de `WORKERS_BACKUP_ENCRYPTION_KEY` (prod-05): hay que
  comprobar que los bundles antiguos siguen abriéndose con el anillo de claves.
- Al menos una vez al año, y siempre que cambie quién custodia el material.

## Enlaces

- Punto de entrada de DR: [04-disaster-recovery.md](./04-disaster-recovery.md).
- Detalle del restore completo: [dr-full-restore.md](./dr-full-restore.md).
- Producir y verificar un bundle: [dr-manual-backup.md](./dr-manual-backup.md).
- Restaurar un solo tenant: [dr-tenant-restore.md](./dr-tenant-restore.md).
- Desellar y rotar claves de Vault: [05-key-rotation.md](./05-key-rotation.md).
- Salud del stack: [health-check.md](./health-check.md).
