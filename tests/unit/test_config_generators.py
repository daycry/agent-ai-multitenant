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
    # NOTIF-2: el Bearer del ingest de Alertmanager se genera por despliegue.
    "API_SERVER_ALERTS_INGEST_TOKEN",
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


# ---------------------------------------------------------------------------
# A QUIÉN le da la base de datos el `.env` generado (auditoría 2026-08-27,
# hallazgos bloqueante-1 y grave-4).
#
# El reparto de roles lo decidió prod-14 (`task_prod14_05` / tenancy-2) y está
# escrito en los cuatro `config.py` del runtime, que es donde nadie lo mira:
#
#   * `app_user`        NOBYPASSRLS — el camino con tenant en la sesión.
#   * `service_user`    BYPASSRLS **sin DDL** — quien trabaja cruzando tenants.
#   * `migrations_user` PROPIETARIO del esquema, GRANT ALL, DDL — Alembic y el
#                       `pg_dump` del backup, y NADIE más.
#
# El `.env` los pisa: una variable de entorno gana al default de pydantic. Así
# que la postura real de una instalación no la fija `config.py`, la fija ESTE
# generador, y hasta hoy devolvía a los workers, al dispatcher y a la superficie
# /admin al dueño del esquema — desde donde un `ALTER TABLE … DISABLE ROW LEVEL
# SECURITY` desmonta el aislamiento multi-tenant de toda la instalación sin
# tocar una fila. La guarda de prod-14 (`tests/security/
# test_service_role_is_the_runtime_default.py`) seguía verde porque sólo lee los
# `config.py`: el contrato roto vivía en la costura entre el default y el `.env`.
#
# Estos tests miran esa costura, que es el único sitio donde se ve.
# ---------------------------------------------------------------------------
def _dsn_user(dsn: str) -> str:
    """El rol de una DSN ``postgresql[+driver]://usuario:clave@host/db``."""
    return dsn.split("://", 1)[1].split(":", 1)[0]


#: Las DSN que consume un servicio que trabaja CRUZANDO tenants sin `app.tenant_id`
#: en la sesión. Todas quieren `service_user`: BYPASSRLS, y NADA de DDL.
_SERVICE_ROLE_DSNS = (
    "WORKERS_DATABASE_URL",
    "ORCHESTRATOR_DATABASE_URL",
    "NOTIFY_DATABASE_URL",
    "API_SERVER_ADMIN_DATABASE_URL",
)


@pytest.mark.parametrize("key", _SERVICE_ROLE_DSNS)
def test_cross_tenant_services_get_the_service_role_not_the_schema_owner(key: str) -> None:
    """Ni el dueño del esquema (radio de explosión) ni el rol de aplicación (no
    despacha nada). Exactamente ``service_user``."""
    env = build_env_vars(_config(), generate_secrets())
    user = _dsn_user(env[key])
    assert user == "service_user", (
        f"{key} corre como {user!r}. Con `migrations_user` el servicio puede "
        "`ALTER TABLE … DISABLE ROW LEVEL SECURITY` y apagar el aislamiento "
        "multi-tenant de toda la instalación; con `app_user` (NOBYPASSRLS) no ve "
        "ninguna fila, porque nadie fija `app.tenant_id` en esa sesión."
    )


def test_the_orchestrator_can_actually_see_a_task() -> None:
    """El error espejo, y merece prueba propia porque falla en SILENCIO.

    Con `app_user` el orchestrator arranca sano, el stack sube entero `healthy` y
    las tareas se quedan en `pending` para siempre: las policies RLS son
    ``tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`` y sin
    la variable de sesión la comparación es NULL, así que la fila se filtra. Cero
    filas es una respuesta válida de SQL: ni error, ni traza. Un test que sólo
    comprobara «no es migrations_user» daría esto por bueno.
    """
    env = build_env_vars(_config(), generate_secrets())
    assert _dsn_user(env["ORCHESTRATOR_DATABASE_URL"]) != "app_user", (
        "el orchestrator despacha para TODOS los tenants y no fija `app.tenant_id` "
        "en ninguna parte de su código: con el rol NOBYPASSRLS no despacharía "
        "ninguna tarea, y sin un solo error que lo delate"
    )


def test_only_alembic_and_the_backup_hold_the_schema_owner() -> None:
    """`migrations_user` aparece en DOS sitios del `.env`, y en ninguno más.

    ``ADMIN_DATABASE_URL`` lo lee el one-shot `migrations` (Alembic necesita DDL)
    y ``WORKERS_BACKUP_DATABASE_URL`` el `pg_dump` del backup (necesita al dueño
    para volcarlo todo). Cualquier tercera aparición es un servicio de larga vida
    con DDL en la mano, que es el hallazgo.
    """
    env = build_env_vars(_config(), generate_secrets())
    holders = sorted(
        key
        for key, value in env.items()
        if isinstance(value, str) and "://migrations_user:" in value
    )
    assert holders == ["ADMIN_DATABASE_URL", "WORKERS_BACKUP_DATABASE_URL"], (
        f"el dueño del esquema viaja en {holders}. Sólo Alembic y el pg_dump del "
        "backup pueden llevarlo"
    )


def test_the_app_role_stays_on_the_tenant_bound_path() -> None:
    """Y el reparto no se arregla moviéndolo TODO a `service_user`: el camino con
    tenant en la sesión (el de las peticiones humanas) sigue siendo NOBYPASSRLS,
    que es lo que hace que la RLS proteja algo."""
    env = build_env_vars(_config(), generate_secrets())
    assert _dsn_user(env["DATABASE_URL"]) == "app_user"
    assert env["API_SERVER_DATABASE_URL"] == env["DATABASE_URL"]


def test_service_user_password_is_generated_and_emitted() -> None:
    """El rol BYPASSRLS no puede nacer con la contraseña publicada en este repo.

    `stack/postgres/init/04-service-role.sql` crea `service_user` con el literal
    de desarrollo y `05-service-role-password.sh` lo corrige DESDE
    `SERVICE_USER_PASSWORD`. Sin esa variable en el `.env`, el rol se queda con la
    contraseña que está escrita en este repositorio: LOGIN + CONNECT + BYPASSRLS +
    DML sobre todas las tablas, alcanzable desde cualquier contenedor de
    `agentic-net`. El único aviso era una línea en el stderr del contenedor de
    postgres, donde nadie mira.
    """
    s = generate_secrets()
    env = build_env_vars(_config(), s)
    assert env["SERVICE_USER_PASSWORD"] == s.service_user_password
    assert len(env["SERVICE_USER_PASSWORD"]) >= 40
    # Y es la MISMA que llevan las DSN de los servicios: si divergen, el rol
    # existe con una clave y los servicios se autentican con otra.
    for key in _SERVICE_ROLE_DSNS:
        assert s.service_user_password in env[key], key
    # Material independiente: reutilizar el de `app_user` haría que comprometer
    # el rol de aplicación entregase también el que se salta la RLS.
    assert s.service_user_password not in (s.app_user_password, s.migrations_user_password)


# ---------------------------------------------------------------------------
# Redis con autenticación (auditoría 2026-08-27, hallazgo grave-3).
# ---------------------------------------------------------------------------
def test_redis_password_is_generated_and_the_dsn_carries_it() -> None:
    """Ese Redis NO es una caché: aloja las SESIONES de servidor, el broker de
    Celery (o sea, la capacidad de encolar trabajo para los workers) y los
    contadores de rate limit (`docs/04-reference/mandatory-env-vars.md`). Sin
    `requirepass`, cualquiera con acceso al puerto los lee y los escribe, y el
    operador no ve nada: el stack funciona perfectamente."""
    s = generate_secrets()
    env = build_env_vars(_config(), s)
    assert env["REDIS_PASSWORD"] == s.redis_password
    assert env["REDIS_URL"] == f"redis://:{s.redis_password}@redis:6379/0"


def test_every_secret_that_travels_inside_a_url_is_url_safe() -> None:
    """La invariante que mantiene coherentes las DSN del `.env` y las del compose.

    El `.env` construye las URLs en Python con el valor en claro; el compose las
    construye interpolando `${REDIS_PASSWORD}` tal cual. Las dos formas coinciden
    SÓLO mientras el secreto no lleve un carácter reservado de URL: un arroba o
    una barra partirían la DSN por sitios distintos en cada lado, y el fallo sería
    un «authentication failed» sin causa visible. `secrets.token_urlsafe` lo
    garantiza — pero garantizado y COMPROBADO no son lo mismo, y esto es lo que
    impide que un cambio de generador lo rompa en silencio.
    """
    s = generate_secrets()
    reserved = set(":/?#[]@ \t\"'\\%")
    for name, value in (
        ("redis_password", s.redis_password),
        ("service_user_password", s.service_user_password),
        ("app_user_password", s.app_user_password),
        ("migrations_user_password", s.migrations_user_password),
        ("postgres_password", s.postgres_password),
    ):
        offenders = sorted(reserved & set(value))
        assert not offenders, f"{name} lleva caracteres que rompen una URL: {offenders}"


def test_new_secrets_carry_no_dev_marker() -> None:
    s = generate_secrets()
    for value in (s.service_user_password, s.redis_password):
        lowered = value.lower()
        for marker in _DEV_SECRET_MARKERS:
            assert marker not in lowered, f"{value!r} contiene el marcador {marker!r}"


# ---------------------------------------------------------------------------
# Los secretos que el operador teclea en `install.yaml` (hallazgo medio-5).
# ---------------------------------------------------------------------------
def test_the_profile_placeholder_never_reaches_the_generated_env() -> None:
    """Los `CHANGE_ME_…` de los perfiles NO viajan al `.env` — y ahora la guarda
    lo comprueba en vez de darlo por hecho.

    `storage.minio_access_key` / `minio_secret_key` son campos OBLIGATORIOS que
    este generador **no consume**: el `.env` lleva las credenciales de MinIO que
    acuña `generate_secrets()`. Eso es deliberado (una clave CSPRNG es más fuerte
    que la que teclee nadie, y es lo que el gate e2e de instalación da por
    supuesto), pero convierte el descarte en la única cosa que separa una
    instalación sana de una con el placeholder del perfil por contraseña raíz del
    almacén de objetos. Si alguien cablea el YAML algún día, que reviente aquí y
    no en producción.
    """
    cfg = _config()
    cfg.storage.minio_access_key = "CHANGE_ME_minio_access"
    text = generate_env_file(cfg, generate_secrets())
    assert "CHANGE_ME" not in text
    assert_env_passes_prod_secret_guard(text)


def test_the_prod_guard_rejects_an_unsubstituted_placeholder() -> None:
    """Y la otra mitad: si un `CHANGE_ME_` llegara al `.env`, el guardián aborta.

    Antes no: el catálogo de marcadores era `changeme` / `dev-only` /
    `minioadmin`, y el placeholder de los perfiles no contiene ninguno (el guion
    bajo rompe `changeme`). Una instalación con el perfil sin editar habría pasado
    el guardián con un marcador público por contraseña.
    """
    with pytest.raises(ValueError, match="desarrollo"):
        assert_env_passes_prod_secret_guard(
            "MINIO_ROOT_PASSWORD=CHANGE_ME_minio_secret_placeholder_value\n"
        )
