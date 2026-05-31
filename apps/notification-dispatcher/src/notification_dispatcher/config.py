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

    # ----- Dead-letter queue + exponential-retry policy (task_10_13) -----
    dead_letter_stream: str = Field(
        default="dlq:notifications",
        description="Redis stream a send that has exhausted its retries is "
        "parked on for operator visibility / manual reprocessing (the manual "
        "re-enqueue endpoint reads a dead-lettered NotificationLog, not this "
        "stream, but the stream is the operator's at-a-glance DLQ view).",
    )
    dead_letter_maxlen: int = Field(
        default=10_000,
        description="Approximate cap on the dead-letter stream length "
        "(XADD MAXLEN ~) so it cannot grow unbounded.",
    )

    # ----- Retry / backoff (task_10_13). A transient channel failure
    # (``ChannelSendError``) is retried with EXPONENTIAL BACKOFF + JITTER up to
    # ``max_retries`` times; once exhausted the send is dead-lettered
    # (status=dead_letter + DLQ stream). All knobs live here — NO magic numbers
    # in the task. Mirrors the workers' bounded-retry policy. -----
    max_retries: int = Field(
        default=5,
        ge=0,
        description="Maximum number of AUTOMATIC retries a transient send "
        "failure is given before it is dead-lettered. NOT unbounded: after "
        "this many retries the send is parked (status=dead_letter + DLQ "
        "stream) for manual reprocessing. 5 matches the human_10_03 checklist "
        "('Tras 5 reintentos, va a dead-letter queue'). Tunable, never inline.",
    )
    retry_base_backoff_s: float = Field(
        default=2.0,
        gt=0,
        description="Base delay (seconds) for the exponential backoff: the "
        "Nth retry waits ~``base * 2**(N-1)`` seconds (then clamped to "
        "max_backoff and jittered). 2s by default. Tunable, never inline.",
    )
    retry_max_backoff_s: float = Field(
        default=600.0,
        gt=0,
        description="Upper clamp (seconds) on a single retry's backoff so an "
        "exponential delay can never grow unbounded. 10 min by default — long "
        "enough to ride out a transient channel/provider outage, short enough "
        "to bound how long a send sits queued. Tunable, never inline.",
    )
    retry_jitter: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="Full-jitter fraction [0..1] applied to each computed "
        "backoff: the actual delay is uniformly sampled from "
        "``[delay * (1 - jitter), delay]`` so a fleet of dispatchers doesn't "
        "retry in a thundering herd. 0.5 = up to 50% jitter. Tunable.",
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

    # ----- Slack channel (task_10_07) -----
    slack_api_base_url: str = Field(
        default="https://slack.com",
        description="Base URL of the Slack Web API the Slack adapter POSTs "
        "chat.postMessage to (``{base}/api/chat.postMessage``). Tunable so a "
        "deployment can route through a proxy and so tests point it at an "
        "httpx.MockTransport — never hardcoded.",
    )
    slack_request_timeout_s: float = Field(
        default=10.0,
        description="Per-request HTTP timeout the Slack adapter applies to the "
        "chat.postMessage call. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a magic "
        "number.",
    )

    # ----- Microsoft Teams channel (task_10_08) -----
    teams_request_timeout_s: float = Field(
        default=10.0,
        description="Per-request HTTP timeout the Teams adapter applies to the "
        "incoming-webhook POST. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a magic "
        "number.",
    )
    teams_adaptive_card_version: str = Field(
        default="1.4",
        description="Adaptive Card schema version the Teams adapter stamps on "
        "the card it builds. Teams supports 1.0 to 1.5; 1.4 is broadly available "
        "across desktop/web/mobile clients. A channel may override it via "
        "config.card_version. Tunable so it tracks Teams client support, never "
        "a magic string buried in the adapter.",
    )

    # ----- Discord channel (task_10_09) -----
    discord_request_timeout_s: float = Field(
        default=10.0,
        description="Per-request HTTP timeout the Discord adapter applies to "
        "the webhook POST. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a magic "
        "number.",
    )
    discord_default_embed_color: int = Field(
        default=0x5865F2,
        description="Fallback Discord embed colour (decimal RGB int) used when "
        "an event carries no severity (or an unknown one). 0x5865F2 is the "
        "Discord blurple. A channel may override it via config.embed_color; "
        "severity-mapped colours take precedence when a severity is present. "
        "Tunable, never a magic number buried in the adapter.",
    )

    # ----- WhatsApp Cloud API channel (task_10_10) -----
    whatsapp_api_base_url: str = Field(
        default="https://graph.facebook.com",
        description="Base URL of the Meta WhatsApp Cloud (Graph) API the "
        "WhatsApp adapter POSTs a template message to "
        "(``{base}/{version}/{phone_number_id}/messages``). Tunable so a "
        "deployment can pin a Graph host / proxy and so tests point it at an "
        "httpx.MockTransport — never hardcoded.",
    )
    whatsapp_api_version: str = Field(
        default="v21.0",
        description="Graph API version segment in the WhatsApp Cloud send URL. "
        "Meta versions the Graph API (vNN.0); pin it here so an API change "
        "never silently shifts the contract. A channel may override it via "
        "config.api_version. Tunable, never a magic string in the adapter.",
    )
    whatsapp_default_language: str = Field(
        default="en_US",
        description="Default WhatsApp template language/locale code (BCP-47 "
        "style, e.g. en_US / es_ES) stamped on the template message when "
        "neither the channel config nor the template registry pins one. "
        "WhatsApp matches the approved template by name + language. Tunable, "
        "never hardcoded.",
    )
    whatsapp_request_timeout_s: float = Field(
        default=10.0,
        description="Per-request HTTP timeout the WhatsApp adapter applies to "
        "the Cloud API send. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a magic "
        "number.",
    )

    # ----- SMS / Twilio channel (task_10_11) -----
    twilio_api_base_url: str = Field(
        default="https://api.twilio.com",
        description="Base URL of the Twilio REST API the SMS adapter POSTs a "
        "Message to (``{base}/2010-04-01/Accounts/{AccountSid}/Messages.json``). "
        "We drive the documented HTTP API with httpx (HTTP Basic auth "
        "AccountSid:AuthToken) rather than the heavy ``twilio`` SDK — uniform "
        "with the other HTTP channels. Tunable so a deployment can pin a region "
        "host / proxy and so tests point it at an httpx.MockTransport — never "
        "hardcoded.",
    )
    twilio_api_version: str = Field(
        default="2010-04-01",
        description="Twilio REST API version segment in the Messages URL. "
        "Twilio's stable API version is dated (2010-04-01); pin it here so an "
        "API change never silently shifts the contract. Tunable, never a magic "
        "string in the adapter.",
    )
    sms_default_from: str = Field(
        default="",
        description="Fallback ``From`` sender (an E.164 number or a Twilio "
        "Messaging Service SID) when the SMS channel config doesn't carry one. "
        "Empty by default — a real deployment sets a verified sender per channel "
        "(config.from / config.messaging_service_sid) or overrides this "
        "globally; never a magic string buried in the adapter.",
    )
    sms_max_body_len: int = Field(
        default=1600,
        description="Upper bound on the SMS body length (characters) the adapter "
        "will send. Twilio accepts up to 1600 chars (it auto-segments a long "
        "body into multiple GSM-7/UCS-2 parts); past that it 400s. We truncate "
        "defensively so an over-long rendered body becomes a delivered, trimmed "
        "message instead of a hard error. Tunable, never inline.",
    )
    twilio_request_timeout_s: float = Field(
        default=10.0,
        description="Per-request HTTP timeout the SMS adapter applies to the "
        "Twilio Messages call. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a magic "
        "number.",
    )

    # ----- Outbound webhook channel (task_10_12) -----
    webhook_request_timeout_s: float = Field(
        default=10.0,
        description="Per-request HTTP timeout the outbound-webhook adapter applies "
        "to the signed POST. Bounded under channel_send_timeout_s so the "
        "dispatcher's overall send budget still wraps it. Tunable, not a magic "
        "number.",
    )
    webhook_signature_max_skew_s: int = Field(
        default=300,
        description="Freshness window (seconds) the outbound-webhook signature is "
        "valid for: the X-Timestamp header bounds replay, and a receiver MUST "
        "reject a signature whose timestamp is older (or further in the future) "
        "than this skew. The reusable verify() helper enforces it. 5 min by "
        "default — long enough for clock drift, short enough to bound a replay "
        "window. Tunable, never a magic number.",
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
