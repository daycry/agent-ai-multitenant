---
plan_id: prod-10-vault-secretos-operables
title: Vault operable y secretos sin defaults conocidos
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
priority: P1
---

# Plan prod-10 — Vault operable y secretos sin defaults conocidos

## Cabecera

| Campo                              | Valor                                             |
| ---------------------------------- | ------------------------------------------------- |
| **ID del Plan**                    | `prod-10-vault-secretos-operables`                |
| **Prioridad**                      | P1                                                |
| **Bloqueado por**                  | — (independiente; coordina con prod-01 y prod-08) |
| **Tiempo estimado (calendario)**   | 3-4 semanas                                       |
| **Tiempo estimado (persona-días)** | 13                                                |
| **Rama git sugerida**              | `plan/prod-10-vault-secretos`                     |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría de producción (2026-06-10) confirma que la gestión de secretos tiene buena higiene en git (nada comiteado, SecretStr, credenciales LLM solo en Vault) pero **falla en operación real**:

1. **El root token y las 5 unseal keys REALES de Vault llevan ≈3 semanas en claro** en `vault-init-output/` dentro del working tree (secrets-1, deploy-11), accesibles a cualquier proceso — incluidos los agentes IA que trabajan sobre este repo — y `.dockerignore` no los excluye del contexto de build (quality-3).
2. **El guard anti-defaults es fail-open**: `environment` por defecto es `"dev"`, así que un despliegue que olvide `API_SERVER_ENVIRONMENT` corre con la JWT secret pública de GitHub (secrets-3).
3. **Vault no es operable sin un humano**: token estático sin renovación que caducará (~32 días) tirando credenciales LLM/MCP (secrets-4), y tras cada reinicio del host Vault queda SELLADO mientras su healthcheck lo reporta healthy y las apps arrancan creyéndolo operativo (secrets-5, deploy-8).
4. **Defaults conocidos y servicios sin auth**: el compose base cae en silencio a contraseñas publicadas (`changeme-dev-only`) si falta la env (secrets-6), Redis aloja sesiones y broker Celery sin password y en dev se publica a toda la LAN (secrets-7), y los secretos de SSO/notificaciones/webhooks pueden vivir cifrados en Postgres con clave derivada de env, contradiciendo el principio "Vault es la única vía" (secrets-8).

Este plan **contiene primero** (retirar los secretos del working tree, revocar el token expuesto), **cierra el fail-open** (entorno y credenciales obligatorios), y **hace Vault operable** (renovación de token, estrategia de unseal, healthcheck honesto) dejando la decisión Fernet-vs-Vault formalizada en ADR.

## Alcance

**Entra**:

- Retirada y custodia offsite de `vault-init-output/`, revocación del root token expuesto y re-emisión controlada.
- `scripts/init-vault.sh` sin persistencia en claro (cifrado age/gpg u opción print-once).
- Guard de CI/pre-commit que falle si `vault-init-output/` existe con contenido; `.dockerignore` ampliado (`vault-init-output/`, `.env*`, `*.log`).
- Guard de entorno **fail-closed** en `apps/api-server/src/api_server/config.py` (y configs espejo de workers/orchestrator/notification-dispatcher): `environment` sin default mágico para secretos + chequeo de entropía mínima.
- Eliminación de fallbacks `:-changeme-dev-only` en `docker/docker-compose.yml` y `docker-compose.monitoring.yml` (sintaxis `${VAR:?msg}`); los fallbacks quedan SOLO en `docker-compose.dev.yml`.
- Autenticación de Redis (`requirepass`) y bind `127.0.0.1` de los puertos publicados por el overlay dev.
- Renovación automática del token de Vault (o AppRole) en todos los servicios que leen Vault + minteo de tokens por servicio.
- Estrategia de unseal post-reinicio: ADR (auto-unseal vs runbook+alerta) + healthcheck/probe que distinga `sealed` de `healthy` + alerta.
- ADR sobre el cifrado Fernet-en-DB vs Vault para SSO/notificaciones/webhooks + implementación de la opción elegida.
- Runbooks actualizados: `restart-services.md` (unseal primer paso post-reboot), `05-key-rotation.md` (rotación token Vault), enlace a `dr-vault-unseal-rotation.md`.

**Queda fuera**:

- El compose generado por el installer y el bug de variables sin prefijo (secrets-2) → **prod-01-despliegue-ejecutable**. Coordinación: las tareas C de este plan definen QUÉ tokens/envs deben existir; prod-01 los cablea en el compose generado.
- Rotación de claves Fernet/JWT en sí (MultiFernet, dual JWT) → **prod-05-rotacion-claves**.
- Cadena de alertas Prometheus/Alertmanager completa → **prod-08-observabilidad-alertas**. Aquí solo se exponen la métrica/probe de Vault sealed y la regla de alerta concreta.
- Separación del dominio criptográfico JWT api-server/workers (secrets-9) → **prod-09-sesiones-autorizacion-frontend**.
- Lockfiles y pin por digest (quality-5) → **prod-11-cadena-suministro** (aquí solo se amplía `.dockerignore`).

## Decisiones clave

1. **Estrategia de unseal tras reinicio** (requiere ADR propuesto, decide un humano):
   - **Opción A — Auto-unseal con transit seal** contra un segundo Vault mínimo (otro contenedor con su propia clave): elimina la intervención humana pero añade un componente más a operar y mueve el problema de custodia a la clave del transit Vault.
   - **Opción B — Auto-unseal con KMS cloud** (AWS/Azure/GCP): el estándar, pero exige conectividad permanente saliente y cuenta cloud — puede chocar con despliegues on-prem aislados.
   - **Opción C — Unseal manual + alerta + healthcheck honesto** (runbook como primer paso post-reboot, alerta "Vault sealed" en monitoring y `/admin/system-health`): cero componentes nuevos, RTO depende del humano de guardia.
   - **Recomendación**: Opción C como mínimo viable de este plan (tareas C) y dejar A/B evaluadas en el ADR para cuando haya requisito de RTO < 15 min. El plan implementa C; si el humano elige A o B, la tarea `task_prod10_09` se reescala.
2. **Autenticación de servicios contra Vault**: AppRole (role_id/secret_id por servicio) vs tokens periódicos renovables. **Recomendación**: tokens periódicos (`period=72h`) + `renew_self` en background ahora — cambio pequeño sobre el `hvac.Client` ya cacheado — y AppRole como evolución en el ADR. Lo decide el ADR de `task_prod10_07`.
3. **Fernet-en-DB vs Vault para SSO/notificaciones/webhooks** (secrets-8): es una decisión de producto/arquitectura cerrable solo por humano porque contradice el principio del CLAUDE.md ("Vault es la única vía"). El plan redacta el ADR con dos opciones (migrar a Vault y degradar el camino Fernet a error 503 como hace el flujo LLM; o bendecir la excepción documentándola y cifrando los backups con clave separada) y **recomienda la primera**. La implementación (`task_prod10_12`) ejecuta lo que el ADR apruebe.
4. **Fail-closed sin romper dev**: el guard pasa a exigir secretos explícitos cuando `environment ∈ {staging, prod}` **y además** cuando `environment` no está seteado explícitamente y el bind no es localhost. En dev local nada cambia (`scripts/dev/up.ps1` ya exporta los valores). No se exige Vault en dev.

## Tareas

### Fase A — Contención inmediata (custodia y working tree limpio)

#### `task_prod10_01` — Retirar `vault-init-output/`, revocar y re-emitir el root token

- [ ] **Título**: Custodia offsite de unseal keys, revocación del root token expuesto y re-init controlado
  - 🚫 **NO la puede cerrar un agente, y sigue ABIERTA (2026-08-10).** Exige a una
    persona con los fragmentos de Shamir: repartir custodias, aportar el umbral de
    3 de 5 para `vault operator generate-root` y hacer un borrado seguro en la
    máquina. Marcarla sería mentir.
  - **Verificado hoy, no supuesto**: `.venv/Scripts/python.exe scripts/check_no_secret_artifacts.py`
    sale en **rojo con 5 artefactos** — `vault-init-output/{init-response.json,
root-token.txt,unseal-keys.txt}`, dos con material `hvs.`, en disco desde el
    **2026-05-20**. El hallazgo secrets-1 está VIVO.
  - **Lo que se entrega en su lugar**: el procedimiento exacto, paso a paso, en
    [`docs/06-runbooks/dr-vault-unseal-rotation.md`](../06-runbooks/dr-vault-unseal-rotation.md)
    §«Incidente abierto», con el orden que importa (acuñar tokens de servicio
    **antes** de revocar el root, para no dejar la plataforma sin secretos), el
    borrado seguro en Windows (`sdelete`, porque `shred` no existe), el comando de
    verificación de que la revocación surtió efecto (403 con el token viejo) y lo
    que el procedimiento **no** arregla (las unseal keys no se revocan, se rotan).
  - **Falta del humano**: las custodias físicas/organizativas, el umbral de
    Shamir y una ventana de mantenimiento. Nada más — el resto ya está: el script
    de init ya no escribe en claro (`task_prod10_02`), el gate lo detecta
    (`task_prod10_03`) y los tokens por servicio existen (`task_prod10_08`).
- **Descripción**: Ejecutar (humano + asistido) los pasos que el propio `scripts/init-vault.sh:134-147` instruye y nunca se aplicaron: (1) mover las 5 unseal keys a custodias separadas (gestor corporativo / sobres), (2) guardar el root token NUEVO en gestor de contraseñas, (3) borrado seguro de `vault-init-output/` (en Windows: sobrescribir antes de borrar). Como el token actual (`hvs.zAntQ…`, en disco desde 2026-05-20) lleva semanas expuesto: `vault token revoke` del actual y regeneración vía unseal keys (`vault operator generate-root`). Documentar la operación realizada en `docs/06-runbooks/dr-vault-unseal-rotation.md` (sección "incidente 2026-06").
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_repo_hygiene.py::test_vault_init_output_absent -v"
  ```

#### `task_prod10_02` — `init-vault.sh` sin persistencia en claro

- [x] **Título**: El script de init cifra (age/gpg) o imprime una sola vez; nunca deja `*.txt` en claro
- **Descripción**: Reescribir el bloque de persistencia de `scripts/init-vault.sh:59-90`: por defecto, cifrar `init-response.json` a una clave pública del operador (`age -r` o `gpg -e`, recipient por env `VAULT_INIT_RECIPIENT`, fail-fast si falta) y NO escribir `unseal-keys.txt`/`root-token.txt` en claro; modo alternativo `--print-once` que emite las claves solo por stdout. El unseal (líneas 95-104) pasa a leer del JSON descifrado en memoria/pipe, no de fichero en claro. Mantener idempotencia y el flujo `docker compose exec`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Dependencias**: ninguna (paralelo a `task_prod10_01`)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_init_vault_script.py -v"
  ```

#### `task_prod10_03` — Guard de CI/pre-commit + `.dockerignore` ampliado

- [x] **Título**: CI falla si `vault-init-output/` existe con contenido; `.dockerignore` excluye secretos del contexto de build
- **Descripción**: (1) Añadir a `.dockerignore` (hoy 39 líneas, sin mención a secretos): `vault-init-output/`, `.env`, `.env.*`, `*.log` — crítico porque `.github/workflows/ci.yml:373` construye los agent-runtimes con la raíz del repo como contexto. (2) Step de CI + hook pre-commit (`scripts/check_no_secret_artifacts.py`) que falle si `vault-init-output/` existe con ficheros, o si aparece un fichero que matchee `hvs\.[A-Za-z0-9]+` fuera de tests. Coordinación: prod-11 (cadena de suministro) reutiliza este step.
- **Tiempo**: 4 h · **Complejidad**: s
- **Dependencias**: `task_prod10_01` (el working tree debe estar limpio antes de activar el gate)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_03_a
    runtime: python-pytest
    command: "pytest tests/unit/test_repo_hygiene.py::test_dockerignore_excludes_secret_artifacts -v"
  ```

### Fase B — Fail-closed: sin defaults conocidos en ningún camino

#### `task_prod10_04` — Guard de entorno fail-closed + entropía mínima

- [x] **Título**: Sin `environment` explícito no hay secretos default; marcador-substring complementado con longitud/entropía
  - ✅ **Cerrada (2026-08-10):** la primera mitad ya estaba (el guard sólo confía
    en un `dev` NO declarado si el DSN es local, `config.py:_forbid_dev_secrets_outside_dev`);
    la **segunda no**, y el propio código llevaba escrita la nota de que faltaba.
    Entregada hoy: `_trivial_secret_reason` + `_ENTROPY_CHECKED_FIELDS` en
    `apps/api-server/src/api_server/config.py`. Suelo de **24 caracteres**, ≥8
    caracteres distintos y ≥2 bits/carácter de entropía de Shannon, sobre el
    **anillo entero** de las ocho familias (JWT, token interno, review-url, SSO,
    notificaciones, webhooks, MinIO y MFA cuando es dedicada).
  - **Por qué dos criterios y no uno**: «distintos» a secas lo esquiva
    `"a"*40 + "bcdefghi"` (9 distintos, relleno igual); Shannon a secas es más
    difícil de explicar en un mensaje de error. Los dos umbrales están
    deliberadamente bajos: `secrets.token_urlsafe(36)` —lo que genera el
    instalador— pasa con seis veces de margen. El riesgo 2 del plan (romper
    arranques reales) pesa más que cazar contraseñas mediocres.
  - **Ámbito acotado a `staging`/`prod` declarados**: el camino «dev implícito +
    BD remota» sigue rechazando sólo lo inequívoco (un marcador de dev).
    Endurecerlo ahí convertiría un olvido de variable en una caída de arranque.
  - **Tests**: `tests/unit/test_secret_entropy_guard.py` — 37 verdes, de los que
    21 son nuevos, incluidos los contrapesos (el secreto del instalador pasa; una
    passphrase humana pasa; dev intacto) y una **guarda de descubrimiento** que
    falla si mañana se añade una familia de secretos sin suelo.
  - **Radio de explosión, medido**: puso en rojo 20 tests de 5 ficheros cuyos
    helpers fingían secretos con `"x" * 48` — que es literalmente el caso que el
    guard rechaza. Arreglados sustituyéndolos por valores deterministas de alta
    entropía (`_fake_secret` = hex de SHA-256) en `test_config_fail_closed.py`,
    `test_security_headers_middleware.py`, `test_settings_prod_validation.py`, y
    alargando los literales cortos de `test_jwt_dual_secrets.py` y
    `test_multifernet_builders.py`. Suite unit completa: **4326 verdes**.
  - **Pendiente reconocido**: el guard vive en el api-server. Los Settings de
    workers/orchestrator/notification-dispatcher tienen el marcador-substring pero
    NO el suelo de entropía; sus secretos propios son el DSN y el token de Vault,
    que no son familias de este suelo. Replicarlo allí es trabajo menor y no se
    ha hecho.
- **Descripción**: En `apps/api-server/src/api_server/config.py`: (1) `environment` deja de tener default silencioso a efectos del guard — `_forbid_dev_secrets_outside_dev` (líneas 371-407) se invierte: los secretos con default dev (`jwt_secret:42`, `review_url_signing_secret:56`, `sso_encryption_key:68`, `notification_encryption_key:81`, `incoming_webhook_encryption_key`, `minio_secret_key`) solo se aceptan si `environment` fue seteado EXPLÍCITAMENTE a `dev` (detectable vía env var presente) o el bind es localhost; en cualquier otro caso el arranque falla con mensaje accionable. (2) Complementar `_DEV_SECRET_MARKERS` (línea 19) con un mínimo de 24 caracteres y rechazo de valores de entropía trivial para los secretos HMAC/Fernet en staging/prod. Replicar el patrón en los Settings de workers, orchestrator y notification-dispatcher. Coordinación: prod-01 garantiza que el installer SÍ pasa `API_SERVER_ENVIRONMENT` (secrets-2); este guard es la red si no lo hace.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_04_a
    runtime: python-pytest
    command: "pytest tests/unit/test_config_fail_closed.py -v"
  - id: auto_prod10_04_b
    runtime: python-pytest
    command: "pytest tests/unit/test_secret_entropy_guard.py -v"
  ```

#### `task_prod10_05` — Compose base sin fallbacks a contraseñas conocidas

- [x] **Título**: `${VAR:?msg}` para toda credencial en `docker-compose.yml` y `docker-compose.monitoring.yml`
- **Descripción**: Aplicar al compose canónico la misma disciplina que ya implementa `_env_ref` del installer (`compose_generator.py:190-201`): `POSTGRES_PASSWORD` (docker-compose.yml:74), `MIGRATIONS_USER_PASSWORD`/`APP_USER_PASSWORD` (78-79), `MINIO_ROOT_PASSWORD` (137) y `GRAFANA_ADMIN_PASSWORD` (monitoring.yml:193) pasan a `${VAR:?set in .env}` sin fallback. Los defaults dev se mueven a `docker-compose.dev.yml` (overlay), y `docker/.env.example` documenta cada variable obligatoria. Verificar que `scripts/dev/up.ps1` sigue funcionando con el overlay.
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_05_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_no_default_credentials.py -v"
  ```

#### `task_prod10_06` — Redis con `requirepass` + binds localhost en el overlay dev

- [x] **Título**: Autenticación en Redis y puertos dev no expuestos a la LAN
  - ✅ **Cerrada (2026-08-10), verificada contra el compose, no supuesta:** el
    servicio `redis` de `docker/docker-compose.yml` arranca con
    `--requirepass ${REDIS_PASSWORD:?…}` (sin fallback), su healthcheck se
    autentica (`redis-cli -a "$$REDIS_PASSWORD"`), las URLs de los servicios de
    `docker-compose.manuals.yml` llevan credencial (`redis://:…@redis:6379/N`) y
    `docker-compose.dev.yml` publica **todos** sus puertos en `127.0.0.1:`.
    `REDIS_PASSWORD` está documentada en `docker/.env.example` y en
    `docs/04-reference/mandatory-env-vars.md` (con su sección propia).
  - **Test**: `auto_prod10_06_a` = `tests/unit/test_compose_redis_auth_and_dev_binds.py`,
    5 verdes, con guardas de descubrimiento en los dos lados (si el parser deja de
    ver URLs o puertos, el test falla en vez de pasar en vacío).
  - **`auto_prod10_06_b` NO existe y no se ha fingido.** Un
    `tests/integration/test_redis_requires_password.py` afirmaría una propiedad
    del contenedor DESPLEGADO, no del código. Comprobado hoy: el Redis que corre
    en `localhost:6379` responde `PING` **sin credencial** — es anterior a este
    cambio. O sea que ese test sólo demostraría que el stack necesita redespliegue,
    y eso ya es `human_prod10_02`.
- **Descripción**: (1) Añadir `--requirepass ${REDIS_PASSWORD:?}` al comando del servicio redis (`docker-compose.yml:100-117`) y propagar la credencial a `redis_url`/`broker_url` de api-server (`config.py:178`), workers, orchestrator y notification-dispatcher (formato `redis://:pass@redis:6379/N`). (2) En `docker-compose.dev.yml`, fijar bind local en TODOS los puertos publicados: `"127.0.0.1:${REDIS_PORT:-6379}:6379"` y equivalentes para postgres (:18), minio (:26-27) y vault (:42) — hoy se publican en 0.0.0.0 exponiendo sesiones reales a la LAN corporativa. (3) Actualizar healthcheck de redis (`redis-cli -a`) y `.env.example`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Dependencias**: `task_prod10_05` (mismo patrón `${VAR:?}`)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_redis_auth_and_dev_binds.py -v"
  # `auto_prod10_06_b` RETIRADO el 2026-08-19. La nota de 2026-08-10 ya decía que
  # `tests/integration/test_redis_requires_password.py` «NO existe y no se ha fingido»,
  # con el argumento correcto: afirmaría una propiedad del contenedor DESPLEGADO, no del
  # código, y por tanto sólo demostraría que el stack necesita redespliegue. Pero el
  # `command:` seguía ahí, así que la casilla marcada declaraba una verificación imposible
  # de pasar. Se retira el bloque; esa comprobación es `human_prod10_02`, que la lleva
  # literalmente en su checklist («redis-cli ping sin -a contra el Redis del stack →
  # NOAUTH; con la password → PONG»).
  # Lo que SÍ es verificable sin desplegar —que el compose lo exige— es `_a`, y muerde:
  # quitar `--requirepass` + `${REDIS_PASSWORD:?…}` del `command:` del servicio `redis`
  # pone en rojo `test_redis_requires_a_password`. Restaurado con `git show HEAD:… > …`;
  # 6 verdes.
  ```

### Fase C — Vault operable: tokens renovables y unseal con estrategia

#### `task_prod10_07` — Renovación automática del token de Vault

- [x] **Título**: ADR corto (token periódico vs AppRole) + `renew_self` en background en todos los clientes hvac
  - ✅ **Cerrada (2026-08-10).** El ADR está firmado ([0145](../05-architecture-decisions/0145-vault-operable-tokens-y-unseal.md),
    `accepted`: tokens periódicos renovables — opción A; AppRole queda como
    evolución con su disparador escrito) y `api_server.vault_client`
    (`VaultTokenManager`, `build_vault_client`, gauge
    `agentic_vault_token_ttl_seconds`) ya cubría el api-server.
  - **Lo que faltaba y se entrega hoy: el worker.** La guarda de descubrimiento
    del api-server recorría `api_server/` y ahí se paraba. Los workers tienen su
    propio token (`WORKERS_VAULT_TOKEN`, política `workers`) y construían
    `hvac.Client` **a mano en tres sitios** —el job semanal de rotación
    (`credential_rotation_task`), la credencial LLM de **cada ejecución de agente**
    (`execution._default_vault_store`) y el clonado de repos (`repo_clone`)— sin
    una sola llamada a `renew_self`. El mismo apagón diferido que el api-server ya
    no tiene seguía programado, y con peor diagnóstico: no sale un 503, salen
    ejecuciones corriendo con `has_credential=False`.
  - **Cómo**: nuevo `apps/workers/src/workers/vault_client.py`
    (`build_worker_vault_client`, cacheado, un solo hilo de renovación para los
    tres consumidores) que **reutiliza** `VaultTokenManager` y `HvacTokenAdapter`
    del api-server — el calendario de renovación no se duplica, que es como uno de
    los dos se habría quedado atrás.
  - **Test**: `tests/unit/test_worker_vault_token_renewal.py` (7 verdes), con la
    guarda de descubrimiento **sobre el árbol de `workers/`**: cualquier
    `hvac.Client(` fuera de la fábrica sale en rojo. Verificada rompiendo
    `repo_clone` a propósito: falló nombrando el fichero, y se restauró.
  - **Fuera de alcance justificado**: el `hvac.Client` de
    `installer_backend/real_bindings.py` es de un solo uso (bootstrap) y muere con
    el proceso del instalador; no hay token que mantener vivo. Y el SSO **no usa
    Vault** por decisión del ADR 0146.
- **Descripción**: Hoy `routers/llm_providers.py:107-119` construye `hvac.Client` una vez con token estático cacheado en `_StoreCache` y no existe NINGUNA llamada a `renew_self`/`lookup_self` en el repo: un service token (~TTL 32 días) caducará y las credenciales LLM/MCP caerán en silencio. Implementar: (1) ADR breve en `docs/05-architecture-decisions/` eligiendo token periódico renovable vs AppRole (recomendación: periódico ahora, ver Decisiones clave §2); (2) wrapper `VaultTokenManager` en el módulo compartido que haga `lookup_self` al arrancar (log de TTL) y `renew_self` en tarea de fondo antes de ttl/2, con métrica `vault_token_ttl_seconds` y log de error si la renovación falla; (3) usarlo en api-server (llm_providers, MCP auth_ref, SSO) y en cualquier otro consumidor de Vault.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_vault_token_manager.py -v"
  - id: auto_prod10_07_b
    runtime: python-pytest
    command: "pytest tests/unit/test_worker_vault_token_renewal.py tests/unit/test_vault_token_manager.py -v"
  ```

#### `task_prod10_08` — Minteo de tokens por servicio contra las políticas existentes

- [x] **Título**: Script/installer mintea tokens periódicos por servicio usando las políticas de `vault_bootstrap.py`
  - ✅ **Cerrada (2026-08-10), verificada:** `scripts/vault-mint-service-tokens.sh`
    acuña un token **periódico** (`-period`, por defecto 72h) y **huérfano**
    (`-orphan`, para que revocar el root token expuesto no se lleve la plataforma
    por delante) por cada una de las cuatro políticas que escribe
    `installer_backend.vault_bootstrap`, y los emite como líneas `.env` por stdout
    sin tocar el disco salvo con `--write`.
  - **Test**: `tests/unit/test_vault_service_tokens.py` (6 verdes) con shim de
    `docker`. El que de verdad envejece: los nombres de política del script se
    comparan con los de `initial_policies()`, porque un script bash no puede
    importar el Python y esa deriva se descubriría a las 3 de la mañana con un
    `permission denied`.
  - **Documentación**: `docs/04-reference/mandatory-env-vars.md` §«Tokens de Vault
    por servicio» (tabla variable→política) y la rotación del token de servicio en
    `docs/06-runbooks/05-key-rotation.md` §10.
  - **Desviación del plan**: el test se llamaba
    `tests/unit/test_vault_service_tokens.py`; vive en `tests/unit/` porque
    con el shim no necesita ni Vault ni base de datos.
- **Descripción**: `vault_bootstrap.py:303` escribe políticas por servicio pero nadie mintea tokens contra ellas (no hay `create_token`); `init-vault.sh` delega en el operador. Añadir a `scripts/` (o al bootstrap del installer, coordinado con prod-01) el paso que crea tokens periódicos por servicio (`vault token create -policy=<svc> -period=72h -orphan`) y los entrega vía `.env` prefijado por servicio, eliminando el uso del root token en configs (en dev se mantiene `dev-root-token`). Documentar la rotación del token de servicio en `docs/06-runbooks/05-key-rotation.md`.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Dependencias**: `task_prod10_07` (los tokens minteados deben ser renovables por el manager)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_08_a
    runtime: python-pytest
    command: "pytest tests/unit/test_vault_service_tokens.py -v"
  ```

#### `task_prod10_09` — Unseal post-reinicio: ADR + healthcheck honesto + alerta "Vault sealed"

- [x] **Título**: Distinguir sealed de healthy, alertar y documentar el desellado como primer paso post-reboot
  - ✅ **Cerrada (2026-08-10), verificada:** ADR [0145](../05-architecture-decisions/0145-vault-operable-tokens-y-unseal.md)
    `accepted` con la **opción C** (desellado manual + alerta + healthcheck
    honesto); auto-unseal queda como evolución con su disparador escrito.
    `api_server.vault_client.probe_vault_seal` consulta `/v1/sys/seal-status` —el
    único endpoint que dice la verdad, porque el healthcheck del compose traduce
    sellado (503) y sin-inicializar (501) a 200 **a propósito**, para que Vault no
    entre en bucle de reinicio antes de que nadie pueda desellarlo—, publica el
    gauge `agentic_vault_sealed` y lo consume `/admin/system-health`
    (`routers/admin.py:_check_vault`, y el agregado pasa a `degraded`).
  - **Semántica cuidada**: «no responde» **no** es «sellado». Si Vault no
    contesta, el gauge se deja como estaba y de eso se ocupa la regla `ServiceDown`
    — escribir un 1 ahí haría que una alerta llamada «Vault sealed» se disparase
    por un contenedor caído, y el operador iría a desellar algo que no está
    sellado.
  - **Test**: `tests/unit/test_vault_seal_probe.py` (8 verdes), con dos guardas de
    cableado: que `admin.py` usa el probe (recorriendo el AST sin docstrings, para
    que se pueda seguir documentando por qué se abandonó `/v1/sys/health`) y que
    **alguna regla de Prometheus mira el gauge** — una métrica que nadie vigila no
    es una alerta.
  - **El test de integración declarado (`auto_prod10_09_b`) ya existe (2026-08-19).**
    Hasta hoy la casilla estaba marcada nombrando un fichero que no existía, y
    figuraba por eso en el inventario congelado de
    `tests/unit/test_declared_tests_exist.py` (entrada ya retirada).
    `tests/integration/test_system_health_vault_sealed.py` (4 verdes) cubre la vuelta
    entera —login de System Admin → `/admin/system-health` → sonda httpx real contra
    un Vault de mentira en `127.0.0.1`— y fija las tres cosas que el unit test de la
    sonda no puede ver: (a) sellado ⇒ **200 con `status: degraded`**, ni un 500 ni un
    `ok`, con el detalle nombrando el sello y el runbook; (b) el veredicto lo causa el
    sello, porque con `sealed: false` y el MISMO montaje el agregado vuelve a `ok`
    —sin ese contraste, un endpoint que respondiera `degraded` siempre pasaría—; (c)
    un Vault que NO contesta sale `down`, no `degraded`, y con detalle genérico (no
    filtra la URL interna a un panel). Rojos verificados: borrando la rama
    `elif vault_h.status == "degraded"` de `routers/admin.py` cae (a), y devolviendo
    `str(exc)` como detalle en `probe_vault_seal` cae (c).
  - **Anotado, no fijado**: cuando Vault **no responde**, el agregado se queda en
    `ok` (la rama del router sólo mira `degraded`). Puede ser correcto —de un
    servicio caído se ocupa la regla `ServiceDown`— o un hueco equivalente al
    secrets-5 en la otra dirección. El test lo deja **sin aserción a propósito**,
    con la razón escrita: es una decisión del operador, no algo que un test deba
    bendecir por su cuenta.
  - **Runbook**: `docs/06-runbooks/restart-services.md` abre con un **PASO 0**
    («tras cualquier reinicio del HOST — desellar Vault») que incluye cómo
    detectarlo, cómo desellar con 3 de 5 shares, cómo confirmarlo y el enlace a
    `dr-vault-unseal-rotation.md`.
- **Descripción**: Hoy `docker-compose.yml:168-177` mapea sealed (503) y uninit (501) a 200 (`sealedcode=200&uninitcode=200`), el compose del installer hace `depends_on: vault: service_healthy` (`compose_generator.py:407-411`) y el watchdog considera healthy cualquier `{"healthy","running","starting"}` (`service_monitor.py:38`): tras un reboot todo arranca contra un Vault inutilizable sin alerta. Implementar la Opción C del ADR de unseal (ver Decisiones clave §1): (1) redactar el ADR con las 3 opciones y registrar la decisión humana; (2) mantener sealed→alive SOLO para el arranque (documentado), pero añadir un probe en el api-server que consulte `/v1/sys/seal-status` y marque la plataforma degradada en `/admin/system-health` + exponer métrica `vault_sealed` (gauge) para la regla de alerta (la telemetría de Vault ya está en `config.hcl:21-24`; la ruta Alertmanager la cierra prod-08); (3) actualizar `docs/06-runbooks/restart-services.md` con el unseal como PRIMER paso post-reboot, enlazando `dr-vault-unseal-rotation.md`.
- **Tiempo**: 2 días · **Complejidad**: l
- **Dependencias**: `task_prod10_07` (comparte cliente Vault instrumentado)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_vault_seal_probe.py -v"
  - id: auto_prod10_09_b
    runtime: python-pytest
    command: "pytest tests/integration/test_system_health_vault_sealed.py -v"
  ```

### Fase D — Decisión Fernet-vs-Vault y cierre documental

#### `task_prod10_10` — ADR: cifrado Fernet-en-DB vs Vault para SSO/notificaciones/webhooks

- [x] **Título**: Formalizar (o eliminar) la excepción al principio "Vault es la única vía"
  - ✅ **Cerrada (2026-08-10):** ADR [0146](../05-architecture-decisions/0146-fernet-en-db-vs-vault.md),
    `accepted` el 2026-07-31, **opción B**: la excepción se bendice y se formaliza.
    Y no sólo en el ADR — está escrita en `CLAUDE.md` §«Dónde vive un secreto (y la
    única excepción a Vault)», con la tabla plataforma→Vault / tenant-a-tercero→
    columna Fernet, el criterio en una línea («si la plataforma no arranca sin ese
    secreto, va a Vault»), las tres condiciones no negociables y **su fecha de
    caducidad**: el día que haya auto-unseal, la objeción de disponibilidad que la
    justifica desaparece y el 0146 se reabre.
  - **Por qué B y no A** (el plan recomendaba A): el ADR 0145 decidió desellado
    **manual**. Encadenando: se reinicia el host → Vault arranca sellado → si el
    SSO leyera su client secret de Vault, **nadie entraría por SSO** hasta que un
    humano apareciese con su fragmento de Shamir. La opción A movía la complejidad
    al peor momento posible.
- **Descripción**: Cuando `API_SERVER_VAULT_TOKEN` no está configurado, los client secrets OIDC (`config.py:62-71`), secretos de canales de notificación (72-85) y signing secrets de webhooks (308-317) se cifran en Postgres con Fernet derivada por SHA-256 de una env — contradiciendo `llm_providers/vault.py:3-9` y el CLAUDE.md. Redactar ADR en `docs/05-architecture-decisions/` con dos opciones: (A) cuando Vault esté wired, migrar estas familias a Vault y degradar el camino Fernet a error 503 (paridad con el flujo LLM, recomendada); (B) bendecir la excepción, documentarla y exigir cifrado de backups con clave separada de las columnas Fernet. La decisión es humana; la tarea entrega el ADR en `proposed`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**: no aplica (documento); la implementación se testea en `task_prod10_11`.

#### `task_prod10_11` — Implementar la opción aprobada del ADR Fernet-vs-Vault

- [x] **Título**: Migración de secretos SSO/notificaciones/webhooks según el ADR (o salvaguardas de la excepción)
  - ✅ **Cerrada en NEGATIVO para la migración, en POSITIVO para la salvaguarda
    (2026-08-10).** La migración a Vault **no se hace**: el ADR 0146 eligió la
    opción B. Lo que la opción B exige —y el propio ADR llama «no opcional», porque
    sin ello «habría bendecido el riesgo sin quitarlo»— **sí está entregado**:
    `apps/workers/src/workers/backup_secrets.py` +
    `WORKERS_BACKUP_COLUMN_SECRET_TABLES` excluyen del `pg_dump` los **datos** (no
    la definición: `--exclude-table-data`) de `sso_configurations`,
    `notification_channels` e `incoming_webhook_configs`. Un bundle robado ya no
    lleva el ciphertext.
  - **Test**: `tests/unit/test_backup_column_secrets.py` (8 verdes).
  - **Por qué excluir y no «cifrar con clave distinta»** (el ADR daba las dos): el
    instalador emite `WORKERS_BACKUP_ENCRYPTION_ENABLED=false`, así que en un stack
    recién instalado el segundo sobre no existiría y el ciphertext viajaría igual.
    Excluir no necesita clave, ni custodia, ni segundo custodio.
  - **Precio, documentado**: tras un DR hay que reconfigurar SSO, canales de
    notificación y webhooks entrantes —
    `docs/06-runbooks/04-disaster-recovery.md`. Es una ausencia **visible** (el
    botón de SSO no aparece), no un fallo silencioso.
  - ✅ **2026-08-19 — el bloque `yaml` ya no declara el test contrario.** La nota de
    arriba decía, con razón, que `test_sso_notification_webhook_secrets_vault.py` «no se
    escribe» porque afirmaría lo contrario de lo que el ADR 0146 decidió… pero el
    `command:` de abajo **seguía declarándolo**, así que la casilla marcada apuntaba a una
    verificación que, de existir, habría que romper. Se sustituye por la que de verdad
    toca: que la salvaguarda de la opción B se cumple **y que la excepción no ha crecido**.
    `tests/unit/test_backup_column_secrets.py`, 8 verdes.
    **Comprobado que muerde, no supuesto**: se añadió `"llm_providers"` a
    `COLUMN_SECRET_TABLES` (una credencial de PLATAFORMA, justo lo que la frontera del ADR
    prohíbe) y saltaron dos —
    `test_the_excluded_tables_are_exactly_the_three_families_the_adr_names` y
    `test_the_settings_default_matches_the_adr`—; restaurado el fichero con
    `git show HEAD:… > …`, verde otra vez.
- **Descripción**: Si se aprueba la opción A: script de migración que mueva los valores Fernet existentes a Vault (`secret/tenants/{tenant}/...`), cambie los read-paths de api-server y notification-dispatcher (`notification_dispatcher/config.py:345`) a Vault-first y devuelva 503 en escrituras sin Vault, replicando `routers/llm_providers.py:90-119`. Si opción B: marcar las columnas, excluirlas del export de backups o cifrarlas con clave distinta, y documentar. Estimación para la opción A (peor caso).
- **Tiempo**: 2 días · **Complejidad**: l
- **Dependencias**: `task_prod10_10` aprobado por humano; `task_prod10_07` (cliente Vault renovable)
- **Tests automáticos**:
  ```yaml
  # REESCRITO el 2026-08-19. El id anterior declaraba
  # `tests/integration/test_sso_notification_webhook_secrets_vault.py`, que nunca existió
  # y que además habría verificado la MIGRACIÓN A VAULT — la opción A, la que el ADR 0146
  # descartó. Un comando así no es sólo un fichero que falta: es una medida que contradice
  # la decisión que la casilla implementa. Lo que hay que verificar de la opción B son las
  # dos mitades de su condición «no opcional»:
  - id: auto_prod10_11_a
    description: >-
      La salvaguarda se cumple: los DATOS de las tres tablas de secretos de columna no
      viajan en el `pg_dump` (`--exclude-table-data`, nunca `--exclude-table`: el esquema
      sí viaja o el restore dejaría la base sin esas tablas), el manifiesto dice cuáles se
      quedaron fuera, y el default del `Settings` es el seguro.
    runtime: python-pytest
    command: "pytest tests/unit/test_backup_column_secrets.py -v"
  - id: auto_prod10_11_b
    description: >-
      Y la excepción NO ha crecido: `COLUMN_SECRET_TABLES` son exactamente las tres
      familias que el ADR 0146 nombra, y cada columna declarada existe en el ORM real (un
      renombrado dejaría la frontera diciendo una cosa y el esquema otra). Añadir aquí una
      credencial de PLATAFORMA pone el test en rojo, que es el punto.
    runtime: python-pytest
    command: "pytest tests/unit/test_backup_column_secrets.py -k 'excluded_tables_are_exactly or every_declared_column_exists or settings_default_matches' -v"
  ```

#### `task_prod10_12` — Runbooks y referencia actualizados

- [x] **Título**: `05-key-rotation.md` (token Vault), `restart-services.md` (unseal), `04-reference` de variables obligatorias
  - ✅ **Cerrada (2026-08-10).** Las cuatro piezas que pedía la tarea:
    1. **`05-key-rotation.md` §10** reescrita: las **cuatro** variables de token
       (no dos), el comando de acuñado, por qué periódico y por qué huérfano, la
       verificación _que el código cumple_ (`vault.token.lookup` /
       `vault.token.renewed` / el gauge `agentic_vault_token_ttl_seconds`, y el
       `vault.token.renew_failed` a nivel error que es lo que hay que mirar) y su
       rollback. Más el script de propagación en §1 y §8 (`task_prod05_06`).
    2. **`restart-services.md`** ya abría con el PASO 0 de desellado
       (`task_prod10_09`); se comprueba desde hoy.
    3. **`docs/04-reference/mandatory-env-vars.md`**: catálogo de variables sin
       default, tabla de tokens de Vault por servicio y tabla «cuando el arranque
       falla».
    4. **`02-troubleshooting.md`**: sección nueva «El arranque falla fail-closed»
       con los **cinco** mensajes de arranque de prod-10/prod-09/prod-05 y su
       arreglo, entrada en el índice de síntomas, y `NOAUTH` en la sección de
       Redis (con el `redis-cli -a`, porque el `ping` a pelo que documentaba ya no
       funciona).
  - **Test**: `auto_prod10_12_a` = `tests/unit/test_docs_runbooks_updated.py`
    (9 verdes). Comprueba **anclas, no prosa** — reescribir un párrafo no lo pone
    en rojo, borrar la única mención a `vault-mint-service-tokens.sh` sí. Incluye
    una guarda de descubrimiento que compara el catálogo de variables contra los
    `${VAR:?…}` del compose canónico (8 hoy): una variable exigida y no
    documentada rompe el test. Verificada en rojo renombrando `SEARXNG_SECRET`.
- **Descripción**: Consolidar la documentación operativa generada por las fases A-D: procedimiento de rotación de token de servicio Vault en `docs/06-runbooks/05-key-rotation.md`, unseal post-reboot en `restart-services.md` (si no quedó cerrado en `task_prod10_09`), tabla de variables de entorno OBLIGATORIAS sin default (resultado de B) en `docs/04-reference/`, y nota en `02-troubleshooting.md` sobre el error de arranque fail-closed y cómo resolverlo.
- **Tiempo**: 1 día · **Complejidad**: s
- **Dependencias**: fases A, B y C completadas
- **Tests automáticos**:
  ```yaml
  - id: auto_prod10_12_a
    runtime: python-pytest
    command: "pytest tests/unit/test_docs_runbooks_updated.py -v"
  ```

## Hallazgos de auditoría cubiertos

| fid       | Severidad | Tarea(s) que lo cierran                           |
| --------- | --------- | ------------------------------------------------- |
| secrets-1 | high      | `task_prod10_01`, `task_prod10_03`                |
| secrets-3 | high      | `task_prod10_04`                                  |
| secrets-4 | medium    | `task_prod10_07`, `task_prod10_08`                |
| secrets-5 | medium    | `task_prod10_09`                                  |
| secrets-6 | medium    | `task_prod10_05`                                  |
| secrets-7 | medium    | `task_prod10_06`                                  |
| secrets-8 | medium    | `task_prod10_10`, `task_prod10_11`                |
| deploy-8  | medium    | `task_prod10_09` (alerta/probe: coordina prod-08) |
| deploy-11 | medium    | `task_prod10_01`, `task_prod10_02`                |
| quality-3 | medium    | `task_prod10_03`                                  |

## Riesgos

1. **Pérdida de acceso a Vault durante la contención (Fase A)**: revocar el root token y re-emitirlo vía unseal keys es una operación destructiva si las custodias se pierden a medias. Mitigación: drill en seco contra un Vault desechable antes de tocar el real, y checklist humano de `dr-vault-unseal-rotation.md`.
2. **El fail-closed rompe entornos existentes**: dev/staging que dependían de defaults silenciosos dejarán de arrancar tras `task_prod10_04`/`05`. Mitigación: mensajes de error accionables, `.env.example` completo y nota de migración en `02-troubleshooting.md`; comunicarlo antes del merge.
3. **Dependencia de decisiones humanas (dos ADRs)**: las tareas `task_prod10_09` y `task_prod10_11` pueden quedar bloqueadas esperando aprobación, alargando el calendario. Mitigación: redactar los ADRs en la primera semana y trabajar las fases A/B en paralelo.
4. **Coordinación con prod-01**: el minteo de tokens por servicio (`task_prod10_08`) toca el bootstrap del installer que prod-01 está reescribiendo. Mitigación: acordar la interfaz (.env prefijado por servicio) antes de implementar; si prod-01 va por delante, este plan solo añade el paso de minteo.
5. **Redis con password puede romper consumidores no inventariados**: cualquier cliente que construya la URL a mano sin credencial fallará. Mitigación: grep de `redis://` en todo el repo dentro de `task_prod10_06` y test de integración con `requirepass` activo.
6. **Renovación de token con bug = caída diferida ~32 días después**: un fallo silencioso en `renew_self` reproduce exactamente el problema que se pretende arreglar. Mitigación: métrica `vault_token_ttl_seconds` con alerta de umbral (prod-08) y log de error explícito.

## Tests humanos del Plan

```yaml
- id: human_prod10_01
  description: "El working tree y el contexto de build están limpios de secretos de Vault"
  hint: "Tras la Fase A, en la máquina del operador"
  checklist:
    - "vault-init-output/ no existe en el working tree"
    - "El root token antiguo (hvs.zAntQ…) está revocado: usarlo contra Vault devuelve 403"
    - "Las 5 unseal keys están en custodias separadas documentadas (sin detallar dónde en el repo)"
    - "Re-ejecutar scripts/init-vault.sh contra un Vault de prueba NO deja ficheros .txt en claro"
    - "Crear vault-init-output/dummy.txt y lanzar el hook pre-commit → falla con mensaje claro"

- id: human_prod10_02
  description: "Producción no arranca con defaults conocidos"
  hint: "Compose base sin overlay dev, .env incompleto a propósito"
  checklist:
    - "docker compose -f docker/docker-compose.yml config sin POSTGRES_PASSWORD en .env → error '… set in .env', no arranca"
    - "Arrancar api-server sin API_SERVER_ENVIRONMENT y con la JWT secret default → el proceso falla al arrancar con mensaje accionable"
    - "Con environment=prod y un secreto de 8 caracteres → rechazo por longitud/entropía"
    - "redis-cli ping sin -a contra el Redis del stack → NOAUTH; con la password → PONG"
    - "En dev, desde OTRA máquina de la LAN: redis/postgres/minio/vault del overlay no responden (bind 127.0.0.1)"

- id: human_prod10_03
  description: "Vault es operable: token renovado y sealed visible"
  hint: "Stack completo levantado, simular reboot"
  checklist:
    - "Logs del api-server al arrancar muestran lookup_self con TTL del token y renovaciones periódicas"
    - "docker restart del contenedor vault → Vault queda sealed; /admin/system-health muestra la plataforma DEGRADADA con aviso 'Vault sealed'"
    - "La métrica vault_sealed=1 es visible en Prometheus/Grafana mientras está sellado"
    - "Seguir restart-services.md desde cero permite desellar y la plataforma vuelve a verde"
    - "El ADR de unseal y el ADR Fernet-vs-Vault están decididos (status accepted/rejected) por un humano"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. Los dos ADRs (estrategia de unseal; Fernet-vs-Vault) decididos por un humano, no en `proposed`.
3. Los 3 tests humanos del plan validados.
4. Hallazgos secrets-1/3/4/5/6/7/8, deploy-8, deploy-11 y quality-3 marcados como resueltos en el registro de la auditoría.
5. Entrada de changelog en `docs/07-changelog/prod-10-vault-secretos-operables.md`.
6. PR del plan mergeado a `master`.

## Próximo Plan

Siguiente de la serie correctiva por prioridad: **prod-11-cadena-suministro** [P1] — SCA en CI, Dependabot, lockfiles y pin por digest. Reutiliza directamente el step de higiene de CI creado aquí (`task_prod10_03`) y el `.dockerignore` ampliado. En paralelo, prod-01 (despliegue ejecutable) consume la interfaz de tokens por servicio definida en `task_prod10_08` y prod-08 (observabilidad) cablea las alertas sobre `vault_sealed` y `vault_token_ttl_seconds`.
