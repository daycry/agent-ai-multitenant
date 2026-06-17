"""Contract: the compose generator emits, for each platform app service, ONLY
environment keys PREFIXED with that service's real Settings ``env_prefix`` — and
every emitted key maps to a real Settings field (Plan prod-01 task_05, finding
secrets-2 / deploy-3 pata 1).

Why this exists: the runtime services read their config through pydantic
``BaseSettings`` with a per-service ``env_prefix`` (``API_SERVER_`` /
``WORKERS_`` / ``ORCHESTRATOR_`` / ``NOTIFY_``). If the installer emits an
UNprefixed key (``DATABASE_URL`` instead of ``API_SERVER_DATABASE_URL``) the app
silently ignores it and falls back to its dev default — and the prod
anti-dev-secret guard never even sees ``environment=prod``. This test crosses the
emitted keys against the real ``env_prefix`` + field names so the two cannot
diverge again.
"""

from __future__ import annotations

import re

import pytest
from api_server.config import Settings as ApiServerSettings
from installer_backend.compose_generator import generate_compose
from installer_backend.config import (
    Environment,
    InstallerConfig,
    OllamaProvider,
    PortsConfig,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
)
from installer_backend.config_generators import build_env_vars, generate_secrets
from notification_dispatcher.config import Settings as NotifySettings
from orchestrator.config import Settings as OrchestratorSettings
from workers.config import Settings as WorkersSettings

pytestmark = pytest.mark.unit

# service name in the compose  ->  the Settings class the container runs with
_APP_SERVICES = {
    "api-server": ApiServerSettings,
    "orchestrator": OrchestratorSettings,
    "workers": WorkersSettings,
    "notification-dispatcher": NotifySettings,
}


def _prod_config() -> InstallerConfig:
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=Environment.PRODUCTION),
        resources=ResourceConfig(
            worker_replicas=2,
            worker_memory_gib=4,
            gpu_enabled=False,
            ollama_mode=None,
            embedding_model="nomic-embed-text",
        ),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )


def _env_prefix(settings_cls: type) -> str:
    prefix = settings_cls.model_config.get("env_prefix")
    assert isinstance(prefix, str) and prefix, f"{settings_cls} has no env_prefix"
    return prefix


@pytest.mark.parametrize("service_name", sorted(_APP_SERVICES))
def test_no_settings_field_is_emitted_bare(service_name: str) -> None:
    """The secrets-2 bug: a key named like a Settings field but WITHOUT the
    service prefix (``DATABASE_URL`` instead of ``API_SERVER_DATABASE_URL``) is
    silently ignored by the app — it reads ``<PREFIX><FIELD>``. Cross-cutting
    wiring that is NOT a Settings field (e.g. the ``LLM_*`` provider toggles read
    by shared-llm) is exempt by construction."""
    settings_cls = _APP_SERVICES[service_name]
    fields = set(settings_cls.model_fields)
    compose = generate_compose(_prod_config())
    env = compose["services"][service_name]["environment"]
    assert env, f"{service_name} emits no environment"

    bare = [k for k in env if k.lower() in fields]
    assert not bare, (
        f"{service_name} emits Settings-field keys UNPREFIXED (the app reads "
        f"{_env_prefix(settings_cls)}* and ignores these): {bare}"
    )


@pytest.mark.parametrize("service_name", sorted(_APP_SERVICES))
def test_every_app_env_key_maps_to_a_real_settings_field(service_name: str) -> None:
    settings_cls = _APP_SERVICES[service_name]
    prefix = _env_prefix(settings_cls)
    fields = set(settings_cls.model_fields)
    compose = generate_compose(_prod_config())
    env = compose["services"][service_name]["environment"]

    unknown = [k for k in env if k.startswith(prefix) and k[len(prefix) :].lower() not in fields]
    assert not unknown, (
        f"{service_name} emits prefixed keys with no matching Settings field "
        f"(name drift — the app would ignore them): {unknown}"
    )


def test_api_server_emits_prod_critical_keys_prefixed() -> None:
    """The keys that gate prod behaviour must reach the api-server prefixed:
    environment (activates the dev-secret guard), DB URLs, JWT secret, Vault."""
    compose = generate_compose(_prod_config())
    env = compose["services"]["api-server"]["environment"]
    for key in (
        "API_SERVER_ENVIRONMENT",
        "API_SERVER_DATABASE_URL",
        "API_SERVER_ADMIN_DATABASE_URL",
        "API_SERVER_JWT_SECRET",
    ):
        assert key in env, f"api-server is missing required prefixed key {key}"


def test_every_prod_compose_env_ref_is_written_to_the_dotenv() -> None:
    """Compose↔.env contract: every ``${VAR}`` the PROD compose references (no
    ``:-default`` fallback in prod) MUST be a key config_generators writes into
    the ``.env`` — otherwise the container env resolves to empty and the app
    falls back to a dev default or fails (Plan prod-01 task_05, the
    config_generators ↔ builders cross-check)."""
    cfg = _prod_config()
    compose = generate_compose(cfg)
    dotenv = set(build_env_vars(cfg, generate_secrets()))

    referenced: set[str] = set()
    for service in compose["services"].values():
        for value in (service.get("environment") or {}).values():
            if isinstance(value, str):
                # prod has no ``:-default``; capture the bare ``${VAR}`` name.
                referenced.update(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value))

    missing = sorted(v for v in referenced if v not in dotenv)
    assert not missing, (
        "compose references ${VAR}s with no matching .env key from "
        f"build_env_vars (prod would resolve them to empty): {missing}"
    )
