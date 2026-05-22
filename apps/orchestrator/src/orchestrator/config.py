"""Runtime configuration for the orchestrator service.

Env-driven via pydantic-settings, prefix `ORCHESTRATOR_`. Mirrors the
pattern in `api_server.config`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ----- Misc -----
    environment: str = Field(
        default="dev", description="Tag emitted in logs: dev | staging | prod."
    )

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
