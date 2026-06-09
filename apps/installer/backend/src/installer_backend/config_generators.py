"""``.env`` + ``config/global.yaml`` + data-tree generators (Plan 15 task_15_08).

Phase B fills the real generators the install orchestration (task 15_05's
``generate_config`` step) calls. :mod:`installer_backend.compose_generator`
(task 15_07) builds the docker-compose; this module builds the rest of what a
real install lays down on disk:

  * the **.env** — every environment variable the runtime services read
    (PostgreSQL / MinIO / JWT / SSO / notification / webhook secrets, the
    derived DSNs, the deployment ``ENVIRONMENT`` markers), with **generated
    high-entropy secrets** (never the dev defaults). A production ``.env`` from
    this generator passes the platform's prod dev-secret guard (Plan 06.14):
    it contains none of the dev-default markers ``changeme`` / ``dev-only`` /
    ``minioadmin``.
  * **config/global.yaml** — the non-secret platform config (domain, environment,
    enabled providers, resource sizing, storage layout, supported languages).
  * the **/data/agent-platform/** directory plan — the directory tree + POSIX
    permissions for the repos / worktrees / dep-cache / object-store / Vault /
    monitoring data.

Secrets
-------
Every generated secret is drawn from a CSPRNG (:mod:`secrets`). They are unique
per run (a fresh :class:`GeneratedSecrets` per :func:`generate_secrets` call)
and high-entropy (URL-safe base64 of >=32 random bytes). The generated ``.env``
is written to disk at install time only — NEVER committed and NEVER logged in
plaintext (the write goes through the injectable :class:`EnvFileWriter` seam,
mocked in tests). Tests assert the *structure* of the artifacts with the real
generated values, or with throwaway placeholders; nothing real is committed.

Disk writes behind a seam
-------------------------
The real install actually ``mkdir``s the data tree under ``/data/agent-platform``
and writes ``.env`` + ``config/global.yaml`` — none of which can run in CI. So
every host-touching action is a Protocol here (:class:`EnvFileWriter`,
:class:`DataTreeProvisioner`) with an in-memory fake for tests; the real binding
shells out / writes files and is exercised only by the plan's Tests Humanos.
This module's pure functions (``generate_secrets`` / ``render_env_file`` /
``generate_global_yaml`` / ``build_data_tree_plan``) perform NO I/O.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import yaml

from installer_backend.compose_generator import enabled_providers
from installer_backend.config import Environment, InstallerConfig

# Dev-default markers the prod secret guard rejects (mirror of
# api_server.config._DEV_SECRET_MARKERS + the MinIO admin default). A generated
# production .env must contain NONE of these.
_DEV_SECRET_MARKERS: tuple[str, ...] = ("changeme", "dev-only", "minioadmin")

#: Minimum number of random bytes behind every generated secret (>=256 bits of
#: entropy once base64-encoded). Tokens are URL-safe so they are .env-clean
#: (no ``=`` padding issues, no quoting needed, no marker substrings possible).
_SECRET_NBYTES = 32

#: The deployment-environment value the runtime services expect. The installer's
#: :class:`Environment` enum says ``production``/``staging``/``development`` but
#: the services' settings guard keys on ``prod``/``staging``/``dev`` (and only
#: ``staging``/``prod`` trip the dev-secret guard). Map installer → runtime here
#: so a production install emits ``ENVIRONMENT=prod`` and the guard fires.
_RUNTIME_ENVIRONMENT: dict[Environment, str] = {
    Environment.PRODUCTION: "prod",
    Environment.STAGING: "staging",
    Environment.DEVELOPMENT: "dev",
}


def _token() -> str:
    """A single high-entropy URL-safe secret (>=256 bits, no dev-marker chars)."""

    return secrets.token_urlsafe(_SECRET_NBYTES)


@dataclass(frozen=True)
class GeneratedSecrets:
    """The CSPRNG-generated secret material for one install.

    Holds ONLY generated, high-entropy values — never a dev default. The
    instance is built fresh per :func:`generate_secrets` call (unique per run).
    ``__repr__``/``__str__`` are redacted so an accidental log line or traceback
    frame can't leak the values; the real ``.env`` write goes through a seam and
    the values otherwise live only in memory until handed to Vault.

    Fields map 1:1 onto the runtime services' secret settings (see
    :mod:`api_server.config` / the workers / dispatcher configs):

    postgres_password
        The ``postgres`` superuser password (initdb).
    migrations_user_password / app_user_password
        Passwords for the two DB roles created on first start (DDL vs DML).
    minio_root_user / minio_root_password
        MinIO admin credentials (the access/secret key the services use).
    jwt_secret
        HMAC secret for signing JWTs (``API_SERVER_JWT_SECRET``).
    review_url_signing_secret
        HMAC secret for signing reviewer URLs.
    sso_encryption_key / notification_encryption_key / incoming_webhook_encryption_key
        Raw secrets the services derive Fernet keys from (at-rest encryption).
        ``notification_encryption_key`` is shared by api-server + dispatcher.
    grafana_admin_password
        Grafana admin password (monitoring overlay).
    vault_root_token_placeholder
        NOT a real Vault token — the real root token comes from
        ``vault operator init`` in task 15_09. This is only a throwaway used to
        keep the bootstrap ``.env`` complete before Vault is initialised; it is
        overwritten by the Vault bootstrap and never used to authenticate.
    """

    postgres_password: str
    migrations_user_password: str
    app_user_password: str
    minio_root_user: str
    minio_root_password: str
    jwt_secret: str
    review_url_signing_secret: str
    sso_encryption_key: str
    notification_encryption_key: str
    incoming_webhook_encryption_key: str
    grafana_admin_password: str
    vault_root_token_placeholder: str

    def __repr__(self) -> str:  # pragma: no cover - security-load-bearing, trivial
        return "GeneratedSecrets(<redacted: high-entropy, written to .env/Vault once>)"

    __str__ = __repr__


def generate_secrets() -> GeneratedSecrets:
    """Mint a fresh set of CSPRNG secrets for one install (unique per call).

    Every field is an independent ``secrets.token_urlsafe`` draw, so two calls
    never collide and no value carries a dev-default marker. The MinIO root user
    is a generated identifier too (not the well-known ``minioadmin``) so the
    object-store admin name itself can't trip the prod guard.
    """

    return GeneratedSecrets(
        postgres_password=_token(),
        migrations_user_password=_token(),
        app_user_password=_token(),
        # A generated admin *name* (not "minioadmin") + a generated key.
        minio_root_user=f"minio-{secrets.token_hex(8)}",
        minio_root_password=_token(),
        jwt_secret=_token(),
        review_url_signing_secret=_token(),
        sso_encryption_key=_token(),
        notification_encryption_key=_token(),
        incoming_webhook_encryption_key=_token(),
        grafana_admin_password=_token(),
        vault_root_token_placeholder=_token(),
    )


def _database_urls(secrets_: GeneratedSecrets) -> dict[str, str]:
    """Build the application + admin DSNs from the generated DB credentials.

    The services read ``DATABASE_URL`` (app role, NOBYPASSRLS) and
    ``ADMIN_DATABASE_URL`` (migrations role, BYPASSRLS). Both point at the
    in-stack ``postgres`` service over the compose network. The passwords are
    the generated ones, so the DSNs carry no dev-default marker.
    """

    db_name = "agentic_platform"
    app_url = f"postgresql+asyncpg://app_user:{secrets_.app_user_password}@postgres:5432/{db_name}"
    admin_url = (
        f"postgresql+asyncpg://migrations_user:{secrets_.migrations_user_password}"
        f"@postgres:5432/{db_name}"
    )
    return {"DATABASE_URL": app_url, "ADMIN_DATABASE_URL": admin_url}


def build_env_vars(
    cfg: InstallerConfig,
    secrets_: GeneratedSecrets,
    *,
    monitoring: bool = False,
) -> dict[str, str]:
    """Assemble the full ordered map of environment variables for the ``.env``.

    Every value is concrete (generated secret or wizard-derived config) so a
    production ``.env`` rendered from this map passes the runtime services' prod
    secret guard. Provider wiring (which ADR-0021 providers are enabled +
    endpoints) is included; provider *credentials* live in Vault (task 15_09),
    not here. The deployment ``ENVIRONMENT`` markers (the bare + per-service
    prefixed ones) are set so the guard actually runs in staging/prod.
    """

    runtime_env = _RUNTIME_ENVIRONMENT[cfg.system.environment]
    db_urls = _database_urls(secrets_)

    env: dict[str, str] = {
        # --- deployment environment (drives the runtime services' guard) ---
        "ENVIRONMENT": runtime_env,
        "PLATFORM_DOMAIN": cfg.system.domain,
        # --- PostgreSQL ---
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": secrets_.postgres_password,
        "POSTGRES_DB": "agentic_platform",
        "POSTGRES_PORT": "5432",
        "MIGRATIONS_USER_PASSWORD": secrets_.migrations_user_password,
        "APP_USER_PASSWORD": secrets_.app_user_password,
        # --- derived DSNs the services read directly ---
        "DATABASE_URL": db_urls["DATABASE_URL"],
        "ADMIN_DATABASE_URL": db_urls["ADMIN_DATABASE_URL"],
        # --- Redis ---
        "REDIS_URL": "redis://redis:6379/0",
        "REDIS_MAX_MEM": "512mb",
        "REDIS_PORT": "6379",
        # --- MinIO ---
        "MINIO_ROOT_USER": secrets_.minio_root_user,
        "MINIO_ROOT_PASSWORD": secrets_.minio_root_password,
        "MINIO_ACCESS_KEY": secrets_.minio_root_user,
        "MINIO_SECRET_KEY": secrets_.minio_root_password,
        "MINIO_BUCKET": cfg.storage.minio_bucket,
        "MINIO_API_PORT": "9000",
        "MINIO_CONSOLE_PORT": str(cfg.ports.minio_console),
        # --- Vault (real token written by the bootstrap, task 15_09) ---
        "VAULT_ADDR": "http://vault:8200",
        "VAULT_PORT": "8200",
        # --- api-server secrets (API_SERVER_ prefixed) ---
        "API_SERVER_ENVIRONMENT": runtime_env,
        "API_SERVER_JWT_SECRET": secrets_.jwt_secret,
        "API_SERVER_REVIEW_URL_SIGNING_SECRET": secrets_.review_url_signing_secret,
        "API_SERVER_SSO_ENCRYPTION_KEY": secrets_.sso_encryption_key,
        "API_SERVER_NOTIFICATION_ENCRYPTION_KEY": secrets_.notification_encryption_key,
        "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY": secrets_.incoming_webhook_encryption_key,
        "API_SERVER_MINIO_ACCESS_KEY": secrets_.minio_root_user,
        "API_SERVER_MINIO_SECRET_KEY": secrets_.minio_root_password,
        "API_SERVER_DATABASE_URL": db_urls["DATABASE_URL"],
        "API_SERVER_ADMIN_DATABASE_URL": db_urls["ADMIN_DATABASE_URL"],
        # --- workers (WORKERS_ prefixed) ---
        "WORKERS_ENVIRONMENT": runtime_env,
        "WORKERS_DATABASE_URL": db_urls["ADMIN_DATABASE_URL"],
        "WORKERS_DATA_ROOT": cfg.storage.data_root,
        "WORKERS_BACKUP_ROOT": f"{cfg.storage.data_root}/backups",
        # --- orchestrator (ORCHESTRATOR_ prefixed) ---
        "ORCHESTRATOR_ENVIRONMENT": runtime_env,
        # --- notification-dispatcher (NOTIFY_ prefixed) ---
        # MUST match API_SERVER_NOTIFICATION_ENCRYPTION_KEY (write/read pair).
        "NOTIFY_ENVIRONMENT": runtime_env,
        "NOTIFY_DATABASE_URL": db_urls["ADMIN_DATABASE_URL"],
        "NOTIFY_NOTIFICATION_ENCRYPTION_KEY": secrets_.notification_encryption_key,
        # --- platform image pins (consumed by the generated compose) ---
        "PLATFORM_IMAGE_TAG": "v1.0.0",
        "PLATFORM_REGISTRY": "ghcr.io/agentic-platform",
    }

    # Provider wiring (non-secret toggles + endpoints). Credentials go to Vault.
    providers = cfg.providers
    if providers.claude_sdk.enabled:
        env["LLM_CLAUDE_SDK_ENABLED"] = "true"
    if providers.copilot.enabled:
        env["LLM_COPILOT_ENABLED"] = "true"
    if providers.azure_foundry.enabled:
        env["LLM_AZURE_FOUNDRY_ENABLED"] = "true"
        if providers.azure_foundry.apim_endpoint:
            env["LLM_AZURE_FOUNDRY_ENDPOINT"] = providers.azure_foundry.apim_endpoint
    if providers.ollama.enabled:
        env["LLM_OLLAMA_ENABLED"] = "true"
        if providers.ollama.endpoint:
            env["LLM_OLLAMA_ENDPOINT"] = providers.ollama.endpoint
        elif cfg.resources.ollama_mode != "none":
            env["LLM_OLLAMA_ENDPOINT"] = "http://ollama:11434"

    # In-stack Ollama service (ADR 0056 — cpu or gpu).
    if cfg.resources.ollama_mode != "none":
        env["OLLAMA_PORT"] = "11434"

    # Monitoring overlay (Grafana admin password only when the overlay is on).
    if monitoring:
        env["GRAFANA_ADMIN_USER"] = "admin"
        env["GRAFANA_ADMIN_PASSWORD"] = secrets_.grafana_admin_password
        env["PROMETHEUS_PORT"] = "9090"

    return env


def _quote_env_value(value: str) -> str:
    """Double-quote a value only when it contains whitespace or a ``#``.

    Generated secrets are URL-safe base64 (no spaces, quotes or ``#``), so they
    never need quoting; a wizard-provided domain/endpoint *might*, so we quote
    defensively. We never emit a value that would break ``docker compose``'s
    dotenv parsing.
    """

    if value == "" or any(c in value for c in " \t#'\""):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def render_env_file(env: dict[str, str]) -> str:
    """Serialise the env map to deterministic ``.env`` text (dotenv format).

    Emits ``KEY=value`` lines in insertion order (so the file reads top-to-bottom
    like ``docker/.env.example``), prefixed by a header that states the file
    holds generated secrets and must never be committed. Performs NO I/O — the
    text is what the :class:`EnvFileWriter` seam writes at install time.
    """

    header = (
        "# Generated by the Agentic Platform installer (Plan 15).\n"
        "# Contains GENERATED high-entropy secrets — DO NOT commit, DO NOT log.\n"
        "# Regenerating this file rotates every secret; coordinate with Vault.\n"
    )
    lines = [f"{key}={_quote_env_value(value)}" for key, value in env.items()]
    return header + "\n".join(lines) + "\n"


def generate_env_file(
    cfg: InstallerConfig,
    secrets_: GeneratedSecrets,
    *,
    monitoring: bool = False,
) -> str:
    """Build the full ``.env`` text from the wizard config + generated secrets."""

    return render_env_file(build_env_vars(cfg, secrets_, monitoring=monitoring))


def assert_env_passes_prod_secret_guard(env_text: str) -> None:
    """Raise ``ValueError`` if a dev-default marker leaked into a prod ``.env``.

    Belt-and-braces self-check mirroring the runtime services'
    ``_DEV_SECRET_MARKERS`` guard: a production ``.env`` from this generator must
    contain NONE of ``changeme`` / ``dev-only`` / ``minioadmin``. The CLI/wizard
    can call this right after generating a production ``.env``.
    """

    lowered = env_text.lower()
    found = [marker for marker in _DEV_SECRET_MARKERS if marker in lowered]
    if found:
        raise ValueError(
            "El .env generado para producción contiene marcadores de secreto de "
            f"desarrollo: {', '.join(found)}."
        )


# ---------------------------------------------------------------------------
# config/global.yaml — the non-secret platform config.
# ---------------------------------------------------------------------------
def generate_global_config(
    cfg: InstallerConfig,
    *,
    monitoring: bool = False,
) -> dict[str, Any]:
    """Build the non-secret ``config/global.yaml`` mapping from the wizard config.

    Carries ONLY non-secret platform config: the domain + environment, which
    ADR-0021 providers are enabled (names only, never credentials), the resource
    sizing, the storage layout, the monitoring flag and the supported languages
    (ES + EN only, per CLAUDE.md principle 12). Serialise with
    :func:`render_global_yaml`.
    """

    provider_names = [kind.value for kind in enabled_providers(cfg)]
    return {
        "version": 1,
        "platform": {
            "domain": cfg.system.domain,
            "environment": cfg.system.environment.value,
            "languages": ["es", "en"],
        },
        "resources": {
            "worker_replicas": cfg.resources.worker_replicas,
            "worker_memory_gib": cfg.resources.worker_memory_gib,
            "ollama_mode": cfg.resources.ollama_mode,
            "embedding_model": cfg.resources.embedding_model,
            "gpu_enabled": cfg.resources.gpu_enabled,
        },
        "storage": {
            "data_root": cfg.storage.data_root,
            "minio_bucket": cfg.storage.minio_bucket,
        },
        "providers": {
            "enabled": provider_names,
        },
        "monitoring": {
            "enabled": monitoring,
        },
        "tenant": {
            "name": cfg.tenant.tenant_name,
            "admin_email": str(cfg.tenant.admin_email),
        },
    }


def render_global_yaml(config: dict[str, Any]) -> str:
    """Serialise the global config mapping to deterministic YAML text.

    ``sort_keys=False`` preserves the section ordering; the output is what the
    install seam writes to ``config/global.yaml``. Performs NO I/O.
    """

    text: str = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, width=100)
    return text


# ---------------------------------------------------------------------------
# /data/agent-platform/ directory plan.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DataDir:
    """One directory in the install's data-tree plan.

    ``path`` is the absolute POSIX path; ``mode`` is the octal POSIX permission
    the provisioner sets (e.g. ``0o700`` for secret-bearing dirs like Vault's
    file backend, ``0o750`` for the rest). ``description`` documents intent.
    """

    path: str
    mode: int
    description: str


#: Sub-paths (relative to the data root) the install must create, with the
#: POSIX mode each gets. Secret-bearing dirs (Vault file/logs) are 0o700; the
#: rest are 0o750. Kept in lockstep with the compose generator's bind mounts.
_DATA_SUBDIRS: tuple[tuple[str, int, str], ...] = (
    ("postgres", 0o700, "PostgreSQL data directory (PGDATA)."),
    ("redis", 0o750, "Redis append-only-file + snapshots."),
    ("minio", 0o750, "MinIO object-store backend."),
    ("vault/file", 0o700, "Vault file storage backend (secret material)."),
    ("vault/logs", 0o700, "Vault audit logs (sensitive)."),
    ("clamav", 0o750, "ClamAV virus signature database."),
    ("projects", 0o750, "Per-tenant/project bare repos (repos/*.git)."),
    ("worktrees", 0o750, "Per-task git worktrees (transient checkouts)."),
    ("dep-cache", 0o750, "Shared dependency cache across worktrees."),
    ("backups", 0o700, "Backup bundles (Plan 12) — may contain dumps."),
    ("ollama", 0o750, "Local Ollama models (GPU profile)."),
    ("prometheus", 0o750, "Prometheus TSDB (monitoring overlay)."),
    ("grafana", 0o750, "Grafana state (monitoring overlay)."),
)


def build_data_tree_plan(
    cfg: InstallerConfig,
    *,
    monitoring: bool = False,
) -> list[DataDir]:
    """Build the ordered list of directories the install creates under the root.

    The root itself (``cfg.storage.data_root``) comes first at ``0o750``, then
    every sub-directory the stateful services bind-mount. The GPU (``ollama``)
    and monitoring (``prometheus`` / ``grafana``) dirs are included only when
    those features are on, mirroring the compose generator's service selection.
    Returns a pure plan (no ``mkdir`` happens here — that's the
    :class:`DataTreeProvisioner` seam).
    """

    root = cfg.storage.data_root
    skip: set[str] = set()
    if cfg.resources.ollama_mode == "none":
        skip.add("ollama")
    if not monitoring:
        skip.update({"prometheus", "grafana"})

    plan: list[DataDir] = [
        DataDir(path=root, mode=0o750, description="Platform data root."),
    ]
    for sub, mode, desc in _DATA_SUBDIRS:
        if sub.split("/", 1)[0] in skip:
            continue
        plan.append(DataDir(path=f"{root}/{sub}", mode=mode, description=desc))
    return plan


# ---------------------------------------------------------------------------
# Injectable seams — everything that touches the host. In-memory fakes for
# tests; the real bindings (file writes + mkdir/chmod) land at install time and
# are exercised only by the plan's Tests Humanos.
# ---------------------------------------------------------------------------
@runtime_checkable
class EnvFileWriter(Protocol):
    """Writes the generated ``.env`` / ``config/global.yaml`` to disk.

    The real binding writes the file with ``0o600`` perms (it holds secrets).
    The fake records the write so tests assert the path + content without
    touching disk.
    """

    def write(self, path: str, content: str, *, mode: int) -> None:
        """Write *content* to *path* with POSIX *mode*."""
        ...


@runtime_checkable
class DataTreeProvisioner(Protocol):
    """Creates the data-tree directories with their POSIX permissions.

    The real binding ``mkdir -p`` + ``chmod``s each dir under
    ``/data/agent-platform``. The fake records the plan it was asked to create.
    """

    def provision(self, plan: list[DataDir]) -> None:
        """Create every directory in *plan* with its declared mode."""
        ...


@dataclass
class FakeEnvFileWriter:
    """Records ``.env``/YAML writes instead of touching disk (test default)."""

    written: dict[str, str] = field(default_factory=dict)
    modes: dict[str, int] = field(default_factory=dict)

    def write(self, path: str, content: str, *, mode: int) -> None:
        self.written[path] = content
        self.modes[path] = mode


@dataclass
class FakeDataTreeProvisioner:
    """Records the data-tree plan it was asked to provision (test default)."""

    provisioned: list[DataDir] = field(default_factory=list)

    def provision(self, plan: list[DataDir]) -> None:
        self.provisioned.extend(plan)
