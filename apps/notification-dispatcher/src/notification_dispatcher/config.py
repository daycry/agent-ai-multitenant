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

    # ----- Telegram channel (task_10_05) -----
    telegram_api_base_url: str = Field(
        default="https://api.telegram.org",
        description="Base URL of the Telegram Bot API the Telegram adapter "
        "POSTs sendMessage to (``{base}/bot{token}/sendMessage``). Tunable "
        "so a deployment can route through a proxy / local Bot API server, "
        "and so tests point it at an httpx.MockTransport — never hardcoded.",
    )
    telegram_default_parse_mode: str = Field(
        default="HTML",
        description="Default Telegram parse_mode for the rendered body when "
        "the channel config doesn't override it. HTML matches the template "
        "engine, which autoescapes the telegram (markup) channel so context "
        "values can't inject markup. A channel may set config.parse_mode to "
        "'MarkdownV2' / 'Markdown' / '' (plain) instead.",
    )
    telegram_request_timeout_s: float = Field(
        default=10.0,
        description="Per-request HTTP timeout the Telegram adapter applies to "
        "the sendMessage call. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a "
        "magic number.",
    )

    # ----- Email channel (task_10_06) -----
    email_default_from: str = Field(
        default="notifications@localhost",
        description="Fallback ``From:`` address when the email channel config "
        "doesn't carry one. A real deployment sets a verified sender per "
        "channel (config.from / config.from_email) or overrides this globally; "
        "never a magic string buried in the adapter.",
    )
    email_request_timeout_s: float = Field(
        default=10.0,
        description="Per-send SMTP wall-clock timeout the Email adapter applies "
        "to the connect+send. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a magic "
        "number.",
    )
    email_default_smtp_port: int = Field(
        default=587,
        description="Default SMTP port when the email channel config doesn't "
        "set one. 587 = submission (STARTTLS); a channel may set config.port "
        "(e.g. 465 for implicit TLS, 25 for relay). Tunable, never hardcoded.",
    )
    sendgrid_api_base_url: str = Field(
        default="https://api.sendgrid.com",
        description="Base URL of the SendGrid v3 API the OPTIONAL SendGrid send "
        "path POSTs to (``{base}/v3/mail/send``). Only used when a channel opts "
        "into the SendGrid provider (config.provider='sendgrid'); the SMTP path "
        "is the primary one. Tunable so tests point it at an httpx.MockTransport.",
    )

    # ----- Event → notification mapping tunables (task_10_04) -----
    default_locale: str = Field(
        default="en",
        description="Locale used to render a notification when neither the "
        "matched preference nor the channel carries one. ES + EN only "
        "(CLAUDE.md §12); the template engine falls back to EN regardless, "
        "this just picks the first choice. Tunable, never hardcoded.",
    )
    quiet_hours_max_defer_s: int = Field(
        default=24 * 3600,
        description="Upper bound on how far a quiet-hours-deferred send is "
        "pushed into the future (seconds). A misconfigured window can never "
        "defer a send beyond this; the resolver clamps the computed ETA. "
        "24h by default — one full quiet-hours cycle.",
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
