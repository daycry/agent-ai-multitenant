---
plan_id: prod-05-rotacion-claves
title: Rotación de claves ejecutable — MultiFernet, re-cifrado, dual JWT y job real
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 13
estimated_cost_human_eur: 5.850 € – 7.800 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P0
---

# Plan prod-05 — Rotación de claves ejecutable: MultiFernet, re-cifrado, dual JWT y job real

## Cabecera

| Campo                              | Valor                                                          |
| ---------------------------------- | -------------------------------------------------------------- |
| **ID del Plan**                    | `prod-05-rotacion-claves`                                      |
| **Prioridad**                      | P0                                                             |
| **Bloqueado por**                  | — (null)                                                       |
| **Tiempo estimado (calendario)**   | 3-4 semanas                                                    |
| **Tiempo estimado (persona-días)** | 13                                                             |
| **Rama git sugerida**              | `plan/prod-05-rotacion-claves`                                 |
| **Origen**                         | Auditoría de producción 2026-06-10 (hallazgos gap2-1 … gap2-7) |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La plataforma **no tiene hoy ningún camino ejecutable para rotar sus claves
de cifrado y firma**. La auditoría de producción confirmó con evidencia
fichero:línea que:

1. El **job de rotación automática de credenciales** (Plan 15) es un no-op:
   `_build_vault_client` devuelve incondicionalmente `FakeVaultRotationClient()`
   (`apps/workers/src/workers/credential_rotation_task.py:48-64`) y el ciclo
   audita `RotationAudit(status=SUCCEEDED)` con `ok: true` sin rotar nada real
   — el runbook canónico dirige la **revocación de emergencia** a este job
   inexistente (gap2-1, critical).
2. Aunque el job escribiera en Vault, **nada propaga el valor rotado**:
   api-server y workers firman/verifican JWT con `API_SERVER_JWT_SECRET` leída
   una vez por proceso (`@lru_cache`, `config.py:418-421`) y nadie lee
   `secret/platform/jwt` ni `platform/minio` en runtime (gap2-2).
3. Las **tres claves Fernet** (SSO/OIDC+MFA+SAML, notificaciones, webhooks
   entrantes) son de **clave única** derivada por SHA-256 de su env var, sin
   `MultiFernet`, sin key-id y sin script de re-cifrado: rotarlas convierte en
   `InvalidToken` todos los secretos almacenados (gap2-3). En particular, rotar
   `API_SERVER_SSO_ENCRYPTION_KEY` invalida los seeds TOTP de todos los
   usuarios y, con `admin_require_mfa=True` (default en staging/prod), **deja a
   los System Admins fuera de `/admin/*`** (gap2-4).
4. La **clave AES-256-GCM de backups** (`WORKERS_BACKUP_ENCRYPTION_KEY`) cifra
   sin key-id en el header y el restore solo deriva de la clave actual: rotar
   sin conservar la anterior deja **ilegibles todos los bundles históricos**
   — pérdida de datos definitiva en un DR (gap2-5).
5. El **JWT no acepta dual old+new**: `decode_jwt` y `decode_agent_token`
   validan contra exactamente un secreto, así que cualquier rotación real corta
   de golpe todas las sesiones y los tokens de agente en vuelo (gap2-7).
6. El **runbook canónico** (`docs/06-runbooks/05-key-rotation.md`) omite 6 de
   las 8 claves de la plataforma y describe verificaciones que el código no
   puede cumplir (gap2-6).

Este plan convierte la rotación de claves en una operación **ejecutable,
verificable y documentada**: MultiFernet con esquema de dos fases + comando de
re-cifrado por tabla, aceptación dual old+new para JWT, binding `hvac` real
para el job (o fallo SKIPPED ruidoso, nunca SUCCEEDED falso), anillo de claves
para bundles de backup históricos y un runbook clave-por-clave cuyas
verificaciones el código realmente cumple.

## Alcance

**Entra**:

- Migrar los 4 builders Fernet a `MultiFernet` con lista ordenada de claves
  por env (`*_ENCRYPTION_KEYS` coma-separada, retro-compatible con la var
  singular actual).
- Comando administrativo de re-cifrado masivo por tabla
  (`sso_configurations`, `user_mfa_totp`, `notification_channels`, configs de
  webhooks entrantes) con dry-run e idempotencia.
- ADR para separar (o no) la clave de los seeds TOTP de la clave SSO, y
  break-glass documentado del lockout MFA.
- `API_SERVER_JWT_SECRETS`: firmar con la primera, verificar contra todas, en
  `decode_jwt`, `decode_agent_token` y workers.
- Adaptador `hvac` real para `VaultRotationClient` + resolver que sin Vault
  vivo devuelve ciclo `SKIPPED` con alerta (nunca `SUCCEEDED` contra el fake).
- Propagación ejecutable de secretos rotados (script de regeneración de env +
  reinicio coordinado) y rotación real de la credencial MinIO en el servicio.
- Key-id en el header de los bundles de backup (formato v2) + anillo de claves
  en restore, retro-compatible con blobs v1.
- Reescritura del runbook `05-key-rotation.md` clave-por-clave + drill de
  rotación e2e.

**Queda fuera**:

- Rotación de las unseal keys de Vault (ya cubierta por
  `dr-vault-unseal-rotation.md`, fuera del alcance de los hallazgos).
- Consumo de credenciales **dinámicas** de Postgres por los servicios (leases
  por request): se documenta el estado real en el runbook, pero cablear leases
  dinámicos en api-server/workers es un plan propio.
- Re-cifrado de bundles de backup históricos ya escritos (el anillo de claves
  los hace legibles; re-cifrarlos en masa no aporta y consume ventana de DR).
- Hardening general de Vault (defaults, políticas): es el plan
  `prod-10-vault-secretos-operables`; aquí solo el binding del job de rotación.

**Coordinación con otros planes de la serie**:

- **prod-04-backup-dr-restaurable**: la tarea `task_prod05_08` (key-id +
  anillo de claves en backups) toca `backup_encryption.py` y `restore.py`,
  ficheros que prod-04 también modifica. Quien aterrice segundo rebasa; el
  contrato es: prod-04 hace el restore ejecutable, prod-05 lo hace
  multi-clave. La regla operativa «conservar toda clave de backup anterior
  junto a los bundles que cifró» se añade aquí Y se referencia desde el
  runbook DR de prod-04.
- **prod-10-vault-secretos-operables**: el adaptador hvac de
  `task_prod05_05` reutiliza el patrón de `HvacLLMProviderVaultStore`
  (`apps/api-server/src/api_server/llm_providers/vault.py:106-111`); si
  prod-10 generaliza ese cliente, este plan consume la pieza común.

## Decisiones clave

- **MultiFernet con lista ordenada por env** (`API_SERVER_SSO_ENCRYPTION_KEYS`,
  `API_SERVER_NOTIFICATION_ENCRYPTION_KEYS`,
  `API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEYS`, coma-separadas): la primera
  clave cifra, todas descifran. La var singular actual sigue siendo válida
  (lista de un elemento) — ningún despliegue existente se rompe. Decisión
  técnica tomada en este plan.
- **Rotación Fernet en dos fases**: (1) añadir clave nueva en cabeza de la
  lista y desplegar → (2) ejecutar el re-cifrado masivo → (3) retirar la clave
  antigua de la lista. El comando de re-cifrado usa `MultiFernet.rotate()` y
  es idempotente (re-ejecutarlo no daña ciphertexts ya migrados).
- **Clave propia para TOTP — ADR propuesto, no decidido aquí**: separar
  `API_SERVER_MFA_ENCRYPTION_KEY` (opción A, recomendada: reduce el blast
  radius de rotar la clave SSO y permite rotaciones independientes) vs
  mantener el acoplamiento documentándolo (opción B: una sola "rotation
  story", menos secretos que custodiar). Se redacta como ADR en
  `docs/05-architecture-decisions/` y lo decide un humano (`task_prod05_03`).
- **Modelo de propagación de secretos rotados — ADR propuesto**: (A) servicios
  leen de Vault en runtime con recarga periódica (más elegante, más invasivo:
  rompe el patrón `@lru_cache` en todo el código) vs (B) la rotación incluye
  el paso ejecutable de regenerar el `.env` + reinicio coordinado de
  api-server y workers en la misma ventana (recomendada para el alcance Docker
  Compose en una sola máquina, y la aceptación dual JWT elimina el corte). El
  plan implementa la opción que el ADR apruebe; las tareas asumen B como base.
- **El job de rotación nunca miente**: sin Vault vivo, el ciclo termina
  `SKIPPED` con alerta vía notifier — un `SUCCEEDED` solo puede salir de un
  cliente hvac real. Un test de regresión lo pinea.
- **Backups: formato v2 con key-id, nunca formato breaking silencioso**: el
  header pasa a `[MAGIC|version=2|key_id(8)|nonce|ciphertext+tag]`; el restore
  reconoce v1 (prueba el anillo completo) y v2 (selecciona por key-id). Los
  blobs v1 siguen siendo legibles para siempre mientras su clave esté en el
  anillo.

## Tareas

### Fase A — Capa Fernet rotable (gap2-3, gap2-4)

#### `task_prod05_01` — Builders MultiFernet con lista de claves

- [x] **Título**: Migrar los 4 builders `Fernet(...)` a `MultiFernet`
- **Descripción**: En `apps/api-server/src/api_server/auth/sso/secrets.py:51-55`,
  `apps/api-server/src/api_server/webhooks/secrets.py:44-48`,
  `apps/api-server/src/api_server/notifications/secrets.py:37-39` y
  `apps/notification-dispatcher/src/notification_dispatcher/secrets.py:45-49`:
  sustituir el `Fernet` único por `MultiFernet([Fernet(k) for k in keys])`,
  donde `keys` sale de una nueva setting lista (`*_ENCRYPTION_KEYS`,
  coma-separada, cada elemento derivado por el mismo SHA-256→urlsafe-b64) con
  fallback a la var singular actual en `config.py:68-85` y `config.py:314-317`.
  Cifrar siempre con la primera; descifrar contra todas. `mfa/secrets.py:18-22`
  y `sso/secrets.py:178-179` (SAML SP key) heredan el cambio al reutilizar el
  builder SSO. Mantener el contrato del par
  `API_SERVER_NOTIFICATION_ENCRYPTION_KEY(S)` == `NOTIFY_NOTIFICATION_ENCRYPTION_KEY(S)`
  con un test que falle si los dos parsers divergen.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_multifernet_builders.py -v"
  # CORREGIDO el 2026-08-19: `tests/integration/test_fernet_rotation_two_keys.py` no ha
  # existido nunca, y no hace falta que exista: la propiedad que iba a probar —«con dos
  # claves en el anillo, lo escrito con la vieja sigue leyéndose»— la prueba
  # `test_multifernet_builders.py` sobre los CUATRO builders a la vez, parametrizado, y
  # sin BD (que es por lo que tampoco es un test de integración). Además fija lo que un
  # roundtrip ingenuo se dejaría: QUÉ clave produjo el token, porque un anillo que
  # descifra con todas pero sigue cifrando con la vieja pasaría en verde y haría que el
  # paso 3 de la rotación destruyese datos.
  # Comprobado que muerde: `MultiFernet([... for raw in ring])` → `ring[:1]` y saltaron
  # tres parametrizaciones de `test_a_token_written_under_the_old_key_survives_adding_a_new_one`
  # (sso, mfa, webhooks). Restaurado con `git show HEAD:… > …`; 30 verdes.
  - id: auto_prod05_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_multifernet_builders.py -k 'survives_adding_a_new_one or notification_secrets_pair_survives' -v"
  ```

#### `task_prod05_02` — Comando de re-cifrado masivo por tabla

- [x] **Título**: CLI `python -m api_server.cli reencrypt-secrets` con dry-run
- **Descripción**: Nuevo comando administrativo (plataforma-global, ejecutado
  por un System Admin dentro del contenedor api-server) que recorre
  `sso_configurations.client_secret_encrypted` (+ clave SAML SP),
  `user_mfa_totp`, `notification_channels` y las configs de webhooks
  entrantes, y aplica `MultiFernet.rotate()` fila a fila. Requisitos:
  `--dry-run` (cuenta filas legibles/ilegibles sin escribir), idempotente,
  transacción por lote, informe final por tabla con conteo de migradas /
  ya-migradas / ilegibles (estas últimas NO abortan el run: se listan por id
  para tratamiento manual), y motor admin que respete el modelo RLS existente
  para tablas con `tenant_id`. Depende de `task_prod05_01`.
- **Tiempo**: 2 días · **Complejidad**: l
- **Dependencias**: `task_prod05_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_reencrypt_secrets_command.py -v"
  ```

#### `task_prod05_03` — ADR clave TOTP propia + break-glass MFA

- [x] **Título**: ADR «clave de cifrado MFA: propia vs acoplada a SSO» +
      procedimiento break-glass documentado
- **Descripción**: Redactar ADR en `docs/05-architecture-decisions/` con las
  opciones A (separar `API_SERVER_MFA_ENCRYPTION_KEY`, recomendada) y B
  (mantener la reutilización de `mfa/secrets.py:4-9` documentada) para que un
  humano decida; implementar la opción aprobada (si A: nueva setting con
  fallback a la clave SSO para no romper despliegues, e inclusión de
  `user_mfa_totp` en el re-cifrado con su propia lista). En el mismo paso,
  documentar el break-glass del lockout admin
  (`API_SERVER_ADMIN_REQUIRE_MFA=false` temporal + re-enrolamiento, ref.
  `config.py:155-158`) en el runbook de `task_prod05_09`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Dependencias**: `task_prod05_01`, `task_prod05_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_mfa_key_rotation_story.py -v"
  ```

### Fase B — Aceptación dual JWT (gap2-7)

#### `task_prod05_04` — `API_SERVER_JWT_SECRETS`: firmar con una, verificar contra todas

- [x] **Título**: Lista ordenada de secretos JWT en api-server y workers
- **Descripción**: Nueva setting lista `API_SERVER_JWT_SECRETS` (coma-separada,
  fallback a `API_SERVER_JWT_SECRET` actual). `encode_jwt`
  (`auth/jwt.py:62-67`) firma siempre con la primera;
  `decode_jwt` (`auth/jwt.py:70-81`) y `decode_agent_token`
  (`auth/internal_agent.py:134-142`) verifican contra todas en orden,
  fallando con `InvalidTokenError` solo si ninguna valida. Aplicar el mismo
  contrato en `apps/workers/src/workers/config.py:58-69` (el worker minta
  agent tokens con la primera clave) para que los `AGENTIC_INTERNAL_TOKEN` ya
  inyectados en agent-runtimes en vuelo (`workers/execution.py:289`) sigan
  validando durante su TTL mientras la clave antigua esté en la lista.
  Documentar el procedimiento de dos fases (añadir nueva → desplegar → retirar
  antigua tras el TTL máximo de token) para `task_prod05_09`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_04_a
    runtime: python-pytest
    command: "pytest tests/unit/test_jwt_dual_secrets.py -v"
  - id: auto_prod05_04_b
    runtime: python-pytest
    command: "pytest tests/unit/test_jwt_dual_secrets.py -v"
  ```

### Fase C — Job de rotación real (gap2-1, gap2-2)

#### `task_prod05_05` — Adaptador hvac real + ciclo SKIPPED ruidoso sin Vault

- [x] **Título**: `HvacVaultRotationClient` y fin del `SUCCEEDED` contra el fake
- **Descripción**: Implementar el adaptador `hvac` real del Protocol
  `VaultRotationClient` (`apps/workers/src/workers/credential_rotation.py:505-559`)
  siguiendo el patrón ya probado de `HvacLLMProviderVaultStore`
  (`apps/api-server/src/api_server/llm_providers/vault.py:106-111`): address +
  token desde settings/env, nunca logueados. Reescribir `_build_vault_client`
  (`credential_rotation_task.py:48-64`): con Vault accesible → cliente real;
  sin Vault → el ciclo retorna `status=SKIPPED` con alerta vía
  `CeleryRotationNotifier`, y `FakeVaultRotationClient` queda SOLO importable
  desde tests. Añadir test de regresión que falle si la task de producción
  reporta `SUCCEEDED` con el cliente fake.
- **Tiempo**: 1,5 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_05_a
    runtime: python-pytest
    command: "pytest tests/unit/test_vault_rotation_client_hvac.py -v"
  # CORREGIDO el 2026-08-19: `tests/integration/test_rotation_never_succeeds_on_fake.py`
  # no ha existido nunca. El «test de regresión que falle si la task de producción reporta
  # SUCCEEDED con el cliente fake» que pide la descripción SÍ está escrito, dentro del
  # mismo fichero que `_a` y con ese nombre casi literal:
  # `test_a_skipped_cycle_never_reports_succeeded`. No es de integración porque no lo
  # necesita —Vault y MinIO están tras seams y los dobles registran el ORDEN de llamadas,
  # que es la propiedad que importa— y el fichero fija además la otra mitad, la que un
  # test de comportamiento solo no protege: `test_the_production_module_does_not_even_import_the_fake`,
  # una guarda estática sobre el fuente para el día que alguien reintroduzca el fallback
  # con otro nombre.
  # Comprobado que muerde: `RotationStatus.SKIPPED` → `SUCCEEDED` en `_skipped_summary` y
  # cayeron las dos (`test_a_skipped_cycle_never_reports_succeeded` y
  # `test_a_cycle_without_vault_is_skipped_alerted_and_not_ok`). Restaurado con
  # `git show HEAD:… > …`; 19 verdes.
  - id: auto_prod05_05_b
    runtime: python-pytest
    command: "pytest tests/unit/test_vault_rotation_client_hvac.py -k 'never_reports_succeeded or skipped_alerted_and_not_ok or does_not_even_import_the_fake' -v"
  ```

#### `task_prod05_06` — ADR modelo de consumo + propagación ejecutable con reinicio coordinado

- [x] **Título**: ADR «Vault runtime vs regenerar env + reinicio» y script
      `scripts/rotate-platform-secret.sh`
  - ✅ **Cerrada (2026-08-10):** el ADR ya estaba (`0144`, `accepted`, opción B);
    lo que faltaba era el automatismo, y ahora existe:
    **`scripts/rotate-platform-secret.sh`** (`jwt` | `minio`) hace las cuatro
    cosas que la tarea pedía —leer el valor de `secret/platform/<n>`, **anteponer**
    la clave nueva a `API_SERVER_JWT_SECRETS` conservando la anterior, reescribir
    el `.env` de forma atómica y reiniciar en la misma ventana— y **sólo después**
    del reinicio revoca lo anterior, vía el llamante que también faltaba:
    **`apps/workers/src/workers/rotation_apply.py`**
    (`python -m workers.rotation_apply --revoke-previous-minio`, síncrono).
    `revoke_previous_minio_credential` existía desde `task_prod05_07` **sin una
    sola forma de invocarla**, y el runbook la nombraba como si fuese ejecutable.
  - **Tests**: `tests/unit/test_rotate_platform_secret_script.py` (11, verdes) con
    un shim de `docker` en el PATH — pinea los dos invariantes caros: que la clave
    se **antepone** (sustituirla corta todas las sesiones en vuelo) y que la
    revocación de MinIO ocurre **después** del reinicio (invertirlo deja la
    plataforma sin object storage, riesgo 4). Más
    `tests/unit/test_rotation_apply_cli.py` (3, verdes): sin Vault o sin credencial
    admin de MinIO el comando **falla** en vez de fingir que cerró la ventana.
  - **Desviación del plan**: el test se llamaba
    `tests/integration/test_rotation_propagation_cycle.py`. Vive en `tests/unit/`
    porque no necesita Postgres ni Redis: lo que verifica es el ORDEN de las
    operaciones y el `.env` resultante, con `docker` doblado. Un test de
    integración de verdad tendría que reiniciar contenedores, y eso es
    `human_prod05_01`.
  - **Sigue manual a propósito**: la retirada de la clave JWT vieja (paso 3 del
    runbook §1) depende del TTL máximo de token en vuelo. Es una decisión con
    reloj, no un efecto secundario.
- **Descripción**: Redactar el ADR (opciones A/B de «Decisiones clave») y,
  asumida la opción B (Docker Compose, una máquina), implementar el script que
  cierra el ciclo de gap2-2: lee el valor rotado de `secret/platform/jwt` /
  `platform/minio` en Vault KV, regenera las entradas del `.env` de
  api-server y workers (en el caso JWT: ANTEPONE la clave nueva a
  `API_SERVER_JWT_SECRETS` conservando la antigua, apoyándose en
  `task_prod05_04`) y reinicia los servicios afectados en la misma ventana
  (`docker compose up -d api-server workers...`). El job de rotación
  (`task_prod05_05`) marca el secreto como `PENDING_APPLY` en su auditoría
  hasta que la propagación confirma; la "verificación sin reinicio" del
  runbook actual (`05-key-rotation.md:174-183`) se elimina.
- **Tiempo**: 2 días · **Complejidad**: l
- **Dependencias**: `task_prod05_04`, `task_prod05_05`
- **Tests automáticos**:
  ```yaml
  # CORREGIDO el 2026-08-19: el propio bloque de arriba ya documentaba la desviación
  # («el test se llamaba tests/integration/test_rotation_propagation_cycle.py … vive en
  # tests/unit/ porque no necesita Postgres ni Redis»), pero NADIE bajó a corregir el
  # `command:`, así que la casilla marcada seguía declarando un fichero inexistente. Es el
  # patrón que este arreglo persigue: la prosa se actualiza y el yaml no.
  - id: auto_prod05_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_rotate_platform_secret_script.py tests/unit/test_rotation_apply_cli.py -v"
  ```

#### `task_prod05_07` — Rotación MinIO real en el servicio

- [x] **Título**: `rotate_static_secret('minio')` cambia la credencial en MinIO,
      no solo en KV
  - ✅ **Cerrada (2026-08-01):** el cableado ya existía (`MinioServiceAccountRotator`
    - `_rotate_minio` + `revoke_previous_minio_credential`); lo que faltaba era el
      test que el plan pedía, y **contra MinIO de verdad**, no contra un doble:
      `tests/integration/test_minio_rotation_applies_to_service.py` (7 tests, verdes
      contra el MinIO del compose en `localhost:9000`). Assertan lo que un doble no
      puede: que la credencial acuñada **autentica**, que la revocada **deja de
      autenticar** y que entre el paso 2 y el 4 conviven las dos. Encontró un defecto
      real: `revoke()` prometía idempotencia en el Protocol y no la tenía — MinIO
      responde `404 XMinioInvalidIAMCredentials` al borrar una service account
      ausente, así que un reintento del paso 4 tras una propagación a medias
      explotaba y dejaba `pending_apply=true` para siempre. Arreglado en
      `credential_rotation_hvac._is_minio_not_found` + 3 tests unitarios que corren
      sin MinIO.
- **Descripción**: Hoy el Protocol solo escribe un valor nuevo en KV v2
  (gap2-2): MinIO sigue aceptando la credencial vieja y los servicios usan la
  vieja de su env. Extender el paso MinIO del ciclo para invocar la API de
  administración de MinIO (service account / `mc admin`) creando la credencial
  nueva ANTES de escribir KV, y revocando la antigua solo tras la propagación
  de `task_prod05_06` (patrón add-then-remove, sin ventana de corte). Si la
  API de admin no es alcanzable, el paso falla ruidoso (nunca KV actualizado
  con servicio desincronizado).
- **Tiempo**: 1 día · **Complejidad**: m
- **Dependencias**: `task_prod05_05`, `task_prod05_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_minio_rotation_applies_to_service.py -v"
  ```

### Fase D — Clave de backups con anillo (gap2-5)

#### `task_prod05_08` — Key-id en el header de bundle + anillo de claves en restore

- [x] **Título**: Formato v2 `[MAGIC|v2|key_id|nonce|ct+tag]` y
      `WORKERS_BACKUP_ENCRYPTION_KEYS`
- **Descripción**: En `apps/workers/src/workers/backup_encryption.py`:
  (1) bump `_FORMAT_VERSION` a 2 añadiendo al header un key-id de 8 bytes
  (primeros bytes de SHA-256 de la clave derivada, líneas 61-63 y 114-125);
  (2) `BackupEncryptor` pasa de UNA clave cacheada (139-160) a un anillo
  ordenado alimentado por `WORKERS_BACKUP_ENCRYPTION_KEYS` (fallback a la var
  singular): cifra con la primera, descifra seleccionando por key-id (v2) o
  probando el anillo completo (blobs v1 legados); (3) `restore.py:140` y
  `restore_per_tenant.py` consumen el anillo. Añadir al runbook DR la regla
  «conservar toda clave de backup anterior junto a los bundles que cifró»
  (coordinación con prod-04, ref. `docs/06-runbooks/dr-full-restore.md:36`).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_08_a
    runtime: python-pytest
    command: "pytest tests/unit/test_backup_encryption_keyring.py -v"
  # CORREGIDO el 2026-08-19: `tests/integration/test_restore_v1_blob_after_rotation.py` no
  # ha existido nunca. Lo que iba a probar —«un blob v1 escrito ANTES del cambio sigue
  # restaurando tras rotar»— es la aserción nº 1 del fichero de `_a`, y allí está mejor
  # construida de lo que habría estado suelta: la cabecera v1 se fabrica a mano desde la
  # spec de AES-GCM y no desde el módulo bajo prueba, así que el test seguiría siendo
  # significativo aunque las constantes del módulo estuviesen mal.
  # Comprobado que muerde: en la rama v1 de `decrypt_bytes`, `for key in ring` →
  # `for key in ring[:1]` (o sea, «solo la clave cabeza descifra») y saltó
  # `test_a_version_1_bundle_still_restores_after_the_rotation` con el mensaje del propio
  # módulo («no key in the ring (2 configured) decrypts it»). Restaurado con
  # `git show HEAD:… > …`; 16 verdes.
  - id: auto_prod05_08_b
    runtime: python-pytest
    command: "pytest tests/unit/test_backup_encryption_keyring.py -k 'version_1_bundle or retired_key' -v"
  ```

### Fase E — Runbook veraz y drill (gap2-6)

#### `task_prod05_09` — Reescribir `docs/06-runbooks/05-key-rotation.md` clave-por-clave

- [x] **Título**: Tabla exhaustiva de las 8 claves con procedimiento ejecutable
      y rollback
- **Descripción**: Reescritura completa con una tabla por clave — JWT, MinIO,
  `API_SERVER_SSO_ENCRYPTION_KEY(S)` (SSO+MFA+SAML),
  `API_SERVER_NOTIFICATION_ENCRYPTION_KEY(S)` (par con
  `NOTIFY_...`, `config.py:84`), `API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY(S)`,
  `WORKERS_BACKUP_ENCRYPTION_KEY(S)`, `API_SERVER_REVIEW_URL_SIGNING_SECRET` y
  la password estática de Postgres (`config.py:26-39`) — con: dónde vive, qué
  cifra/firma, blast radius, procedimiento paso a paso (apoyado en las tareas
  A-D), verificación QUE EL CÓDIGO CUMPLE y rollback. Marcar explícitamente
  «SIN CAMINO DE ROTACIÓN — no rotar» lo que siga sin mecanismo (p.ej. la
  password de Postgres hasta que haya plan propio). Corregir la sección de
  revocación de emergencia (`05-key-rotation.md:217-225`) para que no dependa
  del job mientras este no esté cableado, e incluir el break-glass MFA de
  `task_prod05_03`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Dependencias**: `task_prod05_03`, `task_prod05_06`, `task_prod05_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_runbook_key_rotation_lint.py -v  # valida que cada env var *_KEY(S) del config aparece en el runbook"
  ```

#### `task_prod05_10` — Drill de rotación e2e en entorno dev

- [x] **Título**: Suite de drill que rota cada clave y verifica supervivencia
  - ✅ **Cerrada (2026-08-01):** vive en `tests/integration/test_key_rotation_drill.py`
    (no en `tests/e2e/`: necesita el Postgres de compose y las fixtures de
    integración, no el stack entero). **12 tests verdes**, ejecutados. Cubre las
    cuatro fases que la tarea pedía: (a) JWT en dos fases con sesión y agent token
    en vuelo, (b) las cuatro familias Fernet con `reencrypt-secrets` en medio y la
    clave vieja RETIRADA al verificar, (c) restauración de un bundle escrito bajo
    la clave retirada —incluido un blob v1 sin key-id—, y (d) el job sin Vault →
    `SKIPPED` + alerta, con Vault → `SUCCEEDED` + `pending_apply` → revocación
    sólo en el paso explícito. El propio fichero declara lo que NO prueba (no
    reinicia contenedores, no habla con Vault ni MinIO reales), que es lo que
    queda para los tests humanos; el lado MinIO real lo cubre ahora
    `task_prod05_07`.
- **Descripción**: Test de integración (compose dev) que ejecuta el ciclo
  completo: (a) rotar JWT en dos fases y verificar que una sesión emitida
  antes sigue válida y un agent token en vuelo valida contra
  `/internal/agent/*`; (b) añadir clave Fernet nueva, re-cifrar con el CLI de
  `task_prod05_02`, retirar la antigua y verificar login TOTP + descifrado de
  secretos SSO/canales/webhooks; (c) rotar la clave de backup y restaurar un
  bundle cifrado con la clave anterior; (d) lanzar el job de rotación sin
  Vault → `SKIPPED` + alerta, y con Vault dev → `SUCCEEDED` real con
  `PENDING_APPLY`→aplicado.
- **Tiempo**: 1,5 días · **Complejidad**: l
- **Dependencias**: todas las anteriores
- **Tests automáticos**:
  ```yaml
  - id: auto_prod05_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_key_rotation_drill.py -v"
  ```

## Hallazgos de auditoría cubiertos

| fid    | Severidad | Tarea(s) que lo cierran                              |
| ------ | --------- | ---------------------------------------------------- |
| gap2-1 | critical  | `task_prod05_05`, `task_prod05_09`                   |
| gap2-2 | high      | `task_prod05_06`, `task_prod05_07`, `task_prod05_09` |
| gap2-3 | high      | `task_prod05_01`, `task_prod05_02`, `task_prod05_09` |
| gap2-4 | high      | `task_prod05_02`, `task_prod05_03`, `task_prod05_09` |
| gap2-5 | high      | `task_prod05_08`, `task_prod05_09` (coord. prod-04)  |
| gap2-6 | medium    | `task_prod05_09`, `task_prod05_10`                   |
| gap2-7 | medium    | `task_prod05_04`, `task_prod05_06`, `task_prod05_10` |

## Riesgos

1. **Re-cifrado masivo interrumpido a mitad** → estado mixto en BD. Mitigado
   por diseño: con MultiFernet ambas claves descifran, así que un estado mixto
   es legible y el comando es idempotente; aun así, ejecutar con backup previo.
2. **Retirar la clave JWT antigua antes del TTL máximo de agent token** corta
   ejecuciones de planes en vuelo. El runbook debe fijar la ventana mínima
   (TTL máximo de token + margen) y el drill (`task_prod05_10`) la verifica.
3. **Bundles históricos cuya clave ya se perdió** son irrecuperables: este
   plan protege hacia delante; no puede resucitar claves no conservadas.
   Riesgo residual a aceptar explícitamente en el runbook.
4. **Rotación MinIO con add-then-remove mal ordenado** (revocar antes de
   propagar) corta el object storage de toda la plataforma. La auditoría
   `PENDING_APPLY` y el fallo ruidoso de `task_prod05_07` lo acotan, pero el
   drill debe cubrir el orden.
5. **Conflictos de merge con prod-04** en `backup_encryption.py`/`restore.py`
   y runbooks DR. Mitigación: coordinación explícita declarada en Alcance;
   quien aterrice segundo rebasa y re-corre los tests de ambos planes.
6. **Divergencia del par notification-dispatcher / api-server** al migrar a
   listas de claves (dos parsers en dos apps): un despliegue que actualice una
   sola app rompería el par write/read. El test `auto_prod05_01_a` pinea la
   simetría y el runbook ordena desplegar ambos en la misma ventana.

## Tests humanos del Plan

```yaml
- id: human_prod05_01
  description: "Rotación JWT en dos fases sin corte de sesiones"
  hint: "Seguir el runbook reescrito, sección JWT, en el entorno dev"
  checklist:
    - "Login en /admin antes de rotar; anotar el token"
    - "Añadir clave nueva en cabeza de API_SERVER_JWT_SECRETS (api-server Y workers) y reiniciar con scripts/rotate-platform-secret.sh"
    - "La sesión previa sigue funcionando (sin re-login)"
    - "Un plan con ejecución en curso termina sin errores 401 en /internal/agent/*"
    - "Tras el TTL, retirar la clave antigua: los tokens viejos ya no validan"

- id: human_prod05_02
  description: "Rotación de la clave Fernet SSO sin lockout MFA"
  hint: "Usuario con TOTP enrolado antes de empezar"
  checklist:
    - "Añadir clave nueva a API_SERVER_SSO_ENCRYPTION_KEYS y desplegar"
    - "Ejecutar reencrypt-secrets --dry-run y revisar el informe (0 ilegibles)"
    - "Ejecutar el re-cifrado real; retirar la clave antigua; redeploy"
    - "Login con TOTP funciona; token-exchange OIDC funciona"
    - "El break-glass documentado (ADMIN_REQUIRE_MFA=false temporal) es reproducible"

- id: human_prod05_03
  description: "Backup cifrado con clave vieja restaurable tras rotar"
  checklist:
    - "Crear backup cifrado, rotar WORKERS_BACKUP_ENCRYPTION_KEYS (nueva en cabeza, vieja conservada)"
    - "Crear segundo backup (formato v2 con key-id)"
    - "Restaurar AMBOS bundles con éxito"
    - "Quitar la clave vieja del anillo → el bundle viejo falla con error claro (no InvalidTag críptico)"

- id: human_prod05_04
  description: "El job de rotación ya no miente"
  checklist:
    - "Con Vault parado: lanzar workers.rotate_credentials → auditoría SKIPPED + alerta recibida (nunca SUCCEEDED)"
    - "Con Vault dev vivo: el job rota minio/jwt de verdad (versión KV avanza, credencial MinIO nueva activa)"
    - "El runbook de revocación de emergencia ejecutado tal cual funciona de punta a punta"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. Los 4 tests humanos del plan validados por un humano.
3. Los dos ADR (`task_prod05_03`, `task_prod05_06`) decididos por un humano y
   la opción aprobada implementada.
4. Runbook `05-key-rotation.md` revisado: ninguna verificación que el código
   no cumpla, ninguna clave de la plataforma sin entrada.
5. Entrada de changelog en `docs/07-changelog/prod-05-rotacion-claves.md`.
6. PR del plan mergeado a `master`.

## Próximo Plan

Con prod-01 … prod-05 (los cinco P0) cerrados, la serie continúa con los P1:

- **`prod-06-ciclo-vida-ejecucion`** — Ciclo de vida de ejecución robusto:
  DAG, zombis, cancelación y budgets. Se beneficia directamente de este plan:
  la rotación JWT con dual-accept evita matar ejecuciones en vuelo, y la
  cancelación limpia de prod-06 hace seguro drenar antes de retirar claves.
