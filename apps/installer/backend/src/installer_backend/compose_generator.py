"""docker-compose generator — wizard config → runtime stack (Plan 15 task_15_07).

Phase B fills the real generators the install orchestration (task 15_05's
``generate_config`` step) calls. This module is the **compose generator**: given
the wizard config (profile / GPU on-off / storage choices / enabled LLM
providers / ports) it produces the runtime stack's ``docker-compose.yml`` as a
plain ``dict`` (and, via :func:`render_compose_yaml`, the YAML text written at
install time).

Why a dict, not a template
---------------------------
The canonical base compose (``docker/docker-compose.yml``) is the source of
truth for the *shape* of every service (image pins, healthchecks, named
volumes, the two networks). The installer never ships a half-baked deployment
where the operator hand-edits YAML; instead this builds the compose
*programmatically* from a typed catalogue so the wizard and the CLI share one
generator and the result is deterministic + assertable. The produced mapping is
serialised to YAML with :func:`render_compose_yaml` and written under the data
root at install time (NOT committed — that write lives behind the install
seams; this module is pure, no I/O).

Hardening defaults
------------------
Every generated service carries the platform hardening defaults consistent with
the existing compose: ``restart: unless-stopped``, capped json-file logging,
``cap_drop: [ALL]`` + ``security_opt: ["no-new-privileges:true",
"apparmor=agentic-default"]`` and a ``deploy.resources.limits`` cap. Images are
pinned (never ``:latest``). The two networks (``agentic-net`` + the internal
``agentic-agents``) and the named volumes match the canonical compose.

These are all TRUSTED first-party platform services, so — like the canonical
``docker-compose.yml`` (revised, ADR 0040) — they rely on Docker's DEFAULT
seccomp profile (by NOT overriding it) rather than a hand-rolled default-deny
allowlist. The hand-rolled profile, when force-applied to every service,
SIGSEGV'd the Go services (vault/minio) and broke postgres; the strict
default-deny allowlist is reserved for the UNTRUSTED agent/test/review runtimes
the worker launches (``docker/seccomp/agent-runtime.json``). The generated
services still pin ``apparmor=agentic-default`` for host MAC confinement.

Secrets
-------
The generated compose references credentials via ``${ENV}`` placeholders ONLY —
it NEVER embeds a literal secret, and for a *production* install it omits the
``:-changeme…`` dev fallbacks the dev compose carries (so the generated YAML
passes the platform's prod secret guard: it contains none of the dev-default
markers ``changeme`` / ``dev-only`` / ``minioadmin``). The real values are
written to the ``.env`` / Vault by tasks 15_08-15_09; this module only wires the
references. Nothing here is logged.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import yaml

from installer_backend.config import (
    Environment,
    InstallerConfig,
    LLMProviderKind,
)

# ---------------------------------------------------------------------------
# Pinned images — kept in lockstep with docker/docker-compose.yml +
# docker-compose.monitoring.yml (supply-chain hygiene: never :latest).
# ---------------------------------------------------------------------------
IMAGE_POSTGRES = "pgvector/pgvector:pg16"
IMAGE_REDIS = "redis:7-alpine"
IMAGE_MINIO = "minio/minio:RELEASE.2024-11-07T00-52-20Z"
IMAGE_VAULT = "hashicorp/vault:1.17"
IMAGE_CLAMAV = "clamav/clamav:1.4"
IMAGE_DOCLING = "ghcr.io/docling-project/docling-serve:v1.20.0"
IMAGE_OLLAMA = "ollama/ollama:0.31.1"
IMAGE_PROMETHEUS = "prom/prometheus:v2.54.1"
IMAGE_GRAFANA = "grafana/grafana:11.2.0"
IMAGE_NODE_EXPORTER = "prom/node-exporter:v1.8.2"
IMAGE_ALERTMANAGER = "prom/alertmanager:v0.27.0"
IMAGE_CADVISOR = "gcr.io/cadvisor/cadvisor:v0.49.1"
# Read-only Docker API gateway with a per-endpoint ACL (Plan prod-01 task_09,
# ADR 0060). The workers reach the daemon ONLY through this, never the raw
# socket (Principio 2).
IMAGE_DOCKER_SOCKET_PROXY = "tecnativa/docker-socket-proxy:0.3.0"
IMAGE_CADDY = "caddy:2.8-alpine"
# Voice mode (ADR 0073): STT (faster-whisper) + TTS (Kokoro), OpenAI-compatible
# HTTP APIs, kept in lockstep with docker/docker-compose.yml. Both ship CPU
# images here (the GPU variants are a documented overlay — the canonical compose
# pins the same CPU tags); upstream uses rolling tags (no semver) so pin by
# digest for a fully reproducible prod if needed.
IMAGE_STT = "fedirz/faster-whisper-server:latest-cpu"
IMAGE_TTS = "ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2"

#: The application images the platform builds. The generator references them by
#: tag (the installer pulls the released images); the build context lives in the
#: repo. Pinned to the platform release tag at install time.
APP_IMAGE_TAG = "${PLATFORM_IMAGE_TAG:-v1.0.0}"
APP_IMAGE_REGISTRY = "${PLATFORM_REGISTRY:-ghcr.io/agentic-platform}"

# Dev-default markers the prod secret guard rejects (mirror of
# api_server.config._DEV_SECRET_MARKERS). The generated *production* compose
# must contain none of these.
_DEV_SECRET_MARKERS = ("changeme", "dev-only", "minioadmin")

# Compose top-level name (matches the canonical stack so `docker compose`
# treats a re-generated file as the same project).
PROJECT_NAME = "agentic-platform"

#: Canonical core services always present in the runtime stack.
CORE_SERVICES: tuple[str, ...] = (
    "postgres",
    "redis",
    "minio",
    "vault",
    "clamav",
    "docling-serve",
    "egress-proxy",
    "registry-proxy",
    "docker-socket-proxy",
    "migrations",
    "api-server",
    "orchestrator",
    "workers",
    "workers-privileged",
    "cortex-beat",
    "notification-dispatcher",
    "admin-panel",
    "caddy",
)

#: Services added only when the monitoring overlay is requested. Mirrors
#: docker/docker-compose.monitoring.yml so a production install has the SAME
#: observability as dev — including Alertmanager (routes Prometheus' alert rules
#: to the platform notifier) and cAdvisor (per-container metrics).
MONITORING_SERVICES: tuple[str, ...] = (
    "prometheus",
    "node-exporter",
    "alertmanager",
    "cadvisor",
    "grafana",
)

#: The in-stack Ollama service + its model-pull one-shot, added when
#: ``ollama_mode != "none"`` (ADR 0056). ``GPU_SERVICE`` is kept as a
#: backward-compatible alias of ``OLLAMA_SERVICE``.
OLLAMA_SERVICE = "ollama"
OLLAMA_BOOTSTRAP_SERVICE = "ollama-bootstrap"
GPU_SERVICE = OLLAMA_SERVICE

#: The in-stack voice services, added when ``voice_mode != "none"`` (ADR 0073).
#: ``stt`` = faster-whisper (POST /v1/audio/transcriptions), ``tts`` = Kokoro
#: (POST /v1/audio/speech). Both are reached internally by the api-server (which
#: also serves the córtex voice turn) at ``stt:8000`` / ``tts:8880``.
STT_SERVICE = "stt"
TTS_SERVICE = "tts"
VOICE_SERVICES: tuple[str, ...] = (STT_SERVICE, TTS_SERVICE)

#: Named volume that caches the Whisper model (downloaded on first use) so it
#: survives restarts instead of being re-pulled every boot. Matches the
#: canonical compose's ``whisper_models`` volume.
WHISPER_MODELS_VOLUME = "whisper_models"

#: Name of the AppArmor MAC profile every generated service pins via
#: ``security_opt: apparmor=…`` (Plan 15 task_15_16). Unlike seccomp (a path),
#: AppArmor profiles are referenced by the NAME they were loaded under with
#: ``apparmor_parser`` on the host. The installer ships
#: docker/apparmor/agentic-default.profile and the install/runbook step loads
#: it (real load is a host/HUMAN step — the kernel cannot be exercised in CI).
APPARMOR_DEFAULT_PROFILE = "agentic-default"


def _logging_block() -> dict[str, Any]:
    """The capped json-file logging block every service shares."""

    return {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }


# Capabilities the official infra images need back on top of cap_drop:[ALL] to
# self-initialise: chown/chmod their data dir as root and drop to their service
# user via gosu/su-exec. Without them postgres/redis/clamav/egress-proxy
# crash-loop on start ("chmod/chown: Operation not permitted", "Permission
# denied", "Unable to change to group"). prod-01: the cap_drop baseline
# (task_08) was too broad for stateful official images — these add back ONLY the
# self-init caps, never the dangerous ones (NET_ADMIN, SYS_ADMIN, …). Mirrors the
# canonical compose's x-infra-caps anchor.
_INFRA_CAPS = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]


def _hardening(
    *,
    limits_cpus: str,
    limits_memory: str,
    cap_drop_all: bool = True,
) -> dict[str, Any]:
    """Platform hardening defaults applied to a generated service.

    ``cap_drop: [ALL]`` + ``no-new-privileges`` mirror the canonical compose's
    hardened services; the AppArmor MAC profile (``apparmor=agentic-default``,
    Plan 15 task_15_16) lets the host kernel deny the container-escape
    primitives; ``deploy.resources.limits`` caps CPU/memory so a runaway
    container can't starve the single host. A few infra images (Vault needs
    ``IPC_LOCK``) opt out of the blanket cap-drop via ``cap_drop_all=False``.

    These are TRUSTED first-party services: like the canonical
    ``docker-compose.yml`` (revised, ADR 0040) they rely on Docker's DEFAULT
    seccomp profile (NOT overridden). The hand-rolled default-deny allowlist
    SIGSEGV'd the Go services and broke postgres when force-applied here; it is
    reserved for the UNTRUSTED agent/test runtimes (the worker pins
    ``docker/seccomp/agent-runtime.json`` at launch). Operators who want the
    extra-hardening opt-in profile (``docker/seccomp/default.json``) can pin it
    after validating it on their own kernel.
    """

    block: dict[str, Any] = {
        "restart": "unless-stopped",
        "logging": _logging_block(),
        "security_opt": [
            "no-new-privileges:true",
            f"apparmor={APPARMOR_DEFAULT_PROFILE}",
        ],
        "deploy": {
            "resources": {"limits": {"cpus": limits_cpus, "memory": limits_memory}},
        },
    }
    if cap_drop_all:
        block["cap_drop"] = ["ALL"]
    return block


def _healthcheck(test: str, *, start_period: str = "30s", timeout: str = "10s") -> dict[str, Any]:
    """A CMD-SHELL healthcheck block (task_prod01_07). ``start_period`` gives a
    grace window for boot before failures count (Celery workers take longer)."""

    return {
        "test": ["CMD-SHELL", test],
        "interval": "30s",
        "timeout": timeout,
        "retries": 5,
        "start_period": start_period,
    }


def _http_healthcheck(url: str, *, start_period: str = "30s") -> dict[str, Any]:
    """HTTP liveness probe using python's stdlib (no shell, no external tool).

    The first-party app images are ``python:3.12-slim``, which ships NEITHER
    wget NOR curl. A wget-based healthcheck therefore marks api-server /
    orchestrator permanently unhealthy, so ``depends_on: service_healthy`` is
    never satisfied and the whole stack fails to come up (prod-01: verified live
    — the api-server only went healthy once the probe used python). Celery lanes
    use ``celery inspect ping`` (binary present in their image), so they keep the
    CMD-SHELL ``_healthcheck`` helper.
    """

    code = (
        "import urllib.request,sys;"
        f"sys.exit(0 if urllib.request.urlopen('{url}',timeout=5).status==200 else 1)"
    )
    return {
        "test": ["CMD", "python", "-c", code],
        "interval": "30s",
        "timeout": "5s",
        "retries": 5,
        "start_period": start_period,
    }


def _env_ref(var: str, dev_default: str | None, *, prod: bool) -> str:
    """A ``${VAR}`` reference, keeping a dev fallback only outside prod.

    For a production install we omit the ``:-default`` fallback so the compose
    carries NO dev-default marker (the prod secret guard rejects those) and a
    missing ``.env`` value fails loudly instead of silently using a known
    secret. For dev/staging we keep the convenience fallback.
    """

    if prod or dev_default is None:
        return f"${{{var}}}"
    return f"${{{var}:-{dev_default}}}"


# ---------------------------------------------------------------------------
# Individual service builders. Each returns a compose service mapping. They are
# parametrised by the wizard config (ports, data root, resources, prod-ness).
# ---------------------------------------------------------------------------
def _postgres_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": IMAGE_POSTGRES,
        "environment": {
            "POSTGRES_USER": _env_ref("POSTGRES_USER", "postgres", prod=prod),
            "POSTGRES_PASSWORD": _env_ref("POSTGRES_PASSWORD", None, prod=prod),
            "POSTGRES_DB": _env_ref("POSTGRES_DB", "agentic_platform", prod=prod),
            "POSTGRES_INITDB_ARGS": "--encoding=UTF8 --locale=C",
            "MIGRATIONS_USER_PASSWORD": _env_ref("MIGRATIONS_USER_PASSWORD", None, prod=prod),
            "APP_USER_PASSWORD": _env_ref("APP_USER_PASSWORD", None, prod=prod),
        },
        "volumes": [
            f"{cfg.storage.data_root}/postgres:/var/lib/postgresql/data",
            "./postgres/init:/docker-entrypoint-initdb.d:ro",
        ],
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                "pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-agentic_platform}",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    svc["cap_add"] = list(_INFRA_CAPS)  # postgres self-inits PGDATA as root
    return svc


def _redis_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_REDIS,
        "command": [
            "redis-server",
            "--appendonly",
            "yes",
            "--appendfsync",
            "everysec",
            "--save",
            "60 1",
            "--maxmemory",
            "${REDIS_MAX_MEM:-512mb}",
            "--maxmemory-policy",
            "allkeys-lru",
        ],
        "volumes": [f"{cfg.storage.data_root}/redis:/data"],
        "healthcheck": {
            "test": ["CMD", "redis-cli", "ping"],
            "interval": "10s",
            "timeout": "3s",
            "retries": 5,
            "start_period": "10s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="1g"))
    svc["cap_add"] = list(_INFRA_CAPS)  # redis chowns /data + drops to redis user
    return svc


def _minio_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": IMAGE_MINIO,
        "command": 'server /data --console-address ":9001"',
        "environment": {
            "MINIO_ROOT_USER": _env_ref("MINIO_ROOT_USER", "minioadmin", prod=prod),
            "MINIO_ROOT_PASSWORD": _env_ref("MINIO_ROOT_PASSWORD", None, prod=prod),
        },
        "volumes": [f"{cfg.storage.data_root}/minio:/data"],
        "healthcheck": {
            "test": ["CMD", "mc", "ready", "local"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    return svc


def _vault_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    # Vault drops ALL caps like every other service but adds IPC_LOCK back to
    # mlock its memory (matches the canonical compose's cap_drop:[ALL] +
    # cap_add:[IPC_LOCK] — one hardening criterion, prod-01 task_08).
    svc: dict[str, Any] = {
        "image": IMAGE_VAULT,
        # IPC_LOCK to mlock memory; SETFCAP because the entrypoint setcaps its
        # own binary; plus the self-init/user-drop caps (_INFRA_CAPS).
        "cap_add": ["IPC_LOCK", "SETFCAP", *_INFRA_CAPS],
        "environment": {
            "VAULT_ADDR": "http://0.0.0.0:8200",
            "VAULT_API_ADDR": "http://0.0.0.0:8200",
        },
        "volumes": [
            f"{cfg.storage.data_root}/vault/file:/vault/file",
            f"{cfg.storage.data_root}/vault/logs:/vault/logs",
            "./vault/config.hcl:/vault/config/config.hcl:ro",
        ],
        "command": ["server"],
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                "wget -qO- 'http://127.0.0.1:8200/v1/sys/health"
                "?standbyok=true&sealedcode=200&uninitcode=200' || exit 1",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 10,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    # cap_drop:[ALL] + cap_add:[IPC_LOCK] (above) — drop everything, add back
    # only the cap Vault needs to mlock memory. no-new-privileges + limits apply.
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


def _clamav_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_CLAMAV,
        "environment": {"CLAMAV_NO_FRESHCLAMD": "false"},
        "volumes": [f"{cfg.storage.data_root}/clamav:/var/lib/clamav"],
        "healthcheck": {
            "test": ["CMD-SHELL", "clamdscan --version || exit 1"],
            "interval": "30s",
            "timeout": "10s",
            "retries": 5,
            "start_period": "120s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    svc["cap_add"] = list(_INFRA_CAPS)  # clamav chowns /var/lib/clamav + drops user
    return svc


def _docling_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_DOCLING,
        "environment": {"DOCLING_SERVE_ENABLE_UI": "false"},
        "healthcheck": {
            # GET, not --spider (HEAD): docling's /health rejects HEAD, so a
            # spider check wrongly marks it unhealthy though it serves 200 on GET.
            "test": ["CMD-SHELL", "wget -q -O /dev/null http://localhost:5001/health || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "60s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="4g"))
    return svc


def _egress_proxy_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    # On TWO networks: agentic-net (egress to internet) + the internal
    # agentic-agents (the only path the sandbox runtime has to a provider).
    svc: dict[str, Any] = {
        "build": "./egress-proxy",
        "container_name": "agentic-egress-proxy",
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                "wget -q -O- --no-proxy http://127.0.0.1:8888/ 2>&1 | grep -q tinyproxy || true",
            ],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
        },
        "networks": ["agentic-net", "agentic-agents"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    svc["cap_add"] = list(_INFRA_CAPS)  # tinyproxy setgid/setuid drop on start
    return svc


def _registry_proxy_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    # ADR 0094: egress allowlisted de los runtime-templates a los registries de
    # paquetes públicos. SOLO en agentic-net (egress a internet); NUNCA en
    # agentic-agents — el agent-runtime no debe alcanzar github/pypi/etc. El
    # worker lo conecta a los bridges efímeros per-task de los runtimes.
    svc: dict[str, Any] = {
        "build": "./registry-proxy",
        "container_name": "agentic-registry-proxy",
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                "wget -q -O- --no-proxy http://127.0.0.1:8888/ 2>&1 | grep -q tinyproxy || true",
            ],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    svc["cap_add"] = list(_INFRA_CAPS)  # tinyproxy setgid/setuid drop on start
    return svc


def _docker_socket_proxy_service(
    cfg: InstallerConfig,  # noqa: ARG001 — uniform builder signature
    *,
    prod: bool,  # noqa: ARG001 — uniform builder signature
) -> dict[str, Any]:
    """Least-privilege Docker API gateway (Plan prod-01 task_09 / sandbox-1, ADR
    0060). The workers must launch ephemeral runtime containers, but handing them
    the raw ``/var/run/docker.sock`` is a full host-root escape (Principio 2). So
    this proxy holds the socket (read-only mount) and exposes a TCP API on a
    DEDICATED internal network with a per-endpoint ACL: containers/images/
    networks + POST are allowed (create + wire runtimes); exec/volumes/swarm and
    everything else are denied (no `docker exec`, no host bind-mounts, no swarm).
    """

    svc: dict[str, Any] = {
        "image": IMAGE_DOCKER_SOCKET_PROXY,
        "environment": {
            # Allow only what launching a sandbox runtime needs.
            "CONTAINERS": "1",
            "IMAGES": "1",
            "NETWORKS": "1",
            "POST": "1",
            # Deny the dangerous surface explicitly (defaults are 0, pinned for
            # clarity + as a regression guard).
            "EXEC": "0",
            "VOLUMES": "0",
            "SWARM": "0",
            "SECRETS": "0",
            "CONFIGS": "0",
            "NODES": "0",
            "SERVICES": "0",
            "TASKS": "0",
            "PLUGINS": "0",
            "SYSTEM": "0",
            "INFO": "0",
        },
        "volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"],
        "healthcheck": _healthcheck(
            "wget -q --spider http://localhost:2375/_ping || exit 1", start_period="10s"
        ),
        # Dedicated internal net ONLY (no agentic-net, no agentic-agents): only
        # the workers reach the Docker API, never the untrusted runtimes.
        "networks": ["agentic-docker"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    return svc


def _app_environment(cfg: InstallerConfig, prefix: str, *, prod: bool) -> dict[str, Any]:
    """Config EVERY platform app service reads, emitted PREFIXED with that
    service's pydantic ``env_prefix`` (``API_SERVER_`` / ``ORCHESTRATOR_`` /
    ``WORKERS_`` / ``NOTIFY_``).

    Emitting these UNprefixed (the old behaviour) meant the runtime — which reads
    ``<PREFIX><FIELD>`` — silently fell back to its dev default and the prod
    dev-secret guard never even saw ``environment=prod`` (finding secrets-2,
    deploy-3 pata 1). Only the two keys read by every service live here; each
    service builder adds its own keys (see ``_app_env`` usage). Secrets are
    ``${ENV}`` references only (no ``:-default`` in prod → fail loud).
    """

    return {
        f"{prefix}ENVIRONMENT": cfg.system.environment.value,
        # Reference the per-service DSN the .env carries (config_generators
        # writes one per service: api-server gets the app role, workers/notify
        # the migrations role, etc.) — NOT a shared bare var.
        f"{prefix}DATABASE_URL": _env_ref(f"{prefix}DATABASE_URL", None, prod=prod),
    }


def _migrations_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """One-shot that runs ``alembic upgrade head`` before the apps start (Plan
    prod-01 task_12 / deploy-6). Uses the api-server image (it ships the
    migrations + alembic) as the migrations role (``ADMIN_DATABASE_URL``,
    BYPASSRLS). env.py takes a ``pg_advisory_xact_lock`` so concurrent runs
    serialize. The app services ``depends_on`` it with
    ``service_completed_successfully`` (wired in :func:`generate_compose`)."""

    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/api-server:{APP_IMAGE_TAG}",
        "command": "alembic upgrade head",
        "environment": {
            # Alembic reads DATABASE_URL; migrations run as the migrations role.
            "DATABASE_URL": _env_ref("ADMIN_DATABASE_URL", None, prod=prod),
        },
        "depends_on": {"postgres": {"condition": "service_healthy"}},
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    svc["restart"] = "no"  # one-shot: run once and exit
    return svc


def _api_server_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    env = _app_environment(cfg, "API_SERVER_", prod=prod)
    env.update(
        {
            "API_SERVER_ADMIN_DATABASE_URL": _env_ref(
                "API_SERVER_ADMIN_DATABASE_URL", None, prod=prod
            ),
            # In-stack service URLs are fixed by this compose → literals (no .env
            # ref needed). Redis logical DBs: 0 cache, 1 broker, 2 result.
            "API_SERVER_REDIS_URL": "redis://redis:6379/0",
            "API_SERVER_BROKER_URL": "redis://redis:6379/1",
            "API_SERVER_RESULT_BACKEND": "redis://redis:6379/2",
            "API_SERVER_VAULT_URL": "http://vault:8200",
            "API_SERVER_MINIO_URL": "http://minio:9000",
            # Secrets: reference the per-service prefixed .env var that
            # config_generators.build_env_vars writes (the compose↔.env contract
            # is asserted by tests/unit/test_compose_env_contract.py). VAULT_TOKEN
            # is NOT here: it is optional (default None) and injected by the Vault
            # bootstrap (task 15_09), not the .env.
            "API_SERVER_JWT_SECRET": _env_ref("API_SERVER_JWT_SECRET", None, prod=prod),
            # NOTIF-2: Bearer del ingest de Alertmanager (fail-closed sin el).
            "API_SERVER_ALERTS_INGEST_TOKEN": _env_ref(
                "API_SERVER_ALERTS_INGEST_TOKEN", None, prod=prod
            ),
            "API_SERVER_MINIO_ACCESS_KEY": _env_ref("API_SERVER_MINIO_ACCESS_KEY", None, prod=prod),
            "API_SERVER_MINIO_SECRET_KEY": _env_ref("API_SERVER_MINIO_SECRET_KEY", None, prod=prod),
            "API_SERVER_SSO_ENCRYPTION_KEY": _env_ref(
                "API_SERVER_SSO_ENCRYPTION_KEY", None, prod=prod
            ),
            "API_SERVER_NOTIFICATION_ENCRYPTION_KEY": _env_ref(
                "API_SERVER_NOTIFICATION_ENCRYPTION_KEY", None, prod=prod
            ),
            "API_SERVER_REVIEW_URL_SIGNING_SECRET": _env_ref(
                "API_SERVER_REVIEW_URL_SIGNING_SECRET", None, prod=prod
            ),
            "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY": _env_ref(
                "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY", None, prod=prod
            ),
            # Public base URL the IdP redirects the BROWSER back to. Carries the
            # reverse proxy's /api prefix (ADR 0061): the IdP returns to
            # https://{domain}/api/auth/sso/oidc/callback and Caddy's
            # handle_path /api/* strips /api before reaching the api-server (the
            # app's dev default localhost:8001 is wrong for prod).
            "API_SERVER_SSO_REDIRECT_BASE_URL": f"https://{cfg.system.domain}/api",
        }
    )
    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/api-server:{APP_IMAGE_TAG}",
        "environment": env,
        # No host ports: the TLS reverse proxy (caddy) is the only published
        # surface (ADR 0061 / deploy-7); api-server is reached internally on
        # agentic-net as api-server:8000.
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
            "vault": {"condition": "service_healthy"},
        },
        "healthcheck": _http_healthcheck("http://localhost:8000/healthz"),
        # agentic-agents (internal) so the sandbox runtimes can reach the
        # internal API directly, bypassing the egress-proxy (ADR 0060 B1,
        # task_11). The PUBLIC surface stays on agentic-net / behind the TLS
        # reverse proxy (Fase E).
        "networks": ["agentic-net", "agentic-agents"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    return svc


def _orchestrator_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    env = _app_environment(cfg, "ORCHESTRATOR_", prod=prod)
    env.update(
        {
            "ORCHESTRATOR_REDIS_URL": "redis://redis:6379/0",
            "ORCHESTRATOR_BROKER_URL": "redis://redis:6379/1",
        }
    )
    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/orchestrator:{APP_IMAGE_TAG}",
        "environment": env,
        "healthcheck": _http_healthcheck("http://localhost:8002/healthz"),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="1g"))
    return svc


# Celery queue split (mirror of workers.celery_app.QUEUE_NAMES — the unit test
# cross-checks this against the real queue topology so it cannot drift). The
# generic pool drains every non-privileged queue; the ``privileged`` queue
# (backups, key rotation — touches Vault/secrets) is drained ONLY by the
# singleton workers-privileged lane under the strictest profile, never the
# generic pool (runbook 06-capacity-management.md). ``heavy``/``gpu`` removed by
# ADR 0083 (prod-06 colas_02) — dead lanes on a single host.
_WORKER_GENERIC_QUEUES = "default,ingestion,test,review"
_WORKER_PRIVILEGED_QUEUE = "privileged"

# Both worker lanes sit on three nets (task_09): agentic-net (general), the
# internal agentic-agents (reach the egress-proxy + the runtimes they launch),
# and the internal agentic-docker (reach the docker-socket-proxy).
_WORKER_NETWORKS = ["agentic-net", "agentic-agents", "agentic-docker"]


def _workers_env(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """The WORKERS_* environment shared by both worker lanes (same Settings)."""
    env = _app_environment(cfg, "WORKERS_", prod=prod)
    env.update(
        {
            "WORKERS_BROKER_URL": "redis://redis:6379/1",
            "WORKERS_RESULT_BACKEND": "redis://redis:6379/2",
            # prod-01 A10 (auditoría 2026-07-06): DB 0, la MISMA que lee el WS del
            # api-server (API_SERVER_REDIS_URL) y el orchestrator — los streams
            # exec:{id} del worker se publican aquí. Con /3 (sin consumidor) el
            # streaming en vivo de logs quedaba roto (manuals.yml ya lo corrigió).
            "WORKERS_EVENTS_REDIS_URL": "redis://redis:6379/0",
            "WORKERS_DATA_ROOT": cfg.storage.data_root,
            # Docker API via the least-privilege proxy, never the raw socket
            # (task_09, ADR 0060). DOCKER_HOST is read by the docker SDK itself,
            # not a WORKERS_ Settings field, so it stays unprefixed.
            "DOCKER_HOST": "tcp://docker-socket-proxy:2375",
            # Launched runtimes reach LLM providers only through the egress
            # allowlist proxy (field egress_proxy_url).
            "WORKERS_EGRESS_PROXY_URL": "http://egress-proxy:8888",
            # STRICT profiles the worker pins onto the UNTRUSTED runtimes it
            # launches (task_10 / sandbox-2). Without these the defaults are ""
            # and the sandboxes run with Docker's default profiles. The seccomp
            # JSON is bind-mounted by _workers_volumes; the AppArmor profile is
            # referenced by the NAME loaded on the host (runbook + installer
            # prereq load docker/apparmor/agent-runtime.profile).
            "WORKERS_SECCOMP_PROFILE_PATH": "/etc/agentic/seccomp/agent-runtime.json",
            "WORKERS_APPARMOR_PROFILE": "agent-runtime",
            # Backup wiring (workers-6 / prod-04). The NAMES are pinned here so
            # the .env contract holds; the correct VALUES (a dedicated pg_dump
            # DSN, the bind-mount capture path) are prod-04's job — TODO(prod-04).
            # Default the backup DSN to the migrations-role DSN the workers
            # already carry (pg_dump needs broad read).
            "WORKERS_BACKUP_DATABASE_URL": _env_ref("WORKERS_DATABASE_URL", None, prod=prod),
            "WORKERS_BACKUP_ENCRYPTION_ENABLED": "true",
            "WORKERS_BACKUP_ENCRYPTION_VAULT_KEY": "agentic-platform/backups/encryption-key",
        }
    )
    return env


def _workers_volumes(cfg: InstallerConfig) -> list[str]:
    """Binds both worker lanes need: the data root (bare repos + per-task git
    worktrees, same path in/out so worktree paths resolve) and the seccomp
    profiles the worker pins onto the UNTRUSTED runtimes it launches
    (``docker/seccomp/agent-runtime.json``; ``WORKERS_SECCOMP_PROFILE`` points
    at it — set in task_prod01_10)."""
    return [
        f"{cfg.storage.data_root}:{cfg.storage.data_root}",
        "./docker/seccomp:/etc/agentic/seccomp:ro",
    ]


def _workers_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    # worker_replicas / worker_memory_gib come from the wizard's ResourceConfig
    # (parametrised resource allocation, task 15_03).
    mem = f"{cfg.resources.worker_memory_gib}g"
    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/workers:{APP_IMAGE_TAG}",
        "command": f"celery -A workers.celery_app worker --queues={_WORKER_GENERIC_QUEUES}",
        "environment": _workers_env(cfg, prod=prod),
        "volumes": _workers_volumes(cfg),
        "healthcheck": _healthcheck(
            # G-06: ping a ESTE nodo — sin -d es un broadcast al broker
            # compartido y contesta cualquier worker vivo (falso healthy). El
            # timeout de 30s cubre el arranque de celery bajo carga (el default
            # de 10s producía unhealthy crónico sin fallo real).
            "celery -A workers.celery_app inspect ping -d celery@$$HOSTNAME -t 5 || exit 1",
            start_period="40s",
            timeout="30s",
        ),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": _WORKER_NETWORKS,
    }
    svc.update(_hardening(limits_cpus="4.0", limits_memory=mem))
    # Scale the GENERIC Celery worker pool per the wizard's resource choice.
    svc["deploy"]["replicas"] = cfg.resources.worker_replicas
    return svc


#: Los named volumes que el backup taréa (prod-01 A9 / prod-04). El worker corre
#: `tar` sobre sus _data (owned uid 999/100, modo 0700) → necesita root + el mount
#: de /var/lib/docker/volumes. Los nombres llevan el prefijo del proyecto compose.
_BACKUP_VOLUME_NAMES = (
    "agentic-platform_minio_data",
    "agentic-platform_redis_data",
    "agentic-platform_vault_data",
    "agentic-platform-agent-data",
)


def _workers_privileged_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """Separate lane that drains ONLY the ``privileged`` queue (backups, key
    rotation). Singleton (replicas=1) — its periodic jobs must not double-run.
    Same image + WORKERS_* env as the generic pool; different queue + scale.

    prod-01 A9 (auditoría 2026-07-06): esta lane ejecuta el volume-tar del
    backup, que lee los ``_data`` de los named volumes (redis uid 999, vault uid
    100) a 0700 → necesita correr como ROOT (``WORKERS_RUN_AS_ROOT=1``; el
    entrypoint self-heal baja a 1000 salvo esta bandera) y bind-montear
    ``/var/lib/docker/volumes``. Sin esto el volume-tar daba EACCES y el backup
    fallaba en una instalación por el instalador (solo funcionaba en manuals.yml).
    """
    env = _workers_env(cfg, prod=prod)
    env.update(
        {
            # Backup como root: leer los volume _data a 0700 (prod-01 A9 / prod-04).
            "WORKERS_RUN_AS_ROOT": "1",
            "WORKERS_BACKUP_VOLUMES": json.dumps(list(_BACKUP_VOLUME_NAMES)),
        }
    )
    volumes = [*_workers_volumes(cfg), "/var/lib/docker/volumes:/var/lib/docker/volumes"]
    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/workers:{APP_IMAGE_TAG}",
        "command": (
            f"celery -A workers.celery_app worker "
            f"--queues={_WORKER_PRIVILEGED_QUEUE} --concurrency=1"
        ),
        "environment": env,
        "volumes": volumes,
        "healthcheck": _healthcheck(
            # G-06: ping a ESTE nodo — sin -d es un broadcast al broker
            # compartido y contesta cualquier worker vivo (falso healthy). El
            # timeout de 30s cubre el arranque de celery bajo carga (el default
            # de 10s producía unhealthy crónico sin fallo real).
            "celery -A workers.celery_app inspect ping -d celery@$$HOSTNAME -t 5 || exit 1",
            start_period="40s",
            timeout="30s",
        ),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": _WORKER_NETWORKS,
    }
    # Corre como root (el volume-tar del backup lo exige); NO fijamos user 1000.
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    svc["deploy"]["replicas"] = 1
    return svc


def _cortex_beat_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """El Celery beat (scheduler) — prod-01 A9 (auditoría 2026-07-06).

    Sin este servicio, en una instalación por el instalador NADA se agenda:
    backup diario, rotación de credenciales, sweepers de mantenimiento (zombis,
    promoción DAG de red, reconciliación de pipeline, poda de worktrees/dep-cache),
    sync de precios/FX, escalado humano y los bucles de fondo del córtex — todos
    definidos en ``workers/beat_schedule.py``. Solo existía en ``manuals.yml``.
    Singleton (un solo beat, o los jobs se duplican). Healthcheck propio: beat no
    es un worker (``inspect ping`` no aplica) ni tiene HTTP — se comprueba que el
    proceso beat es el PID 1 vivo del contenedor."""
    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/workers:{APP_IMAGE_TAG}",
        "command": "celery -A workers.celery_app beat --loglevel=info",
        "environment": _workers_env(cfg, prod=prod),
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "import sys; sys.exit(0 if b'beat' in "
                "open('/proc/1/cmdline','rb').read() else 1)",
            ],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
            "start_period": "20s",
        },
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": _WORKER_NETWORKS,
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="512m"))
    svc["deploy"]["replicas"] = 1
    return svc


def _notification_dispatcher_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    env = _app_environment(cfg, "NOTIFY_", prod=prod)
    env.update(
        {
            "NOTIFY_BROKER_URL": "redis://redis:6379/1",
            "NOTIFY_RESULT_BACKEND": "redis://redis:6379/2",
            # AUD16 (H10): la DB del bus de eventos/DLQ debe ser la MISMA que
            # miran los consumidores (workers/api-server/orchestrator = DB 0).
            # Con la antigua DB 3, el stream dlq:notifications era invisible
            # para el sampler de métricas y NotificationsDLQNotEmpty no podía
            # disparar jamás en prod (dev ya usaba DB 0).
            "NOTIFY_EVENTS_REDIS_URL": "redis://redis:6379/0",
            "NOTIFY_NOTIFICATION_ENCRYPTION_KEY": _env_ref(
                "NOTIFY_NOTIFICATION_ENCRYPTION_KEY", None, prod=prod
            ),
        }
    )
    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/notification-dispatcher:{APP_IMAGE_TAG}",
        "environment": env,
        # -A debe ser el módulo REAL de la app (el mismo target del CMD del
        # Dockerfile): `-A notification_dispatcher` no carga («no attribute
        # 'celery'») y dejaba el servicio permanentemente unhealthy (cazado en
        # vivo 2026-07-10). `-d celery@$$HOSTNAME` hace ping a ESTE nodo — el
        # broker es compartido y un ping sin destino contestaría cualquier
        # worker vivo aunque este contenedor estuviera roto.
        "healthcheck": _healthcheck(
            "celery -A notification_dispatcher.celery_app:app inspect ping "
            "-d celery@$$HOSTNAME -t 5 || exit 1",
            start_period="40s",
            timeout="30s",
        ),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


def _admin_panel_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": f"{APP_IMAGE_REGISTRY}/admin-panel:{APP_IMAGE_TAG}",
        "environment": {
            "NODE_ENV": "production" if prod else "development",
            "PLATFORM_DOMAIN": cfg.system.domain,
        },
        # No host ports: the SPA is served through the TLS reverse proxy (caddy),
        # reached internally as admin-panel:3000 (ADR 0061 / deploy-7). NOTE: the
        # caddy service depends_on this one with condition=service_healthy; that
        # is satisfied by the HEALTHCHECK baked into the admin-panel image
        # (apps/admin-panel/Dockerfile), not a compose-level healthcheck here.
        "depends_on": {"api-server": {"condition": "service_healthy"}},
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


def _reverse_proxy_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """The single TLS-terminating reverse proxy — and the ONLY service that
    publishes host ports (ADR 0061, Plan prod-01 task_15 / deploy-7).

    Caddy serves one origin ``https://{domain}``: the admin-panel SPA at ``/``
    and the api-server under ``/api/*`` (see :mod:`installer_backend.proxy_generator`
    for the routing). The generated ``Caddyfile`` is bind-mounted read-only; the
    internal CA / ACME material persists under ``{data_root}/caddy/data`` so the
    self-signed root is not regenerated on every restart. With ``cap_drop:[ALL]``
    the process cannot bind 80/443, so ``NET_BIND_SERVICE`` is added back — the
    single capability needed, mirroring Vault's ``IPC_LOCK`` exception.
    """

    data_root = cfg.storage.data_root
    volumes = [
        "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro",
        f"{data_root}/caddy/data:/data",
        f"{data_root}/caddy/config:/config",
    ]
    if cfg.system.tls_mode == "provided":
        # The corporate cert+key the operator dropped under {data_root}/caddy/tls.
        volumes.append(f"{data_root}/caddy/tls:/etc/caddy/tls:ro")

    svc: dict[str, Any] = {
        "image": IMAGE_CADDY,
        # The ONLY published surface. Caddy listens on 80/443 inside the container.
        "ports": ["80:80", "443:443"],
        "volumes": volumes,
        "depends_on": {
            "api-server": {"condition": "service_healthy"},
            "admin-panel": {"condition": "service_healthy"},
        },
        # Plain-HTTP /healthz on :80 (no redirect to https) so the self-signed
        # cert + 308 don't mark the proxy unhealthy.
        "healthcheck": _healthcheck(
            "wget -q --spider http://127.0.0.1:80/healthz || exit 1", start_period="15s"
        ),
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    svc["cap_add"] = ["NET_BIND_SERVICE"]
    return svc


def _ollama_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """In-stack Ollama for embeddings (+ optional local LLMs) — added when
    ``ollama_mode != "none"`` (ADR 0056).

    On ``cpu`` it runs without any device reservation (enough for embeddings and
    small models). On ``gpu`` it adds the Compose-native NVIDIA reservation
    (``deploy.resources.reservations.devices``) so Docker schedules it on the GPU
    — requires the NVIDIA Container Toolkit. Hardened like the rest of the stack;
    the model lives under ``{data_root}/ollama`` (bind mount).
    """

    svc: dict[str, Any] = {
        "image": IMAGE_OLLAMA,
        "environment": {"OLLAMA_HOST": "0.0.0.0:11434"},
        "volumes": [f"{cfg.storage.data_root}/ollama:/root/.ollama"],
        "healthcheck": {
            "test": ["CMD-SHELL", "ollama list >/dev/null 2>&1 || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="4.0", limits_memory="8g"))
    if cfg.resources.ollama_mode == "gpu":
        # Compose-native GPU reservation (requires the NVIDIA Container Toolkit).
        # `capabilities` is a flat list of strings per the Compose schema.
        svc["deploy"]["resources"]["reservations"] = {
            "devices": [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}],
        }
    return svc


def _ollama_bootstrap_service(
    cfg: InstallerConfig,
    *,
    prod: bool,  # noqa: ARG001 — uniform builder signature; the bootstrap ignores prod
) -> dict[str, Any]:
    """One-shot that pulls the embedding model into the Ollama volume once the
    server is healthy, then exits (ADR 0056 option B-A).

    Without it the first ``/api/embed`` fails with ``model not found``. Idempotent
    (a re-run with the model present is a fast no-op; the model persists in the
    bind mount). Not hardened/limited like the long-lived services — it is a
    short-lived init that shares the model name with the api-server embedder.
    """

    model = cfg.resources.embedding_model
    svc: dict[str, Any] = {
        "image": IMAGE_OLLAMA,
        "depends_on": {OLLAMA_SERVICE: {"condition": "service_healthy"}},
        "environment": {"OLLAMA_HOST": "http://ollama:11434"},
        "entrypoint": ["/bin/sh", "-c"],
        "command": [f"ollama pull {model}"],
        "networks": ["agentic-net"],
    }
    # Same hardening posture as the long-lived services, but a one-shot must not
    # restart (it pulls once and exits) — override the restart policy to "no".
    svc.update(_hardening(limits_cpus="1.0", limits_memory="2g"))
    svc["restart"] = "no"
    return svc


def _python_health(binary: str, url: str, *, start_period: str) -> dict[str, Any]:
    """A python-stdlib HTTP liveness probe (urllib) for the voice images.

    The stt/tts images ship NEITHER wget NOR curl (only a python interpreter),
    so a wget-based check would mark them permanently unhealthy. ``binary`` is
    ``python3`` for faster-whisper and ``python`` for Kokoro (its venv exposes
    ``python``), mirroring docker/docker-compose.yml. ``urlopen`` raises (exit
    != 0) when /health is not yet serving.
    """

    return {
        "test": [
            "CMD",
            binary,
            "-c",
            f"import urllib.request; urllib.request.urlopen('{url}', timeout=4)",
        ],
        "interval": "30s",
        "timeout": "5s",
        "retries": 5,
        "start_period": start_period,
    }


def _stt_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """Speech-to-Text for the Assistant + córtex voice mode (ADR 0073).

    faster-whisper (CTranslate2) with an OpenAI-compatible API. The Whisper
    model is downloaded on first use and cached in the ``whisper_models`` named
    volume (the long ``start_period`` covers that first download). Internal
    service: no host ports — the api-server reaches it at ``stt:8000``. Mirrors
    docker/docker-compose.yml; the healthcheck probes with python3 (no wget in
    the image).
    """

    svc: dict[str, Any] = {
        "image": IMAGE_STT,
        "environment": {
            # CPU-friendly ES+EN default; large-v3 lives behind the GPU overlay.
            "WHISPER__MODEL": "Systran/faster-whisper-small",
            "WHISPER__INFERENCE_DEVICE": "cpu",
        },
        "volumes": [f"{WHISPER_MODELS_VOLUME}:/root/.cache/huggingface"],
        # 1st boot downloads the model → generous grace window.
        "healthcheck": _python_health(
            "python3", "http://localhost:8000/health", start_period="120s"
        ),
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="4g"))
    return svc


def _tts_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """Text-to-Speech for the voice mode (ADR 0073) — Kokoro-82M with an
    OpenAI-compatible API (ES+EN voices). Internal: the api-server reaches it at
    ``tts:8880``. Mirrors docker/docker-compose.yml; healthcheck probes with
    python (the image exposes ``python`` in its venv, not wget)."""

    svc: dict[str, Any] = {
        "image": IMAGE_TTS,
        "healthcheck": _python_health("python", "http://localhost:8880/health", start_period="60s"),
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    return svc


def _prometheus_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_PROMETHEUS,
        "user": "65534:65534",
        "command": [
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/prometheus",
            "--storage.tsdb.retention.time=15d",
            "--web.enable-lifecycle",
        ],
        "volumes": [
            "./monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro",
            "./monitoring/prometheus/rules:/etc/prometheus/rules:ro",
            f"{cfg.storage.data_root}/prometheus:/prometheus",
        ],
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -q --spider http://localhost:9090/-/healthy || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="1g"))
    return svc


def _node_exporter_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_NODE_EXPORTER,
        "command": [
            "--path.procfs=/host/proc",
            "--path.sysfs=/host/sys",
            "--path.rootfs=/host/root",
            "--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)",
        ],
        "pid": "host",
        "volumes": [
            "/proc:/host/proc:ro",
            "/sys:/host/sys:ro",
            "/:/host/root:ro,rslave",
        ],
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    return svc


def _alertmanager_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """Alertmanager — routes Prometheus' firing alerts to the platform notifier.

    Mirrors docker/docker-compose.monitoring.yml: the routing/receiver config is
    the secret-free ``monitoring/alertmanager/alertmanager.yml`` (its default
    receiver webhooks the api-server's ``/internal/alerts/ingest``, reusing the
    Plan 10 notifier — no SMTP/Slack secrets here). Without this service the
    alert RULES Prometheus evaluates would have nowhere to go in production.
    """
    svc: dict[str, Any] = {
        "image": IMAGE_ALERTMANAGER,
        "user": "65534:65534",
        "command": [
            "--config.file=/etc/alertmanager/alertmanager.yml",
            "--storage.path=/alertmanager",
        ],
        "volumes": [
            "./monitoring/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro",
            f"{cfg.storage.data_root}/alertmanager:/alertmanager",
        ],
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -q --spider http://localhost:9093/-/healthy || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    return svc


def _cadvisor_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """cAdvisor — per-container CPU/memory/network/fs metrics.

    prod-12 task_prod12_cadv_01 (sandbox-8, decisión 5 opción a): cAdvisor ya
    NO corre ``privileged`` ni monta ``/dev/kmsg`` — los stats de contenedor
    salen de los bind-mounts read-only (cgroups vía /sys + /rootfs +
    /var/lib/docker), que funcionan con ``cap_drop: [ALL]`` + AppArmor pineado
    como cualquier otro servicio de primera parte (validado empíricamente:
    families container_cpu/memory/network/fs presentes sin privileged; lo que
    se pierde es la decodificación de eventos OOM-kill del kernel vía
    /dev/kmsg — trade-off documentado en el runbook de monitoring, con el
    override legacy-privileged como opt-in manual para quien lo necesite).
    Esto RESUELVE la contradicción sandbox-8 con
    docker/docker-compose.monitoring.yml (que siempre pineó apparmor).
    """
    svc: dict[str, Any] = {
        "image": IMAGE_CADVISOR,
        "command": ["--docker_only=true", "--housekeeping_interval=30s"],
        "volumes": [
            "/:/rootfs:ro",
            "/var/run:/var/run:ro",
            "/sys:/sys:ro",
            "/var/lib/docker/:/var/lib/docker:ro",
            "/dev/disk/:/dev/disk:ro",
        ],
        "healthcheck": {
            "test": ["CMD", "wget", "-q", "--spider", "http://localhost:8080/healthz"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    return svc


def _grafana_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": IMAGE_GRAFANA,
        "user": "472:472",
        "environment": {
            "GF_SECURITY_ADMIN_USER": _env_ref("GRAFANA_ADMIN_USER", "admin", prod=prod),
            "GF_SECURITY_ADMIN_PASSWORD": _env_ref("GRAFANA_ADMIN_PASSWORD", None, prod=prod),
            "GF_USERS_ALLOW_SIGN_UP": "false",
            "GF_AUTH_ANONYMOUS_ENABLED": "false",
            "GF_ANALYTICS_REPORTING_ENABLED": "false",
            "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
        },
        "volumes": [
            "./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro",
            "./monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro",
            f"{cfg.storage.data_root}/grafana:/var/lib/grafana",
        ],
        "depends_on": ["prometheus"],
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -q --spider http://localhost:3000/api/health || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


#: Builder dispatch — one entry per service name.
_BUILDERS = {
    "postgres": _postgres_service,
    "redis": _redis_service,
    "minio": _minio_service,
    "vault": _vault_service,
    "clamav": _clamav_service,
    "docling-serve": _docling_service,
    "egress-proxy": _egress_proxy_service,
    "registry-proxy": _registry_proxy_service,
    "docker-socket-proxy": _docker_socket_proxy_service,
    "migrations": _migrations_service,
    "api-server": _api_server_service,
    "orchestrator": _orchestrator_service,
    "workers": _workers_service,
    "workers-privileged": _workers_privileged_service,
    "cortex-beat": _cortex_beat_service,
    "notification-dispatcher": _notification_dispatcher_service,
    "admin-panel": _admin_panel_service,
    "caddy": _reverse_proxy_service,
    "ollama": _ollama_service,
    "ollama-bootstrap": _ollama_bootstrap_service,
    "stt": _stt_service,
    "tts": _tts_service,
    "prometheus": _prometheus_service,
    "node-exporter": _node_exporter_service,
    "alertmanager": _alertmanager_service,
    "cadvisor": _cadvisor_service,
    "grafana": _grafana_service,
}


def _provider_env_for(cfg: InstallerConfig) -> dict[str, str]:
    """Provider wiring injected into the app services from the enabled providers.

    Each enabled ADR-0021 provider contributes its (non-secret) wiring: a feature
    flag + endpoint reference. The real credentials live in Vault (task 15_09);
    here we only toggle which providers the runtime is configured to use, so a
    disabled provider leaves NO wiring in the compose at all.
    """

    env: dict[str, str] = {}
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
        # Prefer an explicit endpoint; default to the in-stack service when one
        # is deployed (cpu or gpu), otherwise leave the wizard-provided endpoint.
        if providers.ollama.endpoint:
            env["LLM_OLLAMA_ENDPOINT"] = providers.ollama.endpoint
        elif cfg.resources.ollama_mode != "none":
            env["LLM_OLLAMA_ENDPOINT"] = "http://ollama:11434"

    # Embedder wiring (ADR 0056): when the in-stack Ollama is deployed, point the
    # api-server embedder (and the memory back-fill worker) at it and pin the
    # model to the same one the bootstrap pulls. API_SERVER_* are read only by
    # the api-server; WORKERS_* only by the workers — harmless on the others.
    if cfg.resources.ollama_mode != "none":
        env["API_SERVER_OLLAMA_URL"] = "http://ollama:11434"
        env["API_SERVER_EMBEDDING_MODEL"] = cfg.resources.embedding_model
        env["WORKERS_MEMORY_EMBEDDER_BASE_URL"] = "http://ollama:11434"

    # Voice wiring (ADR 0073): when the in-stack stt/tts are deployed, point the
    # api-server (which serves BOTH the Assistant voice loop and the córtex voice
    # turn — they read the same assistant_{stt,tts}_url settings) at them.
    # Without these the runtime falls back to its localhost dev defaults and the
    # voice mode silently fails in production (the bug this fixes). API_SERVER_*
    # are read only by the api-server → harmless on the other app services.
    if cfg.resources.voice_mode != "none":
        env["API_SERVER_ASSISTANT_STT_URL"] = f"http://{STT_SERVICE}:8000"
        env["API_SERVER_ASSISTANT_TTS_URL"] = f"http://{TTS_SERVICE}:8880"
    return env


def enabled_providers(cfg: InstallerConfig) -> tuple[LLMProviderKind, ...]:
    """The ordered tuple of providers enabled in the wizard config."""

    out: list[LLMProviderKind] = []
    if cfg.providers.claude_sdk.enabled:
        out.append(LLMProviderKind.CLAUDE_SDK)
    if cfg.providers.copilot.enabled:
        out.append(LLMProviderKind.COPILOT)
    if cfg.providers.azure_foundry.enabled:
        out.append(LLMProviderKind.AZURE_FOUNDRY)
    if cfg.providers.ollama.enabled:
        out.append(LLMProviderKind.OLLAMA)
    return tuple(out)


def selected_services(cfg: InstallerConfig, *, monitoring: bool) -> list[str]:
    """The ordered list of service names the generated compose will contain.

    Core services are always present; the in-stack ``ollama`` service + its
    ``ollama-bootstrap`` one-shot are added when ``ollama_mode != "none"`` (ADR
    0056); the voice ``stt``/``tts`` services when ``voice_mode != "none"`` (ADR
    0073); the monitoring overlay services only when requested.
    """

    services = list(CORE_SERVICES)
    if cfg.resources.ollama_mode != "none":
        services.append(OLLAMA_SERVICE)
        services.append(OLLAMA_BOOTSTRAP_SERVICE)
    if cfg.resources.voice_mode != "none":
        services.extend(VOICE_SERVICES)
    if monitoring:
        services.extend(MONITORING_SERVICES)
    return services


def _networks_block() -> dict[str, Any]:
    """The platform networks: agentic-net (egress), the internal agentic-agents
    (sandbox ↔ egress-proxy), and the internal agentic-docker — a DEDICATED net
    that carries ONLY the workers ↔ docker-socket-proxy traffic so the Docker
    API is never reachable from the untrusted agent runtimes (Plan prod-01
    task_09, ADR 0060)."""

    return {
        "agentic-net": {"name": "agentic-net", "driver": "bridge"},
        "agentic-agents": {
            "name": "agentic-agents",
            "driver": "bridge",
            "internal": True,
            "driver_opts": {"com.docker.network.bridge.enable_icc": "true"},
        },
        "agentic-docker": {
            "name": "agentic-docker",
            "driver": "bridge",
            "internal": True,
        },
    }


def generate_compose(
    cfg: InstallerConfig,
    *,
    monitoring: bool = False,
) -> dict[str, Any]:
    """Build the runtime stack ``docker-compose`` mapping from the wizard config.

    Parameters mirror the wizard choices:
      * ``cfg.resources.gpu_enabled`` → adds the GPU ``ollama`` service (profile
        ``gpu``) with an NVIDIA device reservation.
      * ``cfg.providers`` → only the enabled ADR-0021 providers get their wiring
        injected into the application services' environment.
      * ``cfg.ports`` → retained in the wizard model for back-compat / dev
        overrides, but NO LONGER mapped to the host in the generated production
        compose: the TLS reverse proxy (``caddy``) is the only published surface
        (ADR 0061).
      * ``cfg.storage.data_root`` → the bind-mount base for every stateful
        service.
      * ``monitoring`` → adds the Prometheus/Grafana/node-exporter overlay.

    Returns a plain ``dict`` (serialise with :func:`render_compose_yaml`). The
    mapping is hardened + secret-free (``${ENV}`` references only) and, for a
    production environment, carries no dev-default secret marker.
    """

    prod = cfg.system.environment is Environment.PRODUCTION
    service_names = selected_services(cfg, monitoring=monitoring)
    provider_env = _provider_env_for(cfg)

    # The platform app services that read the schema → must wait for the
    # one-shot migrations to finish (task_12 / deploy-6).
    migration_dependents = (
        "api-server",
        "orchestrator",
        "workers",
        "workers-privileged",
        "cortex-beat",
        "notification-dispatcher",
    )

    services: dict[str, Any] = {}
    for name in service_names:
        builder = _BUILDERS[name]
        svc = builder(cfg, prod=prod)
        # Inject the provider wiring into the application services only.
        if name in ("api-server", "orchestrator", "workers", "notification-dispatcher"):
            env = svc.setdefault("environment", {})
            assert isinstance(env, dict)
            env.update(provider_env)
        # Gate the apps on the schema being migrated.
        if name in migration_dependents:
            deps = svc.setdefault("depends_on", {})
            assert isinstance(deps, dict)
            deps["migrations"] = {"condition": "service_completed_successfully"}
        services[name] = svc

    compose: dict[str, Any] = {
        "name": PROJECT_NAME,
        "services": services,
        "networks": _networks_block(),
    }
    # Declare the named volume(s) any generated service references. The only one
    # is the Whisper model cache for the voice stt service (every other stateful
    # service uses a {data_root} bind mount). Declared only when voice is on so
    # the compose carries no dangling volume otherwise.
    if STT_SERVICE in services:
        compose["volumes"] = {WHISPER_MODELS_VOLUME: None}
    return compose


def render_compose_yaml(compose: dict[str, Any]) -> str:
    """Serialise a generated compose mapping to deterministic YAML text.

    ``sort_keys=False`` preserves the builder ordering (services first in
    pipeline order), ``default_flow_style=False`` keeps the block style the rest
    of the repo's compose files use. The output is what the install seam writes
    to disk; this function performs NO I/O.
    """

    text: str = yaml.safe_dump(
        copy.deepcopy(compose),
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    return text


def assert_no_dev_secret_markers(yaml_text: str) -> None:
    """Raise ``ValueError`` if a dev-default secret marker leaked into prod YAML.

    The prod secret guard (``api_server.config._DEV_SECRET_MARKERS``) rejects
    these substrings; the generator must never emit them for a production
    install. This is a belt-and-braces self-check the CLI/wizard can call after
    generating a production compose.
    """

    lowered = yaml_text.lower()
    found = [marker for marker in _DEV_SECRET_MARKERS if marker in lowered]
    if found:
        raise ValueError(
            "El docker-compose generado para producción contiene marcadores de "
            f"secreto de desarrollo: {', '.join(found)}."
        )
