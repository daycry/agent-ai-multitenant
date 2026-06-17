"""Unit tests for the docker-compose generator (Plan 15 task_15_07).

Asserts the Phase-B compose generator (`installer_backend.compose_generator`):
  * a minimal config yields a valid compose with the core services;
  * ``gpu_enabled`` adds the GPU ``ollama`` service behind the ``gpu`` profile
    with an NVIDIA device reservation;
  * a provider toggle includes / excludes its wiring in the app services;
  * the monitoring flag adds the Prometheus/Grafana/node-exporter overlay;
  * the rendered YAML parses, round-trips, and has no duplicate keys;
  * ports / volumes are parametrised from the wizard config;
  * platform hardening defaults (cap_drop, no-new-privileges, resource limits)
    are applied consistently;
  * a PRODUCTION compose carries no dev-default secret marker (prod secret
    guard) — secrets are ``${ENV}`` references only;
  * (when ``docker compose`` is available) ``docker compose -f <gen> config -q``
    accepts the generated file.

No host access: the generator is pure (returns a dict / YAML text). The
optional ``docker compose config`` check is the only thing that shells out and
is skipped when the CLI is absent. Real ``docker compose up`` is a HUMAN test.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from installer_backend.compose_generator import (
    _DEV_SECRET_MARKERS,
    APP_IMAGE_REGISTRY,
    CORE_SERVICES,
    GPU_SERVICE,
    MONITORING_SERVICES,
    OLLAMA_BOOTSTRAP_SERVICE,
    OLLAMA_SERVICE,
    assert_no_dev_secret_markers,
    enabled_providers,
    generate_compose,
    render_compose_yaml,
    selected_services,
)
from installer_backend.config import (
    AzureFoundryProvider,
    ClaudeSdkProvider,
    CopilotProvider,
    Environment,
    InstallerConfig,
    LLMProviderKind,
    OllamaProvider,
    PortsConfig,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Builders for test configs. Secrets use throwaway placeholder values; nothing
# real is committed.
# ---------------------------------------------------------------------------
def _config(
    *,
    environment: Environment = Environment.PRODUCTION,
    gpu_enabled: bool = False,
    ollama_mode: str | None = None,
    embedding_model: str = "nomic-embed-text",
    providers: ProvidersConfig | None = None,
    data_root: str = "/data/agent-platform",
    worker_replicas: int = 2,
    ports: PortsConfig | None = None,
) -> InstallerConfig:
    if providers is None:
        providers = ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434"))
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=environment),
        resources=ResourceConfig(
            worker_replicas=worker_replicas,
            worker_memory_gib=4,
            gpu_enabled=gpu_enabled,
            ollama_mode=ollama_mode,
            embedding_model=embedding_model,
        ),
        storage=StorageConfig(
            data_root=data_root,
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=providers,
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=ports or PortsConfig(),
    )


def _render(compose: dict[str, object]) -> dict[str, object]:
    """Render to YAML and parse it back (asserts the text is valid YAML)."""

    text = render_compose_yaml(compose)
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)
    return parsed


# ---------------------------------------------------------------------------
# Minimal config → valid compose with the core services.
# ---------------------------------------------------------------------------
def test_minimal_config_has_all_core_services() -> None:
    compose = generate_compose(_config())
    services = compose["services"]
    assert isinstance(services, dict)
    for name in CORE_SERVICES:
        assert name in services, f"missing core service {name}"
    # No GPU service and no monitoring overlay in a minimal config.
    assert GPU_SERVICE not in services
    for name in MONITORING_SERVICES:
        assert name not in services


def test_minimal_compose_top_level_shape() -> None:
    compose = generate_compose(_config())
    assert compose["name"] == "agentic-platform"
    assert "services" in compose
    # The canonical networks are declared: agentic-net + the two internal ones
    # (agentic-agents for the sandbox, agentic-docker for the socket-proxy lane).
    networks = compose["networks"]
    assert isinstance(networks, dict)
    assert set(networks) == {"agentic-net", "agentic-agents", "agentic-docker"}
    assert networks["agentic-agents"]["internal"] is True
    assert networks["agentic-docker"]["internal"] is True


def test_rendered_yaml_parses_and_round_trips() -> None:
    compose = generate_compose(_config(), monitoring=True)
    parsed = _render(compose)
    # Same service set survives a YAML round-trip.
    assert set(parsed["services"]) == set(compose["services"])


# ---------------------------------------------------------------------------
# Ollama mode none / cpu / gpu (ADR 0056).
# ---------------------------------------------------------------------------
def test_ollama_mode_none_omits_service_and_bootstrap() -> None:
    compose = generate_compose(_config(ollama_mode="none"))
    services = compose["services"]
    assert OLLAMA_SERVICE not in services
    assert OLLAMA_BOOTSTRAP_SERVICE not in services
    names = selected_services(_config(ollama_mode="none"), monitoring=False)
    assert OLLAMA_SERVICE not in names
    # No embedder wiring is injected when there is no in-stack Ollama.
    api_env = services["api-server"]["environment"]
    assert "API_SERVER_OLLAMA_URL" not in api_env


def test_ollama_mode_cpu_adds_service_without_reservation() -> None:
    compose = generate_compose(_config(ollama_mode="cpu"))
    services = compose["services"]
    assert OLLAMA_SERVICE in services
    assert OLLAMA_BOOTSTRAP_SERVICE in services
    ollama = services[OLLAMA_SERVICE]
    # CPU mode: NO GPU profile, NO device reservation.
    assert "profiles" not in ollama
    assert "reservations" not in ollama["deploy"]["resources"]


def test_ollama_mode_gpu_adds_nvidia_reservation() -> None:
    compose = generate_compose(_config(ollama_mode="gpu"))
    ollama = compose["services"][OLLAMA_SERVICE]
    assert "profiles" not in ollama  # inclusion is via selected_services, not a profile
    devices = ollama["deploy"]["resources"]["reservations"]["devices"]
    assert devices[0]["driver"] == "nvidia"
    # Compose schema wants a FLAT list of capability strings.
    assert devices[0]["capabilities"] == ["gpu"]


def test_bootstrap_pulls_configured_embedding_model() -> None:
    compose = generate_compose(
        _config(ollama_mode="cpu", embedding_model="snowflake-arctic-embed:110m")
    )
    boot = compose["services"][OLLAMA_BOOTSTRAP_SERVICE]
    assert boot["command"] == ["ollama pull snowflake-arctic-embed:110m"]
    assert boot["depends_on"] == {OLLAMA_SERVICE: {"condition": "service_healthy"}}
    assert boot["restart"] == "no"


def test_embedder_env_wired_into_app_services_when_ollama_on() -> None:
    compose = generate_compose(_config(ollama_mode="cpu", embedding_model="nomic-embed-text"))
    api_env = compose["services"]["api-server"]["environment"]
    assert api_env["API_SERVER_OLLAMA_URL"] == "http://ollama:11434"
    assert api_env["API_SERVER_EMBEDDING_MODEL"] == "nomic-embed-text"
    # The memory back-fill worker is wired too.
    worker_env = compose["services"]["workers"]["environment"]
    assert worker_env["WORKERS_MEMORY_EMBEDDER_BASE_URL"] == "http://ollama:11434"


def test_legacy_gpu_enabled_maps_to_gpu_mode() -> None:
    # Backward-compat: an old config with gpu_enabled=True (no ollama_mode)
    # behaves as ollama_mode='gpu'.
    cfg = _config(gpu_enabled=True)
    assert cfg.resources.ollama_mode == "gpu"
    ollama = generate_compose(cfg)["services"][OLLAMA_SERVICE]
    assert ollama["deploy"]["resources"]["reservations"]["devices"][0]["driver"] == "nvidia"


def test_default_config_has_no_ollama() -> None:
    # No ollama_mode + no gpu_enabled → mode 'none' (conservative default).
    cfg = _config()
    assert cfg.resources.ollama_mode == "none"
    assert OLLAMA_SERVICE not in generate_compose(cfg)["services"]
    assert GPU_SERVICE not in selected_services(cfg, monitoring=False)


# ---------------------------------------------------------------------------
# Provider toggles → wiring included / excluded.
# ---------------------------------------------------------------------------
def _app_env(compose: dict[str, object], service: str = "api-server") -> dict[str, object]:
    env = compose["services"][service]["environment"]
    assert isinstance(env, dict)
    return env


def test_provider_toggle_includes_wiring_when_enabled() -> None:
    providers = ProvidersConfig(
        claude_sdk=ClaudeSdkProvider(enabled=True, oauth_token="tok-throwaway"),
        azure_foundry=AzureFoundryProvider(
            enabled=True,
            apim_endpoint="https://apim.example.com",
            api_key="key-throwaway",
        ),
    )
    compose = generate_compose(_config(providers=providers))
    env = _app_env(compose)
    assert env["LLM_CLAUDE_SDK_ENABLED"] == "true"
    assert env["LLM_AZURE_FOUNDRY_ENABLED"] == "true"
    assert env["LLM_AZURE_FOUNDRY_ENDPOINT"] == "https://apim.example.com"
    # Disabled providers contribute NO wiring.
    assert "LLM_COPILOT_ENABLED" not in env
    assert "LLM_OLLAMA_ENABLED" not in env


def test_provider_toggle_excludes_wiring_when_disabled() -> None:
    # Only Ollama enabled → only the Ollama wiring is present.
    compose = generate_compose(_config())
    env = _app_env(compose)
    assert env["LLM_OLLAMA_ENABLED"] == "true"
    assert env["LLM_OLLAMA_ENDPOINT"] == "http://o:11434"
    for absent in ("LLM_CLAUDE_SDK_ENABLED", "LLM_COPILOT_ENABLED", "LLM_AZURE_FOUNDRY_ENABLED"):
        assert absent not in env


def test_enabled_providers_reports_only_enabled() -> None:
    providers = ProvidersConfig(copilot=CopilotProvider(enabled=True, oauth_token="x"))
    assert enabled_providers(_config(providers=providers)) == (LLMProviderKind.COPILOT,)


# ---------------------------------------------------------------------------
# Monitoring overlay.
# ---------------------------------------------------------------------------
def test_monitoring_flag_adds_overlay() -> None:
    compose = generate_compose(_config(), monitoring=True)
    for name in MONITORING_SERVICES:
        assert name in compose["services"]


def test_no_monitoring_by_default() -> None:
    compose = generate_compose(_config())
    for name in MONITORING_SERVICES:
        assert name not in compose["services"]


def test_monitoring_includes_alertmanager_and_cadvisor() -> None:
    # Parity with dev (docker-compose.monitoring.yml): production monitoring must
    # also ship Alertmanager (alert routing) + cAdvisor (per-container metrics).
    compose = generate_compose(_config(), monitoring=True)
    services = compose["services"]
    assert "alertmanager" in services
    assert "cadvisor" in services

    am = services["alertmanager"]
    assert am["image"].startswith("prom/alertmanager:")
    # Mounts the secret-free routing config (webhooks the platform notifier).
    assert any("alertmanager.yml" in v for v in am["volumes"])

    cad = services["cadvisor"]
    assert cad["image"].startswith("gcr.io/cadvisor/cadvisor:")
    # Privileged metrics collector: read-only host mounts, no cap_drop/apparmor.
    assert cad["privileged"] is True
    assert "cap_drop" not in cad
    assert all("apparmor=" not in o for o in cad["security_opt"])


# ---------------------------------------------------------------------------
# Ports / volumes parametrised from the wizard config.
# ---------------------------------------------------------------------------
def test_ports_are_parametrised() -> None:
    ports = PortsConfig(admin_panel=18080, api_server=18000)
    compose = generate_compose(_config(ports=ports))
    assert compose["services"]["admin-panel"]["ports"] == ["18080:3000"]
    assert compose["services"]["api-server"]["ports"] == ["18000:8000"]


def test_volumes_use_configured_data_root() -> None:
    compose = generate_compose(_config(data_root="/srv/agentic"))
    pg_vols = compose["services"]["postgres"]["volumes"]
    assert any(v.startswith("/srv/agentic/postgres:") for v in pg_vols)
    minio_vols = compose["services"]["minio"]["volumes"]
    assert any(v.startswith("/srv/agentic/minio:") for v in minio_vols)


def test_worker_replicas_parametrised() -> None:
    compose = generate_compose(_config(worker_replicas=5))
    assert compose["services"]["workers"]["deploy"]["replicas"] == 5


# ---------------------------------------------------------------------------
# Hardening defaults applied consistently.
# ---------------------------------------------------------------------------
def test_hardening_defaults_on_every_service() -> None:
    compose = generate_compose(_config(gpu_enabled=True), monitoring=True)
    # One-shot init services pull-and-exit, so they CANNOT be unless-stopped —
    # they still carry the rest of the hardening posture.
    one_shots = {"ollama-bootstrap"}
    # cAdvisor MUST run privileged with host mounts to read container stats, so
    # it is deliberately NOT cap-dropped and does NOT pin AppArmor (both would
    # deny the host access it needs). It still sets no-new-privileges + limits.
    privileged = {"cadvisor"}
    for name, svc in compose["services"].items():
        assert svc["restart"] == ("no" if name in one_shots else "unless-stopped"), name
        opts = svc["security_opt"]
        assert "no-new-privileges:true" in opts, name
        limits = svc["deploy"]["resources"]["limits"]
        assert "cpus" in limits and "memory" in limits, name
        if name in privileged:
            # Privileged metrics collector: no apparmor, no cap_drop on purpose.
            assert svc.get("privileged") is True, name
            continue
        # AppArmor MAC confinement is pinned on every other generated service.
        assert "apparmor=agentic-default" in opts, name
        # Vault keeps IPC_LOCK; everything else drops ALL caps.
        if name == "vault":
            assert svc["cap_add"] == ["IPC_LOCK"]
        else:
            assert svc["cap_drop"] == ["ALL"], name


def test_generated_services_rely_on_docker_default_seccomp() -> None:
    """TRUSTED first-party services do NOT pin a hand-rolled seccomp profile
    (ADR 0040, revised): they rely on Docker's proven DEFAULT seccomp. The
    custom default-deny allowlist SIGSEGV'd the Go services + broke postgres
    when force-applied, so the generator must never emit a ``seccomp=…`` pin.
    The strict default-deny profile is reserved for the UNTRUSTED runtimes the
    worker launches (docker/seccomp/agent-runtime.json)."""
    compose = generate_compose(_config(gpu_enabled=True), monitoring=True)
    offenders: list[str] = []
    for name, svc in compose["services"].items():
        opts = [str(x) for x in svc.get("security_opt", [])]
        if any(o.startswith("seccomp=") for o in opts):
            offenders.append(name)
    assert not offenders, "generated services pinning a custom seccomp profile: " + ", ".join(
        offenders
    )


def test_images_are_pinned_never_latest() -> None:
    compose = generate_compose(_config(gpu_enabled=True), monitoring=True)
    for name, svc in compose["services"].items():
        image = svc.get("image")
        if image is None:
            # egress-proxy builds from a local context (no image tag).
            assert "build" in svc, name
            continue
        assert ":" in image, f"{name} image not tagged: {image}"
        assert not image.endswith(":latest"), name


# ---------------------------------------------------------------------------
# No duplicate keys + prod secret guard.
# ---------------------------------------------------------------------------
def test_rendered_yaml_has_no_duplicate_keys() -> None:
    text = render_compose_yaml(generate_compose(_config(gpu_enabled=True), monitoring=True))

    class _DupGuardLoader(yaml.SafeLoader):
        pass

    def _no_dups(loader: yaml.Loader, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            assert key not in seen, f"duplicate key in generated YAML: {key!r}"
            seen.add(key)
        return loader.construct_mapping(node, deep)

    _DupGuardLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dups)
    # Loading with the guard raises (via assert) if any mapping has a dup key.
    yaml.load(text, Loader=_DupGuardLoader)


def test_production_compose_has_no_dev_secret_markers() -> None:
    text = render_compose_yaml(generate_compose(_config(environment=Environment.PRODUCTION)))
    lowered = text.lower()
    for marker in _DEV_SECRET_MARKERS:
        assert marker not in lowered, f"prod compose leaked dev marker {marker!r}"
    # The dedicated guard accepts it.
    assert_no_dev_secret_markers(text)


def test_production_secrets_are_env_references_only() -> None:
    compose = generate_compose(_config(environment=Environment.PRODUCTION))
    pg_env = compose["services"]["postgres"]["environment"]
    # Prod password is a bare ${VAR} reference with no dev fallback.
    assert pg_env["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD}"
    assert ":-" not in pg_env["POSTGRES_PASSWORD"]


def test_dev_environment_keeps_convenience_fallbacks() -> None:
    compose = generate_compose(_config(environment=Environment.DEVELOPMENT))
    pg_env = compose["services"]["postgres"]["environment"]
    # Dev keeps the ${VAR:-default} fallback for non-secret knobs.
    assert pg_env["POSTGRES_USER"] == "${POSTGRES_USER:-postgres}"


def test_app_services_reference_release_image() -> None:
    compose = generate_compose(_config())
    api = compose["services"]["api-server"]
    assert api["image"].startswith(APP_IMAGE_REGISTRY)


# ---------------------------------------------------------------------------
# Optional end-to-end validation with the real `docker compose` CLI. Skipped
# when the CLI is absent (CI without Docker). Real `up` stays a HUMAN test.
# ---------------------------------------------------------------------------
def _docker_compose_available() -> bool:
    return shutil.which("docker") is not None


@pytest.fixture()
def written_compose(tmp_path: Path) -> Iterator[str]:
    text = render_compose_yaml(generate_compose(_config(gpu_enabled=True), monitoring=True))
    path = tmp_path / "docker-compose.yml"
    path.write_text(text, encoding="utf-8")
    yield str(path)


@pytest.mark.skipif(not _docker_compose_available(), reason="docker CLI not available")
def test_docker_compose_config_accepts_generated_file(written_compose: str) -> None:
    result = subprocess.run(  # - fixed argv, no shell
        ["docker", "compose", "-f", written_compose, "config", "-q"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # Unset ${ENV} placeholders only produce warnings on stderr; exit 0 means
    # the schema + structure are valid.
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# task_prod01_06 — workers funcional: command celery explícito, lane privileged
# separada, binds (data_root + seccomp), envs de backup.
# ---------------------------------------------------------------------------
def _queues_of(service: dict) -> set[str]:
    """Extract the ``--queues=a,b,c`` set from a service's celery command
    (accepts the command as a string or an argv list)."""
    command = service.get("command")
    text = command if isinstance(command, str) else " ".join(command or [])
    import re

    m = re.search(r"--queues[=\s]+([A-Za-z0-9_,]+)", text)
    return set(m.group(1).split(",")) if m else set()


def test_workers_has_explicit_celery_command_for_generic_queues() -> None:
    workers = generate_compose(_config())["services"]["workers"]
    text = (
        workers["command"] if isinstance(workers["command"], str) else " ".join(workers["command"])
    )
    assert (
        "celery" in text and "worker" in text
    ), f"workers command is not a celery worker: {text!r}"
    queues = _queues_of(workers)
    assert queues, "workers has no --queues"
    assert "privileged" not in queues, "the generic pool must NOT drain the privileged queue"


def test_workers_privileged_lane_drains_only_privileged_as_singleton() -> None:
    services = generate_compose(_config())["services"]
    assert "workers-privileged" in services, "no separate workers-privileged service"
    priv = services["workers-privileged"]
    assert _queues_of(priv) == {
        "privileged"
    }, "workers-privileged must drain exactly the privileged queue"
    # Singleton: periodic privileged jobs (backup/rotation) must not double-run.
    assert (
        priv.get("deploy", {}).get("replicas") == 1
    ), "workers-privileged must be a singleton (replicas=1)"


def test_workers_lanes_cover_every_queue_with_no_orphan() -> None:
    from workers.celery_app import QUEUE_NAMES

    services = generate_compose(_config())["services"]
    covered = _queues_of(services["workers"]) | _queues_of(services["workers-privileged"])
    assert covered == set(QUEUE_NAMES), (
        f"queues drained {covered} != topology {set(QUEUE_NAMES)} — an orphan queue would "
        "be enqueued forever (runbook 06-capacity-management)"
    )


def test_workers_lanes_bind_data_root_and_seccomp_profiles() -> None:
    services = generate_compose(_config(data_root="/data/agent-platform"))["services"]
    for name in ("workers", "workers-privileged"):
        vols = " ".join(services[name].get("volumes", []))
        assert (
            "/data/agent-platform" in vols
        ), f"{name} does not bind the data_root (repos/worktrees)"
        assert (
            "seccomp" in vols
        ), f"{name} does not bind the seccomp profiles (for launched runtimes)"


@pytest.mark.parametrize(
    "service_name",
    ["orchestrator", "workers", "workers-privileged", "notification-dispatcher"],
)
def test_background_services_have_a_healthcheck(service_name: str) -> None:
    """task_prod01_07 (deploy-3 pata 3): the long-lived background services need
    a healthcheck so depends_on conditions + restart policy actually mean
    'ready', not just 'process started'."""
    svc = generate_compose(_config())["services"][service_name]
    hc = svc.get("healthcheck") or {}
    assert hc.get("test"), f"{service_name} has no healthcheck"


def test_background_healthcheck_uses_the_right_probe() -> None:
    services = generate_compose(_config())["services"]

    def _probe(name: str) -> str:
        test = services[name]["healthcheck"]["test"]
        return test if isinstance(test, str) else " ".join(test)

    # Celery workers answer `inspect ping`; the orchestrator is a FastAPI app.
    assert "celery" in _probe("workers") and "ping" in _probe("workers")
    assert "celery" in _probe("workers-privileged")
    assert "celery" in _probe("notification-dispatcher") and "ping" in _probe(
        "notification-dispatcher"
    )
    assert "/healthz" in _probe("orchestrator")


def test_workers_emit_backup_env_prefixed() -> None:
    env = generate_compose(_config())["services"]["workers"]["environment"]
    for key in (
        "WORKERS_BACKUP_DATABASE_URL",
        "WORKERS_BACKUP_ENCRYPTION_ENABLED",
        "WORKERS_BACKUP_ENCRYPTION_VAULT_KEY",
    ):
        assert key in env, f"workers is missing backup env {key}"


# ---------------------------------------------------------------------------
# task_prod01_09 — docker-socket-proxy (ACL minima) + red agentic-agents en los
# workers. El sandbox NUNCA recibe el socket Docker directo (Principio 2).
# ---------------------------------------------------------------------------
def test_docker_socket_proxy_has_minimal_acl_and_mounts_the_socket() -> None:
    proxy = generate_compose(_config())["services"]["docker-socket-proxy"]
    env = proxy["environment"]
    # The worker needs to create/list containers, reference images, attach
    # networks — and POST to create them. Everything else is denied.
    for on in ("CONTAINERS", "IMAGES", "NETWORKS", "POST"):
        assert str(env.get(on)) == "1", f"socket-proxy ACL should allow {on}"
    for off in ("EXEC", "VOLUMES", "SWARM"):
        assert str(env.get(off)) == "0", f"socket-proxy ACL must deny {off}"
    vols = " ".join(proxy.get("volumes", []))
    assert "/var/run/docker.sock" in vols, "socket-proxy must mount the docker socket"


def test_socket_proxy_lives_on_a_dedicated_internal_network_only() -> None:
    compose = generate_compose(_config())
    proxy = compose["services"]["docker-socket-proxy"]
    nets = proxy["networks"]
    # Dedicated + internal: ONLY the workers reach the Docker API, never the
    # untrusted agent runtimes (which sit on agentic-agents) nor the internet.
    assert nets == ["agentic-docker"], f"socket-proxy must be on the dedicated net only: {nets}"
    netblock = compose["networks"]["agentic-docker"]
    assert netblock.get("internal") is True, "agentic-docker must be internal"
    assert compose["networks"]["agentic-net"], "agentic-net still declared"


@pytest.mark.parametrize("service_name", ["workers", "workers-privileged"])
def test_workers_pin_seccomp_and_apparmor_profiles(service_name: str) -> None:
    """task_prod01_10 / sandbox-2: the workers must pin the STRICT runtime
    profiles onto the sandboxes they launch (today the WORKERS_ defaults are ""
    → the runtimes fall back to Docker's default profiles)."""
    svc = generate_compose(_config())["services"][service_name]
    env = svc["environment"]
    assert env.get("WORKERS_SECCOMP_PROFILE_PATH", "").endswith(
        "agent-runtime.json"
    ), f"{service_name} must pin the seccomp profile path"
    assert (
        env.get("WORKERS_APPARMOR_PROFILE") == "agent-runtime"
    ), f"{service_name} must pin the apparmor profile name"
    # The seccomp profile path must actually be mounted (task_06 bind).
    assert "seccomp" in " ".join(svc.get("volumes", [])), f"{service_name} seccomp not mounted"


@pytest.mark.parametrize("service_name", ["workers", "workers-privileged"])
def test_workers_reach_docker_via_proxy_and_join_agents_network(service_name: str) -> None:
    svc = generate_compose(_config())["services"][service_name]
    env = svc["environment"]
    assert (
        env.get("DOCKER_HOST") == "tcp://docker-socket-proxy:2375"
    ), f"{service_name} must talk to the Docker API through the proxy, not the raw socket"
    assert "WORKERS_EGRESS_PROXY_URL" in env, f"{service_name} must get WORKERS_EGRESS_PROXY_URL"
    nets = svc["networks"]
    assert "agentic-agents" in nets, f"{service_name} must join agentic-agents (launch runtimes)"
    assert "agentic-docker" in nets, f"{service_name} must join the socket-proxy network"
