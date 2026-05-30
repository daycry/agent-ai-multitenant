"""Runtime configuration for the notification-dispatcher service.

Env-driven via pydantic-settings, prefix ``NOTIFY_``. Mirrors
``apps/workers/src/workers/config.py``: every operational tunable (queue
names, the DLQ stream, the channel send timeout, the DLQ stream cap)
lives here so nothing is a hardcoded magic number, and a ``model_validator``
guard rejects the dev-default BYPASSRLS DB credentials and the
dev-default notification-encryption key in staging/prod.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Substrings flagging a known dev-only default — forbidden in staging/prod
# (Plan 06.14 task_06_14_03 / secrets-config-5). Mirrors workers/config.py.
_DEV_SECRET_MARKERS = ("changeme", "dev-only")


class Settings(BaseSettings):
    """All env-driven knobs for the notification-dispatcher / Celery app."""

    # ----- Broker + result backend (both Redis) -----
    broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker URL. Redis DB 1 — the same broker DB the "
        "workers use; kept off DB 0 (event bus / DLQ streams) and DB 2 "
        "(result backend).",
    )
    result_backend: str = Field(
        default="redis://localhost:6379/2",
        description="Celery result backend URL. Redis DB 2.",
    )

    # ----- Queue topology (operator-tunable, never hardcoded) -----
    default_queue: str = Field(
        default="notifications.default",
        description="Queue for ordinary notification sends — the common lane.",
    )
    priority_queue: str = Field(
        default="notifications.priority",
        description="Queue for time-sensitive sends (escalations, budget "
        "alerts). A separate worker deployment drains it so a backlog of "
        "ordinary sends never delays a priority alert.",
    )

    # ----- Persistence (Plan 10 task_10_01 tables) -----
    database_url: str = Field(
        default="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
        "@localhost:5432/agentic_platform",
        description="PostgreSQL URL the dispatcher reads channels from and "
        "writes notification_logs to. A BYPASSRLS role — the dispatcher "
        "delivers across tenants, so it MUST validate row.tenant_id == "
        "request.tenant_id at the task boundary (task_10_02) since RLS "
        "cannot catch a tampered Celery payload.",
    )
    events_redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL hosting the dead-letter stream "
        "(`dlq:notifications`). DB 0 — the same instance the workers' "
        "`dlq:executions` lives on, kept off the broker (DB 1) and result "
        "backend (DB 2).",
    )

    # ----- Dead-letter queue (no blind auto-retry; Plan 10 task_10_13 adds
    # the exponential-retry policy. Fase A only parks terminal failures). -----
    dead_letter_stream: str = Field(
        default="dlq:notifications",
        description="Redis stream a failed send is parked on for operator "
        "visibility / manual reprocessing. We do NOT auto-retry here — the "
        "backoff+retry policy is task_10_13.",
    )
    dead_letter_maxlen: int = Field(
        default=10_000,
        description="Approximate cap on the dead-letter stream length "
        "(XADD MAXLEN ~) so it cannot grow unbounded.",
    )

    # ----- Channel delivery tunables -----
    channel_send_timeout_s: float = Field(
        default=15.0,
        description="Per-send wall-clock budget a channel adapter is given "
        "before the dispatcher treats the send as failed. Tunable per "
        "deployment; channel adapters (Fase B/C) honour it.",
    )

    # ----- Secrets at rest (never plaintext; Vault-or-Fernet, mirroring the
    # SSO/marketplace precedent). The dispatcher resolves a channel secret
    # from `secret_ref` (Vault) or `secret_encrypted` (Fernet) at send time;
    # the plaintext only ever lives in memory during a send. -----
    notification_encryption_key: SecretStr = Field(
        default=SecretStr("dev-only-notification-encryption-key-change-me"),
        description="Symmetric key the Fernet-at-rest channel-secret cipher "
        "is derived from (SHA-256 → urlsafe-base64). MUST be a real secret "
        "(Vault-backed) in production — the model_validator below rejects "
        "the dev default in staging/prod.",
    )

    # ----- Misc -----
    environment: str = Field(
        default="dev", description="Tag emitted in logs: dev | staging | prod."
    )

    @model_validator(mode="after")
    def _forbid_dev_secrets_outside_dev(self) -> Settings:
        """Reject dev-default secrets in staging/prod (secrets-config-5).

        Two never-plaintext secrets are guarded: the BYPASSRLS
        ``database_url`` credentials and the notification-encryption key.
        """
        if self.environment not in {"staging", "prod"}:
            return self
        if any(marker in self.database_url.lower() for marker in _DEV_SECRET_MARKERS):
            raise ValueError(
                f"environment={self.environment!r} but NOTIFY_DATABASE_URL still uses "
                "dev-default credentials. Set it to a real secret (Vault-backed in production)."
            )
        key = self.notification_encryption_key.get_secret_value().lower()
        if any(marker in key for marker in _DEV_SECRET_MARKERS):
            raise ValueError(
                f"environment={self.environment!r} but NOTIFY_NOTIFICATION_ENCRYPTION_KEY "
                "still uses a dev-default value. Set it to a real secret "
                "(Vault-backed in production)."
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="NOTIFY_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — read once per process."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached Settings so the next get_settings() re-reads env."""
    get_settings.cache_clear()
