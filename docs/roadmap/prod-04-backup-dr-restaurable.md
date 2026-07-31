---
plan_id: prod-04-backup-dr-restaurable
title: Backup/DR restaurable de verdad — bug de tar, restore ejecutable, clave offsite, RPO/RTO y drill
status: pending_approval
blocking_plan: [prod-01-despliegue-ejecutable]
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 17
estimated_cost_human_eur: 7.650 € – 10.200 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P0
---

# Plan prod-04 — Backup/DR restaurable de verdad

## Cabecera

| Campo                              | Valor                           |
| ---------------------------------- | ------------------------------- |
| **ID del Plan**                    | `prod-04-backup-dr-restaurable` |
| **Prioridad**                      | P0 — bloqueante de producción   |
| **Bloqueado por**                  | `prod-01-despliegue-ejecutable` |
| **Tiempo estimado (calendario)**   | 3-4 semanas                     |
| **Tiempo estimado (persona-días)** | 17                              |
| **Rama git sugerida**              | `plan/prod-04-backup-dr`        |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría de producción (2026-06-10) ha verificado que **la plataforma no tiene hoy capacidad real de backup ni de restore**, pese a que el Plan 12 construyó un subsistema formalmente correcto:

1. **Ningún backup completo real puede producirse**: los argv de `tar` en `backup.py` omiten el flag de modo `--create`; cualquier tar real falla con rc=2 y el motor borra el bundle entero (incluido el `pg_dump` bueno). Nadie lo ha visto porque hasta los tests de `tests/integration/` inyectan un `FakeRunner` que fabrica los artefactos (gap3-1).
2. **El restore completo es inejecutable**: el runbook invoca un servicio `worker` que no existe en ningún compose y los `restore_app_services` por defecto referencian 5 servicios fantasma que abortan el restore en su primer paso destructivo (gap1-2, gap3-2). Tras un fallo a mitad, el código levanta SIEMPRE el stack sobre datos inconsistentes, justo lo que los runbooks prohíben (gap3-7).
3. **Circularidad criptográfica**: la clave que descifra el bundle (`WORKERS_BACKUP_ENCRYPTION_KEY`) vive en el entorno de la misma máquina que se respalda — y el Vault viaja DENTRO del blob cifrado. Las unseal keys custodiadas no descifran AES-GCM; ante pérdida total del host el backup es irrecuperable (gap1-1, gap1-5).
4. **El bundle no cubre el producto de la plataforma**: los repos git de proyectos (`{data_root}/projects`) quedan fuera del backup (deploy-5, gap3-5), el bundle es internamente inconsistente (pg_dump y tars en caliente capturan instantes distintos, gap3-3), los GRANTs de `app_user` no se recrean tras `pg_restore --no-owner --no-privileges` (gap1-4), y nada reconcilia BD↔MinIO↔Vault↔git tras restaurar (gap3-5).
5. **Sin garantías declaradas**: ningún runbook declara RPO/RTO, no hay PITR ni subida remota automática (gap3-4), y en una instalación de producción el backup diario fallaría siempre por defaults de desarrollo (deploy-4).

Este plan arregla el bug del tar con tests de **runner real**, hace el restore ejecutable y fail-safe contra el compose real, mete los repos git en el bundle, rompe la circularidad de la clave con custodia offsite obligatoria, recrea GRANTs/ownership post-restore, declara RPO/RTO, añade reconciliación post-restore, y cierra con un **drill documentado backup → máquina limpia → restore → login + ejecución de un plan** como test humano de cierre (que además sirve de evidencia para `human_12_02` del Plan 12, aún en `pending_human_validation`).

## Alcance

**Entra**:

- Fix del `--create` en los dos argv de tar + test de humo con el binario `tar` REAL + test de integración del backup completo con `SubprocessRunner` real (sin `FakeRunner`).
- Plano de ejecución real del restore (`scripts/restore.sh` host-side), `restore_app_services` alineados con el compose real, fail-stopped por defecto y `pg_restore --exit-on-error`.
- Repos git de proyectos (`{data_root}/projects`, bare repos) dentro del bundle y del restore, con verificación en `backup_verification.py`.
- Consistencia del bundle: Redis vía BGSAVE, snapshot coherente de Vault, ADR sobre quiesce/skew residual.
- Custodia offsite de `WORKERS_BACKUP_ENCRYPTION_KEY` (fingerprint verificable + runbooks corregidos: las unseal keys NO descifran el bundle).
- Re-GRANT/ownership idempotente post-`pg_restore` y validación del rol de conexión.
- Defaults de producción del backup en el instalador (`WORKERS_BACKUP_DATABASE_URL`, captura por bind-mounts).
- Restore por tenant sin mutar el `_data` de un MinIO vivo.
- Orden seguro de borrado de documentos (soft-delete + commit antes de tocar el blob de MinIO).
- RPO/RTO declarados y validados, subida remota automática post-verificación.
- Reconciliación post-restore BD↔MinIO↔Vault↔git con criterios medibles.
- Guion del drill de DR + ejecución y registro de evidencia.

**Queda fuera**:

- Declarar los servicios de apps en el compose y publicar imágenes — es **prod-01** (este plan lo asume completado: `blocking_plan`).
- Enrutado de alertas (Alertmanager → humanos) de `BackupTooOld` y del fallo del backup task — es **prod-08**; aquí solo se garantiza que la señal se emite.
- Job de purga/retención de filas soft-deleted y datos append-only — es **prod-13** (db-4); aquí solo se corrige el orden de borrado del blob (db-3) preservando la semántica recuperable.
- Implementar un cliente Vault real (hvac) para resolver la clave de cifrado — se documenta la realidad actual (env var) y se rompe la circularidad por custodia; el provider hvac queda como mejora futura tras **prod-10**.
- Cierre formal del Plan 12 (frontmatter, changelog) — el drill de este plan genera la evidencia; la actualización del roadmap histórico se coordina con **prod-15**.

## Decisiones clave

1. **Plano de ejecución del restore** — Opciones: (a) `docker compose exec` en un contenedor de workers; (b) script host-side `scripts/restore.sh` que ejecuta el motor en el host con acceso al socket Docker y a los volúmenes/bind-mounts. **Recomendación: (b)** — el restore para el stack del que dependería el contenedor; ejecutarlo desde dentro de lo que se va a parar es frágil por construcción. El proceso que ejecuta el restore NUNCA puede estar en la lista de servicios a parar.
2. **Comportamiento tras fallo a mitad del restore** — Opciones: (a) mantener el auto-arranque incondicional actual; (b) fail-stopped: dejar el stack parado (o solo postgres para diagnóstico) y devolver `RestoreError` con el estado y el siguiente paso. **Recomendación: (b)**, con auto-arranque opt-in vía flag explícito. Es lo que ambos runbooks ya ordenan; el código debe obedecer al procedimiento, no contradecirlo.
3. **Consistencia del bundle (quiesce vs snapshot vs skew aceptado)** — decisión de producto/operación con coste de disponibilidad: (a) quiesce corto de escritores (stop de apps ~1-3 min en la ventana de las 03:00); (b) snapshot de filesystem (LVM/ZFS — exige requisitos de host); (c) aceptar y documentar el skew residual entre artefactos. **No se decide aquí: ADR propuesto** (task_prod_04_06) con recomendación (a) por simplicidad en single-host. Lo que NO es opcional: Redis por BGSAVE y Vault con copia coherente.
4. **Formato del backup de repos git** — Opciones: (a) tar de `{data_root}/projects` excluyendo `worktrees/` y dep-cache; (b) `git bundle` por repo. **Recomendación: (a)** — homogéneo con el resto de artefactos, los worktrees son transitorios y regenerables; (b) queda como optimización futura si el tamaño lo exige.
5. **RPO/RTO objetivo y PITR** — declarar el estado actual (RPO ≤ 24 h con cadencia diaria + copia remota, RTO objetivo ≤ 4 h medido en el drill) es de este plan; si dirección exige RPO menor, activar WAL archiving (archive_mode + wal-g/pgbackrest hacia MinIO/destino remoto) se plantea como **ADR propuesto**, no se implementa aquí.
6. **Evidencia de custodia de la clave** — el control técnico no puede garantizar la custodia humana, solo verificarla indirectamente: el manifest registra el fingerprint SHA-256 de la clave y el backup falla (fail-closed) si `encryption_enabled=true` y el fingerprint configurado como "custodiado" no coincide con la clave activa.

## Tareas

### Fase A — El bug del tar y tests con runner real

#### `task_prod_04_01` — Añadir `--create` a los dos argv de tar + test de humo con tar real

> **Estado (2026-07-31, prod-04)**: CERRADO. El fix del bug (`--create` en los tres argv de
> tar) ya estaba desde el commit `bdea0af`; lo que faltaba —y era lo que importaba— era la
> cobertura de EJECUCIÓN. Añadidos `tests/integration/test_backup_tar_smoke.py` (los argv del
> código de producción contra el binario `tar` real, con ida y vuelta byte a byte) y
> `tests/integration/test_backup_real_runner.py` (task_prod_04_02). Ciclo rojo-verde
> verificado: quitando `--create` de `_tar_volume`, tar devuelve rc=1 («Must specify one of
> -c, -r, -t, -u, -x») y el smoke test se pone en rojo.

- [x] **Título**: Fix de `_tar_volume` y `_encrypt_bundle` + smoke test que ejecuta el binario `tar` REAL
- **Descripción**: En `apps/workers/src/workers/backup.py`, añadir `--create` al argv de `_tar_volume` (≈ líneas 400-409) y al de `_encrypt_bundle` (≈ 451-456). Añadir `tests/integration/test_backup_tar_smoke.py` que ejecute los argv generados con `SubprocessRunner` real contra un directorio temporal (no requiere Docker ni stack vivo) y verifique que el archivo `.tar.gz` se crea, no está vacío y se extrae con el contenido original. Este test debe FALLAR con el código actual (rojo antes del fix).
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_tar_smoke.py -v"
  ```

#### `task_prod_04_02` — Test de integración del backup completo con `SubprocessRunner` real

- [x] **Título**: `run_full_backup` end-to-end con tar/gzip/sha256/AES reales (el `FakeRunner` deja de ser la única cobertura)
- **Descripción**: Nuevo `tests/integration/test_backup_real_runner.py`: ejecutar `run_full_backup` con el runner real sobre directorios temporales que simulan los mounts de volúmenes (el binario `pg_dump` se sustituye por un stub ejecutable en `PATH` que emite un dump sintético — es el ÚNICO seam mockeado; tar, gzip, checksums, manifest y cifrado/descifrado AES-256-GCM corren de verdad). Verificar: bundle creado, `backup_verification.py` lo valida, el blob cifrado se descifra y contiene los artefactos. Documentar en el docstring de `tests/integration/test_backup_full.py` que el `FakeRunner` solo cubre la construcción de argv, no la ejecución. Depende de `task_prod_04_01`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_real_runner.py -v"
  ```

### Fase B — Restore ejecutable y fail-safe contra el compose real

#### `task_prod_04_03` — Plano de ejecución real del restore: `scripts/restore.sh` + defaults alineados

- [x] **Título**: Restore lanzable desde el host y `restore_app_services` sin servicios fantasma
- **Descripción**: Crear `scripts/restore.sh` host-side que ejecute el motor (`python -m workers.restore ...` o entrypoint equivalente) con acceso al socket Docker y a los volúmenes, fuera de los contenedores que se van a parar. Alinear `RestoreConfig.restore_app_services` (`apps/workers/src/workers/config.py:475-482`) con los servicios que prod-01 declara realmente en el compose; excluir de la lista de stop el plano que ejecuta el restore. Añadir un test que parsee `docker/docker-compose.yml` (+ el compose de apps de prod-01) y asserte `restore_app_services ⊆ servicios declarados` — guard permanente contra servicios fantasma. Reescribir el paso 3 de `docs/06-runbooks/dr-full-restore.md` (líneas 81-89: eliminar `exec -T worker`) con el comando que funciona. Depende de prod-01.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_restore_services_alignment.py -v"
  ```

#### `task_prod_04_04` — Fail-stopped por defecto + `pg_restore --exit-on-error`

- [x] **Título**: Un fallo a mitad del restore deja el stack PARADO; los errores de pg_restore no se enmascaran
- **Descripción**: En `apps/workers/src/workers/restore.py:272-280`, invertir el `finally` que ejecuta `docker compose up -d` incondicionalmente: ante fallo en la fase destructiva, dejar el stack parado (opcionalmente levantar solo postgres para diagnóstico) y lanzar `RestoreError` con el estado alcanzado y el siguiente paso. Auto-arranque solo opt-in (`restore_autostart_on_failure`, default `false`). Añadir `--exit-on-error` al argv de `pg_restore` (restore.py:440-448). Alinear `docs/06-runbooks/04-disaster-recovery.md:209-212` y `dr-full-restore.md:140-145` con el nuevo comportamiento (ya lo describen: ahora el código obedece). Actualizar los tests de `test_restore_full.py` afectados.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_restore_full.py -v -k 'fail_stopped or exit_on_error'"
  ```

### Fase C — Cobertura y consistencia del bundle

#### `task_prod_04_05` — Repos git de proyectos dentro del bundle y del restore

- [x] **Título**: `{data_root}/projects` (bare repos) como artefacto de backup verificado
- **Descripción**: Añadir a `backup.py` un artefacto `projects_tar`: tar de `{data_root}/projects` (bare repos por tenant/proyecto, `git_repos.py:6,74`) excluyendo `worktrees/` y dep-cache (transitorios). Nueva setting `backup_projects_root` en `workers/config.py` (default `{data_root}/projects`). Incluir el artefacto en el manifest y en `backup_verification.py`. En `restore.py`, re-extraer a `data_root` en la fase de volúmenes. Test con runner real: crear un bare repo temporal con una rama `plan/xxx`, backup, restore a otro directorio, verificar `git rev-parse` de la rama. Cierra deploy-5 y la mitad estructural de gap3-5 (principios rectores 4 y 5 de CLAUDE.md).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_projects_repos.py -v"
  ```

#### `task_prod_04_06` — Captura coherente: Redis BGSAVE, Vault snapshot y ADR de consistencia del bundle

> **Estado (2026-07-31, prod-04)**: (1) y (2) HECHOS, (3) redactado y **ABIERTO a propósito**
> — el ADR es una decisión de dirección y la casilla no se marca hasta que alguien la tome.
>
> Lo entregado, porque era estrictamente mejor con cualquiera de las tres opciones:
>
> - **(1) Redis**: `BGREWRITEAOF` (con `aof_last_bgrewrite_status` comprobado) + artefacto
>   propio `redis_tar`, verificado y **restaurado** (`wipe=True`). **La letra del task era
>   incorrecta y se midió**: «capturar solo el `dump.rdb`» restaura una base **VACÍA**, porque
>   con `--appendonly yes` (como lo arranca el compose) Redis ignora el RDB si no hay
>   `appendonlydir` — crea un AOF nuevo y sirve `DBSIZE 0` sin un solo error. Medido contra
>   `redis:7-alpine` el 2026-07-31; documentado en
>   `docs/03-guides/gotchas/redis-aof-ignores-a-restored-rdb.md`.
> - **(2) Vault**: captura verificada estable (huella por CONTENIDO antes/después del tar,
>   reintentos, y fallo del run si no converge). La primera versión comparaba
>   `(tamaño, mtime)` y una ejecución real de la suite la pilló dando «estable» sobre un
>   árbol recién reescrito.
> - Skew residual medido y documentado en `04-disaster-recovery.md` («Skew residual del
>   bundle»), que es la condición previa para que la decisión del ADR sea informada.
>
> Lo que falta para marcar la casilla: **la decisión humana del
> [ADR 0149](../05-architecture-decisions/0149-consistencia-del-bundle-de-backup.md)**
> (quiesce corto / snapshot de FS / skew aceptado) + la ligada «¿Redis es crítico o
> recreable?» + la de `vault_data` dentro o fuera del blob cifrado (que task_prod_04_07 pedía
> anotar aquí). Implementar una de las tres antes de que dirección elija sería decidir por
> ella y luego tirarlo. El ADR trae el coste y la estimación de cada opción.

- [ ] **Título**: Eliminar la captura en caliente ingenua y documentar el skew residual aceptado
- **Descripción**: (1) Redis: antes del tar, lanzar `BGSAVE` y capturar solo el `dump.rdb` resultante (o, si dirección lo decide, declarar Redis como no respaldado por recreable — opción del ADR); dejar de tarear el AOF en escritura activa (`docker-compose.yml:104-107`). (2) Vault: captura coherente del file backend (parar el servicio un instante o copia atómica verificada). (3) Redactar **ADR propuesto** «Consistencia del bundle de backup» con las opciones de la Decisión clave 3 (quiesce corto / snapshot FS / skew aceptado), el orden de captura resultante y el skew residual medido — decisión para humano. Implementar la opción elegida tras aprobación (presupuestado: quiesce corto).
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_06_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_consistency.py -v"
  ```

### Fase D — Clave offsite y permisos post-restore

#### `task_prod_04_07` — Custodia offsite de la clave de descifrado + runbooks veraces

- [x] **Título**: Romper la circularidad: la clave que descifra el bundle no puede vivir solo en la máquina respaldada
- **Descripción**: (1) Corregir los tres runbooks que afirman que la clave se resuelve de Vault — falso: `EnvSecretsProvider` lee `WORKERS_BACKUP_ENCRYPTION_KEY` de `os.environ` (`backup_encryption.py:95-102`, `restore.py:547-553`) — y dejar EXPLÍCITO que las unseal keys NO descifran el bundle AES-GCM: `docs/06-runbooks/dr-full-restore.md:35-37,151-153`, `dr-manual-backup.md:36-37`, `04-disaster-recovery.md:70-74`. (2) Añadir paso OBLIGATORIO de custodia offsite del VALOR de la clave (gestor corporativo / sobre sellado, junto a las unseal keys pero diferenciado). (3) Fail-closed técnico: registrar el fingerprint SHA-256 de la clave en el manifest y nueva setting `backup_key_custody_fingerprint`; si `encryption_enabled=true` y el fingerprint no coincide (o está vacío), el backup falla con mensaje accionable. (4) Anotar en el ADR de task_prod_04_06 la opción de excluir `vault_data` del blob o cifrarlo con clave distinta (rompe la circularidad estructuralmente — decisión humana).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_key_custody.py -v"
  ```

#### `task_prod_04_08` — GRANTs y ownership tras `pg_restore`

- [x] **Título**: Re-concesión idempotente post-restore y validación del rol de conexión
- **Descripción**: El dump/restore descartan ownership y ACLs (`backup.py:369-376`, `restore.py:440-448`) y nada recrea los GRANTs de `app_user` (rol NOBYPASSRLS del que depende TODO el stack con FORCE RLS). Añadir al final del restore un paso idempotente: `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user; GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;` y `REASSIGN OWNED` / `ALTER TABLE ... OWNER TO migrations_user` cuando el rol de conexión no sea `migrations_user`. Validar en el motor que la URL de restore conecta como `migrations_user` (fail-closed con mensaje claro si no; `config.py:255-259` hoy solo pide «admin-grade»). Añadir al runbook la comprobación post-restore: conectar como `app_user`, SELECT/INSERT sobre una tabla con RLS, y `alembic upgrade head` como `migrations_user`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_08_a
    runtime: python-pytest
    command: "pytest tests/integration/test_restore_grants.py -v"
  ```

### Fase E — Defaults de producción y vías secundarias

#### `task_prod_04_09` — Defaults de producción del backup en el instalador

> **Estado (2026-07-31, prod-04)**: CERRADO, y el hallazgo era PEOR de lo que el task
> describía. Lo que decía («`config_generators.py` solo emite `WORKERS_BACKUP_ROOT`») ya no
> era cierto —prod-01 añadió `WORKERS_BACKUP_DATABASE_URL` y `WORKERS_BACKUP_VOLUMES`— pero
> los valores que emitía describían **otra máquina**:
>
> 1. **DSN**: `WORKERS_BACKUP_DATABASE_URL` = `${WORKERS_DATABASE_URL}` = la URL de
>    SQLAlchemy (`postgresql+asyncpg://`), que libpq no entiende. Saneado en el motor
>    (`workers.backup.libpq_url`) **y** ahora emitido ya en forma libpq por el instalador.
> 2. **Volúmenes FANTASMA** (esto no lo había visto nadie): el compose generado monta
>    **binds** bajo `{data_root}` y **no declara ningún named volume**, pero
>    `_BACKUP_VOLUME_NAMES` emitía los nombres del stack de manuales
>    (`agentic-platform_minio_data`, …). `tar` sobre
>    `/var/lib/docker/volumes/<fantasma>/_data` devuelve rc≠0 y el contrato clean-failure
>    **borraba el bundle entero, pg_dump bueno incluido**. El backup de una instalación por
>    el instalador fallaba TODAS las noches.
> 3. **PGDATA vivo en los tars**: el default `backup_bind_paths=["/data/agent-platform"]`
>    tarea el data dir de PostgreSQL (copia rota + «file changed as we read it» → rc≠0) y los
>    modelos de Ollama (decenas de GB por bundle). Ahora los bind paths son explícitos.
> 4. **Cifrado encendido a medias**: el compose emitía `ENCRYPTION_ENABLED=true` sin emitir
>    la clave ni la huella de custodia, y el motor es fail-closed (task_prod_04_07) → el
>    backup fallaba antes del dump. Se emite `false` y el opt-in en dos pasos (generar clave
>    → custodiarla → encender) está en `dr-manual-backup.md`.
>
> El test vive en `tests/unit/test_backup_env_contract.py` y no en
> `apps/installer/backend/tests/`, que es lo que el task pedía: ese directorio no existe ni
> tiene configuración de pytest, y el patrón de la casa para cruzar instalador↔runtime es
> `tests/unit/test_compose_env_contract.py`. 12 tests, verde.

- [x] **Título**: El backup diario no puede apuntar a `localhost:15432` ni a volúmenes con nombre inexistentes
- **Descripción**: `config_generators.py:241-242` solo emite `WORKERS_BACKUP_ROOT`, dejando los defaults dev de `workers/config.py` (pg*dump a `localhost:15432` con password dev; tars de `/var/lib/docker/volumes/...` cuando el compose generado usa bind-mounts bajo `{data_root}`). Emitir `WORKERS_BACKUP_DATABASE_URL` (DSN al servicio postgres con la credencial generada) y `WORKERS_BACKUP_VOLUMES`/`WORKERS_BACKUP_VOLUMES_MOUNT_ROOT` coherentes con el layout de bind-mounts del compose generado (`compose_generator.py:255,277,301-303`). Test que genera el `.env` y valida que la config efectiva de backup no contiene `localhost:15432` ni `changeme-` y que las rutas de captura existen en el layout generado. **Coordinación**: el instalador es territorio de prod-01 (este plan solo toca las claves `WORKERS_BACKUP*\*`); la alerta de «último backup correcto > 24 h» ya existe (`BackupTooOld`) y su enrutado a humanos es prod-08.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_backup_env_contract.py -v"
  ```

#### `task_prod_04_10` — Restore por tenant sin mutar el `_data` de un MinIO vivo

- [x] **Título**: La rebanada del tenant se restaura por API S3 (o con MinIO parado), nunca por debajo del servidor
- **Descripción**: `restore_per_tenant.py:875-897` hace `shutil.rmtree(ignore_errors=True)` + `tar --extract` directo sobre `minio_data/_data` con MinIO en marcha — no soportado por el formato xl (objetos invisibles/corruptos). Implementar la vía segura: extraer el tar a un directorio temporal, levantar un MinIO efímero sobre él y `mc mirror` la rebanada del tenant hacia el MinIO del stack vía API S3; como mínimo aceptable (decisión en code review), parar el servicio `minio` durante la extracción y verificar después por API que los objetos del tenant son legibles. Convertir el wipe `ignore_errors=True` en error duro. Actualizar `docs/06-runbooks/dr-tenant-restore.md:141-143`.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_restore_per_tenant.py -v -k 'minio'"
  ```

#### `task_prod_04_11` — Borrado de documentos: soft-delete + commit ANTES de tocar el blob

- [x] **Título**: Invertir el orden en `delete_document` para no perder la fuente si la transacción falla
- **Descripción**: `apps/api-server/src/api_server/routers/knowledge_bases.py:673-677` ejecuta `storage.delete_object` ANTES del `soft_delete`, con el commit al cierre del request: si el commit falla, queda un documento «vivo» cuyo binario ya no existe (reindex imposible). Invertir: soft-delete y commit primero; el borrado del blob pasa a una tarea Celery best-effort posterior (o al job de purga). Mantener la semántica «recuperable hasta la purga» que promete `db/knowledge.py:72-73`. **Coordinación**: el job de purga definitivo (db-4) es de prod-13; aquí la tarea Celery basta y queda lista para que prod-13 la absorba.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_11_a
    runtime: python-pytest
    command: "pytest tests/integration/test_document_delete_ordering.py -v"
  ```

### Fase F — RPO/RTO, reconciliación y drill

#### `task_prod_04_12` — RPO/RTO declarados + subida remota automática post-verificación

- [x] **Título**: Garantías de pérdida y recuperación medibles, y el bundle sale de la máquina solo
- **Descripción**: (1) Declarar en `docs/06-runbooks/04-disaster-recovery.md` (y referenciar desde `dr-full-restore.md`) RPO y RTO explícitos y medibles: RPO ≤ 24 h (cadencia diaria 03:00 + copia remota verificada) y RTO objetivo ≤ 4 h (a confirmar en el drill); validar las cifras con dirección. (2) Cablear la subida automática del bundle verificado al destino remoto tras cada backup (los adaptadores de `backup_destinations` ya existen y están testeados; hoy `run_full_backup()` NO sube nada, `dr-full-restore.md:157-159`), con métrica/log de éxito. (3) Si dirección exige RPO < 24 h: **ADR propuesto** «PITR con WAL archiving (archive_mode + wal-g/pgbackrest)» — no se implementa en este plan.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_remote_upload.py -v"
  ```

#### `task_prod_04_13` — Reconciliación post-restore BD↔MinIO↔Vault↔git

- [x] **Título**: El restore no se da por bueno hasta reconciliar los cuatro almacenes
- **Descripción**: Nuevo módulo `apps/workers/src/workers/restore_reconcile.py` (invocado como paso final del restore y ejecutable standalone) con criterios medibles: (a) BD↔MinIO: conteo de filas de documents/KB cuyo `source_storage_key` no existe en MinIO y de blobs huérfanos sin fila; (b) BD↔Vault: ping de cada `llm_providers.secret_vault_path` contra el Vault restaurado; (c) BD↔git: cada plan activo tiene su rama `plan/{id_short}-{slug}` en el bare correspondiente. Informe de divergencias al operador (exit code ≠ 0 si hay divergencias críticas) ANTES de dar el restore por bueno. Ampliar la «Verificación post-restore» de `04-disaster-recovery.md:175-199` (hoy solo health/login/smoke) con este paso. Depende de `task_prod_04_05`.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_13_a
    runtime: python-pytest
    command: "pytest tests/integration/test_restore_reconcile.py -v"
  ```

#### `task_prod_04_14` — Guion del drill de DR + ejecución asistida y evidencia

- [x] **Título**: Runbook `dr-drill.md` y preparación del drill backup → máquina limpia → restore → login + ejecución
- **Descripción**: Redactar `docs/06-runbooks/dr-drill.md` con el guion paso a paso del drill: backup en la máquina origen (tar real, bundle verificado) → subida al destino remoto → en una **máquina limpia** (sin `.env` original): obtener la clave de descifrado y las unseal keys EXCLUSIVAMENTE de custodia offsite → `scripts/restore.sh` → desellado de Vault → verificación de GRANTs/RLS → reconciliación (task_prod_04_13) → login de un usuario de tenant → ejecución de un plan end-to-end. Incluir plantilla de acta (tiempos medidos → RTO real, divergencias, incidencias). Preparar el entorno del drill y asistir su ejecución; el drill en sí es el test humano `human_prod_04_01` y su acta sirve además como evidencia de `human_12_02` del Plan 12 (cierre del Plan 12 coordinado con prod-15). Depende de todas las tareas anteriores.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod_04_14_a
    runtime: python-pytest
    command: "pytest tests/unit/test_runbook_dr_links.py -v"
  ```
  (valida que los runbooks DR referencian comandos/servicios existentes: anti-regresión del `exec -T worker`).

## Hallazgos de auditoría cubiertos

| fid      | Severidad | Tarea(s) que lo cierran                    |
| -------- | --------- | ------------------------------------------ |
| gap3-1   | critical  | task_prod_04_01, task_prod_04_02           |
| gap1-1   | critical  | task_prod_04_07 (+ ADR de task_prod_04_06) |
| gap1-2   | high      | task_prod_04_03                            |
| gap3-2   | high      | task_prod_04_03                            |
| gap3-7   | medium    | task_prod_04_04                            |
| deploy-5 | high      | task_prod_04_05                            |
| gap3-3   | high      | task_prod_04_06                            |
| gap1-5   | medium    | task_prod_04_07                            |
| gap1-4   | high      | task_prod_04_08                            |
| deploy-4 | high      | task_prod_04_09                            |
| gap3-6   | medium    | task_prod_04_10                            |
| db-3     | medium    | task_prod_04_11                            |
| gap3-4   | high      | task_prod_04_12                            |
| gap3-5   | high      | task_prod_04_05, task_prod_04_13           |
| gap1-3   | high      | task_prod_04_14 + human_prod_04_01         |

## Riesgos

1. **Dependencia de prod-01**: `restore_app_services`, el compose de apps y el instalador deben existir de verdad para que el restore y el drill sean ejecutables. Si prod-01 se retrasa, las fases B, E y F se bloquean (las fases A, C y D pueden avanzar en paralelo).
2. **El drill necesita una máquina limpia real** y varias horas de un operador + responsable de seguridad (custodia de claves). Si no se reserva ese recurso, el plan queda en `pending_human_validation` indefinidamente — exactamente el estado que arrastra el Plan 12.
3. **Cambio de contrato del restore (fail-stopped)**: los tests existentes con `FakeRunner` asumen el auto-arranque; hay que revisarlos todos, y un opt-in mal documentado podría sorprender a un operador que esperase el comportamiento antiguo.
4. **Quiesce de escritores**: la opción recomendada introduce 1-3 min de indisponibilidad diaria a las 03:00; si dirección la rechaza en el ADR, la implementación de task_prod_04_06 cambia de forma (snapshot FS exige LVM/ZFS en el host; skew aceptado exige reforzar task_prod_04_13).
5. **Acoplamiento al layout interno de MinIO**: la vía «MinIO efímero + mc mirror» añade complejidad operativa; la vía mínima (parar minio) penaliza disponibilidad del tenant durante el restore. Riesgo de sobrecoste si se intenta la vía completa sin timebox.
6. **La custodia offsite es un proceso humano**: el fingerprint solo verifica que la clave activa coincide con una declarada como custodiada; no puede probar que el sobre/gestor realmente la contiene. El drill (recuperando la clave SOLO de custodia) es la única verificación real — por eso es criterio de cierre.

## Tests humanos del Plan

```yaml
- id: human_prod_04_01
  description: "Drill de DR completo: backup → máquina limpia → restore → login + ejecución de un plan"
  hint: "Seguir docs/06-runbooks/dr-drill.md al pie de la letra, sin conocimiento previo ni acceso a la máquina origen"
  checklist:
    - "El backup nocturno (o scripts/backup.sh manual) produce un bundle con tar REAL, verificado por backup_verification"
    - "El bundle se subió automáticamente al destino remoto tras la verificación"
    - "La clave de descifrado y las unseal keys se obtienen EXCLUSIVAMENTE de custodia offsite (nadie consulta la máquina origen)"
    - "scripts/restore.sh en la máquina limpia completa sin editar a mano listas de servicios"
    - "Tras el restore: conectar como app_user y hacer SELECT/INSERT sobre una tabla con RLS funciona; alembic upgrade head como migrations_user no falla"
    - "restore_reconcile no reporta divergencias críticas BD↔MinIO↔Vault↔git"
    - "Los repos git restaurados contienen las ramas plan/* de los planes activos"
    - "Login de un usuario de tenant + ejecución de un plan end-to-end funcionan"
    - "Acta del drill rellenada con tiempos (RTO real) y archivada como evidencia (sirve también para human_12_02 del Plan 12)"

- id: human_prod_04_02
  description: "Un fallo a mitad del restore deja el stack PARADO, no sirviendo datos parciales"
  hint: "Provocar un fallo (p. ej. bundle con checksum corrupto tras la fase de stop) en un entorno de prueba"
  checklist:
    - "El restore aborta con RestoreError que indica el estado alcanzado y el siguiente paso"
    - "docker compose ps muestra el stack parado (o solo postgres) — NO se ha auto-arrancado"
    - "Re-ejecutar el restore con un bundle bueno recupera el sistema"

- id: human_prod_04_03
  description: "Restore por tenant: los objetos restaurados son legibles vía API S3"
  hint: "Restaurar la rebanada de un tenant de prueba y verificar por API, no mirando el filesystem"
  checklist:
    - "Tras el restore por tenant, listar y descargar objetos del tenant vía API S3 funciona"
    - "Los documentos de la KB del tenant se abren desde la UI"
    - "Un fallo en el wipe aborta la operación (no es best-effort silencioso)"

- id: human_prod_04_04
  description: "RPO/RTO y custodia revisados y aprobados por dirección"
  checklist:
    - "04-disaster-recovery.md declara RPO y RTO con cifras; dirección las ha validado por escrito"
    - "Los runbooks ya no afirman que Vault resuelve la clave; queda claro que las unseal keys NO descifran el bundle"
    - "El registro de custodia offsite existe (quién, dónde, fingerprint) y el fingerprint coincide con el del manifest del último backup"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde (incluido el test de humo con tar REAL y el de runner real — no cuentan los verdes del `FakeRunner`).
2. Los 4 tests humanos pass, con el acta del drill (`human_prod_04_01`) archivada como evidencia.
3. ADR «Consistencia del bundle de backup» (y, si procede, ADR «PITR/WAL archiving») creados en `docs/05-architecture-decisions/` y decididos por un humano. **Estado**: el ADR existe ([0149](../05-architecture-decisions/0149-consistencia-del-bundle-de-backup.md), `proposed`) con las tres opciones presupuestadas y las dos decisiones ligadas (¿Redis crítico? ¿`vault_data` dentro del blob?); **falta la decisión**.
4. Runbooks DR (`04-disaster-recovery.md`, `dr-full-restore.md`, `dr-manual-backup.md`, `dr-tenant-restore.md`, `dr-drill.md`) veraces y verificados contra el código.
5. Entrada de changelog en `docs/07-changelog/prod-04-backup-dr-restaurable.md`.
6. PR del plan mergeado a `master` y frontmatter actualizado a `completed`.

## Próximo Plan

**`prod-05-rotacion-claves`** [P0] — Rotación de claves ejecutable: MultiFernet, re-cifrado, dual JWT y job real. Comparte superficie con este plan (custodia y ciclo de vida de secretos): la clave de backup custodiada aquí entra en el inventario de rotación de prod-05, y el drill de DR deberá repetirse (o al menos revisarse) tras la primera rotación de `WORKERS_BACKUP_ENCRYPTION_KEY` para garantizar que los bundles antiguos siguen siendo descifrables (retención de claves históricas).
