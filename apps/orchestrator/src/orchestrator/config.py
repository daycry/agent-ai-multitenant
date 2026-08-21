"""Runtime configuration for the orchestrator service.

Env-driven via pydantic-settings, prefix `ORCHESTRATOR_`. Mirrors the
pattern in `api_server.config`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Substrings flagging a known dev-only default — forbidden in staging/prod
# (Plan 06.14 task_06_14_03 / secrets-config-5).
_DEV_SECRET_MARKERS = ("changeme", "dev-only")

# The CLOSED set of deployment environments, and the fail-CLOSED predicate — the
# same posture `api_server.config` adopted in prod-09 task_prod09_02 (authz-2) and
# that this service was left without. Written as "everything except dev" rather
# than "in {staging, prod}" on purpose: the old shape meant any UNRECOGNISED value
# (`production`, an empty var, `prod ` with a trailing space) silently meant dev
# and skipped the guard below.
_KNOWN_ENVIRONMENTS = frozenset({"dev", "staging", "prod"})
_DEV_ENVIRONMENT = "dev"


class Settings(BaseSettings):
    """All env-driven knobs for the orchestrator."""

    # ----- Redis (event bus) -----
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL hosting the event streams.",
    )

    # ----- Event stream -----
    events_stream: str = Field(
        default="events:tasks",
        description="Redis Stream the orchestrator consumes domain events from.",
    )
    consumer_group: str = Field(
        default="orchestrator",
        description="Redis consumer group name. All orchestrator replicas "
        "share it so each event is delivered once.",
    )
    consumer_name: str = Field(
        default="orchestrator-1",
        description="Per-replica consumer name within the group.",
    )
    # XREADGROUP knobs.
    read_count: int = Field(default=32, description="Max events pulled per XREADGROUP call.")
    block_ms: int = Field(
        default=5000,
        description="Milliseconds XREADGROUP blocks waiting for new events.",
    )

    # ----- Dispatch (task_02_31): task → agent → worker queue -----
    database_url: str = Field(
        default="postgresql+asyncpg://service_user:changeme-service-dev-only"
        "@localhost:5432/agentic_platform",
        description="PostgreSQL URL the dispatcher reads tasks/agents from and "
        "writes the task → in_progress transition to. `service_user`: BYPASSRLS "
        "but NO DDL (prod-14 task_05 / tenancy-2). BYPASSRLS is required — the "
        "orchestrator dispatches across every tenant. DDL is not: as "
        "`migrations_user` (schema owner, GRANT ALL) a compromised dispatcher "
        "could disable RLS on every table.",
    )
    broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker URL — must match the workers' broker so "
        "`workers.run_execution` lands on a queue a worker drains.",
    )
    dispatch_queue: str = Field(
        default="default",
        description="Celery queue the dispatcher enqueues agent runs onto.",
    )
    notifications_event_queue: str = Field(
        default="notifications.priority",
        description="Celery queue the orchestrator enqueues a "
        "`notification_dispatcher.dispatch_event` onto when it routes a human "
        "task (task_16_05). The priority lane — a human-task assignment is "
        "time-sensitive (the user has acceptance_timeout_hours to accept). "
        "MUST match the notification-dispatcher's priority_queue.",
    )

    # ----- Misc -----
    environment: str = Field(
        default="dev",
        description=(
            "Deployment environment — a CLOSED set: dev | staging | prod. Any "
            "other value fails startup: an unrecognised tag used to be treated as "
            "`dev`, silently disabling the dev-credential guard below."
        ),
    )

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        """Reject any environment tag outside ``{dev, staging, prod}``.

        A FIELD validator (not a model one) so it runs BEFORE
        :meth:`_forbid_dev_secrets_outside_dev`, which branches on this value.
        Whitespace and case are normalised (`" PROD "` -> `"prod"`): a trailing
        newline in a `.env` is an accident, not an intent to run unguarded.
        """
        normalised = value.strip().lower()
        if normalised not in _KNOWN_ENVIRONMENTS:
            raise ValueError(
                f"ORCHESTRATOR_ENVIRONMENT={value!r} is not a known environment. "
                f"Accepted values: {', '.join(sorted(_KNOWN_ENVIRONMENTS))}. "
                "An unrecognised value used to be treated as 'dev', which "
                "disabled the dev-credential guard."
            )
        return normalised

    @model_validator(mode="after")
    def _forbid_dev_secrets_outside_dev(self) -> Settings:
        """Reject the dev-default `database_url` (BYPASSRLS credentials) in
        anything that is not dev (secrets-config-5).

        FAIL-CLOSED: the predicate is ``environment == "dev"`` (skip), never
        ``environment in {staging, prod}`` (enforce), so a future fourth
        environment is guarded by default rather than by remembering a set literal.
        """
        if self.environment == _DEV_ENVIRONMENT:
            return self
        if any(marker in self.database_url.lower() for marker in _DEV_SECRET_MARKERS):
            raise ValueError(
                f"environment={self.environment!r} but ORCHESTRATOR_DATABASE_URL still uses "
                "dev-default credentials. Set it to a real secret (Vault-backed in production)."
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ORCHESTRATOR_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — read once per process."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached Settings so the next get_settings() re-reads env.
    Tests use this after monkey-patching env vars."""
    get_settings.cache_clear()
