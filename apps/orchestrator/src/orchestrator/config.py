"""Runtime configuration for the orchestrator service.

Env-driven via pydantic-settings, prefix `ORCHESTRATOR_`. Mirrors the
pattern in `api_server.config`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Substrings flagging a known dev-only default — forbidden in staging/prod
# (Plan 06.14 task_06_14_03 / secrets-config-5).
_DEV_SECRET_MARKERS = ("changeme", "dev-only")


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
        default="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
        "@localhost:5432/agentic_platform",
        description="PostgreSQL URL the dispatcher reads tasks/agents from and "
        "writes the task → in_progress transition to. A BYPASSRLS role: the "
        "orchestrator dispatches across every tenant.",
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

    # ----- Misc -----
    environment: str = Field(
        default="dev", description="Tag emitted in logs: dev | staging | prod."
    )

    @model_validator(mode="after")
    def _forbid_dev_secrets_outside_dev(self) -> Settings:
        """Reject the dev-default `database_url` (BYPASSRLS credentials) in
        staging/prod (secrets-config-5)."""
        if self.environment not in {"staging", "prod"}:
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
