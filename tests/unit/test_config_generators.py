"""Unit tests for the .env / global.yaml / data-tree generators (task_15_08).

Asserts the Phase-B config generators (``installer_backend.config_generators``):
  * ``generate_secrets`` produces high-entropy, unique-per-run secrets that
    carry NO dev-default marker;
  * the generated ``.env`` has every required key with a non-dev-default value
    and passes the prod dev-secret guard (Plan 06.14) — including the
    api-server / workers / dispatcher prefixed secrets;
  * the api-server + dispatcher share one notification-encryption key (write/read
    pair) and the derived DSNs use the generated DB passwords;
  * ``config/global.yaml`` is valid YAML carrying only non-secret platform config
    (domain, environment, enabled providers, resources, storage, languages);
  * the data-tree plan lists the expected directories with sane POSIX modes, and
    gates the GPU / monitoring dirs on those features;
  * the disk-write + mkdir seams are exercised with in-memory fakes (no real
    /data writes).

No host access: the generators are pure (return strings / dicts / a plan). The
write seams are mocked. Real disk provisioning is a HUMAN test.
"""

from __future__ import annotations

import pytest
import yaml
from installer_backend.config import (
    AzureFoundryProvider,
    ClaudeSdkProvider,
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
from installer_backend.config_generators import (
    _DEV_SECRET_MARKERS,
    DataDir,
    FakeDataTreeProvisioner,
    FakeEnvFileWriter,
    assert_env_passes_prod_secret_guard,
    build_data_tree_plan,
    build_env_vars,
    generate_env_file,
    generate_global_config,
    generate_secrets,
    render_env_file,
    render_global_yaml,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Config builder. Throwaway placeholder secrets — nothing real is committed.
# ---------------------------------------------------------------------------
def _config(
    *,
    environment: Environment = Environment.PRODUCTION,
    gpu_enabled: bool = False,
    providers: ProvidersConfig | None = None,
    data_root: str = "/data/agent-platform",
) -> InstallerConfig:
    if providers is None:
        providers = ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434"))
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=environment),
        resources=ResourceConfig(worker_replicas=2, worker_memory_gib=4, gpu_enabled=gpu_enabled),
        storage=StorageConfig(
            data_root=data_root,
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=providers,
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )


# The env keys the runtime services' prod secret guard checks (a generated prod
# .env MUST set all of these to a real, non-dev value).
_REQUIRED_SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "MIGRATIONS_USER_PASSWORD",
    "APP_USER_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "API_SERVER_JWT_SECRET",
    "API_SERVER_REVIEW_URL_SIGNING_SECRET",
    "API_SERVER_SSO_ENCRYPTION_KEY",
    "API_SERVER_NOTIFICATION_ENCRYPTION_KEY",
    "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY",
    "API_SERVER_MINIO_SECRET_KEY",
    "API_SERVER_DATABASE_URL",
    "API_SERVER_ADMIN_DATABASE_URL",
    "NOTIFY_NOTIFICATION_ENCRYPTION_KEY",
)


# ---------------------------------------------------------------------------
# Secrets — high-entropy, unique per run, no dev marker.
# ---------------------------------------------------------------------------
def test_secrets_are_high_entropy() -> None:
    s = generate_secrets()
    for value in (s.postgres_password, s.jwt_secret, s.minio_root_password):
        # token_urlsafe(32) → ~43 chars; require comfortably long.
        assert len(value) >= 40, value


def test_secrets_are_unique_per_run() -> None:
    a = generate_secrets()
    b = generate_secrets()
    # Every field differs between two independent runs (CSPRNG).
    assert a.postgres_password != b.postgres_password
    assert a.jwt_secret != b.jwt_secret
    assert a.notification_encryption_key != b.notification_encryption_key
    # And distinct fields within one run don't collide.
    assert a.jwt_secret != a.review_url_signing_secret


def test_secrets_carry_no_dev_marker() -> None:
    s = generate_secrets()
    for value in (
        s.postgres_password,
        s.migrations_user_password,
        s.app_user_password,
        s.minio_root_user,
        s.minio_root_password,
        s.jwt_secret,
        s.review_url_signing_secret,
        s.sso_encryption_key,
        s.notification_encryption_key,
        s.incoming_webhook_encryption_key,
        s.grafana_admin_password,
    ):
        lowered = value.lower()
        for marker in _DEV_SECRET_MARKERS:
            assert marker not in lowered, f"{value!r} contains dev marker {marker!r}"


def test_secrets_repr_is_redacted() -> None:
    s = generate_secrets()
    assert "redacted" in repr(s).lower()
    assert s.jwt_secret not in repr(s)


# ---------------------------------------------------------------------------
# .env — all required keys, non-dev values, passes the prod guard.
# ---------------------------------------------------------------------------
def test_env_has_all_required_keys() -> None:
    env = build_env_vars(_config(), generate_secrets())
    for key in _REQUIRED_SECRET_KEYS:
        assert key in env, f"missing required .env key {key}"
        assert env[key], f"empty value for {key}"


def test_env_sets_runtime_environment_marker_for_prod() -> None:
    env = build_env_vars(_config(environment=Environment.PRODUCTION), generate_secrets())
    # The runtime guard keys on 'prod'/'staging' (not the installer's
    # 'production'); a production install must emit ENVIRONMENT=prod so the
    # guard actually fires.
    assert env["ENVIRONMENT"] == "prod"
    assert env["API_SERVER_ENVIRONMENT"] == "prod"
    assert env["NOTIFY_ENVIRONMENT"] == "prod"


def test_env_dev_environment_marker() -> None:
    env = build_env_vars(_config(environment=Environment.DEVELOPMENT), generate_secrets())
    assert env["ENVIRONMENT"] == "dev"


def test_generated_env_passes_prod_secret_guard() -> None:
    text = generate_env_file(_config(environment=Environment.PRODUCTION), generate_secrets())
    lowered = text.lower()
    for marker in _DEV_SECRET_MARKERS:
        assert marker not in lowered, f"prod .env leaked dev marker {marker!r}"
    # The dedicated self-check accepts it.
    assert_env_passes_prod_secret_guard(text)


def test_prod_guard_rejects_dev_marker() -> None:
    bad = "API_SERVER_JWT_SECRET=changeme\n"
    with pytest.raises(ValueError, match="desarrollo"):
        assert_env_passes_prod_secret_guard(bad)


def test_notification_key_shared_between_api_and_dispatcher() -> None:
    env = build_env_vars(_config(), generate_secrets())
    # The write path (api-server) and read path (dispatcher) MUST derive the
    # same Fernet key from the same raw secret.
    assert (
        env["API_SERVER_NOTIFICATION_ENCRYPTION_KEY"] == env["NOTIFY_NOTIFICATION_ENCRYPTION_KEY"]
    )


def test_database_urls_use_generated_passwords() -> None:
    s = generate_secrets()
    env = build_env_vars(_config(), s)
    assert s.app_user_password in env["DATABASE_URL"]
    assert s.migrations_user_password in env["ADMIN_DATABASE_URL"]
    assert env["DATABASE_URL"] == env["API_SERVER_DATABASE_URL"]
    # No dev marker in the DSNs.
    for marker in _DEV_SECRET_MARKERS:
        assert marker not in env["DATABASE_URL"].lower()


def test_minio_user_is_not_the_dev_default() -> None:
    env = build_env_vars(_config(), generate_secrets())
    assert env["MINIO_ROOT_USER"] != "minioadmin"
    assert "minioadmin" not in env["MINIO_ROOT_USER"].lower()


def test_env_includes_enabled_provider_wiring_only() -> None:
    providers = ProvidersConfig(
        claude_sdk=ClaudeSdkProvider(enabled=True, oauth_token="tok"),
        azure_foundry=AzureFoundryProvider(
            enabled=True, apim_endpoint="https://apim.example.com", api_key="k"
        ),
    )
    env = build_env_vars(_config(providers=providers), generate_secrets())
    assert env["LLM_CLAUDE_SDK_ENABLED"] == "true"
    assert env["LLM_AZURE_FOUNDRY_ENDPOINT"] == "https://apim.example.com"
    assert "LLM_COPILOT_ENABLED" not in env
    assert "LLM_OLLAMA_ENABLED" not in env


def test_env_monitoring_adds_grafana_password() -> None:
    s = generate_secrets()
    without = build_env_vars(_config(), s, monitoring=False)
    assert "GRAFANA_ADMIN_PASSWORD" not in without
    with_mon = build_env_vars(_config(), s, monitoring=True)
    assert with_mon["GRAFANA_ADMIN_PASSWORD"] == s.grafana_admin_password


def test_render_env_file_is_dotenv_and_has_header() -> None:
    text = generate_env_file(_config(), generate_secrets())
    assert text.startswith("#")
    assert "DO NOT commit" in text
    # Every non-comment, non-blank line is KEY=value.
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        assert "=" in line, line
        key = line.split("=", 1)[0]
        assert key == key.strip() and " " not in key, line


def test_render_env_value_quoting() -> None:
    # A value with a space is quoted; a plain token is not.
    text = render_env_file({"PLAIN": "abc123", "SPACED": "a b"})
    assert "PLAIN=abc123" in text
    assert 'SPACED="a b"' in text


# ---------------------------------------------------------------------------
# config/global.yaml — valid YAML, only non-secret config.
# ---------------------------------------------------------------------------
def test_global_yaml_is_valid_and_round_trips() -> None:
    cfg = _config()
    doc = generate_global_config(cfg)
    text = render_global_yaml(doc)
    parsed = yaml.safe_load(text)
    assert parsed == doc
    assert parsed["platform"]["domain"] == "agentic.example.com"
    assert parsed["platform"]["environment"] == "production"
    # ES + EN only (CLAUDE.md principle 12).
    assert parsed["platform"]["languages"] == ["es", "en"]


def test_global_yaml_lists_enabled_providers() -> None:
    providers = ProvidersConfig(
        claude_sdk=ClaudeSdkProvider(enabled=True, oauth_token="tok"),
        ollama=OllamaProvider(enabled=True, endpoint="http://o:11434"),
    )
    doc = generate_global_config(_config(providers=providers))
    assert doc["providers"]["enabled"] == ["claude_sdk", "ollama"]


def test_global_yaml_carries_no_secret() -> None:
    # Build with real generated secrets in the env; the global config must not
    # echo any of them.
    s = generate_secrets()
    cfg = _config()
    text = render_global_yaml(generate_global_config(cfg))
    for value in (s.jwt_secret, s.minio_root_password, s.postgres_password):
        assert value not in text


def test_global_yaml_reflects_monitoring_and_gpu() -> None:
    doc = generate_global_config(_config(gpu_enabled=True), monitoring=True)
    assert doc["monitoring"]["enabled"] is True
    assert doc["resources"]["gpu_enabled"] is True


# ---------------------------------------------------------------------------
# Data-tree plan.
# ---------------------------------------------------------------------------
def _paths(plan: list[DataDir]) -> set[str]:
    return {d.path for d in plan}


def test_data_tree_plan_lists_expected_dirs() -> None:
    plan = build_data_tree_plan(_config(data_root="/data/agent-platform"))
    paths = _paths(plan)
    for sub in ("postgres", "redis", "minio", "vault/file", "projects", "worktrees", "dep-cache"):
        assert f"/data/agent-platform/{sub}" in paths, sub
    # Root itself comes first.
    assert plan[0].path == "/data/agent-platform"


def test_data_tree_uses_configured_root() -> None:
    plan = build_data_tree_plan(_config(data_root="/srv/agentic"))
    assert all(d.path.startswith("/srv/agentic") for d in plan)


def test_data_tree_secret_dirs_are_0700() -> None:
    plan = build_data_tree_plan(_config())
    by_path = {d.path: d for d in plan}
    assert by_path["/data/agent-platform/vault/file"].mode == 0o700
    assert by_path["/data/agent-platform/vault/logs"].mode == 0o700
    assert by_path["/data/agent-platform/backups"].mode == 0o700
    # Non-secret dirs are 0o750.
    assert by_path["/data/agent-platform/redis"].mode == 0o750


def test_data_tree_gates_gpu_and_monitoring_dirs() -> None:
    minimal = _paths(build_data_tree_plan(_config(gpu_enabled=False), monitoring=False))
    assert "/data/agent-platform/ollama" not in minimal
    assert "/data/agent-platform/prometheus" not in minimal
    assert "/data/agent-platform/grafana" not in minimal

    full = _paths(build_data_tree_plan(_config(gpu_enabled=True), monitoring=True))
    assert "/data/agent-platform/ollama" in full
    assert "/data/agent-platform/prometheus" in full
    assert "/data/agent-platform/grafana" in full


# ---------------------------------------------------------------------------
# Disk-write + provisioning seams — exercised with in-memory fakes.
# ---------------------------------------------------------------------------
def test_env_file_writer_seam_records_write() -> None:
    writer = FakeEnvFileWriter()
    text = generate_env_file(_config(), generate_secrets())
    writer.write("/data/agent-platform/.env", text, mode=0o600)
    assert writer.written["/data/agent-platform/.env"] == text
    # Secret-bearing file gets 0o600.
    assert writer.modes["/data/agent-platform/.env"] == 0o600


def test_data_tree_provisioner_seam_records_plan() -> None:
    provisioner = FakeDataTreeProvisioner()
    plan = build_data_tree_plan(_config())
    provisioner.provision(plan)
    assert provisioner.provisioned == plan
