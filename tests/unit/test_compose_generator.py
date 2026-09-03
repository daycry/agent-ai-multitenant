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

import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from installer_backend.compose_generator import (
    _DEV_SECRET_MARKERS,
    APP_IMAGE_REGISTRY,
    BOOTSTRAP_ENTRYPOINT,
    BOOTSTRAP_SERVICE,
    CORE_SERVICES,
    GPU_SERVICE,
    MONITORING_SERVICES,
    OLLAMA_BOOTSTRAP_SERVICE,
    OLLAMA_SERVICE,
    STT_SERVICE,
    TTS_SERVICE,
    VOICE_SERVICES,
    WHISPER_MODELS_VOLUME,
    _env_ref,
    app_image,
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
from installer_backend.config_generators import (
    build_env_vars,
    generate_secrets,
    render_env_file,
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


def test_event_bus_redis_db_is_consistent_across_services() -> None:
    """AUD16 menor C/H10: el dispatcher escribía su DLQ (dlq:notifications) en
    la DB 3 de Redis mientras el sampler de métricas (workers, DB 0) y el resto
    del bus de eventos miran la DB 0 — en prod agentic_dlq_depth habría sido
    SIEMPRE 0 y la alerta NotificationsDLQNotEmpty no podría disparar jamás.
    Productor y consumidores del bus/DLQ deben compartir la MISMA DB."""
    compose = generate_compose(_config())
    services = compose["services"]
    assert isinstance(services, dict)

    def _env(service: str, key: str) -> str:
        env = services[service]["environment"]  # type: ignore[index]
        assert isinstance(env, dict)
        return str(env[key])

    workers_events = _env("workers", "WORKERS_EVENTS_REDIS_URL")
    notify_events = _env("notification-dispatcher", "NOTIFY_EVENTS_REDIS_URL")
    assert notify_events == workers_events, (
        f"DLQ writer ({notify_events}) y reader ({workers_events}) en DBs distintas"
    )


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
    # ADR 0155: los workers resuelven el modelo activo con el MISMO setting que
    # la api-server. La api-server sella el modelo en la KB y el worker embebe
    # con el suyo: si los dos procesos leyeran valores distintos, la guarda de
    # la ingesta rechazaría todos los documentos por un fallo de despliegue.
    assert worker_env["API_SERVER_EMBEDDING_MODEL"] == api_env["API_SERVER_EMBEDDING_MODEL"]


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
    # prod-12 cadv_01 (sandbox-8, decisión 5a): cAdvisor dejó de ser privileged
    # — los stats salen de los bind-mounts read-only, así que lleva el MISMO
    # hardening que el resto (cap_drop ALL + apparmor + no-new-privileges).
    assert "privileged" not in cad
    assert "devices" not in cad  # /dev/kmsg (decodificar OOM-kills) retirado
    assert cad["cap_drop"] == ["ALL"]
    assert any("apparmor=" in o for o in cad["security_opt"])
    assert all(v.endswith(":ro") for v in cad["volumes"])


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
    # `textfile-init` es el tercero: hace `chmod 1777` del drop-dir y sale.
    # `bootstrap` es el cuarto: init de Vault + siembra + revelado, y sale.
    one_shots = {"ollama-bootstrap", "migrations", "textfile-init", BOOTSTRAP_SERVICE}
    # prod-12 cadv_01: ya no queda ningún servicio privileged en el compose
    # generado (cAdvisor pasó al hardening estándar).
    privileged: set[str] = set()
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
        #
        # Con UNA excepción, y por una razón que no se puede resolver de otra
        # manera (2026-08-28, e2e run 33177824929): `agentic-default` deniega el
        # socket de Docker a todo el mundo —Principio 2, «a socket leak == host
        # takeover»— y el `docker-socket-proxy` es el único servicio que existe
        # para sostenerlo. Con el perfil compartido puesto, HAProxy arrancaba y
        # sus peticiones morían con `503 … SC--`.
        #
        # Abrir el socket en el perfil compartido lo habría arreglado, y se lo
        # habría dado también a los workers, que ejecutan código no confiable.
        # Por eso lleva perfil propio: `agentic-socket-proxy`, idéntico al
        # compartido salvo esa línea.
        esperado = (
            "apparmor=agentic-socket-proxy"
            if name == "docker-socket-proxy"
            else "apparmor=agentic-default"
        )
        assert esperado in opts, name
        if name != "docker-socket-proxy":
            assert "apparmor=agentic-socket-proxy" not in opts, (
                f"{name} lleva el perfil del socket-proxy, que PERMITE el socket "
                "de Docker. Sólo el propio proxy puede llevarlo."
            )
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


def test_notification_dispatcher_healthcheck_loads_a_real_celery_app() -> None:
    """Bug cazado en vivo (2026-07-10, al desplegar el dispatcher en dev):
    ``celery -A notification_dispatcher inspect ping`` NO carga la app —
    «Module 'notification_dispatcher' has no attribute 'celery'» — así que el
    servicio quedaba permanentemente unhealthy pese al worker `ready`. El -A
    debe apuntar al módulo real (``notification_dispatcher.celery_app:app``,
    el mismo target del CMD del Dockerfile) y hacer ping a SU nodo (-d
    celery@$$HOSTNAME), no a cualquier worker del broker compartido."""
    compose = generate_compose(_config(), monitoring=False)
    flat = " ".join(compose["services"]["notification-dispatcher"]["healthcheck"]["test"])
    assert "notification_dispatcher.celery_app:app" in flat
    assert "HOSTNAME" in flat


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
            # Un servicio sin `image:` se construye desde un contexto local.
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
    """En prod no hay literal, y tampoco hay default de NINGUNA clase.

    Ni el explícito (`${VAR:-…}`, que arranca con la contraseña publicada en este
    repo) ni el implícito: `${VAR}` a secas interpola a cadena vacía y sigue
    adelante. La forma correcta es `${VAR:?…}`, que aborta el proyecto entero
    antes de crear un contenedor.
    """
    compose = generate_compose(_config(environment=Environment.PRODUCTION))
    pg_env = compose["services"]["postgres"]["environment"]
    assert pg_env["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    assert ":-" not in pg_env["POSTGRES_PASSWORD"]
    assert pg_env["POSTGRES_PASSWORD"] != "${POSTGRES_PASSWORD}"


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
    """El compose generado **y su `.env`**, en el mismo directorio.

    Los dos juntos, porque es como se instalan: el `.env` cuelga del directorio
    del compose y `docker compose` lo carga solo. Escribir sólo el compose dejó
    de valer el 2026-08-27, cuando las credenciales pasaron a `${VAR:?…}`: sin el
    `.env`, `docker compose config` aborta — que es exactamente la conducta
    buscada, así que este test dejaría de comprobar la estructura del fichero
    para comprobar el fail-closed que ya comprueba otro.

    Lo que se gana a cambio es mejor: ahora esto valida el PAR, que es la unidad
    que se instala. Un `${VAR:?…}` sobre una variable que el `.env` no escribe
    —el modo de fallo real, y el que tumba también `ps`, `logs` y `down`— sale
    aquí con el nombre de la variable en el mensaje.
    """
    cfg = _config(gpu_enabled=True)
    (tmp_path / "docker-compose.yml").write_text(
        render_compose_yaml(generate_compose(cfg, monitoring=True)), encoding="utf-8"
    )
    (tmp_path / ".env").write_text(
        render_env_file(build_env_vars(cfg, generate_secrets(), monitoring=True)),
        encoding="utf-8",
    )
    yield str(tmp_path / "docker-compose.yml")


@pytest.mark.skipif(not _docker_compose_available(), reason="docker CLI not available")
def test_docker_compose_config_accepts_generated_file(written_compose: str) -> None:
    result = subprocess.run(  # - fixed argv, no shell
        ["docker", "compose", "-f", written_compose, "config", "-q"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # exit 0 = el esquema, la estructura Y la interpolación contra el `.env`
    # generado son válidos.
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
    assert "celery" in text and "worker" in text, (
        f"workers command is not a celery worker: {text!r}"
    )
    queues = _queues_of(workers)
    assert queues, "workers has no --queues"
    assert "privileged" not in queues, "the generic pool must NOT drain the privileged queue"


def test_workers_privileged_lane_drains_only_privileged_as_singleton() -> None:
    services = generate_compose(_config())["services"]
    assert "workers-privileged" in services, "no separate workers-privileged service"
    priv = services["workers-privileged"]
    assert _queues_of(priv) == {"privileged"}, (
        "workers-privileged must drain exactly the privileged queue"
    )
    # Singleton: periodic privileged jobs (backup/rotation) must not double-run.
    assert priv.get("deploy", {}).get("replicas") == 1, (
        "workers-privileged must be a singleton (replicas=1)"
    )


def test_workers_lanes_cover_every_queue_with_no_orphan() -> None:
    from workers.celery_app import QUEUE_NAMES

    services = generate_compose(_config())["services"]
    # Se recorren TODOS los servicios en vez de nombrar dos: el 2026-08-19 esta
    # aserción se puso roja al entrar la lane `marketplace` (prod-13
    # task_prod13_01) porque la unión estaba escrita a mano con
    # `workers` | `workers-privileged`, o sea que una lane NUEVA la rompía aunque
    # tuviese su consumidor. Lo que la guarda quiere decir es «ninguna cola sin
    # quien la drene», y eso se comprueba sobre el compose entero.
    lanes = {name: _queues_of(svc) for name, svc in services.items() if _queues_of(svc)}
    assert len(lanes) >= 2, (
        f"el descubrimiento de lanes de celery dejó de encontrar servicios (vio "
        f"{len(lanes)}): la guarda pasaría en vacío"
    )
    covered: set[str] = set()
    for queues in lanes.values():
        covered |= queues
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
        assert _celery_app_target(services[name], from_healthcheck=True) == "workers.celery_app", (
            f"{name} healthcheck 'celery inspect ping' must target -A workers.celery_app"
        )


def test_workers_healthchecks_ping_their_own_node() -> None:
    """G-06 (auditoría proyecto 2026-07-17): ``celery inspect ping`` sin ``-d``
    es un broadcast al broker COMPARTIDO — contesta cualquier worker vivo, así
    que un contenedor roto seguía healthy mientras otra lane respondiera (y
    viceversa: se colgaba esperando a todos). El ping debe ir a SU nodo
    (``-d celery@$$HOSTNAME``), como el fix del dispatcher del 2026-07-10."""
    services = generate_compose(_config())["services"]
    for name in ("workers", "workers-privileged", "notification-dispatcher"):
        flat = " ".join(services[name]["healthcheck"]["test"])
        assert "-d celery@$$HOSTNAME" in flat, (
            f"{name} healthcheck must ping its OWN node (-d celery@$$HOSTNAME), "
            "not broadcast to the shared broker"
        )
        # La otra mitad de G-06: celery tarda >10s en arrancar bajo carga — el
        # timeout corto producía unhealthy crónico sin fallo real.
        assert services[name]["healthcheck"]["timeout"] == "30s", (
            f"{name} healthcheck timeout must be 30s (celery startup under load)"
        )


def test_workers_lanes_bind_data_root_and_seccomp_profiles() -> None:
    services = generate_compose(_config(data_root="/data/agent-platform"))["services"]
    for name in ("workers", "workers-privileged"):
        vols = " ".join(services[name].get("volumes", []))
        assert "/data/agent-platform" in vols, (
            f"{name} does not bind the data_root (repos/worktrees)"
        )
        assert "seccomp" in vols, (
            f"{name} does not bind the seccomp profiles (for launched runtimes)"
        )


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
    # networks, POST to create them — and EXEC: it runs the acceptance checks,
    # `pre_install` and the `stack_exec` bridge (ADR 0093) INSIDE the runtime
    # templates it launches, through `exec_run`. With EXEC=0 every one of those
    # answered 403 in a wizard-generated stack (audit 2026-09-01, B-01) while
    # this very test pinned the broken value. Volumes/swarm stay denied.
    for on in ("CONTAINERS", "IMAGES", "NETWORKS", "POST", "EXEC"):
        assert str(env.get(on)) == "1", f"socket-proxy ACL should allow {on}"
    for off in ("VOLUMES", "SWARM"):
        assert str(env.get(off)) == "0", f"socket-proxy ACL must deny {off}"
    vols = " ".join(proxy.get("volumes", []))
    assert "/var/run/docker.sock" in vols, "socket-proxy must mount the docker socket"


def test_docker_socket_proxy_acl_matches_the_dev_compose() -> None:
    """The generator and `docker-compose.manuals.yml` must agree on the ACL.

    The dev compose learnt the hard way that `exec_run` needs EXEC=1 (its
    comment records the 403s); the generator — the production path (ADR 0061)
    — silently kept EXEC=0 for months because nothing crossed the two. A
    divergence here is a production-only failure that dev never sees.
    """
    root = Path(__file__).resolve().parents[2]
    manuals = yaml.safe_load(
        (root / "docker" / "docker-compose.manuals.yml").read_text(encoding="utf-8")
    )
    dev_env = manuals["services"]["docker-socket-proxy"]["environment"]
    gen_env = generate_compose(_config())["services"]["docker-socket-proxy"]["environment"]
    for key in ("CONTAINERS", "IMAGES", "NETWORKS", "POST", "EXEC", "VOLUMES", "SWARM"):
        assert str(gen_env.get(key)) == str(dev_env.get(key)), (
            f"socket-proxy {key}: generator={gen_env.get(key)!r}"
            f" vs manuals.yml={dev_env.get(key)!r}"
        )


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
    assert svc["environment"]["DATABASE_URL"].startswith("${ADMIN_DATABASE_URL:?")
    assert svc["depends_on"]["postgres"]["condition"] == "service_healthy"


@pytest.mark.parametrize(
    "service_name",
    ["api-server", "orchestrator", "workers", "workers-privileged", "notification-dispatcher"],
)
def test_app_services_wait_for_migrations_to_complete(service_name: str) -> None:
    svc = generate_compose(_config())["services"][service_name]
    dep = svc.get("depends_on", {}).get("migrations")
    assert dep == {"condition": "service_completed_successfully"}, (
        f"{service_name} must wait for migrations to finish before starting"
    )


@pytest.mark.parametrize("service_name", ["workers", "workers-privileged"])
def test_workers_pin_seccomp_and_apparmor_profiles(service_name: str) -> None:
    """task_prod01_10 / sandbox-2: the workers must pin the STRICT runtime
    profiles onto the sandboxes they launch (today the WORKERS_ defaults are ""
    → the runtimes fall back to Docker's default profiles)."""
    svc = generate_compose(_config())["services"][service_name]
    env = svc["environment"]
    assert env.get("WORKERS_SECCOMP_PROFILE_PATH", "").endswith("agent-runtime.json"), (
        f"{service_name} must pin the seccomp profile path"
    )
    assert env.get("WORKERS_APPARMOR_PROFILE") == "agent-runtime", (
        f"{service_name} must pin the apparmor profile name"
    )
    # The seccomp profile path must actually be mounted (task_06 bind).
    assert "seccomp" in " ".join(svc.get("volumes", [])), f"{service_name} seccomp not mounted"


@pytest.mark.parametrize("service_name", ["workers", "workers-privileged"])
def test_workers_reach_docker_via_proxy_and_join_agents_network(service_name: str) -> None:
    svc = generate_compose(_config())["services"][service_name]
    env = svc["environment"]
    assert env.get("DOCKER_HOST") == "tcp://docker-socket-proxy:2375", (
        f"{service_name} must talk to the Docker API through the proxy, not the raw socket"
    )
    assert "WORKERS_EGRESS_PROXY_URL" in env, f"{service_name} must get WORKERS_EGRESS_PROXY_URL"
    nets = svc["networks"]
    assert "agentic-agents" in nets, f"{service_name} must join agentic-agents (launch runtimes)"
    assert "agentic-docker" in nets, f"{service_name} must join the socket-proxy network"


def test_api_server_reaches_the_egress_proxy_like_the_workers_do() -> None:
    """ADR 0165 (D9 y corrección A1 del addendum): el api-server también sale por
    el proxy — «Probar conexión» de un MCP remoto deja de ir directa.

    Es el hermano de la aserción de arriba, y existe porque el hueco era
    exactamente esa asimetría: `_workers_env` emitía su `WORKERS_EGRESS_PROXY_URL`
    y `_api_server_env` NO emitía ninguna variable de egress, así que el
    api-server se quedaba con el default de `config.py` (`http://localhost:8888`,
    que dentro del contenedor no es nada). Sin esta línea, la proxificación de la
    prueba de conexión no funciona en NINGUNA instalación salida del wizard, y el
    fallo se ve en producción, no aquí.

    Se comprueba además la red: emitir la URL de un servicio inalcanzable sería
    la misma avería con mejor aspecto."""
    services = generate_compose(_config())["services"]
    api = services["api-server"]

    assert api["environment"].get("API_SERVER_EGRESS_PROXY_URL") == "http://egress-proxy:8888", (
        "api-server must be told where the egress-proxy is (ADR 0165 A1)"
    )
    shared = set(api["networks"]) & set(services["egress-proxy"]["networks"])
    assert shared, "api-server and egress-proxy share no network: the URL would not resolve"


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
    assert env.get("WORKERS_RUN_AS_ROOT") == "1", (
        "la lane de backups necesita root para leer los volume _data a 0700"
    )
    assert "WORKERS_BACKUP_VOLUMES" in env, "faltan los volúmenes a taréar (WORKERS_BACKUP_VOLUMES)"
    vols = " ".join(svc.get("volumes", []))
    assert "/var/lib/docker/volumes" in vols, (
        "falta el mount de los volúmenes Docker para el backup"
    )


# ---------------------------------------------------------------------------
# Textfile collector de node-exporter — el camino ÚNICO por el que las métricas
# de APLICACIÓN llegan a Prometheus en un host sin sidecar de instrumentación.
#
# El stack de desarrollo lo cablea bien (docker-compose.monitoring.yml declara el
# volumen `node_exporter_textfile` + el one-shot `textfile-init`, y
# docker-compose.monitoring.apps.yml monta el drop-dir en `workers`), pero el
# compose que GENERA el instalador no lo hacía: ni el mount, ni el volumen, ni la
# bandera `--collector.textfile.directory` de node-exporter. Consecuencia en una
# instalación de producción: `workers.sample_queue_metrics` escribía en un
# `/host/textfile/` INEXISTENTE dentro del contenedor —el writer trata el sink
# ausente como «topología sin monitorización» y calla (`textfile_collector.py`)—
# así que agentic_celery_queue_depth / agentic_tasks_by_status / agentic_dlq_depth
# / agentic_executions_24h no existían, y las CUATRO reglas de
# docker/monitoring/prometheus/rules/app_alerts.yml montadas sobre ellas no podían
# disparar jamás. Un dashboard vacío se nota; una alerta que no puede sonar
# (`agentic_dlq_depth > 0` — trabajo perdido) parece que no hay nada que sonar.
# ---------------------------------------------------------------------------
#: Contrato compartido con el stack de dev y con el código del worker (los
#: defaults de `workers.config`: queue_metrics_textfile_path /
#: backup_metrics_textfile_path). Se fijan como LITERALES a propósito: si el
#: generador renombra su constante pero cambia el valor, esto lo caza.
_TEXTFILE_DIR = "/host/textfile"
_TEXTFILE_VOLUME = "node_exporter_textfile"
_TEXTFILE_INIT = "textfile-init"

#: Los servicios del compose generado que ESCRIBEN ficheros .prom. `workers`
#: drena la cola `default` (sample_queue_metrics cada 30 s + las métricas de
#: curiosidad del córtex) y `workers-privileged` la cola `privileged` (el backup
#: diario, `agentic_backup_*`). Si solo montásemos uno, la serie del otro
#: faltaría — una métrica incompleta es otra forma de mentir.
_TEXTFILE_WRITERS = ("workers", "workers-privileged")


@pytest.mark.parametrize("service_name", _TEXTFILE_WRITERS)
def test_metric_writing_lanes_mount_the_textfile_drop_dir(service_name: str) -> None:
    svc = generate_compose(_config(), monitoring=True)["services"][service_name]
    vols = svc.get("volumes", [])
    assert f"{_TEXTFILE_VOLUME}:{_TEXTFILE_DIR}" in vols, (
        f"{service_name} no monta el drop-dir del textfile collector "
        f"({_TEXTFILE_VOLUME}:{_TEXTFILE_DIR}): sus métricas se escriben en un "
        f"directorio inexistente y NUNCA llegan a Prometheus; got {vols}"
    )
    # RW: quien escribe no puede montarlo :ro (node-exporter sí, ver abajo).
    assert f"{_TEXTFILE_VOLUME}:{_TEXTFILE_DIR}:ro" not in vols, (
        f"{service_name} monta el drop-dir READ-ONLY — el sampler no podría escribir"
    )


def test_node_exporter_scrapes_the_textfile_drop_dir() -> None:
    """Sin `--collector.textfile.directory` node-exporter no re-exporta NADA de
    lo que dejen los workers: el mount por sí solo no basta."""
    ne = generate_compose(_config(), monitoring=True)["services"]["node-exporter"]
    command = ne["command"]
    flags = command if isinstance(command, list) else [str(command)]
    assert any(f"--collector.textfile.directory={_TEXTFILE_DIR}" in f for f in flags), (
        f"node-exporter no activa el textfile collector: {flags}"
    )
    # Lee, no escribe → :ro (mismo criterio que docker-compose.monitoring.yml).
    assert f"{_TEXTFILE_VOLUME}:{_TEXTFILE_DIR}:ro" in ne.get("volumes", []), (
        "node-exporter debe montar el drop-dir en solo lectura"
    )


def test_textfile_drop_dir_volume_is_declared_when_monitoring() -> None:
    compose = generate_compose(_config(), monitoring=True)
    assert _TEXTFILE_VOLUME in (compose.get("volumes") or {}), (
        "el volumen del drop-dir no está declarado: el compose no levantaría"
    )
    # Convive con el resto de volúmenes nombrados (el cache de Whisper).
    with_voice = generate_compose(_config(voice_mode="cpu"), monitoring=True)
    assert {_TEXTFILE_VOLUME, WHISPER_MODELS_VOLUME} <= set(with_voice.get("volumes") or {})


def test_textfile_init_opens_the_shared_drop_dir_for_both_writers() -> None:
    """El drop-dir es MULTI-ESCRITOR: `workers` escribe como uid 1000 (el
    entrypoint degrada) y `workers-privileged` como root (WORKERS_RUN_AS_ROOT=1,
    lo exige el volume-tar del backup). Un volumen nombrado nace root:root 0755
    → EACCES para el sampler. Se resuelve como en dev: un one-shot que lo deja en
    1777 sticky (como /tmp) ANTES de que arranquen los escritores."""
    compose = generate_compose(_config(), monitoring=True)
    services = compose["services"]
    assert _TEXTFILE_INIT in services, (
        "falta el one-shot que abre el drop-dir: el volumen nace root:root 0755 y "
        "el sampler (uid 1000) recibiría EACCES en cada pasada"
    )
    init = services[_TEXTFILE_INIT]
    assert f"{_TEXTFILE_VOLUME}:{_TEXTFILE_DIR}" in init.get("volumes", [])
    cmd = init["command"]
    joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "1777" in joined and _TEXTFILE_DIR in joined, (
        f"el one-shot debe dejar {_TEXTFILE_DIR} en 1777 (sticky, multi-escritor): {joined!r}"
    )
    assert init["restart"] == "no", "es un one-shot: corre una vez y sale"
    for name in _TEXTFILE_WRITERS:
        dep = (services[name].get("depends_on") or {}).get(_TEXTFILE_INIT)
        assert dep == {"condition": "service_completed_successfully"}, (
            f"{name} debe esperar a {_TEXTFILE_INIT}: si arranca antes del chmod, "
            "el primer sample muere con EACCES"
        )


def test_no_textfile_wiring_without_monitoring() -> None:
    """Sin monitorización NO existe ni el volumen ni node-exporter: un mount
    colado ahí dejaría el compose sin levantar (volumen no declarado) y un
    depends_on huérfano lo rompería del todo."""
    compose = generate_compose(_config(), monitoring=False)
    services = compose["services"]
    assert _TEXTFILE_INIT not in services
    assert _TEXTFILE_VOLUME not in (compose.get("volumes") or {})
    for name in _TEXTFILE_WRITERS:
        svc = services[name]
        assert not [v for v in svc.get("volumes", []) if _TEXTFILE_DIR in v], (
            f"{name} monta el drop-dir sin monitorización (el volumen no existiría)"
        )
        assert _TEXTFILE_INIT not in (svc.get("depends_on") or {})


# ---------------------------------------------------------------------------
# Buzón de credenciales del receiver de RESPALDO de Alertmanager.
#
# QUÉ COMPOSE CUBRE ESTA SECCIÓN: el compose que GENERA EL INSTALADOR
# (`generate_compose`). El mismo montaje sobre el compose de DESARROLLO
# (`docker/docker-compose.monitoring.yml`) lo guarda otro fichero,
# `tests/unit/test_alertmanager_secret_mount.py` — son dos artefactos distintos y
# ninguno de los dos ficheros de test cubre el otro.
#
# El hueco que cierran estos tests: `alertmanager.yml` —el MISMO fichero que el
# compose generado monta— declara el receiver de respaldo leyendo el webhook de
# Slack de `api_url_file: /etc/alertmanager/secrets/slack_api_url`, y el servicio
# generado no montaba nada en esa ruta, así que dentro del contenedor NO EXISTE.
# El fallo es del tipo caro: Alertmanager arranca igual (ese fichero se lee al
# NOTIFICAR, no al cargar la config), el stack sale `healthy`, y el canal de
# último recurso falla en cada envío en silencio — justo en el escenario para el
# que existe, el api-server caído, que no puede entregarse a sí mismo la alerta
# de que está caído. El stack de desarrollo lo cablea bien desde el 2026-08-10;
# esto lo lleva a la instalación generada.
# ---------------------------------------------------------------------------
#: Ruta DENTRO del contenedor. Contrato con `alertmanager.yml`, no una elección
#: libre: es el directorio del que cuelga el `api_url_file` del receiver. Se fija
#: como literal a propósito (si el generador renombra su constante pero cambia el
#: valor, esto lo caza).
_ALERTMANAGER_SECRETS_DIR = "/etc/alertmanager/secrets"

#: El fichero de configuración que el compose generado monta, y del que estos
#: tests derivan qué credenciales-en-fichero hay que respaldar.
_ALERTMANAGER_YML = (
    Path(__file__).resolve().parents[2]
    / "docker"
    / "monitoring"
    / "alertmanager"
    / "alertmanager.yml"
)


def _alertmanager_binds(compose: dict) -> list[tuple[str, str, str]]:
    """`(host, contenedor, modo)` de cada bind-mount del alertmanager generado."""
    svc = compose["services"]["alertmanager"]
    binds: list[tuple[str, str, str]] = []
    for entry in svc.get("volumes") or []:
        parts = str(entry).split(":")
        if len(parts) < 2:
            continue
        binds.append((parts[0], parts[1], parts[2] if len(parts) > 2 else "rw"))
    return binds


def test_generated_alertmanager_mounts_the_backup_receiver_secret_mailbox() -> None:
    """Sin el montaje, la ruta que el receiver lee no existe en el contenedor.

    Y el operador que consiga el webhook de Slack —lo caro— no tiene dónde
    dejarlo sin editar a mano un compose generado, que es justo lo que el runbook
    le pide no hacer.
    """
    compose = generate_compose(_config(), monitoring=True)
    binds = _alertmanager_binds(compose)
    targets = [container for _, container, _ in binds]

    assert _ALERTMANAGER_SECRETS_DIR in targets, (
        f"el alertmanager generado no monta el buzón de credenciales "
        f"({_ALERTMANAGER_SECRETS_DIR}); monta: {targets}. El `api_url_file` del "
        "receiver de respaldo apunta a una ruta inexistente y el envío falla en "
        "silencio (Alertmanager arranca igual)."
    )


def test_generated_alertmanager_secret_mailbox_is_read_only() -> None:
    """Alertmanager solo LEE esa credencial; montarla RW es superficie regalada."""
    compose = generate_compose(_config(), monitoring=True)
    modes = [
        (host, mode)
        for host, container, mode in _alertmanager_binds(compose)
        if container == _ALERTMANAGER_SECRETS_DIR
    ]
    assert modes, "no se comprobó ningún montaje de secretos: la guarda pasó en vacío"
    for host, mode in modes:
        assert mode == "ro", (
            f"el bind `{host}` → `{_ALERTMANAGER_SECRETS_DIR}` no es `:ro` ({mode})"
        )


def test_generated_alertmanager_covers_every_secret_file_its_config_reads() -> None:
    """Coherencia con el fichero REAL que el compose generado monta.

    Por descubrimiento, como la guarda del compose de dev: recorre
    `alertmanager.yml` buscando cualquier clave `*_file` con ruta absoluta
    (`api_url_file`, `auth_password_file`, `bearer_token_file`…) y exige un bind
    detrás. Si mañana un receiver de email añade su credencial en fichero, este
    test la pide solo — y si alguien mueve el buzón en la config sin tocar el
    generador, lo caza.
    """
    config = yaml.safe_load(_ALERTMANAGER_YML.read_text(encoding="utf-8"))
    secrets: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    isinstance(key, str)
                    and key.endswith("_file")
                    and isinstance(value, str)
                    and value.startswith("/")
                ):
                    secrets.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    assert secrets, (
        "ningún receiver lee credenciales de fichero: o el respaldo de "
        "`severity=critical` desapareció, o alguien incrustó el webhook en el YAML"
    )

    generated = generate_compose(_config(), monitoring=True)
    targets = [container for _, container, _ in _alertmanager_binds(generated)]
    for secret in sorted(secrets):
        assert any(
            secret == target or secret.startswith(target.rstrip("/") + "/") for target in targets
        ), (
            f"`{secret}` no está cubierto por ningún bind del alertmanager "
            f"GENERADO (monta: {targets})"
        )


def test_no_alertmanager_secret_mount_without_monitoring() -> None:
    """Sin monitorización no hay alertmanager, y no debe colarse el bind en nadie.

    Un bind a `./monitoring/alertmanager/secrets` en una instalación sin el árbol
    `monitoring/` copiado apuntaría a una ruta inexistente: Docker la inventaría
    como directorio propiedad de root, y el servicio que la montase arrancaría
    con basura montada encima.
    """
    compose = generate_compose(_config(), monitoring=False)
    assert "alertmanager" not in compose["services"]
    for name, svc in compose["services"].items():
        stray = [v for v in (svc.get("volumes") or []) if "alertmanager" in str(v)]
        assert not stray, f"{name} monta rutas de alertmanager sin monitorización: {stray}"


# ---------------------------------------------------------------------------
# Lo que el compose GENERADO hace cuando falta una variable del `.env`
# (auditoría 2026-08-27, hallazgo medio-8).
#
# `${VAR}` a secas NO falla: docker compose avisa por stderr («variable is not
# set, defaulting to a blank string») y sigue adelante con la cadena vacía. Es
# exactamente la forma que prod-10 `secrets-6` declaró insuficiente para el
# compose canónico, y el `_env_ref` del instalador la emitía en modo prod
# mientras su docstring prometía lo contrario.
#
# El escenario concreto que lo convierte en un agujero y no en una molestia:
# `docs/06-runbooks/05-key-rotation.md` hace editar el `.env` a mano. Si en la
# edición se pierde `APP_USER_PASSWORD` y el PGDATA es nuevo, `02-roles.sh` hace
# `${APP_USER_PASSWORD:-<literal de dev>}` — y bash trata la cadena vacía como
# ausente —, así que el rol nace con la contraseña publicada en este repositorio.
# Lo que ve el operador depende de qué variable se le caiga: unas revientan
# ruidosamente y otras no. Esa lotería es justo lo que `:?` elimina.
# ---------------------------------------------------------------------------
#: Toda variable de este catálogo es una CREDENCIAL del stack generado: si falta,
#: el servicio no puede arrancar de forma segura, así que tiene que ABORTAR. Se
#: enumera a mano —igual que en `test_compose_no_default_credentials.py`— para
#: que añadir una credencial sin `:?` sea una decisión consciente.
_MANDATORY_GENERATED_CREDENTIALS = (
    "POSTGRES_PASSWORD",
    "MIGRATIONS_USER_PASSWORD",
    "APP_USER_PASSWORD",
    "SERVICE_USER_PASSWORD",
    "REDIS_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "API_SERVER_JWT_SECRET",
    "API_SERVER_INTERNAL_TOKEN_SECRET",
)

#: `${VAR:?mensaje}` — la forma que aborta el `up`.
_REQUIRED_REF = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*):\?(?P<msg>[^}]*)\}")
#: `${VAR}` a secas sobre una credencial — la forma que interpola a vacío.
_BARE_REF = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*)\}")


def _prod_compose_text() -> str:
    return render_compose_yaml(
        generate_compose(_config(environment=Environment.PRODUCTION), monitoring=True)
    )


@pytest.mark.parametrize("name", _MANDATORY_GENERATED_CREDENTIALS)
def test_a_missing_credential_aborts_the_generated_stack(name: str) -> None:
    text = _prod_compose_text()
    required = {m.group("name"): m.group("msg") for m in _REQUIRED_REF.finditer(text)}
    assert name in required, (
        f"el compose generado no declara {name} obligatoria (`${{{name}:?…}}`): si "
        "falta en el .env, compose interpola cadena vacía y el stack arranca con "
        "una credencial vacía o con el literal de desarrollo del script de init"
    )
    message = required[name]
    assert ".env" in message, (
        f"el mensaje de aborto de {name} no dice dónde ponerla: {message!r}. Un "
        "fallo de arranque sin instrucción es una sesión de depuración"
    )


def test_no_credential_of_the_generated_compose_is_a_bare_reference() -> None:
    """La otra dirección: ninguna credencial se queda en `${VAR}` a secas.

    Guarda contra el paso en vacío incluido — si el parser deja de ver
    referencias, este test debe FALLAR, no aprobar por silencio.
    """
    text = _prod_compose_text()
    bare = {m.group("name") for m in _BARE_REF.finditer(text)}
    assert len(bare) + len(list(_REQUIRED_REF.finditer(text))) > 10, (
        "la guarda dejó de encontrar referencias ${…} en el compose generado"
    )
    offenders = sorted(bare & set(_MANDATORY_GENERATED_CREDENTIALS))
    assert not offenders, (
        f"credenciales referenciadas como `${{VAR}}` a secas: {offenders}. Compose "
        "las interpola a cadena vacía y sigue adelante"
    )


def test_env_ref_keeps_the_dev_fallback_but_never_in_prod() -> None:
    """El contrato de la función, en las dos ramas.

    En dev sigue habiendo `${VAR:-default}` para las comodidades; en prod no hay
    default de ninguna clase, ni el vacío implícito.
    """
    assert _env_ref("SOME_KNOB", "handy", prod=False) == "${SOME_KNOB:-handy}"
    prod_ref = _env_ref("SOME_KNOB", "handy", prod=True)
    assert prod_ref.startswith("${SOME_KNOB:?")
    assert "handy" not in prod_ref, "el default de dev se coló en el mensaje de aborto"
    assert ".env" in prod_ref


# ---------------------------------------------------------------------------
# El rol BYPASSRLS y su contraseña (hallazgo grave-2).
# ---------------------------------------------------------------------------
def test_postgres_receives_the_service_role_password() -> None:
    """Sin esta variable, `05-service-role-password.sh` no puede corregir nada y
    `service_user` —LOGIN + CONNECT + BYPASSRLS + DML sobre todas las tablas—
    se queda con el literal de desarrollo que está escrito en este repositorio.

    Es una regresión con nombre: prod-14 `task_prod14_04` lo arregló en
    `docker/docker-compose.yml:118` y escribió una guarda
    (`tests/security/test_service_user_password_is_wired.py`) que sigue en verde
    porque sólo mira el compose CANÓNICO. El que se instala en casa del operador
    es éste.
    """
    env = generate_compose(_config())["services"]["postgres"]["environment"]
    assert "SERVICE_USER_PASSWORD" in env, (
        "el servicio postgres del compose generado no recibe SERVICE_USER_PASSWORD: "
        "el rol que se salta la RLS de todos los tenants nace con la contraseña "
        "publicada en este repositorio, y el único aviso es una línea de stderr"
    )
    assert env["SERVICE_USER_PASSWORD"].startswith("${SERVICE_USER_PASSWORD:?")


# ---------------------------------------------------------------------------
# Redis autenticado (hallazgo grave-3).
# ---------------------------------------------------------------------------
def _redis_dsns(compose: dict) -> dict[str, str]:
    """{servicio:VARIABLE: valor} de toda DSN `redis://` del compose."""
    found: dict[str, str] = {}
    for name, svc in compose["services"].items():
        for key, value in (svc.get("environment") or {}).items():
            if isinstance(value, str) and value.startswith("redis://"):
                found[f"{name}:{key}"] = value
    return found


def test_redis_requires_a_password() -> None:
    """Ese Redis aloja las SESIONES de servidor, el broker de Celery y los
    contadores de rate limit (`docs/04-reference/mandatory-env-vars.md`).
    Corría sin `requirepass`: un `redis-cli` desde cualquier contenedor de
    `agentic-net` —o desde el host por la IP del bridge, sin puerto publicado—
    leía sesiones vivas, encolaba trabajo para los workers y ponía los contadores
    de rate limit a cero. El operador no veía nada: el stack funciona."""
    redis = generate_compose(_config())["services"]["redis"]
    command = redis["command"]
    text = command if isinstance(command, str) else " ".join(command)
    assert "--requirepass" in text, "redis se instala SIN autenticación"
    assert "${REDIS_PASSWORD:?" in text, (
        "la contraseña de redis tiene que ser obligatoria: con `${REDIS_PASSWORD}` "
        "a secas, un .env incompleto arranca un Redis con `requirepass ''`"
    )


def test_the_redis_healthcheck_authenticates() -> None:
    """Con `requirepass`, un `redis-cli ping` pelado responde NOAUTH y sale != 0:
    el contenedor se quedaría `unhealthy` para siempre y cualquier
    `depends_on: service_healthy` bloquearía el stack entero. Poner la
    contraseña sin arreglar la sonda cambia un agujero por una avería total."""
    redis = generate_compose(_config())["services"]["redis"]
    test = redis["healthcheck"]["test"]
    probe = test if isinstance(test, str) else " ".join(test)
    assert "$$REDIS_PASSWORD" in probe, "la sonda de redis no se autentica"
    assert "PONG" in probe, (
        "la sonda tiene que AFIRMAR la respuesta: `redis-cli -a … ping` devuelve 0 "
        "aunque conteste NOAUTH"
    )
    assert "REDIS_PASSWORD" in (redis.get("environment") or {}), (
        "redis-server no lee REDIS_PASSWORD del entorno, pero el healthcheck corre "
        "DENTRO del contenedor y sí la necesita"
    )


def test_every_redis_dsn_of_the_stack_carries_the_credential() -> None:
    """TODAS, no una muestra — medidas: 23 en un stack con monitorización.

    Son api-server (cache/broker/backend), orchestrator (bus/broker), las tres de
    CADA lane de workers (generic, privileged, marketplace, cortex-beat), las tres
    del dispatcher y las tres del one-shot `bootstrap`. Una sola sin credencial es
    un servicio que no arranca, así que el descubrimiento sale del compose y no de
    una lista escrita a mano — una lista a mano envejece con el primer consumidor
    nuevo, que es exactamente cómo las 20 se quedaron sin credencial a la vez.
    """
    dsns = _redis_dsns(generate_compose(_config(), monitoring=True))
    assert len(dsns) >= 20, f"la guarda dejó de encontrar DSN de redis (vio {len(dsns)})"
    naked = sorted(k for k, v in dsns.items() if not v.startswith("redis://:${REDIS_PASSWORD"))
    assert not naked, f"DSN de redis sin credencial: {naked}"


def test_the_redis_dsn_password_is_mandatory_too() -> None:
    """Y dentro de la DSN también aborta: `redis://:${REDIS_PASSWORD}@…` con la
    variable ausente produce `redis://:@redis:6379/1`, que es una URL válida con
    contraseña vacía — o sea un servicio que arranca y no se puede autenticar."""
    dsns = _redis_dsns(generate_compose(_config()))
    assert dsns
    for where, value in dsns.items():
        assert "${REDIS_PASSWORD:?" in value, f"{where}: {value}"


# ---------------------------------------------------------------------------
# El one-shot `bootstrap` (hallazgo bloqueante-10 / grave-24).
#
# El banner del CLI (`cli.py:_next_steps_banner`) manda ejecutar
# `docker compose run --rm bootstrap` como el segundo de los dos comandos que le
# quedan al operador, y el compose generado no declaraba ese servicio: lo que
# recibía era `no such service: bootstrap`, con un stack `Up (healthy)` —el
# healthcheck de Vault acepta `sealedcode=200&uninitcode=200` a propósito— pero
# con Vault sin inicializar, sin tenant, sin usuario admin y sin ningún revelado
# de credenciales. La instalación PARECE terminada y no lo está.
# ---------------------------------------------------------------------------
def test_the_bootstrap_the_banner_announces_exists() -> None:
    services = generate_compose(_config())["services"]
    assert BOOTSTRAP_SERVICE in services, (
        f"el compose generado no declara «{BOOTSTRAP_SERVICE}», que es lo que el "
        "banner del CLI manda ejecutar como paso 2 de la instalación"
    )


def test_the_bootstrap_does_not_start_with_the_stack() -> None:
    """`profiles: [bootstrap]` es lo que separa «se ejecuta una vez, a mano» de
    «arranca con el stack». Sin el perfil, `docker compose up -d --wait` lo
    lanzaría en cada arranque: un one-shot que reintenta inicializar Vault en
    cada reinicio del host, y `--wait` esperando a un contenedor que sale."""
    svc = generate_compose(_config())["services"][BOOTSTRAP_SERVICE]
    assert svc.get("profiles") == [BOOTSTRAP_SERVICE]
    assert svc.get("restart") == "no", "un one-shot con restart automático es un bucle"


def test_the_bootstrap_runs_inside_the_stack_network() -> None:
    """Es la razón por la que este paso NO lo hace `generate` (ADR 0161, opción
    D): Vault y postgres sólo son alcanzables desde dentro de `agentic-net`."""
    svc = generate_compose(_config())["services"][BOOTSTRAP_SERVICE]
    assert "agentic-net" in svc["networks"]
    assert "ports" not in svc, "un one-shot no publica nada en el host"


def test_the_bootstrap_uses_the_api_server_image() -> None:
    """La imagen que trae los seeds (`api_server.seeds`, `init_tenant`) y `hvac`.
    Reconstruir una imagen propia para tres comandos sería una cuarta cosa que
    publicar, versionar y escanear."""
    services = generate_compose(_config())["services"]
    assert services[BOOTSTRAP_SERVICE]["image"] == services["api-server"]["image"]


def test_the_bootstrap_waits_for_what_it_needs() -> None:
    """Vault arriba (aunque sellado: el healthcheck lo acepta a propósito),
    postgres sano y el esquema YA migrado. Sembrar antes de Alembic falla con
    `relation "organizations" does not exist`, que es el peor momento para
    descubrirlo: después de que Vault haya emitido las unseal keys."""
    deps = generate_compose(_config())["services"][BOOTSTRAP_SERVICE]["depends_on"]
    assert deps["postgres"]["condition"] == "service_healthy"
    assert deps["vault"]["condition"] == "service_healthy"
    assert deps["migrations"]["condition"] == "service_completed_successfully"


def test_the_bootstrap_runs_the_entrypoint_the_seam_declares() -> None:
    """El comando sale del símbolo, no de una cadena suelta en el YAML.

    `BOOTSTRAP_ENTRYPOINT` es la costura con la otra mitad del paso 8 del ADR
    0161: el módulo que la imagen del api-server tiene que exponer. Escribirlo a
    mano aquí y allá es como una de las dos mitades se renombra sola.
    """
    svc = generate_compose(_config())["services"][BOOTSTRAP_SERVICE]
    assert svc["command"] == ["python", "-m", BOOTSTRAP_ENTRYPOINT]


def test_the_bootstrap_gets_what_its_three_jobs_need() -> None:
    """Init de Vault + siembra del tenant + revelado, y cada cosa su entrada."""
    env = generate_compose(_config())["services"][BOOTSTRAP_SERVICE]["environment"]
    # Vault: dónde está.
    assert env["API_SERVER_VAULT_URL"] == "http://vault:8200"
    # Siembra: con el rol BYPASSRLS (los seeds escriben `organizations` y
    # `users`, que una sesión atada a un tenant no puede tocar).
    assert env["API_SERVER_ADMIN_DATABASE_URL"].startswith("${API_SERVER_ADMIN_DATABASE_URL")
    # Y el tenant que hay que sembrar sale del `install.yaml`, no de un default.
    assert env["AGENTIC_BOOTSTRAP_TENANT_NAME"] == "Acme"
    assert env["AGENTIC_BOOTSTRAP_ADMIN_EMAIL"] == "admin@example.com"


def test_the_bootstrap_environment_is_the_one_the_guards_key_on() -> None:
    """Corre con el paquete del api-server, así que construye `api_server.config.
    Settings`: sin `API_SERVER_ENVIRONMENT=prod` los guards anti-defaults no
    disparan dentro de este contenedor y un secreto que falte se degradaría a su
    literal de desarrollo EN SILENCIO — justo en el proceso que siembra al
    primer System Owner de la instalación."""
    env = generate_compose(_config())["services"][BOOTSTRAP_SERVICE]["environment"]
    assert env["API_SERVER_ENVIRONMENT"] == "prod"


def test_the_admin_password_is_not_handed_to_the_bootstrap_by_the_env() -> None:
    """El revelado tiene que ser ÚNICO, y una variable del `.env` no lo es.

    `INIT_ADMIN_PASSWORD` en el compose significaría la contraseña del primer
    System Owner escrita en un fichero del host, legible por cualquiera que ya
    tenga el `.env` y superviviente a la sesión. La mintea el propio one-shot y
    la enseña una vez por stdout, que es lo que el banner promete.
    """
    env = generate_compose(_config())["services"][BOOTSTRAP_SERVICE]["environment"]
    assert "INIT_ADMIN_PASSWORD" not in env


def test_the_bootstrap_gets_the_embedder_wiring_the_catalog_seed_needs() -> None:
    """Sin esto, la siembra revienta a la mitad — y a la mitad ES lo grave.

    `api_server.seeds` embebe el corpus del catálogo contra Ollama
    (`seed_catalog_ingestion`). Si el one-shot no recibe el cableado del
    embebedor, ese paso cae al default de desarrollo (`localhost`), no encuentra
    nada y aborta la siembra: `run_seeds` NO captura excepciones a propósito.
    Y aborta DESPUÉS del init de Vault, o sea después de que las unseal keys se
    hayan mostrado la única vez que se muestran.

    Por eso `bootstrap` está en la lista de servicios a los que
    `generate_compose` inyecta el cableado de proveedores, junto al api-server.
    """
    cfg = _config(ollama_mode="cpu")
    env = generate_compose(cfg)["services"][BOOTSTRAP_SERVICE]["environment"]
    assert env["API_SERVER_OLLAMA_URL"] == "http://ollama:11434"
    assert env["API_SERVER_EMBEDDING_MODEL"] == cfg.resources.embedding_model


def test_the_bootstrap_is_not_part_of_the_running_topology() -> None:
    """No entra en `CORE_SERVICES` a propósito: esa lista es lo que `up -d`
    levanta y lo que los diagramas de topología dibujan. Un one-shot que el
    operador ejecuta una vez y que sale no es parte del stack que corre."""
    assert BOOTSTRAP_SERVICE not in CORE_SERVICES
    assert BOOTSTRAP_SERVICE in selected_services(_config(), monitoring=False)


def test_vault_puede_bloquear_memoria_sin_apagar_mlock() -> None:
    """El `memlock` de Vault, y por qué no se resolvió apagando `mlock`.

    Medido en el e2e (run 33175714605, 2026-08-28), con Postgres y Redis ya
    sanos:

        vault-1 | Error initializing core: Failed to lock memory:
                  cannot allocate memory

    ENOMEM de `mlock`: la firma del `RLIMIT_MEMLOCK` del host, que en un runner
    Linux son 64 KiB. En Docker Desktop no se reproduce —su default es
    efectivamente ilimitado—, así que este fallo NO aparece en ninguna máquina
    de desarrollo Windows: sólo donde se instala de verdad.

    `disable_mlock: true` lo habría arreglado en una línea. También habría
    permitido que las claves de Vault acaben en swap, que es justo lo que el
    ADR 0145 da por hecho que no pasa. Un fallo ruidoso a cambio de una fuga
    silenciosa es mal negocio.
    """
    cfg = _config()
    compose = generate_compose(cfg)
    vault = compose["services"]["vault"]

    assert vault.get("ulimits", {}).get("memlock") == {"soft": -1, "hard": -1}, (
        "Vault no declara `memlock` sin límite: en un host con el default de "
        "64 KiB no arrancará, y el stack entero aborta en `start_stack`."
    )
    assert "IPC_LOCK" in vault.get("cap_add", []), "sin IPC_LOCK, subir el ulimit no basta"
    config_local = str(vault.get("environment", {}).get("VAULT_LOCAL_CONFIG", ""))
    assert '"disable_mlock":true' not in config_local.replace(" ", ""), (
        "se ha apagado `mlock`: eso permite que las claves de Vault vayan a "
        "swap. Si de verdad hace falta en algún entorno, va con su propio ADR, "
        "no como efecto colateral de hacer arrancar el stack."
    )


def test_todo_servicio_con_la_imagen_de_workers_puede_bajar_de_privilegios() -> None:
    """Cuatro servicios comparten imagen y entrypoint; uno solo tenía las caps.

    `apps/workers/docker-entrypoint.sh` arranca como root, repara la propiedad
    del árbol de datos y ejecuta:

        exec setpriv --reuid=1000 --regid=1000 --clear-groups "$@"

    Sin SETUID/SETGID eso falla, y va **sin `|| true`** con `set -eu` detrás: el
    contenedor muere al arrancar, su healthcheck queda `unhealthy` y el
    `up --wait` aborta la instalación entera. Medido en el e2e run 33184204178:

        setpriv: setresuid failed: Operation not permitted

    La lista se comprueba DERIVANDO los servicios de su imagen, no enumerándolos:
    cuando se arregló a mano sólo se tocó `workers` y los otros tres —
    `workers-privileged`, `workers-marketplace`, `cortex-beat`— se quedaron
    fuera, porque cada uno tiene su propio builder. Un servicio nuevo de esa
    familia volvería a nacer sin ellas, y el síntoma aparecería en una
    instalación real.
    """
    compose = generate_compose(_config())
    imagen = app_image("workers")
    familia = {
        nombre: svc for nombre, svc in compose["services"].items() if svc.get("image") == imagen
    }
    assert len(familia) >= 4, (
        f"sólo se han encontrado {len(familia)} servicios con la imagen de "
        "workers: la derivación se ha roto y esta guarda estaría comprobando "
        "casi nada"
    )
    sin_caps = sorted(
        nombre
        for nombre, svc in familia.items()
        if not {"SETUID", "SETGID"} <= set(svc.get("cap_add", []))
    )
    assert not sin_caps, (
        f"{sin_caps} usan la imagen de workers y no pueden bajar de privilegios. "
        "Su entrypoint hace `setpriv` sin red: el contenedor morirá al arrancar "
        "y el `up --wait` abortará la instalación."
    )


def test_los_servicios_sin_http_no_heredan_la_sonda_del_api_server() -> None:
    """Un healthcheck heredado que no aplica es peor que ninguno.

    `apps/watchdog/Dockerfile` y los de la familia de workers se construyen
    `FROM ${BASE_IMAGE}`, que es la imagen del api-server — y ésa declara un
    `HEALTHCHECK` contra `http://localhost:8000/healthz`. Un servicio que NO
    sirve HTTP hereda esa sonda, queda `unhealthy` para siempre y tumba el
    `up --wait` (e2e run 33192295213) mientras funciona perfectamente y lo dice
    en su propio log.

    No mide lo que dice medir, y su rojo permanente enseña a ignorarlo — que es
    la peor consecuencia, porque el día que signifique algo nadie lo mirará.

    La comprobación es que cada servicio SIN puerto declare su propio
    healthcheck, en vez de enumerar cuáles: un servicio nuevo construido sobre
    la misma base volvería a heredarla en silencio.
    """
    compose = generate_compose(_config())
    # Los one-shots salen y no se vigilan; el resto de servicios de aplicación
    # sin puerto publicado ni endpoint HTTP tienen que traer sonda propia.
    sin_sonda = sorted(
        nombre
        for nombre, svc in compose["services"].items()
        if nombre in {"watchdog", "cortex-beat"} and not svc.get("healthcheck")
    )
    assert not sin_sonda, (
        f"{sin_sonda} no declaran healthcheck propio y heredan el del api-server, "
        "que pega a http://localhost:8000/healthz. No sirven HTTP: quedarán "
        "`unhealthy` para siempre y el `up --wait` abortará la instalación."
    )
    # Y que la sonda que traen NO sea la heredada disfrazada.
    for nombre in ("watchdog", "cortex-beat"):
        prueba = " ".join(str(x) for x in compose["services"][nombre]["healthcheck"]["test"])
        assert "8000" not in prueba and "healthz" not in prueba, (
            f"el healthcheck de `{nombre}` sigue apuntando al endpoint HTTP del "
            f"api-server: {prueba!r}. Ese servicio no sirve HTTP."
        )


def test_el_bootstrap_puede_escribir_los_artefactos_del_marketplace() -> None:
    """El último paso de la instalación moría por un directorio sin provisionar.

    `marketplace/seed.py:414` hace `mkdir(parents=True)` bajo
    `/data/agent-platform/marketplace/artifacts`. Ese subárbol NO estaba en el
    árbol de datos, `MARKETPLACE_ARTIFACT_ROOT` no se cablea en ninguna parte, y
    el one-shot no montaba nada — así que el `mkdir` intentaba crear `/data` a
    secas y salía con `Permission denied` (e2e run 33195432130).

    Lo que lo hacía caro: pasaba en el ÚLTIMO paso, con Vault ya inicializado y
    el revelado ya emitido. Una instalación que falla después de acuñar
    credenciales irrepetibles es el peor momento para fallar.

    Se monta el subárbol y no la raíz a propósito: la api-server no monta
    `/data` por decisión documentada —las operaciones de git y disco van al
    worker— y este one-shot corre con su misma imagen. Un almacén de artefactos
    no es el árbol de worktrees.
    """
    compose = generate_compose(_config())
    volumenes = compose["services"][BOOTSTRAP_SERVICE].get("volumes") or []
    montado = [v for v in volumenes if "marketplace" in v]
    assert montado, (
        "el one-shot no monta el almacén de artefactos: la siembra de listings "
        "fallará al escribirlos, y lo hará DESPUÉS de emitir el revelado."
    )
    assert not any(v.split(":")[1] == "/data/agent-platform" for v in volumenes if ":" in v), (
        "monta la raíz de datos entera. La api-server no la monta por decisión "
        "documentada y este one-shot usa su imagen: monta sólo el subárbol."
    )


def test_the_test_lane_is_drained_by_a_worker_that_is_not_the_generic_pool() -> None:
    """Auditoría 2026-09-01 (A-02). La fase de tests espera de forma SÍNCRONA
    (`AsyncResult.get()` bajo `allow_join_result`) dentro del task `run_execution`
    de la lane `default`. Si la cola `test` la sirviera SÓLO el pool genérico, dos
    runs esperando a la vez ocuparían los dos slots y la fase de tests no tendría
    dónde correr: inanición hasta el presupuesto (1 h). El compose de dev ya tiene
    `workers-aux` (`--queues=test,review`) por exactamente ese motivo, medido; el
    generado por el instalador —el de producción— no lo tenía."""
    services = generate_compose(_config())["services"]
    aux = [
        name
        for name, svc in services.items()
        if _queues_of(svc) and "test" in _queues_of(svc) and "default" not in _queues_of(svc)
    ]
    assert aux, (
        "ninguna lane sirve la cola `test` sin servir también `default`: la espera "
        "síncrona de la fase de tests puede inanicionarse en el pool genérico"
    )
