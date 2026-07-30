"""Runtime configuration for the api-server service.

Settings are loaded from environment variables (and a local `.env` file
when running outside Docker) via pydantic-settings. Phase 15's installer
will switch JWT_SECRET to a Vault-backed source; until then it lives in
the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
        """
        if self.environment == _DEV_ENVIRONMENT:
            return self
        candidates = {
            "API_SERVER_JWT_SECRET": self.jwt_secret.get_secret_value(),
            "API_SERVER_INTERNAL_TOKEN_SECRET": self.internal_token_secret.get_secret_value(),
            "API_SERVER_REVIEW_URL_SIGNING_SECRET": (
                self.review_url_signing_secret.get_secret_value()
            ),
            "API_SERVER_SSO_ENCRYPTION_KEY": self.sso_encryption_key.get_secret_value(),
            "API_SERVER_NOTIFICATION_ENCRYPTION_KEY": (
                self.notification_encryption_key.get_secret_value()
            ),
            "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY": (
                self.incoming_webhook_encryption_key.get_secret_value()
            ),
            "API_SERVER_MINIO_SECRET_KEY": self.minio_secret_key.get_secret_value(),
            "API_SERVER_MINIO_ACCESS_KEY": self.minio_access_key,
            "API_SERVER_DATABASE_URL": self.database_url,
            "API_SERVER_ADMIN_DATABASE_URL": self.admin_database_url,
        }
        offending = sorted(
            name
            for name, value in candidates.items()
            if any(marker in value.lower() for marker in _DEV_SECRET_MARKERS)
        )
        if offending:
            raise ValueError(
                f"environment={self.environment!r} but these settings still use dev "
                f"defaults: {', '.join(offending)}. Set them to real secrets "
                "(Vault-backed in production)."
            )

        # Length floor for the secrets that sign BEARER TOKENS (task_prod09_02
        # point 3). "Not a dev default" is not the same as "strong": `x` passes
        # the marker check and mints valid sessions. Kept separate from the
        # marker check so the error tells the operator which problem they have.
        hmac_secrets = {
            "API_SERVER_JWT_SECRET": self.jwt_secret.get_secret_value(),
            "API_SERVER_INTERNAL_TOKEN_SECRET": self.internal_token_secret.get_secret_value(),
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

        # The whole point of task_prod09_03 is that the two signing domains are
        # DIFFERENT. Setting both env vars to the same value would satisfy every
        # check above while restoring the exact blast radius we set out to shrink,
        # so it is rejected explicitly rather than left as a footgun.
        if self.internal_token_secret.get_secret_value() == self.jwt_secret.get_secret_value():
            raise ValueError(
                "API_SERVER_INTERNAL_TOKEN_SECRET must differ from "
                "API_SERVER_JWT_SECRET: sharing one key lets a compromised "
                "worker forge human sessions (secrets-9)."
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
