"""Runtime configuration for the api-server service.

Settings are loaded from environment variables (and a local `.env` file
when running outside Docker) via pydantic-settings. Phase 15's installer
will switch JWT_SECRET to a Vault-backed source; until then it lives in
the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ----- Redis (sessions, rate limit) -----
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )

    # ----- External services (probed by /admin/system-health) -----
    vault_url: str = Field(
        default="http://localhost:8200",
        description="Vault HTTP base URL.",
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
    ollama_url: str = Field(
        default="http://localhost:11434",
        description=(
            "Ollama HTTP base URL — used for local embeddings"
            " (default nomic-embed-text-v1.5, Plan 04 task_04_14)."
        ),
    )
    clamav_host: str = Field(default="localhost", description="ClamAV TCP host.")
    clamav_port: int = Field(default=3310, description="ClamAV TCP port (INSTREAM).")

    # ----- Rate limits -----
    login_rate_limit_count: int = Field(default=5, description="Max login attempts per window.")
    login_rate_limit_window_seconds: int = Field(
        default=15 * 60, description="Sliding window for login rate limiting."
    )

    # ----- Misc -----
    environment: str = Field(
        default="dev", description="Tag emitted in logs: dev | staging | prod."
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Origins allowed by CORS (frontend admin-panel, etc.).",
    )

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
