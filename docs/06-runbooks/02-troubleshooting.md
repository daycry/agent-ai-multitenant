---
title: Troubleshooting común
docs_language: es
audience: operador, system admin
updated: 2026-05-31
---

# Runbook — Troubleshooting común

Modos de fallo frecuentes tras instalar o durante la operación normal del
stack (Docker Compose en una sola máquina), con **diagnóstico** y **fix**.
Está pensado para usarse de forma reactiva: localizas el síntoma en el
índice, sigues su sección.

> Antes de inventar un fix para un error del **toolchain** (Docker,
> asyncpg, mypy, pre-commit, OTEL, Windows…), busca primero en
> [`docs/03-guides/gotchas/`](../03-guides/gotchas/) — las trampas
> conocidas están ahí con síntoma + causa raíz + fix. Si resuelves una que
> no estaba documentada, **añádela** (CLAUDE.md).

## Antes de tocar nada: localiza el servicio caído

El primer paso de **cualquier** incidencia es saber **qué** está mal, no
adivinar. No reinicies a ciegas:

1. Ejecuta [health-check.md](./health-check.md): `docker compose ps`,
   `GET /healthz` y `GET /admin/system-health`.
2. Identifica el/los servicio(s) en `Restarting` / `down` / `unhealthy`.
3. Mira sus logs antes de actuar:

   ```bash
   docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
     logs <servicio> --tail 100
   ```

4. Solo entonces aplica el fix de la sección correspondiente. Si el fix es
   un reinicio controlado, hazlo con [restart-services.md](./restart-services.md)
   (nunca `docker compose down -v`, que **borra volúmenes**).

## Índice de síntomas

| Síntoma                                              | Sección                                                                           |
| ---------------------------------------------------- | --------------------------------------------------------------------------------- |
| El instalador aborta en el paso 1 (prereqs)          | [Fallos de prerequisitos de instalación](#fallos-de-prerequisitos-de-instalación) |
| Un contenedor no arranca / queda en `Restarting`     | [Un contenedor no arranca](#un-contenedor-no-arranca)                             |
| Vault sellado / `503` / la API no encuentra secretos | [Vault sellado o no responde](#vault-sellado-o-no-responde)                       |
| La API/worker no conecta a PostgreSQL                | [Conectividad con PostgreSQL](#conectividad-con-postgresql)                       |
| Redis no responde / colas Celery atascadas           | [Conectividad con Redis](#conectividad-con-redis)                                 |
| MinIO no acepta lecturas/escrituras de objetos       | [Conectividad con MinIO](#conectividad-con-minio)                                 |
| Llamadas salientes (LLM, OAuth) fallan / time out    | [Egress-proxy y red](#egress-proxy-y-red)                                         |
| Migraciones Alembic fallan al arrancar               | [Migraciones de base de datos](#migraciones-de-base-de-datos)                     |
| Procesos OOM-killed, host lento, disco lleno         | [Presión de recursos: OOM, RAM y disco](#presión-de-recursos-oom-ram-y-disco)     |
| Saltó una alerta de monitorización                   | [Alertas de monitorización](#alertas-de-monitorización-plan-12)                   |
| El api-server aborta al arrancar por un secreto      | [El arranque falla fail-closed](#el-arranque-falla-fail-closed)                   |

---

## Fallos de prerequisitos de instalación

El paso 1 del wizard y el gate previo del CLI validan los prerequisitos y
**abortan antes de aprovisionar** si algo falla
(`apps/installer/backend/src/installer_backend/prereqs.py`). El CLI sale con
**código 3 (PREREQ)** y no toca nada en disco.

| Síntoma                           | Causa raíz                                               | Fix                                                                                       |
| --------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| «Docker Engine < 24»              | El host tiene un Docker viejo                            | Actualiza a Docker Engine **24.0+** (cap-drop / seccomp / rootfs read-only estables ahí). |
| «Compose v2 no encontrado»        | Solo está el `docker-compose` v1 legado                  | Instala el plugin `docker compose` (v2). El stack usa la sintaxis v2.                     |
| «RAM insuficiente»                | Menos de 8 GiB                                           | El suelo son **8 GiB** (PostgreSQL+pgvector, Redis, MinIO, Vault, API, workers).          |
| «Disco insuficiente»              | Menos de 50 GiB libres en la raíz de datos               | Libera/expande disco hasta **50 GiB** (imágenes + `pgdata` + object store + backups).     |
| «Ningún proveedor LLM habilitado» | El config no habilita ≥ 1 proveedor del catálogo cerrado | Habilita al menos uno (ADR 0021: Claude SDK, Copilot, Azure Foundry/APIM, Ollama).        |

Corrige la causa y **reintenta** desde el paso 1 / relanza
`scripts/install.sh --config install.yaml`. Detalle completo en
[01-installation-from-scratch.md](./01-installation-from-scratch.md).

## Un contenedor no arranca

Un servicio en `Restarting` o `Exited` casi siempre es config, un puerto
ocupado, un volumen con permisos malos o una imagen no descargada.

1. **Lee los logs** (manda casi siempre la última excepción):

   ```bash
   docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
     logs <servicio> --tail 100
   ```

2. **Inspecciona el estado de salida** (exit code + OOM):

   ```bash
   docker inspect --format '{{.State.ExitCode}} oom={{.State.OOMKilled}}' \
     $(docker compose ps -q <servicio>)
   ```

   `oom=true` → ve a [Presión de recursos](#presión-de-recursos-oom-ram-y-disco).

3. Causas frecuentes y su fix:

   | Causa                                  | Fix                                                                                                                                                                                                                                                                                      |
   | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | Puerto ya en uso en el host            | Trampas conocidas: [postgres-port-clash-with-laragon](../03-guides/gotchas/postgres-port-clash-with-laragon.md), [vault-dev-mode-port-conflict](../03-guides/gotchas/vault-dev-mode-port-conflict.md), [windows-tcp-ghost-listener](../03-guides/gotchas/windows-tcp-ghost-listener.md). |
   | Variable de entorno mal / faltante     | Revisa `docker/.env`; recrea con `up -d --force-recreate <servicio>` ([restart-services.md](./restart-services.md)).                                                                                                                                                                     |
   | Volumen con permisos POSIX incorrectos | Los dirs de datos llevan `0700`/`0750` (ver el árbol en [01-installation-from-scratch.md](./01-installation-from-scratch.md)); `vault/file` y `postgres` deben ser `0700`.                                                                                                               |
   | Imagen no descargada                   | `docker compose pull <servicio>` y reintenta.                                                                                                                                                                                                                                            |
   | Flag de seguridad bloquea el arranque  | Si añadiste `seccomp`/`apparmor` y el servicio muere al instante, verifica que el perfil AppArmor está cargado en el host ([apparmor-profiles.md](./apparmor-profiles.md)).                                                                                                              |

4. Tras corregir, recrea el servicio y vuelve a [health-check.md](./health-check.md).

## Vault sellado o no responde

Vault arranca **sellado** tras cada reinicio: hasta que se desella, no
entrega secretos y la API/worker que dependen de él fallan al leer
credenciales.

**Diagnóstico**:

```bash
docker compose exec vault vault status   # Sealed: true → hay que desellar
```

- `Sealed: true` → **desella** con las unseal keys del revelado único. El
  procedimiento exacto está en
  [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md), sección
  «Desellar». Sin las unseal keys **no hay recuperación** (ver el aviso de
  [01-installation-from-scratch.md](./01-installation-from-scratch.md#guardar-las-credenciales-y-las-unseal-keys)).
- Vault atascado en `Restarting` (no llega ni a sellado) →
  [vault-dev-mode-port-conflict](../03-guides/gotchas/vault-dev-mode-port-conflict.md)
  y [vault-entrypoint-config-flag](../03-guides/gotchas/vault-entrypoint-config-flag.md).
- La API responde `503` en endpoints que necesitan secretos pero Vault
  está `Sealed: false` → comprueba la política/token con
  `GET /admin/system-health` (campo `vault`); revisa logs del servicio que
  falla.

> La rotación periódica de unseal keys y de credenciales está en
> [05-key-rotation.md](./05-key-rotation.md).

## El arranque falla fail-closed

Desde prod-10 (`task_prod10_04`, `task_prod10_05`) el stack **prefiere no
arrancar antes que arrancar con un secreto conocido**. Es intencionado: la
alternativa es correr meses en producción con la JWT secret que está publicada en
GitHub. Los tres mensajes y su arreglo:

| Mensaje al arrancar                                                                 | Qué pasa                                                                                                                                             | Arreglo                                                                                                                                         |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `required variable POSTGRES_PASSWORD is missing a value` (y equivalentes)           | El compose canónico ya no cae a `changeme-dev-only`: usa `${VAR:?…}`.                                                                                | `cp docker/.env.example docker/.env` y rellena. Catálogo completo en [mandatory-env-vars.md](../04-reference/mandatory-env-vars.md).            |
| `environment='dev' but these settings still use dev defaults: …`                    | No se declaró `API_SERVER_ENVIRONMENT`, el DSN apunta a un host remoto (o sea: **no** es un portátil) y algún secreto lleva `changeme` / `dev-only`. | Declara `API_SERVER_ENVIRONMENT=dev\|staging\|prod`, **o** pon secretos de verdad. El mensaje nombra la variable ofensora.                      |
| `environment='prod' rejects trivially weak secrets: … (only 1 distinct characters)` | Un secreto sin marcador de dev pero que es relleno (`xxxxxxxx…`). Suelo: 24 caracteres, ≥8 distintos, ≥2 bits/carácter.                              | Genera uno: `python -c "import secrets; print(secrets.token_urlsafe(36))"`. Aplica sólo con `staging`/`prod` **declarados**.                    |
| `environment='prod' requires HMAC signing secrets of at least 32 characters`        | `API_SERVER_JWT_SECRET(S)` o `API_SERVER_INTERNAL_TOKEN_SECRET(S)` demasiado cortos — **en cualquier posición del anillo**, no sólo en la cabeza.    | Igual que arriba. Una clave retirada que sigue en la cola no firma nada nuevo, pero **verifica**: una entrada débil ahí es una sesión forjable. |
| `INTERNAL_TOKEN_SECRET(S) must share NO key with JWT_SECRET(S)`                     | Los dos dominios criptográficos ([ADR 0136](../05-architecture-decisions/0136-dominios-criptograficos-worker-api.md)) se han fusionado.              | Valores distintos, y anillos **disjuntos**: una clave en ambos deja a un worker comprometido forjar sesiones humanas.                           |

> **En dev no cambia nada.** El guard se salta entero cuando `environment` es
> `dev` **declarado**, o cuando no se declaró y el DSN apunta a
> localhost/127.0.0.1 — que es lo que hacen `scripts/dev/up.ps1` y el conftest de
> integración.

## Conectividad con PostgreSQL

Síntomas: la API/worker arranca pero falla con errores de conexión,
`GET /admin/system-health` reporta `postgres: down`, o las migraciones no
corren.

1. ¿Está sano el contenedor? `docker compose ps postgres` →
   `Up (healthy)`. Si no, [Un contenedor no arranca](#un-contenedor-no-arranca).
2. Prueba la conexión desde dentro de la red:

   ```bash
   docker compose exec postgres pg_isready -U "$POSTGRES_USER"
   ```

3. Causas frecuentes:

   | Causa                                                   | Fix                                                                                                                                                                                                                    |
   | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | Puerto chocando con un PostgreSQL local (Laragon, etc.) | [postgres-port-clash-with-laragon](../03-guides/gotchas/postgres-port-clash-with-laragon.md).                                                                                                                          |
   | DSN mal en `.env` (host/puerto/credenciales)            | El generador deriva la DSN; si la editaste a mano, revisa que el host sea el nombre de servicio `postgres`.                                                                                                            |
   | RLS bloquea queries (acceso aparentemente vacío)        | El acceso multi-tenant pasa por roles/RLS: [postgres-roles-bypassrls](../03-guides/gotchas/postgres-roles-bypassrls.md), [asyncpg-set-local-no-bind-params](../03-guides/gotchas/asyncpg-set-local-no-bind-params.md). |
   | Error «cannot insert multiple commands» con asyncpg     | [asyncpg-no-multistatement](../03-guides/gotchas/asyncpg-no-multistatement.md).                                                                                                                                        |

## Conectividad con Redis

Redis es el broker de Celery y la caché. Si cae, las tareas dejan de
procesarse y la cola se acumula.

1. `docker compose ps redis` → `Up (healthy)`; `GET /admin/system-health`
   campo `redis`.
2. Prueba directa:

   ```bash
   # Redis pide contraseña desde prod-10: sin `-a` responde NOAUTH, no PONG.
   docker compose exec redis sh -c 'redis-cli -a "$REDIS_PASSWORD" ping'   # PONG
   ```

3. Causas frecuentes:

   | Causa                                  | Fix                                                                                                                                                         |
   | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | Redis caído / `Restarting`             | [Un contenedor no arranca](#un-contenedor-no-arranca) y [restart-services.md](./restart-services.md).                                                       |
   | Colas atascadas pero Redis sano        | El cuello suele estar en los **workers**: mira sus logs; escala réplicas (ver el runbook de capacity, `06-capacity-management.md`).                         |
   | AOF/RDB corrupto tras un corte abrupto | Restaura desde backup ([dr-full-restore.md](./dr-full-restore.md)); Redis es estado efímero/recuperable, prioriza recuperar el servicio.                    |
   | `NOAUTH Authentication required`       | Desde prod-10 Redis arranca con `--requirepass`. La URL del cliente tiene que ser `redis://:<REDIS_PASSWORD>@redis:6379/<db>`, y `redis-cli` necesita `-a`. |

## Conectividad con MinIO

MinIO es el object storage (S3-compatible) para artefactos, ingestión
documental y bundles.

1. `docker compose ps minio`; `GET /admin/system-health` campo `minio`
   (sonda `/minio/health/live`).
2. Causas frecuentes:

   | Causa                                          | Fix                                                                                                    |
   | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
   | Credenciales de acceso mal (`access`/`secret`) | Revisa las claves de MinIO en `.env`; deben coincidir con las que usa la API/worker.                   |
   | El bucket inicial no existe                    | El aprovisionamiento crea el bucket; si lo borraste, recréalo con el `mc`/cliente o reinstala config.  |
   | Disco lleno bajo `minio/`                      | Ver [Presión de recursos](#presión-de-recursos-oom-ram-y-disco); MinIO rechaza escrituras sin espacio. |

## Egress-proxy y red

**Todo el tráfico saliente** (proveedores LLM, OAuth de Copilot, APIM de
Azure, webhooks…) sale por el `egress-proxy`. Si los runtimes/agentes no
hablan con el exterior, sospecha aquí antes que de la red del host.

1. `docker compose ps egress-proxy`; `GET /admin/system-health` campo
   `egress-proxy` (sonda TCP).
2. Causas frecuentes:

   | Causa                                                     | Fix                                                                                                                                        |
   | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
   | El destino no está en la allowlist del proxy              | El proxy es deny-by-default; añade el host destino a su allowlist y recréalo.                                                              |
   | DNS no resuelve dentro de la red `agentic-net`            | Verifica que el servicio usa el nombre de servicio (no `localhost`); reinicia el proxy.                                                    |
   | Los runtimes efímeros tienen red restringida (por diseño) | Es esperado: los runtimes **no** ejecutan código con red abierta (CLAUDE.md, principio 2). El egress legítimo va por el proxy, no directo. |

> Recuerda: los workers **no** ejecutan código del usuario y los runtimes
> corren con red restringida y sin socket Docker. Una llamada saliente que
> «debería» funcionar pero no pasa por el proxy es, casi siempre, un
> destino que falta en la allowlist, no un bug de red.

## Migraciones de base de datos

La API aplica migraciones Alembic al arrancar (y `scripts/dev/up` las
reaplica en dev). Un fallo aquí deja el esquema a medias.

1. Lee el error exacto en los logs del api-server / del runner de
   migraciones.
2. Causas frecuentes:

   | Causa                                                | Fix                                                                                                                |
   | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
   | Migración con varias sentencias en un solo `execute` | asyncpg no admite multi-statement: [asyncpg-no-multistatement](../03-guides/gotchas/asyncpg-no-multistatement.md). |
   | `SET LOCAL` con parámetros enlazados                 | [asyncpg-set-local-no-bind-params](../03-guides/gotchas/asyncpg-set-local-no-bind-params.md).                      |
   | `DEFAULT PRIVILEGES` no aplica a la BD esperada      | [postgres-alter-default-privileges-per-db](../03-guides/gotchas/postgres-alter-default-privileges-per-db.md).      |
   | Una migración no es reversible                       | CLAUDE.md exige migraciones **reversibles** antes de `up -d --build`. Corrige el `downgrade` antes de promover.    |

> Si una migración dejó el esquema inconsistente y no hay `downgrade`
> limpio, trátalo como recuperación de datos: restaura desde el último
> backup bueno ([dr-full-restore.md](./dr-full-restore.md)) en lugar de
> editar el esquema a mano en producción.

## Presión de recursos: OOM, RAM y disco

Síntomas: el host va lento, contenedores reiniciándose solos, `oom=true`
en `docker inspect`, escrituras rechazadas por «no space left on device».

1. **Mira las métricas** primero — el overlay de monitorización (Plan 12)
   trae el dashboard `host-overview` en Grafana (CPU / RAM / disco / red +
   por contenedor). Si no tienes el overlay arrancado:

   ```bash
   docker compose -f docker/docker-compose.yml \
     -f docker/docker-compose.monitoring.yml up -d
   ```

2. **Disco**:

   ```bash
   df -h /data/agent-platform
   du -sh /data/agent-platform/* | sort -h
   docker system df            # imágenes/volúmenes/build cache huérfanos
   ```

   Fixes: podar bundles de backup antiguos respetando la retención; `docker
image prune` / `docker builder prune` para reclamar; expandir el disco.
   **No** borres a ciegas bajo `postgres/`, `vault/file/` ni `minio/`.

3. **RAM / OOM**: identifica el contenedor culpable y sube su límite o
   arregla la fuga:

   ```bash
   docker stats --no-stream
   docker inspect --format '{{.Name}} oom={{.State.OOMKilled}}' $(docker compose ps -q)
   ```

   En un host mono-máquina, bajar el número de réplicas de workers o su
   memoria por worker alivia la presión (ver `06-capacity-management.md`).

## Alertas de monitorización (Plan 12)

El overlay de monitorización define cinco alertas en
`docker/monitoring/prometheus/rules/host_alerts.yml`; Alertmanager las
enruta al notificador de la plataforma. Cuando salte una, esta es la
respuesta:

| Alerta (regla)                         | Qué significa                                            | Respuesta                                                                                                                                      |
| -------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `HostDiskUsageHigh` (disco > 80%, 10m) | Un filesystem real está > 80% lleno; backups en riesgo   | [Disco](#presión-de-recursos-oom-ram-y-disco): poda bundles según retención, reclama Docker, expande.                                          |
| `HostMemoryUsageHigh` (RAM > 90%, 5m)  | RAM por encima del 90% sostenido; riesgo de OOM          | [RAM / OOM](#presión-de-recursos-oom-ram-y-disco): identifica el contenedor pesado; baja réplicas/límites.                                     |
| `HostSwapActive` (swap en uso, 5m)     | El host está haciendo swap → presión de memoria temprana | Igual que RAM: reduce carga antes de que escale a OOM.                                                                                         |
| `HostOOMKills` / `ContainerOOMKilled`  | El kernel mató procesos / un contenedor concreto fue OOM | Sube el límite de memoria del contenedor nombrado o corrige la fuga; revisa logs del culpable.                                                 |
| `BackupLastRunFailed` / `BackupTooOld` | El último backup falló/no verificó, o no hay uno < 26h   | Inspecciona logs del worker de backup; revisa Celery beat; ver [dr-manual-backup.md](./dr-manual-backup.md) para forzar y verificar un backup. |

> `BackupTooOld` es defensa en profundidad: si el motor **ni siquiera
> corrió** (beat caído, worker muerto), `BackupLastRunFailed` se queda
> mudo, pero `BackupTooOld` salta cuando el último éxito supera las 26h
> (cadencia diaria 03:00 + 2h de gracia).

## Cuándo escalar a un runbook dedicado

| Situación                                                  | Runbook                                                                                                              |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Hay que recuperar el stack entero desde un backup          | [dr-full-restore.md](./dr-full-restore.md)                                                                           |
| Hay que recuperar **un solo tenant** sin tocar a los demás | [dr-tenant-restore.md](./dr-tenant-restore.md)                                                                       |
| Rotar unseal keys / credenciales comprometidas             | [05-key-rotation.md](./05-key-rotation.md)                                                                           |
| Falta capacidad (escalar workers, dimensionar)             | `06-capacity-management.md`                                                                                          |
| Sospecha de incidente de seguridad / aislamiento           | [internal-pentest-methodology.md](./internal-pentest-methodology.md), [apparmor-profiles.md](./apparmor-profiles.md) |

## Enlaces

- Salud y reinicio: [health-check.md](./health-check.md),
  [restart-services.md](./restart-services.md).
- Instalación y prereqs: [01-installation-from-scratch.md](./01-installation-from-scratch.md).
- DR y backups: [dr-full-restore.md](./dr-full-restore.md),
  [dr-tenant-restore.md](./dr-tenant-restore.md),
  [dr-manual-backup.md](./dr-manual-backup.md).
- Vault: [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md),
  [05-key-rotation.md](./05-key-rotation.md).
- Trampas del toolchain: [`docs/03-guides/gotchas/`](../03-guides/gotchas/).
