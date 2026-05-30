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

    # ----- Backup engine (Plan 12 task_12_01) -----
    # The full-backup routine (pg_dump LOGICAL + tar of the data volumes +
    # a checksummed manifest) is driven by these operator-tunable knobs —
    # never hardcoded magic numbers. The live enable/disable + the cron
    # cadence are PLATFORM settings a System Admin owns (task_12_04); these
    # envs are the host-side wiring the backup process reads at runtime.
    backup_root: str = Field(
        default="/data/agent-platform/backups",
        description="Host filesystem root where backup bundles are written, "
        "one timestamped subdirectory per run. Defaults under data_root so a "
        "single bind-mount covers all platform-managed state.",
    )
    backup_database_url: str = Field(
        default="postgresql://migrations_user:changeme-migrations-dev-only"
        "@localhost:15432/agentic_platform",
        description="LIBPQ-style URL pg_dump connects with for the FULL logical "
        "dump. A BYPASSRLS / admin-grade role so the dump captures every "
        "tenant's rows. NOTE: a plain libpq URL (postgresql://), NOT the "
        "SQLAlchemy +asyncpg form — pg_dump speaks libpq.",
    )
    backup_retention_days: int = Field(
        default=7,
        description="Local retention window in days (Plan 12: 'Retención local "
        "7 días'). Bundles whose timestamp is older than now-this are pruned "
        "after a successful run. Operator-tunable.",
    )
    backup_volumes: list[str] = Field(
        default_factory=lambda: ["minio_data", "redis_data", "vault_data"],
        description="Docker named volumes captured in the tar+gzip step: MinIO "
        "objects, the Redis RDB/AOF, and the Vault file backend (snapshots). "
        "Names match docker/docker-compose.yml.",
    )
    backup_volumes_mount_root: str = Field(
        default="/var/lib/docker/volumes",
        description="Host directory under which the named docker volumes are "
        "materialised (`<root>/<volume>/_data`). The backup tars each volume's "
        "_data tree from here. Override when volumes live elsewhere (e.g. a "
        "bind-mounted /data root).",
    )
    backup_cron: str = Field(
        default="0 3 * * *",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "scheduled daily backup. Default 03:00 (Plan 12). Operator-tunable; the "
        "beat process reads it at boot. The live enable/disable lever is the "
        "`backup_enabled` PLATFORM setting (a System Admin flips it from the "
        "admin panel and it takes effect on the next fire without a restart).",
    )
    # ----- Optional at-rest encryption (Plan 12 task_12_02) -----
    # AES-256 (Decisiones Clave). OFF by default: encryption is OPTIONAL and
    # adds a Vault dependency, so an operator opts in explicitly. When ON, the
    # assembled bundle is wrapped into a single AES-256-GCM blob keyed by a
    # Vault-resolved secret (`backup_encryption_vault_key`); when OFF the
    # plaintext bundle is left unchanged. Never a magic number — both knobs are
    # operator-tunable env.
    backup_encryption_enabled: bool = Field(
        default=False,
        description="Whether to AES-256 encrypt the backup bundle at rest "
        "(Plan 12 Decisiones Clave). OFF by default — encryption is optional and "
        "requires a Vault key. When ON the bundle is wrapped into a single "
        "encrypted blob and the manifest records `encrypted: true`.",
    )
    backup_encryption_vault_key: str = Field(
        default="backup_encryption_key",
        description="Name of the secret the workers' Vault/secret provider "
        "resolves for the AES-256 backup key (never plaintext, never logged). "
        "Only consulted when `backup_encryption_enabled` is true.",
    )

    # ----- Remote backup destinations — S3 (Plan 12 task_12_05) -----
    # After a successful, verified backup the bundle is uploaded to every
    # configured + enabled remote destination (Plan 12: "destinos remotos
    # opcionales (S3, B2, SFTP/NAS, rclone)"). These are the NON-secret S3
    # tunables (bucket, prefix, endpoint, region) — the access key + secret are
    # SECRETS resolved through the workers' secret seam (Vault/env), NEVER here.
    # OFF by default: a destination is opt-in. `endpoint_url` is the lever that
    # makes ANY S3-compatible provider work (MinIO, Backblaze B2, Wasabi, R2);
    # leave it empty for AWS.
    backup_s3_enabled: bool = Field(
        default=False,
        description="Whether to upload each successful backup bundle to the S3 "
        "destination. OFF by default — remote destinations are opt-in.",
    )
    backup_s3_bucket: str = Field(
        default="",
        description="S3 bucket the backup bundle is uploaded to. Required when "
        "`backup_s3_enabled` is true.",
    )
    backup_s3_prefix: str = Field(
        default="",
        description="Key prefix ('folder') under which bundles are stored in the "
        "bucket. Empty = bucket root.",
    )
    backup_s3_endpoint_url: str = Field(
        default="",
        description="S3 endpoint URL for a NON-AWS S3-compatible provider (MinIO, "
        "Backblaze B2, Wasabi, Cloudflare R2). Empty = real AWS S3.",
    )
    backup_s3_region: str = Field(
        default="",
        description="S3 region name. Empty = let the SDK/endpoint decide.",
    )

    # ----- Remote backup destinations — Backblaze B2 (Plan 12 task_12_06) -----
    # B2 is S3-COMPATIBLE but with quirks (Plan 12): the endpoint is derived from
    # the region as `s3.<region>.backblazeb2.com`, multipart wants a larger part
    # size than AWS's 5 MiB default, and auth is an application keyId + key. These
    # are the NON-secret B2 tunables; the application key id + key are SECRETS
    # resolved through the workers' secret seam (Vault/env), NEVER here. OFF by
    # default — a destination is opt-in. The B2 adapter reuses the S3 adapter via
    # the S3-compatible endpoint, so no endpoint_url knob is needed: it is built
    # from the region.
    backup_b2_enabled: bool = Field(
        default=False,
        description="Whether to upload each successful backup bundle to the "
        "Backblaze B2 destination. OFF by default — remote destinations are opt-in.",
    )
    backup_b2_bucket: str = Field(
        default="",
        description="B2 bucket the backup bundle is uploaded to. Required when "
        "`backup_b2_enabled` is true.",
    )
    backup_b2_prefix: str = Field(
        default="",
        description="Key prefix ('folder') under which bundles are stored in the "
        "B2 bucket. Empty = bucket root.",
    )
    backup_b2_region: str = Field(
        default="",
        description="B2 region (e.g. `us-west-002`, `eu-central-003`). The "
        "S3-compatible endpoint is derived from it as "
        "`https://s3.<region>.backblazeb2.com`. Required when "
        "`backup_b2_enabled` is true.",
    )

    # ----- Remote backup destinations — SFTP / NAS (Plan 12 task_12_07) -----
    # Any SSH-reachable host (a NAS, an offsite box) is a remote destination.
    # These are the NON-secret SFTP tunables (host, port, remote path, username,
    # host-key policy); the password / private key are SECRETS resolved through
    # the workers' secret seam (Vault/env), NEVER here. OFF by default — a
    # destination is opt-in. `host_key_policy` defaults to "reject" (the host
    # must be in a known_hosts file) — never silently disable host-key checking.
    backup_sftp_enabled: bool = Field(
        default=False,
        description="Whether to upload each successful backup bundle to the SFTP/"
        "NAS destination. OFF by default — remote destinations are opt-in.",
    )
    backup_sftp_host: str = Field(
        default="",
        description="SFTP/NAS hostname or IP the backup bundle is uploaded to. "
        "Required when `backup_sftp_enabled` is true.",
    )
    backup_sftp_port: int = Field(
        default=22,
        description="SFTP (SSH) port. Default 22.",
    )
    backup_sftp_username: str = Field(
        default="",
        description="SFTP username. Required when `backup_sftp_enabled` is true. "
        "The password / private key are SECRETS (secret seam), never here.",
    )
    backup_sftp_path: str = Field(
        default="",
        description="Remote directory under which bundles are stored. Empty = the "
        "session's default directory (the user's home).",
    )
    backup_sftp_host_key_policy: str = Field(
        default="reject",
        description="How an unknown server host key is handled: `reject` (default, "
        "safest — host must be in known_hosts), `auto_add` (trust-on-first-use), "
        "or `warn`. Never silently disable host-key checking.",
    )
    backup_sftp_known_hosts_path: str = Field(
        default="",
        description="Path to a known_hosts file loaded before connecting (for the "
        "`reject`/`warn` policies). Empty = paramiko's system host keys only.",
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
