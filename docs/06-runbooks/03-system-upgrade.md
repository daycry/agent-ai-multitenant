---
title: Upgrade del sistema
docs_language: es
audience: operador, system admin
updated: 2026-05-31
---

# Runbook — Upgrade del sistema

Actualizar una instalación **ya en marcha** a una versión nueva de la
plataforma (Docker Compose en una sola máquina): nuevas imágenes de los
servicios + migraciones de esquema, conservando todos los datos.

> Alcance: **Docker Compose en una sola máquina** (CLAUDE.md). No
> Kubernetes, no rolling-update multi-instancia. El upgrade es una
> ventana de mantenimiento corta con parada controlada del stack.

La regla de oro de este runbook: **siempre un backup verificado primero**
y **nunca** una migración Alembic sin `downgrade` probado. Si algo va mal,
el camino de vuelta es restaurar el backup pre-upgrade, no editar el
esquema a mano en producción.

## Cuándo

- Subir el stack a una release nueva (imágenes + esquema).
- Aplicar un parche de seguridad que requiere imágenes nuevas.

Para una **reinstalación** sobre los mismos datos (regenerar
config/compose sin cambiar de versión) usa el camino de preservación de
Fase B ([Reinstalación con preservación](#alternativa--reinstalación-con-preservación-de-datos)).
Para recuperar tras una pérdida total, [04-disaster-recovery.md](./04-disaster-recovery.md).

## Comprobación previa

1. **Lee las notas de la versión destino** (changelog del plan en
   [`docs/07-changelog/`](../07-changelog/)): qué imágenes cambian, qué
   migraciones nuevas hay, si alguna es **no reversible** (eso cambia el
   plan de rollback — ver [Rollback](#rollback)).
2. **Stack sano antes de empezar** — no actualices sobre un stack ya roto.
   Ejecuta [health-check.md](./health-check.md): `docker compose ps` todos
   `Up (healthy)`, `GET /healthz` → 200, `GET /admin/system-health` →
   `status: ok`.
3. **Versión actual anotada** — apunta el tag/commit desplegado ahora
   (`git -C <repo> rev-parse HEAD`, tags de las imágenes en uso). Es tu
   punto de retorno si decides revertir el código.
4. **Ventana de mantenimiento** — avisa a los tenants; el stack se reinicia
   y queda no disponible unos minutos.

## Pasos

### 1. Backup pre-upgrade (obligatorio)

> Decisión clave del Plan 12: **antes de cualquier upgrade** se dispara un
> backup completo y **verificado**. El motor de backup ya lo contempla
> como caso de uso explícito (backup manual fuera del cron diario). No
> sigas si el backup no verifica.

Lanza el backup manual y confirma la verificación de integridad siguiendo
[dr-manual-backup.md](./dr-manual-backup.md):

```bash
./scripts/backup.sh
```

Anota el `backup_id` que imprime: es el bundle al que volverás si el
upgrade falla. Confirma que verifica (paso 2 de
[dr-manual-backup.md](./dr-manual-backup.md)); un bundle que no verifica
**no** sirve de red de seguridad — repítelo antes de continuar.

### 2. Trae el código y las imágenes nuevas

Actualiza el repositorio a la versión destino y descarga las imágenes
nuevas **sin** recrear todavía los contenedores (el `pull` no reinicia
nada por sí mismo):

```bash
git -C <repo> fetch --tags
git -C <repo> checkout <tag-destino>     # p. ej. v1.1.0

docker compose -f docker/docker-compose.yml pull
```

Si tu despliegue usa overlays (monitoring, GPU), inclúyelos en el `pull`
con sus `-f` correspondientes para no dejar una imagen atrás.

### 3. Para el stack (ventana de mantenimiento)

Detén los servicios **sin** borrar volúmenes. Nunca `down -v`, que
**elimina los datos**:

```bash
docker compose -f docker/docker-compose.yml down       # SIN -v
```

(`down` sin `-v` conserva los volúmenes; ver
[restart-services.md](./restart-services.md).)

### 4. Migraciones de esquema (Alembic, reversibles)

> **Ojo: el servicio `migrations` NO existe en el stack de dev** (corregido el
> 2026-07-28, tras perder una vuelta con esto). Lo **genera el instalador**
> (`installer_backend.compose_generator._migrations_service`) en el compose de
> una instalación de producción; los `docker/docker-compose*.yml` escritos a
> mano —el stack de dev/manuals— no lo declaran. Comprueba cuál tienes antes de
> copiar el comando:
>
> ```bash
> docker compose <tus -f> config --services | grep -x migrations || echo "SIN servicio migrations: usa la variante de dev"
> ```
>
> Y en ninguno de los dos casos migra `up -d` por sí solo en dev: el
> `depends_on: service_completed_successfully` que dispara el one-shot vive
> únicamente en el compose generado.

**Producción (compose del instalador).** El esquema lo aplica el **servicio
one-shot `migrations`** del propio stack — no un Python local desde un
checkout. La imagen `api-server` que acabas de traer en el paso 2 ya trae
Alembic y los modelos, así que el host de producción **no** necesita Python ni
el repo instalado. El servicio corre `alembic upgrade head` con el rol
`migrations_user` (no el rol de aplicación), toma su DSN
(`ADMIN_DATABASE_URL`) del `.env` generado y termina (`restart: no`).
Aplícalo **antes** de levantar la aplicación:

```bash
docker compose -f docker/docker-compose.yml run --rm migrations
```

**Dev / manuals (sin servicio `migrations`).** Dos vías equivalentes; las dos
usan el rol `migrations_user` y `env.py` toma el mismo advisory lock:

```bash
# (a) desde la IMAGEN NUEVA — la única válida si el host no tiene Python.
#     El contenedor de api-server que está corriendo NO sirve: lleva la imagen
#     ANTERIOR, sin las revisiones nuevas. Y `env.py` exige `DATABASE_URL`
#     pelado, no el `API_SERVER_*` que el contenedor trae.
docker compose <tus -f> run --rm --no-deps --entrypoint sh api-server \
  -c 'export DATABASE_URL="$API_SERVER_ADMIN_DATABASE_URL"; alembic upgrade head'

# (b) desde el venv del repo, como hace scripts/dev/up.ps1 (postgres expuesto
#     en 15432). Es la vía corta cuando ya tienes el .venv montado.
cd apps/api-server
DATABASE_URL="postgresql+asyncpg://migrations_user:<pwd-dev>@localhost:15432/agentic_platform" \
  ../../.venv/Scripts/python.exe -m alembic upgrade head
```

La variante (a) resuelve la credencial **dentro** del contenedor, así que no
aparece en el historial del shell ni en los logs.

`run --rm` arranca PostgreSQL como dependencia, ejecuta el one-shot y
**propaga el exit code** de Alembic: `0` = esquema al día. El servicio toma
además un **advisory lock** (`pg_advisory_xact_lock`), de modo que dos
`upgrade head` concurrentes (réplicas, un run manual a la vez que el del
arranque) se serializan en vez de colisionar.

> En un `up -d` normal este servicio corre **solo**: los servicios de
> aplicación dependen de él con `service_completed_successfully`, así que
> el paso 5 lo dispararía igualmente. Lo lanzamos aquí aparte para **ver el
> resultado de la migración antes** de arrancar la aplicación: si falla, el
> stack no llega a subir a medias.

**Reversibilidad — invariante del proyecto.** CLAUDE.md prohíbe promover
una migración sin `downgrade` probado. Ese round-trip se valida en
**dev/CI** (donde sí hay Python + repo) antes de cortar la release, no en
el host de producción; el guard de CI lo ejecuta sobre el mismo one-shot:

```bash
docker compose -f docker/docker-compose.yml run --rm migrations alembic upgrade head    # aplica
docker compose -f docker/docker-compose.yml run --rm migrations alembic downgrade -1     # revierte la última
docker compose -f docker/docker-compose.yml run --rm migrations alembic upgrade head    # round-trip OK
```

Si una migración de la versión destino **no es reversible** (lo dice el
changelog), su `downgrade` no te sacará del apuro: el rollback de esa
release pasa **obligatoriamente** por restaurar el backup pre-upgrade del
paso 1. Tenlo decidido **antes** de aplicarla.

> Si `upgrade head` falla (esquema a medias), trátalo como en
> [02-troubleshooting.md](./02-troubleshooting.md#migraciones-de-base-de-datos):
> normalmente es una trampa conocida de asyncpg
> ([asyncpg-no-multistatement](../03-guides/gotchas/asyncpg-no-multistatement.md),
> [asyncpg-set-local-no-bind-params](../03-guides/gotchas/asyncpg-set-local-no-bind-params.md)).
> Si el esquema quedó inconsistente y no hay `downgrade` limpio, **no**
> lo edites a mano: restaura el backup pre-upgrade.

### 5. Levanta el stack con las imágenes nuevas

> **Antes del `up -d`, cuenta las reclamaciones huérfanas.** Una tarea
> `in_progress` **sin fila viva en `executions`** es invisible al chequeo
> instintivo de «¿queda algo corriendo?», y el reconciler la devuelve a `ready`
> a los 90 s de arrancar el beat: el despliegue **lanza runs que nadie pidió**.
> Pasó el 2026-07-28 (2 tareas de hacía 10 días, ~165 k tokens).
>
> ```sql
> SELECT count(*) FROM tasks t
>  WHERE t.status = 'in_progress'
>    AND NOT EXISTS (SELECT 1 FROM executions e
>                     WHERE e.task_id = t.id
>                       AND e.status IN ('running','pending','queued'));
> ```
>
> Si es `> 0`, levanta con `--scale orchestrator=0` y arranca el orchestrator
> aparte cuando hayas decidido qué hacer con esas tareas. Detalle en
> [gotchas/deploy-relaunches-frozen-tasks.md](../03-guides/gotchas/deploy-relaunches-frozen-tasks.md).

```bash
docker compose -f docker/docker-compose.yml up -d
```

Vault arranca **sellado** tras cada parada: deséllalo con las unseal keys
guardadas (ver [04-disaster-recovery.md](./04-disaster-recovery.md),
sección de desellado, y [05-key-rotation.md](./05-key-rotation.md)). Hasta
que Vault esté desellado, la API/worker no leen secretos y fallan.

## Alternativa — reinstalación con preservación de datos

Cuando lo que cambia es la **configuración/compose** (no la versión de las
imágenes), el camino limpio es re-ejecutar el instalador en modo
**preservación** (Fase B, `task_15_13`), que regenera config + compose y
**reutiliza los secretos y las unseal keys existentes** sin tocar los
datos:

```bash
./scripts/reinstall.sh            # PRESERVE es el modo por defecto, no borra nada
```

La reutilización de los secretos existentes es **obligatoria** en modo
preservación: los datos de PostgreSQL/MinIO conservados y el árbol de
secretos cifrado por Vault están ligados a ellos; regenerar secretos
**huérfanaría** los datos cifrados. No uses `--fresh` para un upgrade
(`--fresh` borra el árbol de datos tras doble confirmación — eso es
desinstalar y reinstalar, no actualizar). Detalle del flujo y los códigos
de salida en [01-installation-from-scratch.md](./01-installation-from-scratch.md)
(`scripts/reinstall.sh` delega en
`installer_backend.cli reinstall`).

## Verificación post-upgrade

1. **Salud del stack** — [health-check.md](./health-check.md):
   `docker compose ps` todos `Up (healthy)`, `GET /healthz` → 200,
   `GET /admin/system-health` → `status: ok` sin ningún servicio en `down`.
2. **Smoke tests post-deploy** — ejecuta la batería de smoke tests
   (`task_15_26`), que ejercita los caminos críticos de extremo a extremo
   sobre el stack recién actualizado:

   ```bash
   pytest tests/smoke/ -v
   ```

   Los smoke tests se **autoexcluyen** (skip-guard) cuando no detectan un
   stack vivo, así que en CI quedan en verde; contra un stack real
   recién actualizado **sí** corren y validan el upgrade. Si fallan,
   revisa el servicio implicado en [02-troubleshooting.md](./02-troubleshooting.md).

3. **Esquema al día** — confirma que la BD quedó en la última revisión:
   `docker compose run --rm migrations alembic current` debe imprimir la
   misma revisión que `alembic heads` (sufijo `(head)`), sin revisiones
   pendientes.
4. **Vault desellado** — `docker compose exec vault vault status` →
   `Sealed: false`.
5. **Login y datos intactos** — entra al panel admin y confirma que un
   tenant existente puede hacer login con sus credenciales previas y que
   sus proyectos/planes siguen ahí (los datos no se tocan en un upgrade).

## Rollback

El plan de rollback depende de **qué** falló y de si las migraciones de la
versión destino eran reversibles:

| Situación                                                              | Cómo revertir                                                                                                                                              |
| ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Falló el `pull` o un servicio no arranca, **sin** haber migrado aún    | `git checkout` del tag anterior + `docker compose up -d` con las imágenes previas. No hubo cambio de esquema: nada que deshacer en la BD.                  |
| Migraste, la release **es reversible** y quieres volver a la previa    | `docker compose run --rm migrations alembic downgrade <rev-previa>` a la revisión de la versión anterior, luego `git checkout` del tag anterior + `up -d`. |
| Migraste, la release **NO es reversible**, o el esquema quedó a medias | Restaura el **backup pre-upgrade** del paso 1 con [04-disaster-recovery.md](./04-disaster-recovery.md): vuelve al punto exacto antes del upgrade.          |
| Dudas sobre el estado del esquema                                      | No improvises: restaura el backup pre-upgrade. Es el único camino con garantía de consistencia.                                                            |

> Por eso el paso 1 (backup verificado) es **obligatorio**: es la única
> red de seguridad que cubre todos los casos, incluido el de una migración
> no reversible. Sin él, un upgrade fallido puede ser irrecuperable.

Tras cualquier rollback, vuelve a [health-check.md](./health-check.md) y a
los smoke tests para confirmar que el sistema quedó consistente.

## A quién avisar

- **DevOps / operador**: ejecuta el upgrade y vigila la ventana de
  mantenimiento.
- **System Admin**: aprueba la ventana, custodia las unseal keys
  necesarias para desellar Vault tras el reinicio, y decide el rollback si
  hay que restaurar el backup.

## Enlaces

- Backup pre-upgrade: [dr-manual-backup.md](./dr-manual-backup.md).
- Parar/reiniciar sin perder datos: [restart-services.md](./restart-services.md).
- Salud del stack: [health-check.md](./health-check.md).
- Fallos de migración / arranque: [02-troubleshooting.md](./02-troubleshooting.md).
- Recuperación total: [04-disaster-recovery.md](./04-disaster-recovery.md).
- Vault y rotación de claves: [05-key-rotation.md](./05-key-rotation.md).
- Reinstalación (Fase B): [01-installation-from-scratch.md](./01-installation-from-scratch.md).
