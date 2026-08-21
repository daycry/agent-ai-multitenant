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

## Garantías declaradas: RPO y RTO

Un plan de recuperación sin cifras no es un plan, es una intención. Estas son
las garantías que la plataforma declara hoy (prod-04 task_prod_04_12). **Están
pendientes de validación por dirección** y el RTO se confirma midiéndolo en el
simulacro ([dr-drill.md](./dr-drill.md)), no estimándolo.

| Métrica                                | Objetivo declarado | De dónde sale                                                                                 |
| -------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------- |
| **RPO** (datos que se pueden perder)   | **≤ 24 h**         | Cadencia diaria a las 03:00 (`WORKERS_BACKUP_CRON`) + subida al destino remoto tras verificar |
| **RTO** (tiempo hasta volver a operar) | **≤ 4 h**          | Objetivo; el valor REAL se mide en cada simulacro y se anota en el acta                       |
| Retención local                        | 7 días             | `WORKERS_BACKUP_RETENTION_DAYS`                                                               |

Qué significa el RPO en la práctica: si el host muere a las 02:55, se pierde
**casi un día entero** de trabajo — planes, ejecuciones, documentos subidos y
commits de los agentes desde las 03:00 del día anterior. No hay PITR: no se
archivan WAL. Si dirección necesita un RPO menor, hace falta WAL archiving
(`archive_mode` + wal-g/pgbackrest hacia el destino remoto), que es un **ADR
propuesto**, no algo que exista hoy.

Qué NO cubre el RPO: lo que no entra en el bundle. Hoy entran el dump lógico de
PostgreSQL, el estado de MinIO y de Vault, el data dir de **Redis**
(`redis_tar`), los **bare repos de los proyectos** (`projects_tar`) y los bind
paths declarados. Los worktrees por tarea y la cache de dependencias quedan fuera
**a propósito**: son regenerables desde el bare repo. `clamav` (firmas de virus)
y los modelos de `ollama` también: se re-descargan, y meter decenas de GB en cada
bundle nocturno haría inviable la retención.

Señal de que el RPO se está incumpliendo: la alerta `BackupTooOld` (último
backup correcto > 24 h) y `BackupLastRunFailed`. Su enrutado a humanos es
prod-08; aquí solo se garantiza que la señal se emite.

## Skew residual del bundle: qué NO es coherente, y hasta dónde

Esto es lo que hay que saber ANTES de un DR, porque cambia lo que se considera un
restore correcto. El bundle se ensambla con el stack **vivo**, así que cada
artefacto retrata un instante distinto (prod-04 task_prod_04_06, hallazgo gap3-3).

> **Decidido el 2026-08-01** en el
> [ADR 0149](../05-architecture-decisions/0149-consistencia-del-bundle-de-backup.md):
> **opción A, quiesce corto de escritores, con un plazo que degrada**. El backup
> para `api-server`, `orchestrator`, `workers`, `cortex-beat`,
> `notification-dispatcher` y `admin-panel` mientras dura la captura, espera un
> máximo de `WORKERS_BACKUP_QUIESCE_TIMEOUT_SECONDS` (180 s) y **sigue adelante
> pase lo que pase**. Los detalles operativos están en §«El quiesce» más abajo.

Con el quiesce en `full` la tabla siguiente ya no aplica: todos los artefactos
retratan un stack sin escritores. **Aplica tal cual cuando el quiesce degrada a
`partial`** (y cuando el operador lo desactiva vaciando
`WORKERS_BACKUP_QUIESCE_SERVICES`), que es el escenario para el que hay que
saberla:

| Artefacto           | Instante | Coherencia interna                                      |
| ------------------- | -------- | ------------------------------------------------------- |
| `pg_dump` (lógico)  | t₀       | **Total** — snapshot MVCC                               |
| `redis_tar`         | t₁       | **Sí** — `BGREWRITEAOF` completado y comprobado antes   |
| `bind_tar` de Vault | t₂       | **Verificada** — huella del árbol antes/después del tar |
| `bind_tar` de MinIO | t₃       | **NO** — se escribe durante la captura                  |
| `projects_tar`      | t₄       | **NO** — un agente puede comitear durante la captura    |

Entre t₀ y t₄ puede haber minutos. Consecuencias que hay que **esperar**, no
tratar como incidencia:

1. **Documentos sin binario (o binarios sin fila).** Un documento subido entre t₀
   y t₃ tiene fila en la BD y su blob no viajó. `restore_reconcile` lo enumera; la
   acción correcta es marcarlo para re-subida, no dudar del restore.
2. **Un commit de agente que no está en el bare repo restaurado**, si cayó después
   de t₄. La tarea se re-ejecuta.
3. **Redis**: se pierde, como mucho, la cola del incr del AOF que se escribiese
   durante el tar. Redis la tolera por diseño (`aof-load-truncated yes` descarta
   el último comando incompleto y arranca). Traducido: algunas sesiones caídas y
   algún mensaje de cola re-encolado desde la BD.

Lo que **NO** es skew aceptable y por tanto falla el backup en vez de producirse:
un árbol de Vault que cambia durante su captura (el motor reintenta y, si no
converge, aborta el run), y un `BGREWRITEAOF` que termina con estado ≠ `ok`.

> **Trampa de Redis, medida.** Capturar solo el `dump.rdb` —lo que parece la vía
> obvia— restaura una base **vacía**: con `--appendonly yes` (como lo arranca el
> compose), un Redis que encuentra un `dump.rdb` y ningún `appendonlydir` no lee
> el RDB, crea un AOF nuevo vacío y sirve `DBSIZE 0`, sin un solo error. Por eso el
> artefacto es el **directorio** (AOF + RDB) y el restore lo extrae **vaciando** el
> destino: un `appendonlydir` residual con una secuencia más alta que la capturada
> le ganaría al restaurado, porque Redis lee el manifest que encuentra.
> Verificación post-restore obligatoria: `redis-cli DBSIZE` > 0.

## El quiesce: qué pasa a las 03:00, y qué pasa cuando no pasa

El backup **para los escritores** durante la captura (ADR 0149, opción A). Lo que
un operador necesita saber:

| Qué                             | Valor                                                                                                                                         |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Servicios que se paran          | `WORKERS_BACKUP_QUIESCE_SERVICES` — api-server, orchestrator, workers, workers-marketplace, cortex-beat, notification-dispatcher, admin-panel |
| Servicios que NUNCA se paran    | `WORKERS_BACKUP_QUIESCE_NEVER_STOP` — `workers-privileged`, **la lane que ejecuta el propio backup**                                          |
| Lo que sigue en pie a propósito | PostgreSQL, MinIO, Redis y Vault: son los que se leen                                                                                         |
| Plazo de espera                 | `WORKERS_BACKUP_QUIESCE_TIMEOUT_SECONDS`, 180 s                                                                                               |
| Corte de servicio esperado      | 1-3 min diarios en la ventana del backup                                                                                                      |
| Desactivarlo                    | `WORKERS_BACKUP_QUIESCE_SERVICES=[]` — vuelve el skew de la tabla de arriba                                                                   |
| A qué stack le habla            | `WORKERS_RESTORE_COMPOSE_PROJECT` + `WORKERS_RESTORE_COMPOSE_FILE` (las mismas del restore: es el mismo stack)                                |

> **Comprueba esto al desplegar.** El instalador **no emite**
> `WORKERS_RESTORE_COMPOSE_FILE`, y su default (`/data/agent-platform/docker-compose.yml`)
> sólo acierta si instalaste con el `data_root` por defecto. Con otro data root el
> quiesce no encuentra el compose y degrada a `partial` **todas las noches**; el
> log lo dice con `backup.quiesce.no_compose_file` y nombra la variable. Nótese
> que ese mismo puntero es el que usa el restore completo, así que arreglarlo
> tampoco es opcional para el DR.

**Lo que hace cuando algo no para.** Un `docker compose stop` que se cuelga —un
run largo, un contenedor que no atiende SIGTERM— NO tumba el backup: vencido el
plazo, el motor **sigue capturando** con lo que quede en pie y lo registra. Un
backup con skew registrado es mejor que un backup que no existe, y muchísimo
mejor que un stack parado a las 03:00 esperando a un worker que no va a responder.

Los servicios **rearrancan siempre**, aunque la captura falle (`start`, y si el
contenedor ya no existe, `up --detach`). Si ni eso funciona, el log lo dice con
`backup.quiesce.resume_failed` y la acción es `docker compose up -d` a mano.

**Dónde se ve.** En el `manifest.json` del bundle:

```json
"quiesce": { "mode": "partial", "requested": ["api-server", "workers"],
             "still_running": ["workers"], "duration_s": 181.4, "resumed": true }
```

Y en Prometheus: `agentic_backup_quiesce_seconds` (el corte real, para saber si
los 1-3 min estimados son ciertos) y `agentic_backup_quiesce_degraded` (1 esa
noche). Un `degraded` suelto no es una incidencia — el ADR lo prevé; varias
noches seguidas dicen que algo dejó de atender la señal de parada.

**Y esto es lo que cambia en un DR:** si el bundle que vas a restaurar tiene
`"mode": "partial"`, las divergencias que reporte `restore_reconcile` sobre los
almacenes de filesystem son el **comportamiento acordado**, no un fallo del
restore. Con `"mode": "full"` no deberían aparecer, y si aparecen, investígalas.

## Lo que NO viaja en el bundle a propósito: los secretos de integración

**Antes de dar un DR por terminado, lee esto.** Por el
[ADR 0146](../05-architecture-decisions/0146-fernet-en-db-vs-vault.md) los
secretos que un tenant configura para integrarse con terceros viven cifrados en
columnas de Postgres, y **sus filas se excluyen del `pg_dump`**: con el
ciphertext dentro, quien robase el bundle y conociera
`API_SERVER_*_ENCRYPTION_KEY` tendría los secretos — y el bundle viaja a MinIO y
a destinos externos.

Tras un restore completo vuelven **vacías** estas tres tablas (la definición sí
viaja: la aplicación arranca con normalidad):

| Tabla                      | Qué hay que rehacer tras el DR                                                                                                                             |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sso_configurations`       | Volver a dar de alta cada IdP (OIDC/SAML) con su client secret. **Entra con la cuenta local de admin: el botón de SSO no estará en la pantalla de login.** |
| `notification_channels`    | Volver a crear los canales (Slack/Teams/email/webhook) con sus credenciales                                                                                |
| `incoming_webhook_configs` | Volver a crear cada webhook entrante y **re-entregar su signing secret al emisor**                                                                         |

El bundle lo declara, así que no hay que fiarse de la memoria:

```bash
python -c "import json;print(json.load(open('<bundle>/manifest.json'))['column_secrets'])"
# {'excluded_tables': ['sso_configurations', 'notification_channels',
#  'incoming_webhook_configs'], 'adr': '0146', 'runbook': '06-runbooks/04-disaster-recovery.md'}
```

Si `excluded_tables` sale **vacía**, ese bundle SÍ lleva el ciphertext (alguien
vació `WORKERS_BACKUP_COLUMN_SECRET_TABLES`): trátalo como material sensible y
no lo dejes en un destino remoto sin cifrar.

> **Por qué no se hizo al revés** (cifrar esas columnas con una segunda clave en
> vez de excluirlas): el instalador emite `WORKERS_BACKUP_ENCRYPTION_ENABLED=false`,
> así que en un stack recién instalado el segundo sobre no existiría y el
> ciphertext viajaría igual. Y hacer que el backup FALLE sin cifrado convertiría la
> ventana nocturna en una caída — justo lo que el ADR 0149 acaba de descartar.

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
# Desde el HOST (el cliente de PostgreSQL tiene que estar instalado ahí).
python -c '
from workers.backup_verification import verify_bundle
print("verify:", verify_bundle("<backup_id>"))
'
```

Nunca restaures desde un bundle que no verifica: usa uno anterior o recupera
una copia íntegra del destino remoto.

### 2. Descifrado: la clave NO está en Vault (corregido en prod-04)

Si el bundle está **cifrado** (sufijo `.enc`), hace falta el **valor** de
`WORKERS_BACKUP_ENCRYPTION_KEY`. Y hay que decirlo sin rodeos porque este
runbook afirmó lo contrario durante meses:

> **Vault NO resuelve la clave del backup, y las unseal keys NO descifran el
> bundle.** El proveedor de secretos del backup (`EnvSecretsProvider`) lee la
> clave de `os.environ`, no de Vault. Y el backend de Vault viaja **dentro** del
> blob cifrado: aunque tuvieras las unseal keys, primero habría que descifrar el
> bundle para llegar a Vault. Creer lo contrario convierte el primer DR real en
> una pérdida total.

Por eso el valor de la clave se **custodia offsite** (gestor corporativo o sobre
sellado), junto a las unseal keys pero como un elemento **distinto y etiquetado
como tal**. El backup registra la huella SHA-256 de la clave activa en el
`manifest.json` (`key_fingerprint`) y **falla** si no coincide con
`WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT`: así una rotación de clave sin
actualizar la custodia se detecta esa misma noche, y no meses después con un
bundle que nadie puede abrir.

Las unseal keys siguen siendo imprescindibles para **desellar Vault** tras el
restore (ver §«Desellar Vault tras un restore»). Son dos cosas distintas, en el
mismo sobre, con etiquetas distintas.

### 3. Disponibilidad física del bundle

Tras cada backup **verificado**, el bundle se sube automáticamente a los
destinos remotos configurados (S3/B2/SFTP/rclone) y el resultado aparece en el
resumen de la tarea (`uploaded` / `upload_failures`) y en la métrica
`agentic_backup_offsite_uploaded`. La subida es best-effort **por diseño**: un
destino caído no invalida el backup local, pero deja de haber copia fuera de la
máquina — por eso existe la alerta de «offsite obsoleto».

Un bundle que NO verifica no se sube nunca: una copia remota corrupta es peor
que no tener copia, porque da confianza.

Para un DR sobre máquina limpia, descarga el bundle del remoto a
`WORKERS_BACKUP_ROOT` con la herramienta nativa del destino
(`aws s3 cp --recursive`, `b2`, `sftp`, `rclone copy`).

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
3. Lanza `./scripts/restore.sh <backup_id>` **desde el host** con la **doble
   confirmación**: localiza, descifra, **verifica** (fail-closed), comprueba en
   preflight que todos los servicios a parar existen en el compose, para la
   aplicación, `pg_restore --clean --exit-on-error`, re-concede los GRANTs de
   `app_user`, y re-extrae volúmenes, repos de proyectos y binds.
4. **Desella Vault** y reconcilia (`python -m workers.restore_reconcile`).

> El restore se lanza **desde el host, nunca con `docker compose exec`**: para
> el stack, y `workers` está entre los servicios que para. Un restore que corre
> dentro de un contenedor se mata a sí mismo a mitad de una operación
> destructiva.

> **Si el compose desplegado es anterior a prod-13, el preflight aborta nombrando
> `workers-marketplace`.** Esa lane se sumó a los servicios que el restore para
> (escribe en PostgreSQL: estado de instalación, auditoría y materialización), y
> el preflight exige que todo lo que va a parar esté declarado en
> `WORKERS_RESTORE_COMPOSE_FILE`. Aborta **antes** de tocar nada y dice qué
> falta, así que no hay daño: regenera el compose con el instalador (que además
> es lo que hace falta para que las puertas del marketplace se drenen: sin esa
> lane, una instalación se queda en `analyzing` para siempre) y repite. No la
> quites de la lista para salir del paso.

> **Fail-stopped**: si un paso de la fase destructiva falla, el stack queda
> **PARADO** (solo PostgreSQL sigue alcanzable, porque nunca se para) y el error
> dice hasta dónde se llegó. No lo arranques: corrige y re-ejecuta el restore
> completo, que es idempotente.

Este es el test humano `human_12_02` del Plan 12 («Restore completo en máquina
virgen»), y el simulacro completo con máquina limpia y custodia offsite está en
**[dr-drill.md](./dr-drill.md)** (`human_prod_04_01`).

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

2. **Permisos y RLS** (solo restore completo) — el dump se hace con
   `--no-owner --no-privileges`, así que el restore recrea los objetos sin ACLs
   y `app_user` (el rol NOBYPASSRLS del que depende TODO el stack con FORCE RLS)
   se queda sin GRANTs. El motor los re-concede al terminar el `pg_restore`;
   compruébalo con los ojos, porque el síntoma de que falló es «la aplicación
   arranca y falla en la primera consulta»:

   ```bash
   psql "$APP_DATABASE_URL" -c "SET app.tenant_id = '<tenant>';" \
                            -c "SELECT count(*) FROM projects;"
   alembic upgrade head    # como migrations_user
   ```

3. **Redis no volvió vacío** (solo restore completo) — la comprobación de una
   línea que separa «Redis restaurado» de «Redis arrancó de cero y nadie lo
   notó». Es el síntoma exacto de la trampa del `dump.rdb` descrita arriba:

   ```bash
   docker compose -f docker/docker-compose.yml exec redis \
     redis-cli -a "$REDIS_PASSWORD" DBSIZE     # tiene que ser > 0
   ```

   Y en los logs del contenedor tiene que aparecer `DB loaded from base file
appendonly.aof.<n>.base.rdb`. Si en su lugar dice `Creating AOF base file … on
server start`, Redis **no** cargó el backup: el `appendonlydir` no llegó al
   data dir. Revisa que el artefacto `redis_tar` esté en el manifest y que
   `WORKERS_BACKUP_REDIS_DIR` apunte al bind correcto.

4. **Reconciliación de los cuatro almacenes** (solo restore completo) — un
   bundle son fotos tomadas en instantes ligeramente distintos (ver
   [«Skew residual del bundle»](#skew-residual-del-bundle-qué-no-es-coherente-y-hasta-dónde));
   el restore no se da por bueno hasta que la base de datos, MinIO, Vault y los
   repos git cuentan la misma historia:

   ```bash
   python -m workers.restore_reconcile
   ```

   Sale con código ≠ 0 si hay divergencias críticas. **Las divergencias que el
   skew explica (un documento sin binario, un commit posterior a la captura de su
   repo) son esperadas**: se anotan en el acta del drill y se resuelven
   re-subiendo o re-ejecutando, no cuestionando el restore.

5. **Login y datos** — un usuario del tenant restaurado hace login con sus
   credenciales previas y ve sus proyectos / planes / conversaciones intactos
   al punto del backup elegido.
6. **Smoke tests post-deploy** (`task_15_26`) — ejercitan los caminos
   críticos de extremo a extremo sobre el stack recuperado:

   ```bash
   pytest tests/smoke/ -v
   ```

   Se **autoexcluyen** (skip-guard) cuando no hay stack vivo, así que en CI
   quedan en verde; contra el stack recuperado **sí** corren y validan la
   recuperación.

7. **Aislamiento (solo restore selectivo)** — confirma que un usuario de
   **otro** tenant sigue viendo sus datos actuales **sin cambios**, y que el
   audit log refleja quién hizo el restore y sobre qué tenant.

8. **Ejecutar un plan de punta a punta** — el paso que descubre lo que ningún
   otro descubre (credenciales de proveedor que no sobrevivieron, imágenes de
   runtime ausentes, un worktree que no se puede crear sobre el bare restaurado).
   Obligatorio en el simulacro; muy recomendable tras un DR real.

## Rollback / aborto

- **Antes de lanzar el restore**: nada escrito; basta con no ejecutarlo.
- **Token de confirmación incorrecto**: el motor rechaza la operación sin
  tocar la base ni los volúmenes. Reintenta con el `backup_id` (y `tenant_id`)
  correcto.
- **Bundle que no verifica**: el restore aborta antes de cualquier escritura;
  usa un bundle anterior o recupera una copia íntegra del remoto.
- **Fallo a mitad — restore completo**: el motor deja el stack **PARADO** por sí
  solo (`RestorePartialError`, fail-stopped desde prod-04; antes lo arrancaba
  incondicionalmente, contradiciendo a este mismo runbook). No lo arranques: el
  error indica el `stage` alcanzado. Corrige la causa y repite el restore
  completo, que es idempotente. Detalle en
  [dr-full-restore.md](./dr-full-restore.md).
- **Fallo a mitad — restore selectivo**: la transacción única hace **ROLLBACK**
  y la base viva queda como estaba; corrige la causa (típicamente `dblink` no
  habilitado) y reintenta. Detalle en [dr-tenant-restore.md](./dr-tenant-restore.md).

## A quién avisar

- **System Admin**: aprobador de cualquier restore (acción sensible y
  destructiva) y quien programa la ventana.
- **Responsable de seguridad**: custodia DOS elementos distintos —el **valor de
  la clave de cifrado del backup** (la que descifra el bundle) y las **unseal
  keys de Vault** (las que desellan Vault tras restaurarlo)—. No son
  intercambiables: sin la primera no hay bundle que abrir.
- **Responsable del tenant** afectado (restore selectivo): confirma el punto
  de restauración deseado y valida los datos recuperados.
- **Responsable de seguridad**: si el bundle está cifrado y la clave de Vault
  no está disponible, o si hay que coordinar las unseal keys
  ([05-key-rotation.md](./05-key-rotation.md)).
- **DBA / DevOps** (restore selectivo): si `dblink` no está habilitado o la
  transacción de copiado falla por integridad referencial.

## Enlaces

- Simulacro completo (máquina limpia + custodia offsite): [dr-drill.md](./dr-drill.md).
- Detalle del restore completo: [dr-full-restore.md](./dr-full-restore.md).
- Detalle del restore selectivo por tenant: [dr-tenant-restore.md](./dr-tenant-restore.md).
- Producir / verificar / subir un bundle: [dr-manual-backup.md](./dr-manual-backup.md).
- Backup manual a nivel de volumen (procedimiento básico): [backups.md](./backups.md).
- Desellar y rotar claves de Vault: [05-key-rotation.md](./05-key-rotation.md).
- Rollback de un upgrade fallido: [03-system-upgrade.md](./03-system-upgrade.md#rollback).
- Salud del stack: [health-check.md](./health-check.md).
