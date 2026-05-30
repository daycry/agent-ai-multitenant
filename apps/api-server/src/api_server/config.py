"""Runtime configuration for the api-server service.

Settings are loaded from environment variables (and a local `.env` file
when running outside Docker) via pydantic-settings. Phase 15's installer
will switch JWT_SECRET to a Vault-backed source; until then it lives in
the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Substrings that flag a value as a known dev-only default. A staging/prod
# deployment that still carries any of these is misconfigured (Plan 06.14
# task_06_14_03 / secrets-config-1/2/3/5/7).
_DEV_SECRET_MARKERS = ("changeme", "dev-only", "minioadmin")


class Settings(BaseSettings):
    """All env-driven knobs for api-server."""

    # ----- PostgreSQL -----
    database_url: str = Field(
        default="postgresql+asyncpg://app_user:changeme-app-dev-only"
        "@localhost:15432/agentic_platform",
        description="SQLAlchemy URL for the *application* role (NOBYPASSRLS).",
    )
    admin_database_url: str = Field(
        default="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
        "@localhost:15432/agentic_platform",
        description=(
            "SQLAlchemy URL for the System Admin endpoints. Connects as a "
            "BYPASSRLS role so cross-tenant reads and audit_log inserts go "
            "through without setting app.tenant_id."
        ),
    )

    # ----- Auth / sessions -----
    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-only-jwt-secret-change-me"),
        description="HMAC secret for signing JWTs.",
    )
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60 * 24  # 24h

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
    # Public base URL the IdP redirects back to after authentication.
    # The OIDC callback path (`/auth/sso/oidc/callback`) is appended to
    # this. In dev the api-server is reachable at localhost:8000; in prod
    # this is the external gateway URL the IdP's redirect-URI allowlist
    # is configured with.
    sso_redirect_base_url: str = Field(
        default="http://localhost:8000",
        description="Public base URL used to build the OIDC redirect_uri.",
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

    # ----- Redis (sessions, rate limit) -----
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
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
    ollama_url: str = Field(
        default="http://localhost:11434",
        description=(
            "Ollama HTTP base URL — used for local embeddings"
            " (default nomic-embed-text-v1.5, Plan 04 task_04_14)."
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
        default="dev", description="Tag emitted in logs: dev | staging | prod."
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Origins allowed by CORS (frontend admin-panel, etc.).",
    )

    @model_validator(mode="after")
    def _forbid_dev_secrets_outside_dev(self) -> Settings:
        """Fail fast when a staging/prod deployment still carries a dev-only
        default for any secret. Phase 15 moves these to Vault; until then
        this guard stops a publicly-known JWT secret, MinIO password or DB
        credential from silently reaching production (secrets-config-*)."""
        if self.environment not in {"staging", "prod"}:
            return self
        candidates = {
            "API_SERVER_JWT_SECRET": self.jwt_secret.get_secret_value(),
            "API_SERVER_REVIEW_URL_SIGNING_SECRET": (
                self.review_url_signing_secret.get_secret_value()
            ),
            "API_SERVER_SSO_ENCRYPTION_KEY": self.sso_encryption_key.get_secret_value(),
            "API_SERVER_NOTIFICATION_ENCRYPTION_KEY": (
                self.notification_encryption_key.get_secret_value()
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
