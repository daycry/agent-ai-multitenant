"""Los servicios que el restore para tienen que existir Y ser TODOS (prod-04 task_prod_04_03).

Dos formas distintas de romper una restauración con una lista de strings:

**Un fantasma en la lista.** `_stop_app_stack` hace `docker compose stop <lista>`
y eleva si el código de salida no es 0; compose devuelve error ante un servicio
desconocido. La restauración completa abortaba en el paso 3, ANTES de restaurar
nada. Pasó de verdad: `web-app` estuvo en la lista y nunca existió en ningún
compose (ADR 0117 c). `tests/unit/test_restore_services_exist.py` ya cubre eso.

**Un escritor que FALTA en la lista.** Menos visible y más caro: PostgreSQL se
deja a propósito en marcha para que `pg_restore` pueda conectar, así que cualquier
servicio que no se pare sigue escribiendo en la base de datos mientras el restore
hace `--clean` (DROP + CREATE de todo). El resultado es un restore que «funciona»
con filas nuevas encima del dump o con un DROP que falla por dependencias. Antes
de prod-04 faltaban TRES: `workers-privileged` (la lane que corre backups y
rotación), `cortex-beat` y `notification-dispatcher`.

Este módulo mira los composes REALES — el versionado y el que genera el
instalador, que es el que corre en producción — y deriva del propio compose quién
escribe en la base de datos, en vez de mantener a mano una segunda lista que se
desincronizaría igual que la primera.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml
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
from workers.config import Settings
from workers.restore import _declared_compose_services

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERSIONED_COMPOSE = _REPO_ROOT / "docker" / "docker-compose.yml"

#: Servicios que NO se paran a propósito, con la razón. Cualquier otro escritor
#: de la base de datos tiene que estar en `restore_app_services`.
_DELIBERATELY_NOT_STOPPED = {
    # Tiene que seguir alcanzable: es el destino del pg_restore.
    "postgres",
    # One-shot: corre `alembic upgrade head` y termina. No hay nada que parar.
    "migrations",
    # Se paran aparte, alrededor de la re-extracción de sus volúmenes.
    "minio",
    "redis",
    "vault",
}


def _generated_compose() -> dict[str, Any]:
    cfg = InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=Environment.PRODUCTION),
        resources=ResourceConfig(
            worker_replicas=1,
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
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=False)),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )
    return generate_compose(cfg, monitoring=True)


def _settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------- #
# Ningún fantasma
# --------------------------------------------------------------------------- #


def test_every_service_the_restore_stops_is_declared_in_the_production_compose() -> None:
    declared = set(_generated_compose()["services"])
    # No vacuo: si el generador dejara de declarar servicios, esto avisa antes de
    # que el subset pase por estar comparando contra el vacío.
    assert len(declared) >= 15, f"el compose generado declara muy poco: {sorted(declared)}"

    settings = _settings()
    wanted = [*settings.restore_app_services, *settings.restore_volume_services]
    missing = sorted(set(wanted) - declared)
    assert not missing, (
        f"la restauración pararía servicios inexistentes {missing}: `docker compose stop` "
        f"devuelve != 0 y el restore aborta antes de restaurar nada"
    )


def test_the_volume_services_also_exist_in_the_versioned_compose() -> None:
    """El compose versionado sí declara la infraestructura, y es el que usa dev."""
    declared = _declared_compose_services(_VERSIONED_COMPOSE)
    assert declared, f"no se pudieron leer los servicios de {_VERSIONED_COMPOSE}"
    missing = sorted(set(_settings().restore_volume_services) - declared)
    assert not missing, missing


# --------------------------------------------------------------------------- #
# Ningún escritor suelto
# --------------------------------------------------------------------------- #


def _db_writing_services(compose: dict[str, Any]) -> set[str]:
    """Servicios cuyo entorno lleva un DSN de PostgreSQL: escriben en la BD."""
    writers: set[str] = set()
    for name, svc in compose["services"].items():
        env = svc.get("environment") or {}
        if not isinstance(env, dict):
            continue
        for key, value in env.items():
            if key.endswith("DATABASE_URL") and isinstance(value, str) and value:
                writers.add(name)
                break
    return writers


def test_every_database_writer_is_either_stopped_or_deliberately_exempt() -> None:
    compose = _generated_compose()
    writers = _db_writing_services(compose)
    # No vacuo: la heurística tiene que estar encontrando a los sospechosos
    # habituales. Si el nombre de la variable de entorno cambiase, este assert
    # rompe en vez de dejar pasar una lista vacía como «todo correcto».
    assert {
        "api-server",
        "workers",
        "orchestrator",
    } <= writers, f"la detección de escritores de BD dejó de funcionar (vio {sorted(writers)})"

    stopped = set(_settings().restore_app_services)
    unguarded = sorted(writers - stopped - _DELIBERATELY_NOT_STOPPED)
    assert not unguarded, (
        f"estos servicios escriben en PostgreSQL y el restore NO los para: {unguarded}. "
        f"PostgreSQL se deja vivo a propósito para el pg_restore, así que seguirían "
        f"escribiendo mientras el dump se restaura con --clean. Añádelos a "
        f"WORKERS_RESTORE_APP_SERVICES o justifica la excepción."
    )


def test_the_three_writers_prod_04_added_are_still_there() -> None:
    """El caso concreto, explícito, para que un revert cuente una historia."""
    stopped = set(_settings().restore_app_services)
    for service in ("workers-privileged", "cortex-beat", "notification-dispatcher"):
        assert service in stopped, (
            f"{service} escribe en la base de datos y volvió a quedar fuera de la "
            f"lista de servicios que el restore para"
        )


def test_postgres_is_still_deliberately_absent() -> None:
    stopped = set(_settings().restore_app_services)
    assert not {"postgres", "postgresql", "db"} & stopped


# --------------------------------------------------------------------------- #
# El compose al que apunta el restore por defecto
# --------------------------------------------------------------------------- #


def test_the_default_compose_file_is_not_the_versioned_one() -> None:
    """El default era `docker/docker-compose.yml`, que a propósito NO declara los
    servicios de aplicación (lo dice su propia cabecera). Con ese default el
    preflight aborta y `docker compose stop api-server` daría != 0.

    Además era una ruta RELATIVA: el resultado dependía del cwd del proceso.
    """
    default = _settings().restore_compose_file
    # PurePosixPath a propósito: el destino de despliegue es Linux, y en Windows
    # `WindowsPath("/data/...").is_absolute()` es False por no llevar unidad.
    assert PurePosixPath(default).is_absolute(), f"{default!r} es relativa: depende del cwd"
    assert PurePosixPath(default) != PurePosixPath("docker/docker-compose.yml")

    versioned = _declared_compose_services(_VERSIONED_COMPOSE)
    app_services = set(_settings().restore_app_services)
    assert not (app_services & versioned), (
        "si el compose versionado empieza a declarar los servicios de aplicación, "
        "revisa este test y el default de restore_compose_file"
    )


def test_the_default_compose_file_matches_where_the_installer_writes_it() -> None:
    """El instalador escribe el compose en `compose_dir`, que es el `data_root`
    (cli.py:793). El default del restore tiene que apuntar ahí."""
    settings = _settings()
    expected = PurePosixPath(settings.data_root) / "docker-compose.yml"
    assert PurePosixPath(settings.restore_compose_file) == expected, (
        f"restore_compose_file={settings.restore_compose_file!r} no coincide con "
        f"donde el instalador deja el compose ({expected})"
    )


# --------------------------------------------------------------------------- #
# El parser del preflight
# --------------------------------------------------------------------------- #


def test_declared_services_reads_a_real_compose(tmp_path: Path) -> None:
    path = tmp_path / "docker-compose.yml"
    path.write_text(
        yaml.safe_dump({"name": "x", "services": {"a": {"image": "i"}, "b": {"image": "j"}}}),
        encoding="utf-8",
    )
    assert _declared_compose_services(path) == {"a", "b"}


@pytest.mark.parametrize(
    "body",
    [
        "",  # vacío
        "no-soy: un compose\n",  # sin `services`
        "services: [esto, no, es, un, mapa]\n",  # `services` mal formado
        "services:\n  a: {image: i\n",  # YAML roto
    ],
)
def test_declared_services_returns_empty_instead_of_guessing(tmp_path: Path, body: str) -> None:
    """No verificable ≠ inválido: el motor lo registra y sigue, en vez de abortar
    un DR por no poder leer un fichero."""
    path = tmp_path / "docker-compose.yml"
    path.write_text(body, encoding="utf-8")
    assert _declared_compose_services(path) == set()


def test_declared_services_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert _declared_compose_services(tmp_path / "no-existe.yml") == set()
