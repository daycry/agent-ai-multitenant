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
    # monitoring=True EN AMBOS LADOS: el overlay también forma parte del
    # contrato (grafana referencia ${GRAFANA_ADMIN_USER}/${..PASSWORD}, que el
    # .env solo emite con el flag). El RealStepExecutor pasa el MISMO flag a
    # compose y env — generarlos con flags distintos deja a Grafana con
    # credenciales vacías (verificación del instalador, 2026-07-18).
    compose = generate_compose(cfg, monitoring=True)
    dotenv = set(build_env_vars(cfg, generate_secrets(), monitoring=True))

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


@pytest.mark.parametrize("env_profile", list(Environment))
def test_compose_emits_an_environment_value_the_runtime_accepts(env_profile: Environment) -> None:
    """El valor de ``<PREFIX>ENVIRONMENT`` que emite el compose tiene que estar en
    el enum cerrado que valida el runtime, para los TRES perfiles.

    Este test nace de un fallo con dos generadores y una sola mitad arreglada.
    `config_generators.py` (el `.env`) traduce el enum del instalador al del
    runtime con `_RUNTIME_ENVIRONMENT` — `production` -> `prod`. El compose no lo
    hacía: emitía `cfg.system.environment.value` en crudo, o sea `production`.
    Mientras el guard de `environment` era fail-OPEN eso pasaba desapercibido (un
    valor desconocido se trataba como dev, que es justo el agujero que prod-09
    task_02 cerró). Al volverlo fail-CLOSED, el api-server generado por el
    instalador dejó de arrancar: `API_SERVER_ENVIRONMENT='production' is not a
    known environment`.

    Se parametriza por los tres perfiles a propósito: con solo `production` el
    test pasaría el día que alguien "arreglase" el mapeo con un `if prod`.
    """
    cfg = _prod_config()
    cfg.system.environment = env_profile
    compose = generate_compose(cfg, monitoring=False)

    accepted = {"dev", "staging", "prod"}
    seen: dict[str, str] = {}
    for name, service in compose["services"].items():
        for key, value in (service.get("environment") or {}).items():
            if key.endswith("ENVIRONMENT") and isinstance(value, str):
                seen[f"{name}:{key}"] = value

    assert seen, "el compose dejó de emitir ENVIRONMENT: el test se quedó sin objeto"
    bad = {k: v for k, v in seen.items() if v not in accepted}
    assert not bad, (
        f"el compose emite valores de ENVIRONMENT que el runtime RECHAZA al arrancar: {bad}. "
        f"Aceptados: {sorted(accepted)}. Traduce el enum del instalador como hace "
        "config_generators._RUNTIME_ENVIRONMENT."
    )


def test_internal_token_secret_reaches_api_server_and_workers() -> None:
    """`API_SERVER_INTERNAL_TOKEN_SECRET` llega a los DOS servicios que lo usan.

    El ADR 0136 separó el secreto que firma los tokens internos del sandbox del
    que firma las sesiones humanas: comprometer el worker ya no permite forjar la
    sesión de un System Admin. Pero quien MINTEA el token del sandbox es el
    **worker**, que importa `mint_agent_token` del paquete del api-server y por
    tanto lee la variable con el prefijo `API_SERVER_`. Es la excepción que el
    contrato de prefijos no puede expresar (el worker corre dos clases de
    Settings), así que se fija aquí a mano.

    Sin esta guarda el instalador no lo emitía en ninguno de los tres sitios y,
    con el guard fail-closed de `environment`, el api-server generado NO ARRANCA:
    `environment='prod' but these settings still use dev defaults`.
    """
    cfg = _prod_config()
    compose = generate_compose(cfg, monitoring=False)
    key = "API_SERVER_INTERNAL_TOKEN_SECRET"

    for service in ("api-server", "workers"):
        env = compose["services"][service]["environment"]
        assert key in env, (
            f"{service} no recibe {key}: el api-server no arranca en prod, y el "
            "worker no puede mintear el token del sandbox"
        )

    dotenv = build_env_vars(cfg, generate_secrets(), monitoring=False)
    assert key in dotenv, f"{key} no se escribe en el .env: el compose lo resolvería a vacío"


def test_internal_token_secret_differs_from_the_jwt_secret() -> None:
    """Los dos secretos tienen que ser DISTINTOS, y no por higiene: `config.py`
    tiene una guarda que rechaza el arranque si coinciden. Emitir el mismo valor
    en las dos variables reproduciría el agujero que el ADR 0136 cerró —
    comprometer el worker volvería a permitir firmar sesiones— sin que nada
    avisara, así que el instalador debe generar material independiente."""
    dotenv = build_env_vars(_prod_config(), generate_secrets(), monitoring=False)
    assert (
        dotenv["API_SERVER_INTERNAL_TOKEN_SECRET"] != dotenv["API_SERVER_JWT_SECRET"]
    ), "el instalador emite el MISMO secreto para sesiones y tokens internos"


@pytest.mark.parametrize("env_profile", list(Environment))
def test_workers_get_the_api_server_environment_so_its_guards_fire(
    env_profile: Environment,
) -> None:
    """El worker recibe `API_SERVER_ENVIRONMENT`, y con un valor que el enum acepta.

    El worker corre DOS clases de `Settings`: las suyas (`WORKERS_*`) y las del
    api-server, porque mintea el token del sandbox importando `mint_agent_token`.
    Sin `API_SERVER_ENVIRONMENT`, esa segunda instancia se cree en `dev`: los
    guards anti-defaults NO disparan dentro del worker, así que un secreto que
    falte se degrada a su default de dev EN SILENCIO en vez de impedir el arranque.
    Es un fail-OPEN que sobrevivía al endurecimiento de `prod-09 task_02`, porque
    ese cerró el enum pero nadie emitía la variable en este servicio.
    """
    cfg = _prod_config()
    cfg.system.environment = env_profile
    compose = generate_compose(cfg, monitoring=False)
    env = compose["services"]["workers"]["environment"]

    assert "API_SERVER_ENVIRONMENT" in env, (
        "el worker no recibe API_SERVER_ENVIRONMENT: sus Settings de api-server se "
        "creen en dev y los guards anti-defaults no disparan"
    )
    assert env["API_SERVER_ENVIRONMENT"] in {"dev", "staging", "prod"}
