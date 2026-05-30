"""Runtime configuration for the Celery workers service.

Env-driven via pydantic-settings, prefix `WORKERS_`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Substrings flagging a known dev-only default — forbidden in staging/prod
# (Plan 06.14 task_06_14_03 / secrets-config-5).
_DEV_SECRET_MARKERS = ("changeme", "dev-only")


class Settings(BaseSettings):
    """All env-driven knobs for the workers / Celery app."""

    # ----- Broker + result backend (both Redis) -----
    broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker URL. Redis DB 1 — kept off DB 0 "
        "(sessions / rate-limit / event bus) so a FLUSHDB on one "
        "doesn't nuke the other.",
    )
    result_backend: str = Field(
        default="redis://localhost:6379/2",
        description="Celery result backend URL. Redis DB 2.",
    )

    # ----- Execution persistence + live stream (Plan 02 Fase G) -----
    database_url: str = Field(
        default="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
        "@localhost:5432/agentic_platform",
        description="PostgreSQL URL the worker persists `executions` rows to. "
        "A BYPASSRLS role — the worker writes execution records across tenants.",
    )
    events_redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL hosting the per-execution event streams "
        "(`exec:{id}`). DB 0 — the same instance the api-server WebSocket "
        "tails, kept off the broker (DB 1) and result backend (DB 2).",
    )

    # ----- Agent-runtime containers (Plan 02 Fase B) -----
    agent_runtime_image: str = Field(
        default="agent-runtime:v1",
        description="Image the worker launches for each agent task.",
    )
    agent_network: str = Field(
        default="agentic-agents",
        description="Dedicated Docker network for agent containers — kept "
        "off agentic-net so agents cannot reach Postgres/Redis/Vault or "
        "the platform services.",
    )
    agent_network_internal: bool = Field(
        default=True,
        description="Create the agent network as `internal` (no egress to "
        "the host or the internet). El egress controlado a proveedores LLM "
        "y a la allowlist de `http_request` va por `egress_proxy_url` "
        "(ADR 0019 / task_02_35), no abriendo esta red.",
    )
    egress_proxy_url: str = Field(
        default="",
        description="URL del proxy de egress allowlisted que el sandbox "
        "agent-runtime usa para alcanzar a los proveedores LLM y a los "
        "dominios de `http_request`. Cuando está vacío NO se inyecta "
        "HTTP_PROXY en el contenedor — los ModelClient reales no podrán "
        "salir desde dentro del sandbox y sólo funcionará el "
        "ScriptedModelClient (ADR 0019). En producción: "
        "`http://egress-proxy:8888` (el servicio del compose, task_02_35).",
    )
    container_mem_limit: str = Field(
        default="512m",
        description="Hard memory cap for an agent container (a leak or a "
        "runaway model can't take the host down).",
    )
    container_pids_limit: int = Field(
        default=256,
        description="Max process count inside an agent container — caps " "fork bombs.",
    )
    container_tmp_size: str = Field(
        default="64m", description="Size of the /tmp tmpfs in an agent container."
    )
    container_workspace_size: str = Field(
        default="256m",
        description="Size of the /workspace tmpfs when no host workspace is "
        "bind-mounted (real worktree mounts arrive in Plan 06).",
    )
    container_run_timeout_s: int = Field(
        default=600,
        description="Default wall-clock budget for one container run before "
        "the worker kills it. Per-task overrides land with the Fase C "
        "safeguards (task_02_13).",
    )
    seccomp_profile_path: str = Field(
        default="",
        description="Path to a custom seccomp JSON profile. Empty = rely on "
        "Docker's built-in default-deny (SCMP_ACT_ERRNO) profile.",
    )
    apparmor_profile: str = Field(
        default="",
        description="AppArmor profile name to pin. Empty = Docker's automatic "
        "docker-default profile where the host kernel supports AppArmor.",
    )

    # ----- Test-runtime aux services + DinD proxy hardening (Plan 06.14
    # task_06_14_11 / container-isolation-1/2). These sidecars are transient
    # and live only on the task's private bridge, but they still get the
    # cap-drop + no-new-privileges + mem/pids envelope so a runaway or
    # malicious test cannot exhaust the host. Tunable by the operator; the
    # per-service AuxServiceSpec may still override the limits per service. -----
    aux_postgres_mem_limit: str = Field(
        default="256m",
        description="Hard memory cap for the postgres-test aux sidecar.",
    )
    aux_redis_mem_limit: str = Field(
        default="128m",
        description="Hard memory cap for the redis-test aux sidecar.",
    )
    aux_default_pids_limit: int = Field(
        default=128,
        description="Max process count inside an aux-service container — caps fork bombs.",
    )
    dind_proxy_mem_limit: str = Field(
        default="128m",
        description="Hard memory cap for the testcontainers DinD socket-proxy sidecar.",
    )
    dind_proxy_pids_limit: int = Field(
        default=64,
        description="Max process count inside the DinD socket-proxy sidecar.",
    )

    # ----- Memorizer (Plan 04.5 task_04_5_02) -----
    memorizer_llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible base URL the Memorizer distillation "
        "step calls. Defaults to local Ollama (`ollama serve`). Override in "
        "envs without a local Ollama by pointing at a managed endpoint.",
    )
    memorizer_llm_model: str = Field(
        default="llama3.1",
        description="Model id the Memorizer asks for. Distillation is cheap; "
        "a small local model is the right trade-off (no quota, no egress).",
    )

    # ----- Plan 06 / 06.5: shared data root for worktrees + dep-cache -----
    data_root: str = Field(
        default="/data/agent-platform",
        description=(
            "Host filesystem root for platform-managed state: bare repos, "
            "worktrees, dep-cache. The maintenance beat tasks "
            "(prune_worktrees, purge_dep_cache) resolve their working "
            "directories under this root."
        ),
    )

    # ----- Scheduled price-catalog sync (Plan 11 task_11_18) -----
    # The price-sync beat job runs the LiteLLM-feed sync (ADR 0021: data feed
    # only, NOT a provider runtime) on a CONFIGURABLE cadence. The cron string
    # is read by the beat process at boot — change it (and restart beat) to
    # alter the cadence. The live enable/disable lever is the `price_sync_enabled`
    # PLATFORM setting (a System Admin flips it from the admin panel and it takes
    # effect on the next fire without a restart) — NOT this env. A scheduled run
    # applies non-spiking changes automatically but DEFERS a >10% rise for manual
    # confirmation (the task_11_16 gate), even when scheduled.
    price_sync_cron: str = Field(
        default="0 4 * * *",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "scheduled price-catalog sync. Default daily at 04:00 UTC. Operator-tunable; "
        "the beat process reads it at boot.",
    )
    litellm_price_feed_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/BerriAI/litellm/main/"
            "model_prices_and_context_window.json"
        ),
        description="URL of the community LiteLLM price JSON consumed strictly as a "
        "DATA FEED (ADR 0021) by the scheduled sync — never a provider runtime. "
        "Point at an internal mirror to avoid egress.",
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
                f"environment={self.environment!r} but WORKERS_DATABASE_URL still uses "
                "dev-default credentials. Set it to a real secret (Vault-backed in production)."
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="WORKERS_",
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
