"""Runtime configuration for the api-server service.

Settings are loaded from environment variables (and a local `.env` file
when running outside Docker) via pydantic-settings. Phase 15's installer
will switch JWT_SECRET to a Vault-backed source; until then it lives in
the environment.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from math import log2
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Import-safe by construction: `auth.crypto_keys` depends only on `cryptography`
# (and `api_server.auth` is an empty package), so there is no cycle back to this
# module. It owns the ONE key-ring parser + the ONE key derivation.
from api_server.auth.crypto_keys import parse_key_ring

# Substrings that flag a value as a known dev-only default. A staging/prod
# deployment that still carries any of these is misconfigured (Plan 06.14
# task_06_14_03 / secrets-config-1/2/3/5/7).
_DEV_SECRET_MARKERS = ("changeme", "dev-only", "minioadmin")

# The CLOSED set of deployment environments (prod-09 task_prod09_02, authz-2).
# It used to be a free-form string, and every guard in the codebase asked
# "environment in {staging, prod}?" — so ANY unrecognised value (a typo like
# `production`, an empty var, a stray `PROD ` with a trailing space) silently
# meant "dev": no dev-secret guard, no admin MFA, no IP allowlist, no short admin
# session. A misspelling downgraded the whole security posture without a single
# log line. The enum makes that impossible: an unknown value FAILS THE ARRANCADA.
_KNOWN_ENVIRONMENTS = frozenset({"dev", "staging", "prod"})

# Environments where the deployment is expected to be real, i.e. everything that
# is not `dev`. Written as "not dev" rather than "in {staging, prod}" on purpose:
# adding a fourth environment later must default to ENFORCING the guards, not to
# skipping them.
_DEV_ENVIRONMENT = "dev"

# Minimum length of an HMAC signing secret outside dev. HS256 keys shorter than
# the hash output (32 bytes) weaken the MAC and, far more importantly in
# practice, a short secret is a guessable/brute-forceable one — and this secret
# mints SESSIONS. 32 chars is the floor, not a recommendation (the installer
# generates 48+). Applies to the two secrets that sign BEARER TOKENS
# (`jwt_secret`, `internal_token_secret`); prod-10 (secrets-3) generalises the
# length floor to the rest of the secret families.
_MIN_HMAC_SECRET_LEN = 32

# --- Suelo de longitud y entropía (prod-10 task_prod10_04, secrets-3) --------
#
# El marcador-substring de arriba sólo reconoce los defaults que ESTE repo
# publica. `"x" * 48` no lleva ninguno, mide más que el suelo HMAC y arrancaba
# producción tan campante: firma sesiones y cifra secretos exactamente igual de
# bien que la cadena del instalador, y se adivina en un intento. El plan pedía
# complementar el marcador «con un mínimo de 24 caracteres y rechazo de valores
# de entropía trivial».
#
# Dos criterios, porque cada uno tapa el agujero del otro:
#   * longitud: aleatorio pero corto sigue siendo adivinable;
#   * variedad: `"x" * 48` es largo y no tiene ninguna. Se exige un mínimo de
#     caracteres distintos Y un mínimo de entropía de Shannon, porque «distintos»
#     a secas lo esquiva `"a"*40 + "bcdefghi"` (9 distintos, trivial igual).
#
# Los umbrales son deliberadamente BAJOS. `secrets.token_urlsafe(36)` —lo que
# genera el instalador— da ~30 caracteres distintos y ~5,3 bits/carácter: pasa
# con seis veces de margen. Lo que se persigue es el relleno de plantilla, no la
# contraseña mediocre de un operador con prisa; un falso positivo aquí no es un
# aviso, es un servicio que no arranca (riesgo 2 del plan).
_MIN_SECRET_LEN = 24
_MIN_DISTINCT_CHARS = 8
_MIN_SHANNON_BITS_PER_CHAR = 2.0


def _shannon_bits_per_char(value: str) -> float:
    """Entropía de Shannon del valor, en bits por carácter.

    Es una medida de la DISTRIBUCIÓN de caracteres, no de la aleatoriedad real
    (nada distingue aquí una cadena de `/dev/urandom` de una barajada a mano), y
    eso basta: lo único que tiene que detectar es el relleno.
    """
    if not value:
        return 0.0
    total = len(value)
    counts = Counter(value)
    return -sum((n / total) * log2(n / total) for n in counts.values())


def _trivial_secret_reason(value: str) -> str | None:
    """``None`` si el secreto supera el suelo; si no, POR QUÉ no lo supera.

    Devolver la razón —y no un booleano— es lo que permite que el error de
    arranque le diga al operador qué arreglar. Un «secreto inválido» a secas le
    deja probando cadenas.
    """
    if len(value) < _MIN_SECRET_LEN:
        return f"only {len(value)} chars, minimum is {_MIN_SECRET_LEN}"
    distinct = len(set(value))
    if distinct < _MIN_DISTINCT_CHARS:
        return f"only {distinct} distinct characters, minimum is {_MIN_DISTINCT_CHARS}"
    bits = _shannon_bits_per_char(value)
    if bits < _MIN_SHANNON_BITS_PER_CHAR:
        return (
            f"entropy is {bits:.2f} bits/char, minimum is "
            f"{_MIN_SHANNON_BITS_PER_CHAR:.1f} (the value looks like filler)"
        )
    return None


#: Familias sujetas al suelo: campo -> (variable de entorno, atributo a leer).
#:
#: Una sola fuente de verdad, a propósito. `Settings._weak_secrets` la recorre y
#: `entropy_checked_secret_fields()` la publica, así que el test de descubrimiento
#: mira exactamente lo mismo que el guard aplica: añadir aquí una familia nueva y
#: olvidarse de probarla pone en rojo `test_secret_entropy_guard.py`, en vez de
#: dejar la parametrización pasando en vacío sobre las de siempre.
#:
#: El atributo es un anillo (`tuple[str, ...]`) donde la familia tiene lista de
#: claves, y un `SecretStr` donde el valor es único; `_weak_secrets` distingue por
#: tipo en vez de por una tabla de flags que habría que mantener sincronizada.
_ENTROPY_CHECKED_FIELDS: dict[str, tuple[str, str]] = {
    "jwt_secret": ("API_SERVER_JWT_SECRET(S)", "jwt_secret_ring"),
    "internal_token_secret": (
        "API_SERVER_INTERNAL_TOKEN_SECRET(S)",
        "internal_token_secret_ring",
    ),
    "review_url_signing_secret": (
        "API_SERVER_REVIEW_URL_SIGNING_SECRET",
        "review_url_signing_secret",
    ),
    "sso_encryption_key": ("API_SERVER_SSO_ENCRYPTION_KEY(S)", "sso_encryption_key_ring"),
    "notification_encryption_key": (
        "API_SERVER_NOTIFICATION_ENCRYPTION_KEY(S)",
        "notification_encryption_key_ring",
    ),
    "incoming_webhook_encryption_key": (
        "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY(S)",
        "incoming_webhook_encryption_key_ring",
    ),
    "minio_secret_key": ("API_SERVER_MINIO_SECRET_KEY", "minio_secret_key"),
    "mfa_encryption_key": ("API_SERVER_MFA_ENCRYPTION_KEY(S)", "mfa_encryption_key_ring"),
}


def entropy_checked_secret_fields() -> tuple[str, ...]:
    """Las familias de secretos con suelo de longitud/entropía en staging/prod."""
    return tuple(_ENTROPY_CHECKED_FIELDS)


# Hosts que delatan un despliegue local. Se usan para decidir si un
# `environment` que NO se declaró explícitamente puede seguir contando como
# `dev` (prod-10 task_prod10_04 / hallazgo secrets-3; ver
# `_forbid_dev_secrets_outside_dev`).
_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})


def _dsn_host_is_local(dsn: str) -> bool:
    """¿El DSN apunta a la propia máquina?

    Es el desempate para un `environment` sin declarar: un `postgres:5432` (o un
    host de verdad) significa despliegue real; `localhost` significa el portátil
    de alguien. Si el DSN no se puede parsear se responde ``False`` —
    fail-CLOSED: la duda cuenta como despliegue real, que es el lado en el que
    equivocarse sólo cuesta un mensaje de error.
    """
    try:
        host = urlsplit(dsn).hostname
    except ValueError:
        return False
    return bool(host) and host in _LOCAL_DB_HOSTS


class Settings(BaseSettings):
    """All env-driven knobs for api-server."""

    # ----- PostgreSQL -----
    database_url: str = Field(
        default="postgresql+asyncpg://app_user:changeme-app-dev-only"
        "@localhost:15432/agentic_platform",
        description="SQLAlchemy URL for the *application* role (NOBYPASSRLS).",
    )
    admin_database_url: str = Field(
        default="postgresql+asyncpg://service_user:changeme-service-dev-only"
        "@localhost:15432/agentic_platform",
        description=(
            "SQLAlchemy URL for the System Admin endpoints. Connects as "
            "`service_user`: BYPASSRLS so cross-tenant reads and audit_log "
            "inserts go through without setting app.tenant_id, but NO DDL "
            "(prod-14 task_05 / tenancy-2). It used to be `migrations_user`, the "
            "schema OWNER with GRANT ALL, which put `ALTER TABLE ... DISABLE ROW "
            "LEVEL SECURITY` inside the blast radius of the /admin surface. "
            "`migrations_user` is now referenced only by Alembic (migrations/env.py)."
        ),
    )

    # ----- Pool de conexiones (prod-13 task_prod13_06, hallazgo db-2/perf-2) --
    # Los cuatro engines iban con los defaults de SQLAlchemy (pool_size=5 +
    # max_overflow=10 = 15 conexiones, pool_timeout=30 s, sin reciclado). Con la
    # transacción por request retenida durante el turno LLM completo, ~15 chats
    # concurrentes agotaban el pool y TODA la API empezaba a dar TimeoutError.
    # Ahora son settings de entorno, con los defaults de la decisión clave 4 del
    # plan para un despliegue de una sola máquina.
    db_pool_size: int = Field(
        default=10,
        ge=1,
        description=(
            "Conexiones que cada engine mantiene abiertas de forma permanente. "
            "El total contra PostgreSQL es (pool_size + max_overflow) x nº de "
            "engines del proceso, y tiene que caber en `max_connections`."
        ),
    )
    db_max_overflow: int = Field(
        default=20,
        ge=0,
        description=(
            "Conexiones EXTRA que el pool puede abrir por encima de `pool_size` "
            "en un pico, y que cierra al devolverlas."
        ),
    )
    db_pool_timeout: float = Field(
        default=10.0,
        gt=0,
        description=(
            "Segundos que una request espera por una conexión libre antes de "
            "fallar. Corto a propósito: 30 s de espera es una request que el "
            "cliente ya abandonó, y mientras espera retiene su worker."
        ),
    )
    db_pool_recycle: int = Field(
        default=1800,
        description=(
            "Segundos tras los que una conexión se descarta y se vuelve a abrir. "
            "Evita heredar conexiones que un pgbouncer/firewall intermedio dio "
            "por muertas. -1 desactiva el reciclado."
        ),
    )

    # ----- Auth / sessions -----
    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-only-jwt-secret-change-me"),
        description="HMAC secret for signing JWTs.",
    )
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60 * 24  # 24h

    # ----- Internal worker->api tokens (prod-09 task_prod09_03, secrets-9) -----
    # HMAC secret for the `AGENTIC_INTERNAL_TOKEN` the worker mints per agent run
    # (`auth/internal_agent.mint_agent_token`) and the api-server verifies on
    # `/internal/agent/*`. It used to be `jwt_secret` — the SAME key that signs
    # human SESSIONS — which put "can forge a System-Admin session" inside the
    # blast radius of the workers container. The workers container legitimately
    # holds this secret; it must NOT be able to mint user sessions with it.
    #
    # SEPARATE CRYPTOGRAPHIC DOMAINS, not merely separate values: agent tokens are
    # already discriminated by the `kind=agent` claim, but a claim only helps if
    # the verifier checks it — a shared key means one forgotten check is a full
    # privilege escalation. Different keys make the two token families
    # unforgeable across domains by construction.
    #
    # DEPLOYMENT (see docker/docker-compose.yml): the workers container must
    # receive `API_SERVER_INTERNAL_TOKEN_SECRET` with the same value as the
    # api-server (the worker mints through `api_server.config`, so the env var
    # carries the api-server prefix); it no longer needs the api-server's
    # `API_SERVER_JWT_SECRET` at all. The tokens are ephemeral per container, so
    # rotation needs no migration — just a coordinated restart.
    internal_token_secret: SecretStr = Field(
        default=SecretStr("dev-only-internal-token-secret-change-me"),
        description=(
            "HMAC secret for worker->api internal agent tokens. MUST differ from "
            "jwt_secret so a compromised worker cannot forge user sessions."
        ),
    )

    # ----- Review URL signing (Plan 06.5 task_06_5_08/10) -----
    # HMAC key used by `workers.review_runtime.sign_review_url` to mint
    # the one-shot URL the human reviewer opens. The api-server verifies
    # incoming `sig`/`exp` against this same key before serving the SPA
    # or accepting the `/rerun` POST. Production deployments MUST set
    # this to a random 32+ byte secret; the default is rejected outside
    # of `environment=dev`.
    review_url_signing_secret: SecretStr = Field(
        default=SecretStr("dev-only-review-url-secret-change-me"),
        description="HMAC secret for signing reviewer URLs.",
    )
    # Public base URL where the signed review URLs are reachable. Carries the
    # reverse-proxy's /api prefix (ADR 0061/0062): Caddy routes /api/* to the
    # api-server (stripping /api), so `/api/review/{id}` lands on the review
    # router. The reviewer's browser reaches the preview app through
    # `{review_public_base_url}/review/{id}/app/...`. Dev default points at the
    # containerized manuals stack (Caddy on :8080).
    review_public_base_url: str = Field(
        default="http://localhost:8080/api",
        description="Public base URL (incl. /api prefix) for signed review URLs.",
    )

    # ----- SSO / OIDC (Plan 08 task_08_01) -----
    # Fernet-derived key used to encrypt OIDC client secrets at rest when
    # Vault is NOT wired (no API_SERVER_VAULT_TOKEN). When Vault IS wired,
    # the secret lives there and this key is unused. The raw value is run
    # through SHA-256 + urlsafe-base64 to produce a valid 32-byte Fernet
    # key, so any non-empty string works. Production MUST override the
    # dev default; the dev-secret guard below rejects it outside dev.
    sso_encryption_key: SecretStr = Field(
        default=SecretStr("dev-only-sso-encryption-key-change-me"),
        description="Secret used to derive the Fernet key for OIDC client secrets at rest.",
    )
    # ----- Notifications (Plan 10 task_10_15) -----
    # Fernet-derived key used to encrypt a notification CHANNEL secret (bot
    # token, SMTP password, webhook signing key, …) at rest when Vault is
    # NOT wired. MUST equal the dispatcher's NOTIFY_NOTIFICATION_ENCRYPTION_KEY
    # so the dispatcher (read path) can decrypt what the api-server (write
    # path) encrypts — the two services derive the SAME Fernet key from the
    # SAME raw string (SHA-256 → urlsafe-base64). The dev default matches the
    # dispatcher's dev default so dev works out of the box; the guard below
    # rejects it in staging/prod. Mirrors `sso_encryption_key`.
    notification_encryption_key: SecretStr = Field(
        default=SecretStr("dev-only-notification-encryption-key-change-me"),
        description="Secret used to derive the Fernet key for notification "
        "channel secrets at rest. MUST match NOTIFY_NOTIFICATION_ENCRYPTION_KEY.",
    )
    # Public base URL the IdP redirects back to after authentication. The
    # OIDC callback (`/auth/sso/oidc/callback`) + SAML ACS / EntityID are
    # appended to it, so it must point at the API SERVER's PUBLIC URL (NOT
    # the admin-panel). This is the BOOTSTRAP fallback only: a System Admin
    # overrides it live from the SSO settings page (platform setting
    # `sso.redirect_base_url`, ADR 0047). In dev the api-server is on :8001.
    sso_redirect_base_url: str = Field(
        default="http://localhost:8001",
        description="Bootstrap public app base URL (System-Admin-overridable).",
    )
    # Path prefix under which the API is published behind a reverse proxy
    # (single-origin, ADR 0061/0069). Inserted BETWEEN the public origin and the
    # SSO/SCIM paths: e.g. prefix `/api` → callback `https://host/api/auth/sso/...`.
    # Default "" = no prefix (api-server reachable at the origin root, dev). A
    # System Admin overrides it live (platform setting `app.api_path_prefix`).
    api_path_prefix: str = Field(
        default="",
        description="Bootstrap API path prefix for single-origin proxies (e.g. /api).",
    )
    # TTL of the short-lived state/nonce record stored in Redis between
    # the /login redirect and the /callback. Bounds how long a login can
    # legitimately take before the anti-CSRF state expires.
    sso_login_state_ttl_seconds: int = Field(
        default=600,
        description="Seconds an OIDC login state/nonce stays valid in Redis.",
    )

    # ----- MFA (Plan 08 task_08_09) -----
    # TTL of the interim MFA challenge token stored in Redis between the
    # password/SSO step (returns `mfa_required`) and the TOTP verify call
    # that mints the real session. Tight by design: it grants no access,
    # only a brief window to complete the second factor before the user
    # must re-authenticate the first factor.
    mfa_challenge_ttl_seconds: int = Field(
        default=300,
        description="Seconds an interim MFA challenge token stays valid in Redis.",
    )

    # ----- MFA WebAuthn / FIDO2 (Plan 08 task_08_10) -----
    # The Relying Party id MUST be a registrable suffix of the origin the
    # browser runs the ceremony from (typically the bare host, e.g.
    # "example.com" for https://app.example.com). The browser binds a
    # credential to this RP id, so it must stay stable across deploys; a
    # mismatch makes every authenticator refuse to sign. Defaults to
    # localhost for dev.
    webauthn_rp_id: str = Field(
        default="localhost",
        description="WebAuthn Relying Party id (a registrable suffix of the origin host).",
    )
    # Human-readable RP name shown by the authenticator UI at registration.
    webauthn_rp_name: str = Field(
        default="Agentic Platform",
        description="WebAuthn Relying Party display name shown in the authenticator prompt.",
    )
    # The exact origin(s) the ceremony is expected to come from, verified
    # against the signed clientDataJSON. Must include scheme + host (+ port),
    # e.g. "http://localhost:3000". Defaults to the dev frontend origin.
    webauthn_origin: str = Field(
        default="http://localhost:3000",
        description="Expected WebAuthn origin (scheme://host[:port]) verified in clientDataJSON.",
    )
    # TTL of the WebAuthn challenge stashed in Redis between the
    # options call and the verify call. Same tight bound as the OIDC state:
    # a challenge is single-use and expires quickly so it cannot be replayed.
    webauthn_challenge_ttl_seconds: int = Field(
        default=300,
        description="Seconds a WebAuthn registration/authentication challenge stays valid.",
    )

    # ----- Admin-panel hardening (Plan 15 task_15_18) -----
    # The System-Admin surface (`/admin/*`) is the highest-value target on
    # the platform, so production hardens it beyond a normal session. All
    # three knobs are ONLY enforced when `environment` is staging/prod
    # (dev stays usable without MFA, an allowlist, or a 15-minute clock).
    #
    # 1. MANDATORY MFA — an admin without an enrolled+confirmed second
    #    factor (TOTP/WebAuthn, Plan 08) is locked out of `/admin/*` (a
    #    forced-enrollment gate). Toggleable for break-glass scenarios.
    admin_require_mfa: bool = Field(
        default=True,
        description="Require an enrolled+confirmed MFA factor for /admin/* (staging/prod only).",
    )
    # 2. IP ALLOWLIST — admin access restricted to these CIDRs (reuses the
    #    api-token allowlist CIDR semantics: each entry is a network,
    #    `strict=False`, so a bare host is a /32). An EMPTY list means "no
    #    network restriction" (the operator opted out); a non-empty list
    #    rejects every source IP outside it with 403.
    admin_ip_allowlist: list[str] = Field(
        default_factory=list,
        description="CIDRs allowed to reach /admin/* (empty = any). Staging/prod only.",
    )
    # 3. SHORT SESSIONS — an admin request on a session older than this many
    #    minutes is rejected (401), forcing re-authentication. Independent of
    #    the JWT/session TTL: a regular user's session can live longer; the
    #    admin surface clamps to this short window.
    admin_session_ttl_minutes: int = Field(
        default=15,
        description="Max age (minutes) of a session for /admin/* access (staging/prod only).",
    )

    # ----- WebSockets (prod-09 task_prod09_13, authz-3) -----
    # How often an OPEN socket re-checks that its session is still live and its
    # token has not expired. The accept-time check was the ONLY one, so
    # `routers/ws.py`'s documented guarantee ("logout/revocation closes existing
    # sockets") was false for every socket already connected: a logged-out user,
    # a SCIM-deprovisioned account or an expired token kept streaming kanban /
    # execution / conversation events for as long as the browser tab stayed open.
    # 30 s bounds the leak without adding meaningful load (one Redis GET + one
    # HMAC verify per socket per interval). 0 disables the re-check.
    ws_session_revalidate_seconds: int = Field(
        default=30,
        description=(
            "Seconds between session/expiry re-checks inside an open WebSocket "
            "(0 disables). A revoked session closes the socket with 1008."
        ),
    )

    # ----- Backpressure de envío (task_audit14_07, hallazgo AUD14-05) -----
    # Deadline de CADA `send_json` del pump. Sin él, un cliente que deja de
    # drenar (pestaña dormida, red caída sin FIN, móvil en suspensión) llena la
    # ventana TCP y el `await` del envío NO VUELVE: la corrutina del pump se
    # queda colgada con su `receive()` y su `xread` detrás, reteniendo una
    # conexión de Redis y —lo peor— sin volver al principio del bucle, o sea
    # sin re-validar la credencial. 10 s es holgado para cualquier cliente sano
    # (los eventos son de bytes) y corto para uno que ya no está. 0 desactiva el
    # deadline; el socket se cierra con 1013 «Try Again Later» al agotarlo.
    ws_send_timeout_seconds: float = Field(
        default=10.0,
        description=(
            "Seconds a single WebSocket send may take before the client is "
            "treated as a slow consumer and closed with 1013 (0 disables)."
        ),
    )

    # ----- Redis (sessions, rate limit) -----
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )

    # NOTIF-2 (auditoría 2026-07-12 / prod-08 alert_ingest_01): token Bearer
    # compartido que Alertmanager presenta en POST /internal/alerts/ingest.
    # Sin configurar → el endpoint responde 503 (fail-closed, nunca abierto).
    alerts_ingest_token: str | None = Field(
        default=None,
        description="Shared bearer token for the Alertmanager ingest endpoint.",
    )
    # TTL del dedup por fingerprint+status: justo por debajo del repeat_interval
    # de 1h de las alertas critical para tragarse los repeats sin silenciar el
    # siguiente ciclo.
    alerts_dedup_ttl_s: int = Field(
        default=3300,
        description="Dedup TTL (seconds) for repeated Alertmanager notifications.",
    )

    # ----- Celery broker (enqueue-only; api-server runs no tasks) -----
    broker_url: str = Field(
        default="redis://localhost:6379/1",
        description=(
            "Celery broker URL the api-server enqueues onto (e.g. the "
            "document ingestion task `workers.ingest_document`, Plan 06.11). "
            "Redis DB 1 — same broker the workers consume; kept off DB 0 "
            "(sessions / events). api-server only produces, never consumes."
        ),
    )
    result_backend: str = Field(
        default="redis://localhost:6379/2",
        description=(
            "Celery result backend URL the api-server READS to poll a "
            "long-running background job's status (Plan 12 task_12_12: the "
            "restore job's progress/result). Must match the workers' "
            "`WORKERS_RESULT_BACKEND` (Redis DB 2) so `AsyncResult(job_id)` "
            "resolves the state the restore task wrote. api-server only reads "
            "it — it runs no tasks."
        ),
    )

    # ----- Backup bundles (read-only; for the restore UI list/preview) -----
    backup_root: str = Field(
        default="/data/agent-platform/backups",
        description=(
            "Host filesystem root where the workers write backup bundles (one "
            "timestamped subdirectory per run). The restore UI's list/preview "
            "endpoints (Plan 12 task_12_12) READ each bundle's manifest.json from "
            "here to enumerate + introspect available backups. Must match the "
            "workers' `WORKERS_BACKUP_ROOT`. api-server only READS it — the "
            "destructive restore itself runs in a workers background job."
        ),
    )

    # ----- External services (probed by /admin/system-health) -----
    vault_url: str = Field(
        default="http://localhost:8200",
        description="Vault HTTP base URL.",
    )
    vault_token: SecretStr | None = Field(
        default=None,
        description=(
            "Vault auth token. None means 'no Vault wiring' — the api-server "
            "starts without a working VaultResolver, and any MCP config with "
            "`auth_ref` falls back to a typed AUTH_ERROR. In dev compose the "
            "env var is API_SERVER_VAULT_TOKEN=dev-root-token."
        ),
    )
    minio_url: str = Field(
        default="http://localhost:9000",
        description="MinIO HTTP API base URL.",
    )
    minio_access_key: str = Field(
        default="minioadmin",
        description="MinIO access key (S3 access_key_id). Matches MINIO_ROOT_USER in dev.",
    )
    minio_secret_key: SecretStr = Field(
        default=SecretStr("changeme-dev-only"),
        description="MinIO secret key (S3 secret_access_key). Vault-sourced in prod.",
    )
    docling_serve_url: str = Field(
        default="http://localhost:5001",
        description="docling-serve HTTP base URL (Plan 04 Fase C).",
    )
    docling_mcp_url: str = Field(
        default="http://localhost:3000",
        description="docling-mcp HTTP base URL (Plan 04 Fase E).",
    )
    # --- Voz del Asistente (ADR 0073, voz F1) ---------------------------------
    # Servicios de medios (NO providers LLM, ADR 0021): STT + TTS HTTP
    # OpenAI-compatibles. En el stack docker apuntan a stt:8000 / tts:8880.
    assistant_stt_url: str = Field(
        default="http://localhost:8000",
        description="STT (faster-whisper) base URL — /v1/audio/transcriptions (ADR 0073).",
    )
    assistant_tts_url: str = Field(
        default="http://localhost:8880",
        description="TTS (Kokoro-FastAPI) base URL — /v1/audio/speech (ADR 0073).",
    )
    assistant_tts_default_voice: str = Field(
        default="ef_dora",
        description=(
            "Voz TTS por defecto del asistente (Kokoro). Despliegue ES-first: "
            "ef_dora (femenina, español) — el prefijo Kokoro fija el IDIOMA de "
            "síntesis (e*=ES, a*=EN-US, b*=EN-GB), así que un default inglés "
            "leía el español con fonemizador inglés. La UI permite elegir M/F."
        ),
    )
    cortex_tts_default_voice: str = Field(
        default="ef_dora",
        description=(
            "Voz TTS por defecto del córtex del System Owner (Kokoro). Propia "
            "del córtex — antes reutilizaba la del asistente y el frame "
            "'ready' pisaba la elección en español del frontend."
        ),
    )
    ollama_url: str = Field(
        default="http://localhost:11434",
        description=(
            "Ollama HTTP base URL — used for local embeddings"
            " (Plan 04 task_04_14). In the docker stack this points at the"
            " in-stack `ollama` service (http://ollama:11434), ADR 0056."
        ),
    )
    embedding_model: str = Field(
        default="nomic-embed-text",
        description=(
            "Model the embedder requests from Ollama's /api/embed (ADR 0056)."
            " Default is the REAL registry name `nomic-embed-text` (== v1.5,"
            " 768 dims); the older `-v1.5` suffix is not a valid Ollama tag and"
            " yields `model not found`. Overridable via API_SERVER_EMBEDDING_MODEL."
        ),
    )
    clamav_host: str = Field(default="localhost", description="ClamAV TCP host.")
    clamav_port: int = Field(default=3310, description="ClamAV TCP port (INSTREAM).")
    # egress-proxy (ADR 0019). En prod la api-server vive en
    # `agentic-net` y resuelve `agentic-egress-proxy:8888`. En dev la
    # api-server corre fuera de docker, así que el override expone el
    # puerto al host (ver docker-compose.dev.yml).
    egress_proxy_host: str = Field(default="localhost", description="egress-proxy TCP host.")
    egress_proxy_port: int = Field(default=8888, description="egress-proxy TCP port (tinyproxy).")

    # ----- Web del córtex (ADR 0067 — web-search / web-fetch provider-agnósticas) -----
    # TODA salida a Internet de las host tools del córtex (``web_search`` / ``web_fetch``)
    # va por el egress-proxy (tinyproxy, allowlist en docker/egress-proxy/filter.txt).
    # NUNCA se conecta directo desde el api-server. En prod la api-server vive en
    # `agentic-net` y resuelve `agentic-egress-proxy:8888`; en dev (api-server fuera de
    # docker) el override expone el puerto al host (docker-compose.dev.yml), de ahí que
    # el default apunte a localhost. Si se deja vacío, las web tools fallan con un error
    # claro (nunca salen sin proxy).
    cortex_egress_proxy_url: str = Field(
        default="http://localhost:8888",
        description=(
            "URL del egress-proxy (tinyproxy) por el que las host tools del córtex "
            "salen a Internet. En el stack docker: http://agentic-egress-proxy:8888."
        ),
    )
    # Proveedor de búsqueda por defecto del córtex (catálogo cerrado, ADR 0067):
    # 'searxng' (self-host, sin key — el camino por defecto) o 'brave' (API key en
    # Vault/env). Un System Admin puede sobreescribirlo en vivo con el platform
    # setting `cortex.web_search_provider`.
    cortex_web_search_provider: str = Field(
        default="searxng",
        description="Proveedor de búsqueda web del córtex por defecto: 'searxng' | 'brave'.",
    )
    # SearXNG self-host (sin API key). En el stack docker el servicio `searxng`
    # escucha en searxng:8080; en dev se puede apuntar a una instancia local.
    cortex_searxng_url: str = Field(
        default="http://searxng:8080",
        description="Base URL de la instancia SearXNG self-host (sin key) — /search?format=json.",
    )
    # API de Brave Search. La key vive idealmente en Vault; como camino testeable sin
    # Vault, se lee también de este env (API_SERVER_BRAVE_SEARCH_API_KEY). Cuando está
    # vacía y el proveedor activo es 'brave', web_search falla con un error claro.
    cortex_brave_search_url: str = Field(
        default="https://api.search.brave.com/res/v1/web/search",
        description="Endpoint de la Brave Search API (web search).",
    )
    brave_search_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "API key de Brave Search. Idealmente en Vault; como fallback testeable se "
            "lee de API_SERVER_BRAVE_SEARCH_API_KEY. Nunca se loguea ni se devuelve."
        ),
    )

    # ----- Rate limits -----
    login_rate_limit_count: int = Field(default=5, description="Max login attempts per window.")
    login_rate_limit_window_seconds: int = Field(
        default=15 * 60, description="Sliding window for login rate limiting."
    )
    # `/auth/register` por IP (authz-6). Desde el ADR 0134 (opción C) el alta
    # exige un token de invitación, así que este endpoint anónimo es el único
    # sitio donde se puede probar un secreto en bucle: sin ventana era un
    # oráculo de adivinación gratis. El presupuesto es más holgado que el de
    # login porque una IP compartida (NAT de oficina) puede dar de alta a
    # varias personas legítimas seguidas.
    register_rate_limit_count: int = Field(
        default=10, description="Max /auth/register attempts per IP and window."
    )
    register_rate_limit_window_seconds: int = Field(
        default=60 * 60, description="Sliding window for /auth/register rate limiting."
    )
    # Public-API per-token rate limit (Plan 13). Default budget a freshly
    # minted ApiToken gets when the Tenant Admin does not override it; the
    # per-token `rate_limit` column wins when set. Enforced by the
    # sliding-window limiter in task_13_04.
    api_token_default_rate_limit: int = Field(
        default=100, description="Default per-minute request budget for a public-API token."
    )
    # Sliding window over which a public-API token's `rate_limit` budget is
    # counted (Plan 13 task_13_04). The budget is expressed per minute, so
    # the window is 60s by default; the limiter keys the window per token.
    api_token_rate_limit_window_seconds: int = Field(
        default=60,
        description="Sliding-window length (seconds) for per-token public-API rate limiting.",
    )
    # TTL of the X-API-Token -> tenant resolution cached in Redis by the
    # public-API auth middleware (Plan 13 task_13_03). Short by design: it
    # avoids a DB hit per request while bounding how long a stale entry can
    # linger if the explicit cache invalidation on revocation is ever missed.
    # Revocation deletes the cache key directly, so the common case is
    # immediate; this TTL is the worst-case staleness ceiling.
    api_token_cache_ttl_seconds: int = Field(
        default=30,
        description="Seconds an X-API-Token -> tenant resolution stays cached in Redis.",
    )

    # ----- Incoming webhooks (Plan 13 Fase C, task_13_08) -----
    # Fernet-derived key used to encrypt a per-project incoming-webhook SIGNING
    # SECRET at rest when Vault is NOT wired. The raw value is run through
    # SHA-256 + urlsafe-base64 to a valid 32-byte Fernet key (any non-empty
    # string works). Mirrors `sso_encryption_key`; production MUST override the
    # dev default (the dev-secret guard below rejects it outside dev).
    incoming_webhook_encryption_key: SecretStr = Field(
        default=SecretStr("dev-only-incoming-webhook-encryption-key-change-me"),
        description="Secret used to derive the Fernet key for incoming-webhook signing secrets.",
    )
    # Hard cap (bytes) on an incoming-webhook request body. The endpoint is
    # PUBLIC, so an oversize body is rejected (413) BEFORE the HMAC math — a
    # DDoS / memory-exhaustion guard (Plan 13 Riesgos: webhooks as a DDoS
    # vector). 1 MiB comfortably fits any GitHub/Jira/Sentry payload.
    incoming_webhook_max_body_bytes: int = Field(
        default=1_048_576,
        description="Max accepted incoming-webhook request body size in bytes (413 if exceeded).",
    )
    # Ventana de frescura para la marca de tiempo que declara el emisor
    # (authz-5). Mismo orden de magnitud que el verificador saliente: 5 min
    # absorbe el desfase de reloj razonable de un emisor y descarta el
    # reintento rancio de una cola que se drena horas tarde. Solo se aplica a
    # los orígenes que declaran cabecera de timestamp.
    incoming_webhook_max_skew_seconds: int = Field(
        default=300,
        description="Accepted clock skew (seconds) for an incoming webhook's declared timestamp.",
    )
    # Per-config sliding-window request budget for the PUBLIC incoming-webhook
    # endpoint, counted over `incoming_webhook_rate_limit_window_seconds`. Keyed
    # by webhook config id so one project's traffic never throttles another.
    incoming_webhook_rate_limit: int = Field(
        default=120,
        description="Max incoming-webhook requests per window per config (429 if exceeded).",
    )
    incoming_webhook_rate_limit_window_seconds: int = Field(
        default=60,
        description="Sliding-window length (s) for per-config incoming-webhook rate limiting.",
    )

    # ------------------------------------------------------------------------
    # ----- Key RINGS (prod-05 task_prod05_01 / task_prod05_04) -----
    # ------------------------------------------------------------------------
    # Each secret family above is a SINGLE value, and that is what made key
    # rotation impossible: change `API_SERVER_SSO_ENCRYPTION_KEY` and every
    # ciphertext already in the database becomes an InvalidToken; change
    # `API_SERVER_JWT_SECRET` and every session in flight 401s at once.
    #
    # These `*_KEYS` / `*_SECRETS` variables are the ROTATABLE form: a
    # comma-separated ORDERED list where the FIRST entry encrypts/signs and ALL
    # entries decrypt/verify. Deliberately kept as one contiguous block (rather
    # than one field next to each singular counterpart) so the whole rotation
    # surface is auditable in one place.
    #
    # BACKWARDS COMPATIBLE BY CONSTRUCTION: every list defaults to empty, and an
    # empty list means "use the singular value as a one-element ring". No
    # existing deployment changes behaviour, and no operator has to set anything
    # to install this. The rings are resolved by the `*_ring` properties below
    # (see `api_server.auth.crypto_keys.parse_key_ring` for the precedence
    # rules: a non-blank list WINS and the singular value is ignored, so
    # retiring a key means deleting it from the list and nothing else).
    #
    # The `_ring` properties return the RAW configuration strings, not derived
    # key material: derivation lives in `auth/crypto_keys.py` so there is exactly
    # one SHA-256 → urlsafe-base64 in the codebase.
    jwt_secrets: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Comma-separated HMAC secrets for human sessions. The FIRST signs, "
            "ALL verify — that is what lets a JWT rotation keep live sessions "
            "alive. Empty = use API_SERVER_JWT_SECRET alone."
        ),
    )
    internal_token_secrets: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Comma-separated HMAC secrets for worker->api agent tokens. The "
            "FIRST mints, ALL verify, so an AGENTIC_INTERNAL_TOKEN already "
            "injected into a running agent-runtime survives the rotation for "
            "the rest of its TTL. Empty = use API_SERVER_INTERNAL_TOKEN_SECRET."
        ),
    )
    sso_encryption_keys: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Comma-separated keys for the SSO/OIDC + SAML SP secrets at rest. "
            "Empty = use API_SERVER_SSO_ENCRYPTION_KEY alone."
        ),
    )
    # ----- MFA/TOTP key (ADR 0143, option A) -----
    # The TOTP seeds used to be encrypted with the SSO key, which coupled two
    # rotations with very different blast radii: rotating the OIDC client secret
    # key also invalidated every TOTP seed, and with `admin_require_mfa=true`
    # that locks every System Admin out of /admin (gap2-4). ADR 0143 splits the
    # key. The fallback is what keeps the split non-breaking: unset (the default)
    # means "keep using the SSO ring", so existing rows keep decrypting and an
    # operator opts into the separation when they choose to.
    mfa_encryption_key: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Secret deriving the Fernet key for TOTP seeds at rest. Empty = "
            "inherit the SSO ring (pre-ADR-0132 behaviour, still supported)."
        ),
    )
    mfa_encryption_keys: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Comma-separated keys for TOTP seeds at rest. Empty = fall back to "
            "API_SERVER_MFA_ENCRYPTION_KEY, then to the SSO ring."
        ),
    )
    notification_encryption_keys: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Comma-separated keys for notification channel secrets at rest. "
            "MUST match NOTIFY_NOTIFICATION_ENCRYPTION_KEYS (the dispatcher is "
            "the READ side of the same ciphertext). Empty = use the singular."
        ),
    )
    incoming_webhook_encryption_keys: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Comma-separated keys for incoming-webhook signing secrets at rest. "
            "Empty = use API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY alone."
        ),
    )

    @property
    def jwt_secret_ring(self) -> tuple[str, ...]:
        """Ordered human-session signing secrets: [0] signs, all verify."""
        return parse_key_ring(
            plural=self.jwt_secrets.get_secret_value(),
            singular=self.jwt_secret.get_secret_value(),
            name="API_SERVER_JWT_SECRET(S)",
        )

    @property
    def internal_token_secret_ring(self) -> tuple[str, ...]:
        """Ordered worker->api signing secrets: [0] mints, all verify."""
        return parse_key_ring(
            plural=self.internal_token_secrets.get_secret_value(),
            singular=self.internal_token_secret.get_secret_value(),
            name="API_SERVER_INTERNAL_TOKEN_SECRET(S)",
        )

    @property
    def sso_encryption_key_ring(self) -> tuple[str, ...]:
        """Ordered SSO/OIDC + SAML at-rest keys: [0] encrypts, all decrypt."""
        return parse_key_ring(
            plural=self.sso_encryption_keys.get_secret_value(),
            singular=self.sso_encryption_key.get_secret_value(),
            name="API_SERVER_SSO_ENCRYPTION_KEY(S)",
        )

    @property
    def mfa_encryption_key_ring(self) -> tuple[str, ...]:
        """Ordered TOTP-seed at-rest keys (ADR 0143).

        Three-step fallback, and the ORDER matters: the dedicated list, then the
        dedicated single key, then — only if neither is configured — the SSO
        ring. That last step is the compatibility hinge: it is what makes ADR
        0132 deployable without a re-encryption run.
        """
        plural = self.mfa_encryption_keys.get_secret_value()
        singular = self.mfa_encryption_key.get_secret_value()
        if not plural.strip() and not singular.strip():
            return self.sso_encryption_key_ring
        return parse_key_ring(
            plural=plural,
            singular=singular,
            name="API_SERVER_MFA_ENCRYPTION_KEY(S)",
        )

    @property
    def mfa_key_is_dedicated(self) -> bool:
        """True when MFA has its OWN key ring rather than inheriting SSO's.

        The runbook branches on this: with a dedicated ring, rotating the SSO key
        does NOT touch the TOTP seeds (and vice versa); without one, the two
        rotations are the same operation and the MFA lockout risk applies.
        """
        return bool(
            self.mfa_encryption_keys.get_secret_value().strip()
            or self.mfa_encryption_key.get_secret_value().strip()
        )

    @property
    def notification_encryption_key_ring(self) -> tuple[str, ...]:
        """Ordered notification-channel at-rest keys: [0] encrypts, all decrypt."""
        return parse_key_ring(
            plural=self.notification_encryption_keys.get_secret_value(),
            singular=self.notification_encryption_key.get_secret_value(),
            name="API_SERVER_NOTIFICATION_ENCRYPTION_KEY(S)",
        )

    @property
    def incoming_webhook_encryption_key_ring(self) -> tuple[str, ...]:
        """Ordered incoming-webhook at-rest keys: [0] encrypts, all decrypt."""
        return parse_key_ring(
            plural=self.incoming_webhook_encryption_keys.get_secret_value(),
            singular=self.incoming_webhook_encryption_key.get_secret_value(),
            name="API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY(S)",
        )

    # ----- Plan 06: shared data root for worktrees + dep-cache -----
    data_root: str = Field(
        default="/data/agent-platform",
        description=(
            "Host filesystem root for platform-managed state: bare repos, "
            "worktrees, dep-cache. The api-server only needs read access "
            "(to surface state to the UI) and write-via-invalidate-button "
            "(Plan 06 task_06_12). Workers do the heavy lifting."
        ),
    )

    # ----- Misc -----
    # ----- Price catalog sync (Plan 11 task_11_15) -----
    # The community LiteLLM price JSON, consumed strictly as a DATA FEED
    # (ADR 0021) to refresh the model_prices catalog — NOT a provider
    # runtime. Overridable to an internal mirror; the System-Admin sync
    # endpoint also accepts a per-call URL override.
    litellm_price_feed_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/BerriAI/litellm/main/"
            "model_prices_and_context_window.json"
        ),
        description="URL of the LiteLLM community price JSON used as a data feed.",
    )

    environment: str = Field(
        default="dev",
        description=(
            "Deployment environment — a CLOSED set: dev | staging | prod. Any "
            "other value fails startup (prod-09 task_prod09_02): an "
            "unrecognised tag used to be treated as `dev`, silently disabling "
            "the dev-secret guard and the whole admin hardening."
        ),
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Origins allowed by CORS (frontend admin-panel, etc.).",
    )

    # ----- Interactive API docs (prod-09 task_prod09_14, api-7) -----
    # `/docs` + `/openapi.json` published the COMPLETE internal schema —
    # `/admin/*`, `/internal/agent/*`, every tenant route — to anyone who could
    # reach the port, with no authentication. That is a free reconnaissance map of
    # the whole attack surface. Default: ON in dev, OFF everywhere else.
    # `None` means "derive from environment"; an explicit True/False is the
    # break-glass override (e.g. a staging environment used as a demo).
    # NOTE: this does NOT affect the PUBLIC `/api/v1` contract, which is a
    # separate, curated document (`routers/api_v1/openapi.py`) that intentionally
    # stays published — only the internal all-routes schema is withdrawn.
    api_docs_enabled: bool | None = Field(
        default=None,
        description=(
            "Publish /docs + /openapi.json (the FULL internal schema). None = "
            "only in dev. Does not affect the public /api/v1 docs."
        ),
    )

    @property
    def api_docs_published(self) -> bool:
        """Whether the internal Swagger UI + OpenAPI JSON should be mounted.

        A property rather than a resolved field so the fail-closed default is
        computed from the FINAL environment (validated + normalised) and cannot
        be desynchronised by a later env change in tests.
        """
        if self.api_docs_enabled is None:
            return self.environment == _DEV_ENVIRONMENT
        return self.api_docs_enabled

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        """Reject any environment tag outside ``{dev, staging, prod}`` (authz-2).

        A field validator (not a model one) so it runs BEFORE
        :meth:`_forbid_dev_secrets_outside_dev`, which branches on this value:
        the secret guard must never decide anything from an unvalidated tag.

        Whitespace and case are normalised (``" PROD "`` -> ``"prod"``) because a
        trailing newline in a compose/`.env` file is a configuration accident,
        not an intent to run unguarded. Anything else — ``production``,
        ``staging2``, ``""`` — is a hard failure with the accepted values
        spelled out, so the operator fixes it in seconds instead of running a
        publicly-known JWT secret in production for months.
        """
        normalised = value.strip().lower()
        if normalised not in _KNOWN_ENVIRONMENTS:
            raise ValueError(
                f"API_SERVER_ENVIRONMENT={value!r} is not a known environment. "
                f"Accepted values: {', '.join(sorted(_KNOWN_ENVIRONMENTS))}. "
                "An unrecognised value used to be treated as 'dev', which "
                "disabled the dev-secret guard and the /admin hardening."
            )
        return normalised

    def _weak_secrets(self) -> list[str]:
        """Las entradas de secreto que no superan el suelo, con su porqué.

        Se recorre el ANILLO entero de cada familia, no sólo la clave de cabeza:
        una clave retirada que sigue en la cola ya no cifra nada nuevo, pero
        **descifra** — y una que se adivina descifra igual de bien que la buena.
        Mismo razonamiento que el guard de marcadores.

        La familia de MFA sólo entra cuando es DEDICADA. Si hereda el anillo de
        SSO, nombrar ``API_SERVER_MFA_ENCRYPTION_KEY`` en un error mandaría al
        operador a cambiar una variable que nunca puso.
        """
        weak: list[str] = []
        for field_name, (ring_name, attribute) in _ENTROPY_CHECKED_FIELDS.items():
            if field_name == "mfa_encryption_key" and not self.mfa_key_is_dedicated:
                continue
            raw = getattr(self, attribute)
            ring = (raw.get_secret_value(),) if isinstance(raw, SecretStr) else tuple(raw)
            for position, value in enumerate(ring):
                reason = _trivial_secret_reason(value)
                if reason is None:
                    continue
                label = ring_name if len(ring) == 1 else f"{ring_name}[{position}]"
                weak.append(f"{label} ({reason})")
        return sorted(weak)

    @model_validator(mode="after")
    def _forbid_dev_secrets_outside_dev(self) -> Settings:
        """Fail fast when a non-dev deployment still carries a dev-only default
        for any secret. Phase 15 moves these to Vault; until then this guard
        stops a publicly-known JWT secret, MinIO password or DB credential from
        silently reaching production (secrets-config-*).

        FAIL-CLOSED since prod-09 task_prod09_02 (authz-2): the predicate is
        ``environment == "dev"`` (skip), not ``environment in {staging, prod}``
        (enforce). The old shape meant any environment value the guard did not
        recognise skipped it — the very definition of fail-open. The enum on
        ``environment`` already closes today's hole; writing the guard as
        "everything except dev" is what keeps a FUTURE fourth environment
        guarded by default instead of by remembering to update this set.

        prod-10 task_prod10_04 (secrets-3) closes the fail-open that survived in
        the DEFAULT of the switch itself. ``environment`` defaults to ``"dev"``,
        so a deployment that simply FORGOT ``API_SERVER_ENVIRONMENT`` skipped
        every check below and ran with the JWT secret that is published on
        GitHub. "No X ⇒ Y" broke exactly where X is missing *by design*
        (verificar-antes-de-implementar §3).

        Demanding the variable always would break every developer's local boot,
        so the criterion is narrower: an UNDECLARED ``dev`` is trusted only when
        the deployment looks local (its DB DSN points at localhost). A remote DSN
        with an undeclared environment falls through to the checks — which still
        only fail if a secret is *actually* a dev default or trivially weak. A
        properly-configured installer stack that forgot the variable keeps
        booting; the one that forgot the variable AND carries `changeme` does not.
        """
        if self.environment == _DEV_ENVIRONMENT and (
            "environment" in self.model_fields_set or _dsn_host_is_local(self.database_url)
        ):
            return self
        # Undeclared `dev` + remote DSN: the checks below run, and their message
        # has to name the missing variable too — otherwise the operator reads
        # "environment='dev' but these settings use dev defaults" and concludes
        # the guard is broken.
        implicit_dev = self.environment == _DEV_ENVIRONMENT
        implicit_hint = (
            " Also: API_SERVER_ENVIRONMENT is NOT set, so `environment` fell back "
            "to its 'dev' default while the database DSN points at a remote host. "
            "Set API_SERVER_ENVIRONMENT explicitly (dev|staging|prod)."
            if implicit_dev
            else ""
        )
        # prod-05: the six ring families are checked over their EFFECTIVE ring,
        # not over the singular var. Checking the singular one would reject a
        # perfectly good rotated deployment (list set to real keys, singular left
        # at its dev default and ignored) — and, worse, would PASS a deployment
        # whose list still carried a dev key while the singular one was fine.
        # Every key that can decrypt is a key that must not be publicly known.
        candidates: dict[str, str] = {
            "API_SERVER_REVIEW_URL_SIGNING_SECRET": (
                self.review_url_signing_secret.get_secret_value()
            ),
            "API_SERVER_MINIO_SECRET_KEY": self.minio_secret_key.get_secret_value(),
            "API_SERVER_MINIO_ACCESS_KEY": self.minio_access_key,
            "API_SERVER_DATABASE_URL": self.database_url,
            "API_SERVER_ADMIN_DATABASE_URL": self.admin_database_url,
        }
        rings: dict[str, tuple[str, ...]] = {
            "API_SERVER_JWT_SECRET(S)": self.jwt_secret_ring,
            "API_SERVER_INTERNAL_TOKEN_SECRET(S)": self.internal_token_secret_ring,
            "API_SERVER_SSO_ENCRYPTION_KEY(S)": self.sso_encryption_key_ring,
            "API_SERVER_NOTIFICATION_ENCRYPTION_KEY(S)": self.notification_encryption_key_ring,
            "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY(S)": (
                self.incoming_webhook_encryption_key_ring
            ),
        }
        # The MFA ring only gets its own entry when it is DEDICATED; when it
        # inherits the SSO ring, reporting it separately would name a variable the
        # operator never set.
        if self.mfa_key_is_dedicated:
            rings["API_SERVER_MFA_ENCRYPTION_KEY(S)"] = self.mfa_encryption_key_ring
        for ring_name, ring in rings.items():
            for position, value in enumerate(ring):
                candidates[f"{ring_name}[{position}]"] = value
        offending = sorted(
            name
            for name, value in candidates.items()
            if any(marker in value.lower() for marker in _DEV_SECRET_MARKERS)
        )
        if offending:
            raise ValueError(
                f"environment={self.environment!r} but these settings still use dev "
                f"defaults: {', '.join(offending)}. Set them to real secrets "
                f"(Vault-backed in production).{implicit_hint}"
            )

        # Length floor for the secrets that sign BEARER TOKENS (task_prod09_02
        # point 3). "Not a dev default" is not the same as "strong": `x` passes
        # the marker check and mints valid sessions. Kept separate from the
        # marker check so the error tells the operator which problem they have.
        # prod-05: over the RINGS, for the same reason as above — an old key kept
        # around only to verify tokens in flight still signs nothing new, but it
        # still VERIFIES, so a 3-char entry in the tail is a forgeable session.
        hmac_secrets = {
            f"API_SERVER_JWT_SECRET(S)[{i}]": value for i, value in enumerate(self.jwt_secret_ring)
        } | {
            f"API_SERVER_INTERNAL_TOKEN_SECRET(S)[{i}]": value
            for i, value in enumerate(self.internal_token_secret_ring)
        }
        too_short = sorted(
            f"{name} ({len(value)} chars)"
            for name, value in hmac_secrets.items()
            if len(value) < _MIN_HMAC_SECRET_LEN
        )
        if too_short:
            raise ValueError(
                f"environment={self.environment!r} requires HMAC signing secrets of at "
                f"least {_MIN_HMAC_SECRET_LEN} characters; too short: "
                f"{', '.join(too_short)}."
            )

        # prod-10 task_prod10_04 (secrets-3), SEGUNDA MITAD (2026-08-10). Al
        # marcador-substring le faltaba un suelo de longitud/entropía: `"x" * 48`
        # no lleva marcador, supera el suelo de 32 de los anillos de bearer y las
        # cinco familias Fernet/MinIO no tenían suelo ninguno. Criterio en
        # `_trivial_secret_reason`.
        #
        # Va DESPUÉS del suelo HMAC a propósito: para un secreto de firma corto,
        # «tiene que medir 32» es un diagnóstico más útil que «parece relleno», y
        # el primer error que lanza el validador es el único que el operador lee.
        #
        # Se aplica SÓLO con `environment` declarado explícitamente a
        # staging/prod, que es el ámbito que pide el plan. El camino de «dev
        # implícito + BD remota» sigue rechazando únicamente lo inequívoco (un
        # marcador de dev): endurecerlo ahí convertiría un olvido de variable en
        # una caída de arranque por un secreto que quizá sea perfectamente válido,
        # y ese es justo el falso positivo que el riesgo 2 del plan prohíbe.
        weak = self._weak_secrets() if self.environment != _DEV_ENVIRONMENT else []
        if weak:
            raise ValueError(
                f"environment={self.environment!r} rejects trivially weak secrets. "
                "A value with no dev marker is not automatically a strong value: "
                f"{', '.join(weak)}. Generate them with "
                '`python -c "import secrets; print(secrets.token_urlsafe(36))"`.'
            )

        # The whole point of task_prod09_03 is that the two signing domains are
        # DIFFERENT. Setting both env vars to the same value would satisfy every
        # check above while restoring the exact blast radius we set out to shrink,
        # so it is rejected explicitly rather than left as a footgun.
        #
        # prod-05 widens it from "the two values differ" to "the two RINGS are
        # DISJOINT". With rings, equality of the head keys is no longer the only
        # way to merge the domains: leaving a retired session key in the worker's
        # verify list would let that worker's key mint sessions again, which is
        # the same escalation by a slower route.
        shared = set(self.internal_token_secret_ring) & set(self.jwt_secret_ring)
        if shared:
            raise ValueError(
                "API_SERVER_INTERNAL_TOKEN_SECRET(S) must share NO key with "
                "API_SERVER_JWT_SECRET(S): a key present in both rings lets a "
                "compromised worker forge human sessions (secrets-9). "
                f"{len(shared)} key(s) appear in both."
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="API_SERVER_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — read once per process."""
    return Settings()
