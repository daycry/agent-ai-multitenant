"""``.env`` + ``config/global.yaml`` + data-tree generators (Plan 15 task_15_08).

Phase B fills the real generators the install orchestration (task 15_05's
``generate_config`` step) calls. :mod:`installer_backend.compose_generator`
(task 15_07) builds the docker-compose; this module builds the rest of what a
real install lays down on disk:

  * the **.env** — every environment variable the runtime services read
    (PostgreSQL / MinIO / JWT / SSO / notification / webhook secrets, the
    derived DSNs, the deployment ``ENVIRONMENT`` markers), with **generated
    high-entropy secrets** (never the dev defaults). A production ``.env`` from
    this generator passes the platform's prod dev-secret guard (Plan 06.14):
    it contains none of the dev-default markers ``changeme`` / ``dev-only`` /
    ``minioadmin``.
  * **config/global.yaml** — the non-secret platform config (domain, environment,
    enabled providers, resource sizing, storage layout, supported languages).
  * the **/data/agent-platform/** directory plan — the directory tree + POSIX
    permissions for the repos / worktrees / dep-cache / object-store / Vault /
    monitoring data.

Secrets
-------
Every generated secret is drawn from a CSPRNG (:mod:`secrets`). They are unique
per run (a fresh :class:`GeneratedSecrets` per :func:`generate_secrets` call)
and high-entropy (URL-safe base64 of >=32 random bytes). The generated ``.env``
is written to disk at install time only — NEVER committed and NEVER logged in
plaintext (the write goes through the injectable :class:`EnvFileWriter` seam,
mocked in tests). Tests assert the *structure* of the artifacts with the real
generated values, or with throwaway placeholders; nothing real is committed.

Disk writes behind a seam
-------------------------
The real install actually ``mkdir``s the data tree under ``/data/agent-platform``
and writes ``.env`` + ``config/global.yaml`` — none of which can run in CI. So
every host-touching action is a Protocol here (:class:`EnvFileWriter`,
:class:`DataTreeProvisioner`) with an in-memory fake for tests; the real binding
shells out / writes files and is exercised only by the plan's Tests Humanos.
This module's pure functions (``generate_secrets`` / ``render_env_file`` /
``generate_global_yaml`` / ``build_data_tree_plan``) perform NO I/O.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import yaml

from installer_backend.compose_generator import STACK_ASSETS_DIR_NAME, enabled_providers
from installer_backend.config import Environment, InstallerConfig

# Dev-default markers the prod secret guard rejects (mirror of
# api_server.config._DEV_SECRET_MARKERS + the MinIO admin default). A generated
# production .env must contain NONE of these.
#
# `change_me` NO está en el catálogo del runtime, y está aquí a propósito: es el
# marcador de los perfiles de `scripts/install-profiles/`, que el operador copia
# y edita. `CHANGE_ME_minio_secret_placeholder_value` no contiene `changeme` —el
# guion bajo lo rompe—, así que un perfil sin editar cuyo valor llegara al `.env`
# pasaría el guardián con un secreto publicado en este repositorio por
# contraseña. Hoy esos campos se descartan (ver `build_env_vars`), o sea que este
# marcador es un cable trampa: no cambia nada mientras el descarte siga ahí, y
# revienta el día que alguien cablee el YAML sin exigir la sustitución.
_DEV_SECRET_MARKERS: tuple[str, ...] = ("changeme", "change_me", "dev-only", "minioadmin")

#: Minimum number of random bytes behind every generated secret (>=256 bits of
#: entropy once base64-encoded). Tokens are URL-safe so they are .env-clean
#: (no ``=`` padding issues, no quoting needed, no marker substrings possible).
_SECRET_NBYTES = 32

#: The deployment-environment value the runtime services expect. The installer's
#: :class:`Environment` enum says ``production``/``staging``/``development`` but
#: the services' settings guard keys on ``prod``/``staging``/``dev`` (and only
#: ``staging``/``prod`` trip the dev-secret guard). Map installer → runtime here
#: so a production install emits ``ENVIRONMENT=prod`` and the guard fires.
#: Alias del mapeo, que ahora vive en el propio enum (`Environment.runtime_value`)
#: para que no pueda volver a existir en un generador y faltar en el otro — que es
#: exactamente lo que pasó con el compose hasta el 2026-07-30.
_RUNTIME_ENVIRONMENT: dict[Environment, str] = {e: e.runtime_value for e in Environment}


def _token() -> str:
    """A single high-entropy URL-safe secret (>=256 bits, no dev-marker chars)."""

    return secrets.token_urlsafe(_SECRET_NBYTES)


@dataclass(frozen=True)
class GeneratedSecrets:
    """The CSPRNG-generated secret material for one install.

    Holds ONLY generated, high-entropy values — never a dev default. The
    instance is built fresh per :func:`generate_secrets` call (unique per run).
    ``__repr__``/``__str__`` are redacted so an accidental log line or traceback
    frame can't leak the values; the real ``.env`` write goes through a seam and
    the values otherwise live only in memory until handed to Vault.

    Fields map 1:1 onto the runtime services' secret settings (see
    :mod:`api_server.config` / the workers / dispatcher configs):

    postgres_password
        The ``postgres`` superuser password (initdb).
    migrations_user_password / app_user_password / service_user_password
        Passwords for the THREE DB roles created on first start. El reparto es
        el de prod-14 (``task_prod14_05`` / tenancy-2) y no es cosmético:

        * ``migrations_user`` — PROPIETARIO del esquema, ``GRANT ALL``, DDL.
          Sólo Alembic y el ``pg_dump`` del backup.
        * ``app_user`` — NOBYPASSRLS: el camino de las peticiones humanas, con
          ``app.tenant_id`` fijado en la sesión. Es lo que hace que la RLS
          proteja algo.
        * ``service_user`` — BYPASSRLS **sin DDL**: los servicios que trabajan
          cruzando tenants (workers, orchestrator, dispatcher, superficie
          /admin). Necesitan ver todas las filas; lo que NO necesitan es poder
          ejecutar ``ALTER TABLE … DISABLE ROW LEVEL SECURITY``.

        ``service_user_password`` la consume ``stack/postgres/init/
        05-service-role-password.sh``, que corrige el literal de desarrollo con
        el que ``04-service-role.sql`` crea el rol. Sin ella el rol BYPASSRLS
        nace con una contraseña escrita en este repositorio, y el único aviso es
        una línea en el stderr del contenedor de postgres.
    redis_password
        ``requirepass`` de Redis. Ese Redis aloja las SESIONES de servidor, el
        broker de Celery y los contadores de rate limit: sin autenticación,
        cualquiera con acceso al puerto lee sesiones vivas y encola trabajo para
        los workers. Viaja además dentro de cada DSN ``redis://:<clave>@redis``.
    minio_root_user / minio_root_password
        MinIO admin credentials (the access/secret key the services use).
    jwt_secret
        HMAC secret for signing JWTs (``API_SERVER_JWT_SECRET``).
    review_url_signing_secret
        HMAC secret for signing reviewer URLs.
    sso_encryption_key / notification_encryption_key / incoming_webhook_encryption_key
        Raw secrets the services derive Fernet keys from (at-rest encryption).
        ``notification_encryption_key`` is shared by api-server + dispatcher.
    grafana_admin_password
        Grafana admin password (monitoring overlay).
    vault_root_token_placeholder
        NOT a real Vault token — the real root token comes from
        ``vault operator init`` in task 15_09. This is only a throwaway used to
        keep the bootstrap ``.env`` complete before Vault is initialised; it is
        overwritten by the Vault bootstrap and never used to authenticate.
    """

    postgres_password: str
    migrations_user_password: str
    app_user_password: str
    # prod-14 task_prod14_04/05 (tenancy-2). El tercer rol, el que de verdad
    # corren los servicios de larga vida. Ver el reparto en el docstring.
    service_user_password: str
    # prod-10 secrets-7. `--requirepass` de Redis, y credencial de las nueve DSN.
    redis_password: str
    minio_root_user: str
    minio_root_password: str
    jwt_secret: str
    # ADR 0136: secreto DEDICADO para los tokens internos del sandbox
    # (`AGENTIC_INTERNAL_TOKEN`), independiente de `jwt_secret`. Antes uno solo
    # firmaba las sesiones humanas Y los tokens del sandbox, y como quien mintea
    # el del sandbox es el WORKER, comprometerlo permitía forjar la sesión de
    # cualquier System Admin. `config.py` tiene una guarda que rechaza el arranque
    # si los dos coinciden, así que este draw tiene que ser independiente.
    internal_token_secret: str
    # NOTIF-2 / prod-08: Bearer que Alertmanager presenta en
    # /internal/alerts/ingest (API_SERVER_ALERTS_INGEST_TOKEN). El lado
    # Alertmanager (http_config.authorization en su yml) se templetiza en
    # prod-08; hasta entonces el operador lo copia del .env al yml.
    alerts_ingest_token: str
    review_url_signing_secret: str
    sso_encryption_key: str
    notification_encryption_key: str
    incoming_webhook_encryption_key: str
    grafana_admin_password: str
    vault_root_token_placeholder: str

    def __repr__(self) -> str:  # pragma: no cover - security-load-bearing, trivial
        return "GeneratedSecrets(<redacted: high-entropy, written to .env/Vault once>)"

    __str__ = __repr__


def generate_secrets() -> GeneratedSecrets:
    """Mint a fresh set of CSPRNG secrets for one install (unique per call).

    Every field is an independent ``secrets.token_urlsafe`` draw, so two calls
    never collide and no value carries a dev-default marker. The MinIO root user
    is a generated identifier too (not the well-known ``minioadmin``) so the
    object-store admin name itself can't trip the prod guard.
    """

    return GeneratedSecrets(
        postgres_password=_token(),
        migrations_user_password=_token(),
        app_user_password=_token(),
        # Tirada INDEPENDIENTE, no una copia de la de `app_user`: si compartieran
        # valor, comprometer el rol de aplicación entregaría también el rol que se
        # salta la RLS de todos los tenants.
        service_user_password=_token(),
        redis_password=_token(),
        # A generated admin *name* (not "minioadmin") + a generated key.
        minio_root_user=f"minio-{secrets.token_hex(8)}",
        minio_root_password=_token(),
        jwt_secret=_token(),
        internal_token_secret=_token(),
        alerts_ingest_token=_token(),
        review_url_signing_secret=_token(),
        sso_encryption_key=_token(),
        notification_encryption_key=_token(),
        incoming_webhook_encryption_key=_token(),
        grafana_admin_password=_token(),
        vault_root_token_placeholder=_token(),
    )


def _database_urls(secrets_: GeneratedSecrets) -> dict[str, str]:
    """Las TRES DSN del stack, una por rol, con las credenciales generadas.

    El reparto lo fijó prod-14 (``task_prod14_05`` / tenancy-2) y vive escrito en
    los cuatro ``config.py`` del runtime. Lo que hay que tener presente al leer
    esto: **el `.env` gana al default de pydantic**, así que la postura real de
    una instalación no la decide `config.py`, la decide este generador. Hasta el
    2026-08-27 devolvía a los workers, al dispatcher y a la superficie /admin al
    PROPIETARIO del esquema, deshaciendo prod-14 sin que ninguna guarda lo viera
    (las de prod-14 leen los `config.py`, no el `.env`).

    * ``DATABASE_URL`` — ``app_user``, NOBYPASSRLS. El camino de las peticiones
      humanas, con ``app.tenant_id`` fijado por el middleware. Es el rol que hace
      que la RLS proteja algo.
    * ``SERVICE_DATABASE_URL`` — ``service_user``, BYPASSRLS y **sin DDL**. Lo
      que corren los servicios de larga vida que trabajan cruzando tenants:
      workers (la superficie más expuesta, porque ejecutan las tools de los
      agentes), orchestrator, notification-dispatcher y los endpoints /admin.
      Ven todas las filas; lo que no pueden es apagar la RLS.
    * ``ADMIN_DATABASE_URL`` — ``migrations_user``, dueño del esquema con
      ``GRANT ALL``. SÓLO el one-shot ``migrations`` (Alembic necesita DDL) y el
      ``pg_dump`` del backup, que necesita al dueño para volcarlo todo.

    Los tres apuntan al servicio ``postgres`` por la red del compose, y las
    contraseñas son las generadas, así que ninguna DSN lleva marcador de
    desarrollo.
    """

    db_name = "agentic_platform"
    app_url = f"postgresql+asyncpg://app_user:{secrets_.app_user_password}@postgres:5432/{db_name}"
    service_url = (
        f"postgresql+asyncpg://service_user:{secrets_.service_user_password}"
        f"@postgres:5432/{db_name}"
    )
    admin_url = (
        f"postgresql+asyncpg://migrations_user:{secrets_.migrations_user_password}"
        f"@postgres:5432/{db_name}"
    )
    return {
        "DATABASE_URL": app_url,
        "SERVICE_DATABASE_URL": service_url,
        "ADMIN_DATABASE_URL": admin_url,
    }


def _redis_url(secrets_: GeneratedSecrets, db: int) -> str:
    """DSN autenticada contra el Redis del stack (``requirepass``, prod-10).

    Redis no tiene usuario en este stack, sólo contraseña, así que la forma es
    ``redis://:<clave>@redis:6379/<db>`` — con los dos puntos y el usuario vacío.

    El valor va **en claro** y sin percent-encoding a propósito: el compose
    construye estas mismas DSN interpolando ``${REDIS_PASSWORD}`` tal cual, y las
    dos formas sólo coinciden mientras el secreto no lleve un carácter reservado
    de URL. Codificarlo aquí y no allí es como se consigue que el `.env` y el
    compose se autentiquen con cadenas distintas. La invariante que lo sostiene
    —que ``_token()`` es URL-safe— la comprueba
    ``test_every_secret_that_travels_inside_a_url_is_url_safe``.
    """

    return f"redis://:{secrets_.redis_password}@redis:6379/{db}"


def _backup_env(cfg: InstallerConfig, secrets_: GeneratedSecrets) -> dict[str, str]:
    """Las variables que describen QUÉ respalda el backup en ESTE stack (prod-04).

    Hallazgo deploy-4. El motor de backup es correcto y sus defaults son los de
    **desarrollo**: `pg_dump` contra `localhost:15432` con credencial `changeme-`,
    y `tar` de los *named volumes* que existen en el stack de manuales. El compose
    que genera el instalador no se parece a eso: monta **binds** bajo
    ``{data_root}`` y no declara ningún named volume. El resultado era un backup
    diario que fallaba todas las noches — el peor sitio donde tener un fallo
    silencioso, porque no se nota hasta el día del desastre.

    Las cuatro claves de aquí las emite quien conoce el layout (este generador),
    no quien tiene que adivinarlo:

    * ``WORKERS_BACKUP_DATABASE_URL`` — DSN en forma **libpq** (sin el sufijo
      ``+asyncpg`` de SQLAlchemy, que libpq no entiende) contra el servicio
      ``postgres`` del compose y con la credencial generada.
    * ``WORKERS_BACKUP_VOLUMES`` — vacío **a propósito**: en este layout no hay
      named volumes que tarear. Explícito y no por omisión, para que el default
      de dev no se cuele.
    * ``WORKERS_BACKUP_BIND_PATHS`` — los dos stores que se capturan por
      filesystem: los objetos de MinIO y el file backend de Vault (sin él, ningún
      secreto del stack restaurado se puede descifrar). Deliberadamente NO el data
      root entero: eso metía el ``PGDATA`` vivo (copia rota, y el «file changed as
      we read it» de tar tira el bundle) y los modelos de Ollama (decenas de GB
      re-descargables) en cada bundle nocturno. ``clamav`` queda fuera por lo
      mismo: firmas de virus que `freshclam` recupera solo.
    * ``WORKERS_BACKUP_PROJECTS_ROOT`` — los bare repos, que viajan como su
      propio artefacto verificado (``projects_tar``, task_prod_04_05).
    * ``WORKERS_BACKUP_REDIS_DIR`` — Redis, que NO va como bind: tiene artefacto
      propio precedido de un ``BGREWRITEAOF`` completado, porque copiar un
      ``appendonlydir`` mientras el servidor le escribe es la captura en caliente
      que task_prod_04_06 quita.
    * ``WORKERS_BACKUP_STABLE_SNAPSHOT_PATHS`` — el árbol de Vault, cuya captura
      se verifica estable (huella antes/después). No MinIO: se escribe todo el
      rato por diseño y exigirle estabilidad sería un fallo nocturno garantizado.
    """
    data_root = cfg.storage.data_root.rstrip("/")
    vault_dir = f"{data_root}/vault"
    return {
        "WORKERS_BACKUP_ROOT": f"{data_root}/backups",
        "WORKERS_BACKUP_DATABASE_URL": (
            f"postgresql://migrations_user:{secrets_.migrations_user_password}"
            f"@postgres:5432/agentic_platform"
        ),
        "WORKERS_BACKUP_VOLUMES": json.dumps([]),
        "WORKERS_BACKUP_BIND_PATHS": json.dumps([f"{data_root}/minio", vault_dir]),
        "WORKERS_BACKUP_PROJECTS_ROOT": f"{data_root}/projects",
        "WORKERS_BACKUP_REDIS_DIR": f"{data_root}/redis",
        "WORKERS_BACKUP_STABLE_SNAPSHOT_PATHS": json.dumps([vault_dir]),
    }


def build_env_vars(
    cfg: InstallerConfig,
    secrets_: GeneratedSecrets,
    *,
    monitoring: bool = False,
) -> dict[str, str]:
    """Assemble the full ordered map of environment variables for the ``.env``.

    Every value is concrete (generated secret or wizard-derived config) so a
    production ``.env`` rendered from this map passes the runtime services' prod
    secret guard. Provider wiring (which ADR-0021 providers are enabled +
    endpoints) is included; provider *credentials* live in Vault (task 15_09),
    not here. The deployment ``ENVIRONMENT`` markers (the bare + per-service
    prefixed ones) are set so the guard actually runs in staging/prod.

    Lo que este mapa NO consume del ``install.yaml``, dicho aquí porque no se
    deduce leyendo el código y hoy el operador lo teclea creyendo lo contrario:

    * ``storage.minio_access_key`` / ``minio_secret_key`` — campos OBLIGATORIOS
      que se descartan. ``MINIO_ROOT_USER``/``MINIO_ROOT_PASSWORD`` salen de
      :func:`generate_secrets`, que es más fuerte que lo que teclee nadie y es lo
      que el gate e2e de instalación da por supuesto. El descarte lo fija
      ``test_the_profile_placeholder_never_reaches_the_generated_env``.
    * ``providers.*.api_key`` / ``oauth_token`` — el bootstrap de Vault sólo hace
      la orquestación (init → unseal → KV → políticas); escribir VALORES en el KV
      es dominio de prod-10, que todavía no existe. Así que hoy no hay destino
      para esas credenciales y no se escriben en ningún artefacto.

    Que se descarten es defendible; lo que no lo es es PEDIRLAS afirmando que van
    a Vault. Esa mitad no vive en este fichero — está en
    ``scripts/install-profiles/*.yaml`` (que promete «el instalador escribe los
    secretos generados en Vault»), en la obligatoriedad de
    :class:`~installer_backend.config.StorageConfig` y en el runbook de
    producción, y sigue abierta.
    """

    runtime_env = _RUNTIME_ENVIRONMENT[cfg.system.environment]
    db_urls = _database_urls(secrets_)

    env: dict[str, str] = {
        # --- deployment environment (drives the runtime services' guard) ---
        "ENVIRONMENT": runtime_env,
        "PLATFORM_DOMAIN": cfg.system.domain,
        # --- PostgreSQL ---
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": secrets_.postgres_password,
        "POSTGRES_DB": "agentic_platform",
        "POSTGRES_PORT": "5432",
        "MIGRATIONS_USER_PASSWORD": secrets_.migrations_user_password,
        "APP_USER_PASSWORD": secrets_.app_user_password,
        # prod-14 task_prod14_04. La consume `stack/postgres/init/
        # 05-service-role-password.sh` en el PRIMER arranque para corregir el
        # literal de desarrollo con el que `04-service-role.sql` crea el rol. Sin
        # ella, el rol que se salta la RLS de todos los tenants nace con una
        # contraseña escrita en este repositorio.
        "SERVICE_USER_PASSWORD": secrets_.service_user_password,
        # --- derived DSNs the services read directly ---
        "DATABASE_URL": db_urls["DATABASE_URL"],
        "SERVICE_DATABASE_URL": db_urls["SERVICE_DATABASE_URL"],
        "ADMIN_DATABASE_URL": db_urls["ADMIN_DATABASE_URL"],
        # --- Redis ---
        # prod-10 secrets-7: `requirepass` obligatorio. Ahí viven las sesiones de
        # servidor, el broker de Celery y los contadores de rate limit.
        "REDIS_PASSWORD": secrets_.redis_password,
        "REDIS_URL": _redis_url(secrets_, 0),
        "REDIS_MAX_MEM": "512mb",
        "REDIS_PORT": "6379",
        # --- MinIO ---
        "MINIO_ROOT_USER": secrets_.minio_root_user,
        "MINIO_ROOT_PASSWORD": secrets_.minio_root_password,
        "MINIO_ACCESS_KEY": secrets_.minio_root_user,
        "MINIO_SECRET_KEY": secrets_.minio_root_password,
        "MINIO_BUCKET": cfg.storage.minio_bucket,
        "MINIO_API_PORT": "9000",
        "MINIO_CONSOLE_PORT": str(cfg.ports.minio_console),
        # --- Vault (real token written by the bootstrap, task 15_09) ---
        "VAULT_ADDR": "http://vault:8200",
        "VAULT_PORT": "8200",
        # --- api-server secrets (API_SERVER_ prefixed) ---
        "API_SERVER_ENVIRONMENT": runtime_env,
        "API_SERVER_JWT_SECRET": secrets_.jwt_secret,
        # ADR 0136. Lleva prefijo `API_SERVER_` aunque el WORKER también lo
        # necesite: quien mintea el token del sandbox es el worker, pero lo hace
        # importando `mint_agent_token` del paquete del api-server, así que lee
        # `api_server.config` y por tanto las variables `API_SERVER_*`.
        "API_SERVER_INTERNAL_TOKEN_SECRET": secrets_.internal_token_secret,
        "API_SERVER_ALERTS_INGEST_TOKEN": secrets_.alerts_ingest_token,
        "API_SERVER_REVIEW_URL_SIGNING_SECRET": secrets_.review_url_signing_secret,
        "API_SERVER_SSO_ENCRYPTION_KEY": secrets_.sso_encryption_key,
        "API_SERVER_NOTIFICATION_ENCRYPTION_KEY": secrets_.notification_encryption_key,
        "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY": secrets_.incoming_webhook_encryption_key,
        "API_SERVER_MINIO_ACCESS_KEY": secrets_.minio_root_user,
        "API_SERVER_MINIO_SECRET_KEY": secrets_.minio_root_password,
        "API_SERVER_DATABASE_URL": db_urls["DATABASE_URL"],
        # La superficie /admin: BYPASSRLS para leer cruzando tenants e insertar
        # en `audit_log` sin `app.tenant_id`, pero SIN DDL. Con el dueño del
        # esquema, `ALTER TABLE … DISABLE ROW LEVEL SECURITY` quedaba dentro del
        # radio de explosión de /admin (prod-14 task_prod14_05).
        "API_SERVER_ADMIN_DATABASE_URL": db_urls["SERVICE_DATABASE_URL"],
        # --- workers (WORKERS_ prefixed) ---
        "WORKERS_ENVIRONMENT": runtime_env,
        # Los workers ejecutan las tools de los agentes LLM: son la superficie
        # más expuesta de la plataforma. Con el dueño del esquema, desde su DSN
        # se apaga el aislamiento multi-tenant de TODA la instalación sin tocar
        # una fila. BYPASSRLS sí (escriben `executions` cruzando tenants), DDL no.
        "WORKERS_DATABASE_URL": db_urls["SERVICE_DATABASE_URL"],
        "WORKERS_DATA_ROOT": cfg.storage.data_root,
        # Backup: el QUÉ se respalda depende del layout que genera el instalador,
        # así que se emite aquí y no se hereda del default de dev (prod-04).
        **_backup_env(cfg, secrets_),
        # --- orchestrator (ORCHESTRATOR_ prefixed) ---
        "ORCHESTRATOR_ENVIRONMENT": runtime_env,
        # El error espejo del de los workers, y el que falla en SILENCIO: con
        # `app_user` (NOBYPASSRLS) y sin un solo `set_config('app.tenant_id')` en
        # todo `apps/orchestrator/src/`, las policies filtran cada fila y el
        # despachador no ve NINGUNA tarea. El stack sube entero `healthy` y los
        # planes se quedan en `pending` para siempre: cero filas es una respuesta
        # válida de SQL, así que no hay error ni traza que seguir.
        "ORCHESTRATOR_DATABASE_URL": db_urls["SERVICE_DATABASE_URL"],
        # --- notification-dispatcher (NOTIFY_ prefixed) ---
        # MUST match API_SERVER_NOTIFICATION_ENCRYPTION_KEY (write/read pair).
        "NOTIFY_ENVIRONMENT": runtime_env,
        # Entrega cruzando tenants (BYPASSRLS), y valida `row.tenant_id ==
        # request.tenant_id` en el límite de la tarea Celery porque la RLS no
        # puede cazar un payload manipulado. DDL nunca lo necesitó.
        "NOTIFY_DATABASE_URL": db_urls["SERVICE_DATABASE_URL"],
        "NOTIFY_NOTIFICATION_ENCRYPTION_KEY": secrets_.notification_encryption_key,
        # --- platform image pins (consumed by the generated compose) ---
        "PLATFORM_IMAGE_TAG": "v1.0.0",
        "PLATFORM_REGISTRY": "ghcr.io/daycry",
    }

    # Provider wiring (non-secret toggles + endpoints). Credentials go to Vault.
    providers = cfg.providers
    if providers.claude_sdk.enabled:
        env["LLM_CLAUDE_SDK_ENABLED"] = "true"
    if providers.copilot.enabled:
        env["LLM_COPILOT_ENABLED"] = "true"
    if providers.azure_foundry.enabled:
        env["LLM_AZURE_FOUNDRY_ENABLED"] = "true"
        if providers.azure_foundry.apim_endpoint:
            env["LLM_AZURE_FOUNDRY_ENDPOINT"] = providers.azure_foundry.apim_endpoint
    if providers.ollama.enabled:
        env["LLM_OLLAMA_ENABLED"] = "true"
        if providers.ollama.endpoint:
            env["LLM_OLLAMA_ENDPOINT"] = providers.ollama.endpoint
        elif cfg.resources.ollama_mode != "none":
            env["LLM_OLLAMA_ENDPOINT"] = "http://ollama:11434"

    # In-stack Ollama service (ADR 0056 — cpu or gpu).
    if cfg.resources.ollama_mode != "none":
        env["OLLAMA_PORT"] = "11434"

    # Monitoring overlay (Grafana admin password only when the overlay is on).
    if monitoring:
        env["GRAFANA_ADMIN_USER"] = "admin"
        env["GRAFANA_ADMIN_PASSWORD"] = secrets_.grafana_admin_password
        env["PROMETHEUS_PORT"] = "9090"

    return env


def _quote_env_value(value: str) -> str:
    """Double-quote a value only when it contains whitespace or a ``#``.

    Generated secrets are URL-safe base64 (no spaces, quotes or ``#``), so they
    never need quoting; a wizard-provided domain/endpoint *might*, so we quote
    defensively. We never emit a value that would break ``docker compose``'s
    dotenv parsing.
    """

    if value == "" or any(c in value for c in " \t#'\""):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def render_env_file(env: dict[str, str]) -> str:
    """Serialise the env map to deterministic ``.env`` text (dotenv format).

    Emits ``KEY=value`` lines in insertion order (so the file reads top-to-bottom
    like ``docker/.env.example``), prefixed by a header that states the file
    holds generated secrets and must never be committed. Performs NO I/O — the
    text is what the :class:`EnvFileWriter` seam writes at install time.
    """

    header = (
        "# Generated by the Agentic Platform installer (Plan 15).\n"
        "# Contains GENERATED high-entropy secrets — DO NOT commit, DO NOT log.\n"
        "# Regenerating this file rotates every secret; coordinate with Vault.\n"
    )
    lines = [f"{key}={_quote_env_value(value)}" for key, value in env.items()]
    return header + "\n".join(lines) + "\n"


def generate_env_file(
    cfg: InstallerConfig,
    secrets_: GeneratedSecrets,
    *,
    monitoring: bool = False,
) -> str:
    """Build the full ``.env`` text from the wizard config + generated secrets."""

    return render_env_file(build_env_vars(cfg, secrets_, monitoring=monitoring))


def assert_env_passes_prod_secret_guard(env_text: str) -> None:
    """Raise ``ValueError`` if a dev-default marker leaked into a prod ``.env``.

    Belt-and-braces self-check mirroring the runtime services'
    ``_DEV_SECRET_MARKERS`` guard: a production ``.env`` from this generator must
    contain NONE of ``changeme`` / ``dev-only`` / ``minioadmin``. The CLI/wizard
    can call this right after generating a production ``.env``.
    """

    lowered = env_text.lower()
    found = [marker for marker in _DEV_SECRET_MARKERS if marker in lowered]
    if found:
        raise ValueError(
            "El .env generado para producción contiene marcadores de secreto de "
            f"desarrollo: {', '.join(found)}."
        )


# ---------------------------------------------------------------------------
# config/global.yaml — the non-secret platform config.
# ---------------------------------------------------------------------------
def generate_global_config(
    cfg: InstallerConfig,
    *,
    monitoring: bool = False,
) -> dict[str, Any]:
    """Build the non-secret ``config/global.yaml`` mapping from the wizard config.

    Carries ONLY non-secret platform config: the domain + environment, which
    ADR-0021 providers are enabled (names only, never credentials), the resource
    sizing, the storage layout, the monitoring flag and the supported languages
    (ES + EN only, per CLAUDE.md principle 12). Serialise with
    :func:`render_global_yaml`.
    """

    provider_names = [kind.value for kind in enabled_providers(cfg)]
    return {
        "version": 1,
        "platform": {
            "domain": cfg.system.domain,
            "environment": cfg.system.environment.value,
            "languages": ["es", "en"],
        },
        "resources": {
            "worker_replicas": cfg.resources.worker_replicas,
            "worker_memory_gib": cfg.resources.worker_memory_gib,
            "ollama_mode": cfg.resources.ollama_mode,
            "embedding_model": cfg.resources.embedding_model,
            "gpu_enabled": cfg.resources.gpu_enabled,
        },
        "storage": {
            "data_root": cfg.storage.data_root,
            "minio_bucket": cfg.storage.minio_bucket,
        },
        "providers": {
            "enabled": provider_names,
        },
        "monitoring": {
            "enabled": monitoring,
        },
        "tenant": {
            "name": cfg.tenant.tenant_name,
            "admin_email": str(cfg.tenant.admin_email),
        },
    }


def render_global_yaml(config: dict[str, Any]) -> str:
    """Serialise the global config mapping to deterministic YAML text.

    ``sort_keys=False`` preserves the section ordering; the output is what the
    install seam writes to ``config/global.yaml``. Performs NO I/O.
    """

    text: str = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, width=100)
    return text


# ---------------------------------------------------------------------------
# /data/agent-platform/ directory plan.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DataDir:
    """One directory in the install's data-tree plan.

    ``path`` is the absolute POSIX path; ``mode`` is the octal POSIX permission
    the provisioner sets (e.g. ``0o700`` for secret-bearing dirs like Vault's
    file backend, ``0o750`` for the rest). ``description`` documents intent.
    """

    path: str
    mode: int
    description: str


#: Sub-paths (relative to the data root) the install must create, with the
#: POSIX mode each gets. Secret-bearing dirs (Vault file/logs) are 0o700; the
#: rest are 0o750. Kept in lockstep with the compose generator's bind mounts.
_DATA_SUBDIRS: tuple[tuple[str, int, str], ...] = (
    ("postgres", 0o700, "PostgreSQL data directory (PGDATA)."),
    ("redis", 0o750, "Redis append-only-file + snapshots."),
    ("minio", 0o750, "MinIO object-store backend."),
    ("vault/file", 0o700, "Vault file storage backend (secret material)."),
    ("vault/logs", 0o700, "Vault audit logs (sensitive)."),
    ("clamav", 0o750, "ClamAV virus signature database."),
    ("caddy/data", 0o700, "Caddy data dir: internal CA + ACME certs (sensitive)."),
    ("caddy/config", 0o750, "Caddy autosave config."),
    ("caddy/tls", 0o700, "Operator-supplied corporate cert+key (tls_mode=provided)."),
    ("projects", 0o750, "Per-tenant/project bare repos (repos/*.git)."),
    ("worktrees", 0o750, "Per-task git worktrees (transient checkouts)."),
    ("dep-cache", 0o750, "Shared dependency cache across worktrees."),
    ("backups", 0o700, "Backup bundles (Plan 12) — may contain dumps."),
    ("ollama", 0o750, "Local Ollama models (ollama_mode cpu/gpu)."),
    ("prometheus", 0o750, "Prometheus TSDB (monitoring overlay)."),
    ("alertmanager", 0o750, "Alertmanager state (monitoring overlay)."),
    ("grafana", 0o750, "Grafana state (monitoring overlay)."),
)


def build_data_tree_plan(
    cfg: InstallerConfig,
    *,
    monitoring: bool = False,
) -> list[DataDir]:
    """Build the ordered list of directories the install creates under the root.

    The root itself (``cfg.storage.data_root``) comes first at ``0o750``, then
    every sub-directory the stateful services bind-mount. The GPU (``ollama``)
    and monitoring (``prometheus`` / ``alertmanager`` / ``grafana``) dirs are
    included only when those features are on, mirroring the compose generator's
    service selection. Returns a pure plan (no ``mkdir`` happens here — that's
    the :class:`DataTreeProvisioner` seam).
    """

    root = cfg.storage.data_root
    skip: set[str] = set()
    if cfg.resources.ollama_mode == "none":
        skip.add("ollama")
    if not monitoring:
        skip.update({"prometheus", "alertmanager", "grafana"})

    plan: list[DataDir] = [
        DataDir(path=root, mode=0o750, description="Platform data root."),
    ]
    for sub, mode, desc in _DATA_SUBDIRS:
        if sub.split("/", 1)[0] in skip:
            continue
        plan.append(DataDir(path=f"{root}/{sub}", mode=mode, description=desc))
    return plan


def build_stack_dirs_plan(compose_dir: str, *, monitoring: bool = False) -> list[DataDir]:
    """Directorios del subárbol ``stack/`` que ningún fichero trae consigo.

    Casi todo ``stack/`` aparece solo: el ``EnvFileWriter`` hace
    ``mkdir(parents=True)`` al escribir cada auxiliar, así que sus directorios
    existen porque existe su contenido. La excepción es un bind que monta un
    directorio **vacío**: no hay fichero que lo cree, y si no está, Docker lo crea
    él —como ``root``— con el contenedor corriendo como usuario sin privilegios y
    sin poder leerlo. Es el mismo motivo por el que el equivalente del stack de
    desarrollo se versiona con un ``.gitignore`` dentro
    (``docker/monitoring/alertmanager/secrets/.gitignore``).

    Cuelga del **directorio del compose**, no de ``storage.data_root``: es donde
    resuelven los ``./stack/…`` del compose generado y donde se escriben los demás
    auxiliares. Hoy el instalador los hace coincidir (``cli.py`` →
    ``compose_dir = config.storage.data_root``), y precisamente por eso conviene
    que este cálculo no dependa de que sigan coincidiendo.
    """

    if not monitoring:
        return []
    stack = f"{compose_dir}/{STACK_ASSETS_DIR_NAME}"
    return [
        DataDir(
            # 0o755 y no 0o700 pese a ser un buzón de credenciales: el fichero
            # que el operador deja aquí (`slack_api_url`) lo lee Alertmanager
            # DESDE DENTRO del contenedor y como usuario sin privilegios. Un
            # directorio que él no puede atravesar rompe el receiver de respaldo
            # EN SILENCIO —`api_url_file` se lee al notificar, no al cargar la
            # config— y precisamente en el escenario para el que existe. El
            # secreto lo protege el modo del fichero, no el del buzón; lo que sí
            # se mantiene es que no sea escribible por otros.
            path=f"{stack}/monitoring/alertmanager/secrets",
            mode=0o755,
            description="Alertmanager fallback-receiver credential mailbox (operator-filled).",
        )
    ]


# ---------------------------------------------------------------------------
# Injectable seams — everything that touches the host. In-memory fakes for
# tests; the real bindings (file writes + mkdir/chmod) land at install time and
# are exercised only by the plan's Tests Humanos.
# ---------------------------------------------------------------------------
@runtime_checkable
class EnvFileWriter(Protocol):
    """Writes the generated ``.env`` / ``config/global.yaml`` to disk.

    The real binding writes the file with ``0o600`` perms (it holds secrets).
    The fake records the write so tests assert the path + content without
    touching disk.
    """

    def write(self, path: str, content: str, *, mode: int) -> None:
        """Write *content* to *path* with POSIX *mode*."""
        ...


@runtime_checkable
class DataTreeProvisioner(Protocol):
    """Creates the data-tree directories with their POSIX permissions.

    The real binding ``mkdir -p`` + ``chmod``s each dir under
    ``/data/agent-platform``. The fake records the plan it was asked to create.
    """

    def provision(self, plan: list[DataDir]) -> None:
        """Create every directory in *plan* with its declared mode."""
        ...


@dataclass
class FakeEnvFileWriter:
    """Records ``.env``/YAML writes instead of touching disk (test default)."""

    written: dict[str, str] = field(default_factory=dict)
    modes: dict[str, int] = field(default_factory=dict)

    def write(self, path: str, content: str, *, mode: int) -> None:
        self.written[path] = content
        self.modes[path] = mode


@dataclass
class FakeDataTreeProvisioner:
    """Records the data-tree plan it was asked to provision (test default)."""

    provisioned: list[DataDir] = field(default_factory=list)

    def provision(self, plan: list[DataDir]) -> None:
        self.provisioned.extend(plan)
