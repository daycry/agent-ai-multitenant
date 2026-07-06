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
    STT_SERVICE,
    TTS_SERVICE,
    VOICE_SERVICES,
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
    voice_mode: str | None = None,
    embedding_model: str = "nomic-embed-text",
    providers: ProvidersConfig | None = None,
    data_root: str = "/data/agent-platform",
    worker_replicas: int = 2,
    ports: PortsConfig | None = None,
    system: SystemConfig | None = None,
) -> InstallerConfig:
    if providers is None:
        providers = ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434"))
    return InstallerConfig(
        system=system or SystemConfig(domain="agentic.example.com", environment=environment),
        resources=ResourceConfig(
            worker_replicas=worker_replicas,
            worker_memory_gib=4,
            gpu_enabled=gpu_enabled,
            ollama_mode=ollama_mode,
            voice_mode=voice_mode,
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
# Voice mode (stt / tts) — modo voz del Asistente + córtex (ADR 0073).
#
# El instalador de producción NO generaba stt/tts, así que el modo voz no
# arrancaba en instalaciones reales. La definición de referencia vive en
# docker/docker-compose.yml; estas pruebas fijan que el compose generado los
# incluye con su imagen + healthcheck en python (NO wget: esas imágenes no lo
# traen) y que el api-server queda cableado a stt:8000 / tts:8880.
# ---------------------------------------------------------------------------
def test_voice_mode_cpu_adds_stt_and_tts_services() -> None:
    compose = generate_compose(_config(voice_mode="cpu"))
    services = compose["services"]
    assert STT_SERVICE in services
    assert TTS_SERVICE in services
    # The reference (docker/docker-compose.yml) images.
    assert services[STT_SERVICE]["image"].startswith("fedirz/faster-whisper-server:")
    assert services[TTS_SERVICE]["image"].startswith("ghcr.io/remsky/kokoro-fastapi-cpu:")


def test_voice_mode_none_omits_stt_and_tts() -> None:
    compose = generate_compose(_config(voice_mode="none"))
    services = compose["services"]
    assert STT_SERVICE not in services
    assert TTS_SERVICE not in services
    names = selected_services(_config(voice_mode="none"), monitoring=False)
    for name in VOICE_SERVICES:
        assert name not in names
    # No STT/TTS wiring injected into the api-server when voice is off.
    api_env = services["api-server"]["environment"]
    assert "API_SERVER_ASSISTANT_STT_URL" not in api_env
    assert "API_SERVER_ASSISTANT_TTS_URL" not in api_env


def test_voice_enabled_by_default() -> None:
    # The default config (no voice_mode given) ships the voice stack so the
    # Assistant/córtex voice mode works out of the box on a real install — the
    # bug this fixes was that prod NEVER generated stt/tts.
    cfg = _config()
    assert cfg.resources.voice_mode == "cpu"
    services = generate_compose(cfg)["services"]
    assert STT_SERVICE in services
    assert TTS_SERVICE in services


def test_voice_wiring_points_api_server_at_stt_and_tts() -> None:
    env = generate_compose(_config(voice_mode="cpu"))["services"]["api-server"]["environment"]
    assert env["API_SERVER_ASSISTANT_STT_URL"] == "http://stt:8000"
    assert env["API_SERVER_ASSISTANT_TTS_URL"] == "http://tts:8880"


def test_stt_service_matches_reference_definition() -> None:
    stt = generate_compose(_config(voice_mode="cpu"))["services"][STT_SERVICE]
    env = stt["environment"]
    # Whisper model env from docker/docker-compose.yml (ES+EN CPU-friendly).
    assert env["WHISPER__MODEL"] == "Systran/faster-whisper-small"
    assert env["WHISPER__INFERENCE_DEVICE"] == "cpu"
    # Model cache volume under the configured data root.
    assert any("/.cache/huggingface" in v for v in stt["volumes"])
    # Internal-only: no host ports, on agentic-net.
    assert "ports" not in stt
    assert stt["networks"] == ["agentic-net"]


def test_voice_healthchecks_use_python_not_wget() -> None:
    # The stt/tts images ship NEITHER wget NOR curl — a wget-based probe would
    # mark them permanently unhealthy. They must probe with python (urllib).
    services = generate_compose(_config(voice_mode="cpu"))["services"]
    for name, port in ((STT_SERVICE, 8000), (TTS_SERVICE, 8880)):
        flat = " ".join(services[name]["healthcheck"]["test"])
        assert "wget" not in flat and "curl" not in flat, f"{name} healthcheck uses a missing tool"
        assert "python" in flat, f"{name} healthcheck must use python"
        assert f":{port}/health" in flat, f"{name} healthcheck must hit :{port}/health"


def test_stt_uses_named_model_cache_volume() -> None:
    # The Whisper model (~hundreds of MB) must persist across restarts so it is
    # not re-downloaded on every boot — mounted from a named volume declared at
    # the compose top level.
    compose = generate_compose(_config(voice_mode="cpu"))
    stt = compose["services"][STT_SERVICE]
    vol_name = stt["volumes"][0].split(":", 1)[0]
    assert vol_name in compose["volumes"], "the whisper model cache volume must be declared"


def test_voice_services_are_hardened_like_the_rest() -> None:
    services = generate_compose(_config(voice_mode="cpu"))["services"]
    for name in (STT_SERVICE, TTS_SERVICE):
        svc = services[name]
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert "apparmor=agentic-default" in svc["security_opt"], name
        assert "limits" in svc["deploy"]["resources"], name
        assert svc["restart"] == "unless-stopped", name


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
def test_proxy_is_the_only_service_publishing_host_ports() -> None:
    # ADR 0061 / deploy-7: after Fase E the single TLS reverse proxy (caddy) is
    # the ONLY service mapping host ports; everything else is internal-only.
    compose = generate_compose(_config())
    publishers = {name for name, svc in compose["services"].items() if "ports" in svc}
    assert publishers == {"caddy"}
    assert compose["services"]["caddy"]["ports"] == ["80:80", "443:443"]


def test_api_server_and_admin_panel_publish_no_host_ports() -> None:
    # Both used to publish on 0.0.0.0 (HTTP plano); now they live only on the
    # internal network behind the proxy. PortsConfig stays in the model but no
    # longer maps to the host in the generated production compose.
    ports = PortsConfig(admin_panel=18080, api_server=18000)
    compose = generate_compose(_config(ports=ports))
    assert "ports" not in compose["services"]["api-server"]
    assert "ports" not in compose["services"]["admin-panel"]


def test_proxy_sso_redirect_base_url_carries_api_prefix() -> None:
    # The IdP redirects the browser to {base}/auth/sso/oidc/callback; the base
    # must carry the proxy's /api prefix so handle_path strips it to the backend.
    compose = generate_compose(_config())
    env = compose["services"]["api-server"]["environment"]
    assert env["API_SERVER_SSO_REDIRECT_BASE_URL"] == "https://agentic.example.com/api"


def test_caddy_proxy_in_core_services_and_hardened() -> None:
    compose = generate_compose(_config())
    assert "caddy" in CORE_SERVICES
    caddy = compose["services"]["caddy"]
    assert caddy["image"].startswith("caddy:")
    assert caddy["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in caddy["security_opt"]
    assert caddy["restart"] == "unless-stopped"
    assert "limits" in caddy["deploy"]["resources"]


def test_caddy_proxy_adds_net_bind_service_cap() -> None:
    # cap_drop:[ALL] removes the ability to bind 80/443; NET_BIND_SERVICE is the
    # single capability added back (same pattern as Vault's IPC_LOCK).
    compose = generate_compose(_config())
    assert compose["services"]["caddy"]["cap_add"] == ["NET_BIND_SERVICE"]


def test_caddy_proxy_on_agentic_net_only() -> None:
    compose = generate_compose(_config())
    assert compose["services"]["caddy"]["networks"] == ["agentic-net"]


def test_caddy_proxy_depends_on_api_server_and_admin_panel() -> None:
    compose = generate_compose(_config())
    deps = compose["services"]["caddy"]["depends_on"]
    assert deps["api-server"]["condition"] == "service_healthy"
    assert deps["admin-panel"]["condition"] == "service_healthy"


def test_caddy_proxy_mounts_the_generated_caddyfile_readonly() -> None:
    compose = generate_compose(_config())
    volumes = compose["services"]["caddy"]["volumes"]
    assert "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro" in volumes
    # The internal CA / ACME material persists across restarts.
    assert any(v.endswith("/caddy/data:/data") for v in volumes)


def test_tls_provided_mode_mounts_the_cert_dir_readonly() -> None:
    sys_cfg = SystemConfig(
        domain="agentic.example.com",
        tls_mode="provided",
        tls_cert_path="/etc/ssl/server.crt",
        tls_key_path="/etc/ssl/server.key",
    )
    compose = generate_compose(_config(system=sys_cfg))
    volumes = compose["services"]["caddy"]["volumes"]
    assert any(v.endswith("/caddy/tls:/etc/caddy/tls:ro") for v in volumes)


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
    one_shots = {"ollama-bootstrap", "migrations"}
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
        # Every non-privileged service drops ALL caps. Official infra images that
        # self-init as root (chown their data dir + drop to a service user via
        # gosu/su-exec) add the self-init caps back on top of the blanket drop;
        # Vault additionally needs IPC_LOCK (mlock) + SETFCAP (setcaps its binary).
        assert svc["cap_drop"] == ["ALL"], name
        infra_caps = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
        if name == "vault":
            assert set(svc["cap_add"]) >= infra_caps | {"IPC_LOCK", "SETFCAP"}, name
        elif name in {"postgres", "redis", "clamav", "egress-proxy"}:
            assert set(svc["cap_add"]) >= infra_caps, name


def test_official_infra_images_keep_self_init_caps() -> None:
    """postgres/redis/clamav/egress-proxy run official images that self-init as
    root (chown their data dir + drop to a service user via gosu/su-exec). Under
    cap_drop:[ALL] they crash-loop ("chmod/chown: Operation not permitted",
    "Unable to change to group") unless the self-init caps are added back. This
    guards the prod-01 hardening regression where the blanket cap-drop was too
    broad for stateful official images."""
    compose = generate_compose(_config(), monitoring=False)
    infra_caps = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
    for name in ("postgres", "redis", "clamav"):
        svc = compose["services"][name]
        assert svc["cap_drop"] == ["ALL"], name
        assert set(svc["cap_add"]) >= infra_caps, name
    if "egress-proxy" in compose["services"]:  # tinyproxy setgid/setuid on start
        assert set(compose["services"]["egress-proxy"]["cap_add"]) >= infra_caps
    # Vault: self-init caps + IPC_LOCK (mlock) + SETFCAP (setcaps its own binary).
    vault = compose["services"]["vault"]
    assert set(vault["cap_add"]) >= infra_caps | {"IPC_LOCK", "SETFCAP"}


def test_python_app_healthchecks_do_not_rely_on_wget() -> None:
    """api-server + orchestrator run on python:3.12-slim, which ships NO
    wget/curl. Their HTTP healthcheck must use python's stdlib — a wget-based
    check marks them permanently unhealthy, so depends_on:service_healthy is
    never satisfied and the WHOLE stack fails to come up (prod-01: verified live
    — the containers only became healthy once the check used python). The Celery
    lanes (workers, notification-dispatcher) use `celery inspect ping`, which IS
    in their image, so they are unaffected."""
    compose = generate_compose(_config(), monitoring=False)
    for name in ("api-server", "orchestrator"):
        flat = " ".join(compose["services"][name]["healthcheck"]["test"])
        assert "wget" not in flat and "curl" not in flat, f"{name} healthcheck uses a missing tool"
        assert "python" in flat, f"{name} healthcheck must use python (no wget in the image)"


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


def _celery_app_target(service: dict, *, from_healthcheck: bool = False) -> str | None:
    """Extract the ``-A <target>`` of a service's celery command (or its
    healthcheck's ``celery inspect`` probe). Accepts string or argv list."""
    import re

    if from_healthcheck:
        raw = service.get("healthcheck", {}).get("test")
        text = raw if isinstance(raw, str) else " ".join(raw or [])
    else:
        cmd = service.get("command")
        text = cmd if isinstance(cmd, str) else " ".join(cmd or [])
    m = re.search(r"-A\s+([A-Za-z0-9_.]+)", text)
    return m.group(1) if m else None


def test_workers_celery_app_target_is_the_importable_module() -> None:
    """task_prod01: ``celery -A workers`` does NOT resolve — there is no
    ``workers/celery.py`` nor a top-level app attribute, so Celery exits with a
    usage error and the worker (and its ``inspect ping`` healthcheck) never
    starts. The app lives in ``workers.celery_app``; the command AND the
    healthcheck must target that module, in BOTH lanes."""
    services = generate_compose(_config())["services"]
    for name in ("workers", "workers-privileged"):
        assert _celery_app_target(services[name]) == "workers.celery_app", (
            f"{name} command must target -A workers.celery_app (bare 'workers' "
            "does not resolve and the worker never boots)"
        )
        assert (
            _celery_app_target(services[name], from_healthcheck=True) == "workers.celery_app"
        ), f"{name} healthcheck 'celery inspect ping' must target -A workers.celery_app"


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


def test_worker_lanes_bind_data_root_same_path_not_named_volume() -> None:
    """DooD invariant (ADR 0063 / sesión 2026-06-18): the worker launches the
    agent-runtime / review-runtime through the docker-socket-proxy, so the
    daemon resolves the bind ``source`` against ITS OWN filesystem. The
    worktree path the worker passes only resolves if ``data_root`` is bound
    with the SAME path inside and out. A NAMED volume mounted at ``data_root``
    would make the daemon bind a nonexistent host path → the launched runtime
    sees an EMPTY ``/workspace`` and serves nothing. Lock the same-path bind."""
    import re

    data_root = "/data/agent-platform"
    services = generate_compose(_config(data_root=data_root))["services"]
    for name in ("workers", "workers-privileged"):
        vols = services[name].get("volumes", [])
        assert f"{data_root}:{data_root}" in vols, (
            f"{name} must bind data_root SAME-PATH ({data_root}:{data_root}) for DooD "
            f"worktree resolution; got {vols}"
        )
        named = [v for v in vols if re.match(rf"^[A-Za-z0-9_]+:{re.escape(data_root)}(:|$)", v)]
        assert not named, (
            f"{name} mounts data_root via a NAMED volume {named} — the daemon would bind "
            "an empty host dir and the launched runtime would see an EMPTY /workspace"
        )


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


# ---------------------------------------------------------------------------
# task_prod01_12 — one-shot `migrations` service + apps wait for it.
# ---------------------------------------------------------------------------
def test_migrations_is_a_oneshot_alembic_upgrade() -> None:
    svc = generate_compose(_config())["services"]["migrations"]
    cmd = svc["command"] if isinstance(svc["command"], str) else " ".join(svc["command"])
    assert "alembic" in cmd and "upgrade" in cmd and "head" in cmd
    assert svc["restart"] == "no", "migrations is a one-shot, it must not restart"
    assert "api-server" in svc["image"], "runs from the api-server image (it ships the migrations)"
    # Runs as the migrations role (BYPASSRLS) and only needs postgres up.
    assert svc["environment"]["DATABASE_URL"] == "${ADMIN_DATABASE_URL}"
    assert svc["depends_on"]["postgres"]["condition"] == "service_healthy"


@pytest.mark.parametrize(
    "service_name",
    ["api-server", "orchestrator", "workers", "workers-privileged", "notification-dispatcher"],
)
def test_app_services_wait_for_migrations_to_complete(service_name: str) -> None:
    svc = generate_compose(_config())["services"][service_name]
    dep = svc.get("depends_on", {}).get("migrations")
    assert dep == {
        "condition": "service_completed_successfully"
    }, f"{service_name} must wait for migrations to finish before starting"


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


# --- prod-01 A9/A10 (auditoría 2026-07-06): el compose GENERADO por el
# instalador divergía del stack real (manuals.yml). Estos tests fijan la
# reconciliación.
def test_workers_events_redis_url_matches_consumers() -> None:
    """A10: el worker publica los streams exec:{id} en WORKERS_EVENTS_REDIS_URL,
    pero el WS del api-server (y el orchestrator) los LEEN en la DB 0 del Redis.
    Con /3 (sin consumidor) el streaming en vivo queda roto — manuals.yml ya lo
    corrigió a /0 con un comentario explícito; el generador debía seguirlo."""
    services = generate_compose(_config())["services"]
    workers_events = services["workers"]["environment"]["WORKERS_EVENTS_REDIS_URL"]
    api_redis = services["api-server"]["environment"]["API_SERVER_REDIS_URL"]
    # Ambos deben apuntar a la MISMA base de datos Redis (el stream de eventos).
    assert workers_events.rsplit("/", 1)[-1] == api_redis.rsplit("/", 1)[-1] == "0"


def test_cortex_beat_service_is_present_and_schedules() -> None:
    """A9: sin un servicio Celery beat, en una instalación por el instalador
    NADA se agenda (backups, rotación, mantenimiento, sync de precios, córtex).
    Solo existía en manuals.yml."""
    services = generate_compose(_config())["services"]
    assert "cortex-beat" in services, "falta el servicio Celery beat en el compose generado"
    beat = services["cortex-beat"]
    cmd = beat["command"]
    joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "beat" in joined, "el servicio cortex-beat debe lanzar `celery ... beat`"
    # Comparte el broker/DB de los workers (agenda las mismas colas).
    assert beat["environment"]["WORKERS_BROKER_URL"].startswith("redis://")


def test_privileged_lane_can_run_backups() -> None:
    """A9: la lane privileged drena la cola de backups, pero corría sin
    WORKERS_RUN_AS_ROOT ni el mount de /var/lib/docker/volumes → el volume-tar
    daba EACCES leyendo los _data a 0700 (redis uid 999, vault uid 100)."""
    svc = generate_compose(_config())["services"]["workers-privileged"]
    env = svc["environment"]
    assert (
        env.get("WORKERS_RUN_AS_ROOT") == "1"
    ), "la lane de backups necesita root para leer los volume _data a 0700"
    assert "WORKERS_BACKUP_VOLUMES" in env, "faltan los volúmenes a taréar (WORKERS_BACKUP_VOLUMES)"
    vols = " ".join(svc.get("volumes", []))
    assert (
        "/var/lib/docker/volumes" in vols
    ), "falta el mount de los volúmenes Docker para el backup"
