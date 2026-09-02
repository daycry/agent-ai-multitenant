"""ADR 0129 — per-project runtime services + custom image.

Unit tests for the pure translation ``build_project_runtime_services``:
declared services → hardened AuxServiceSpec sidecars + derived connection env
(DATABASE_URL/REDIS_URL/…) + optional custom runtime image, with validation
that refuses unknown types, unsafe aliases/env, bad image refs and duplicates.
"""

from __future__ import annotations

import pytest
from workers.runtime_services import (
    SERVICE_CATALOG,
    RuntimeServicesConfigError,
    build_project_runtime_services,
)

pytestmark = pytest.mark.unit

_DIGEST = "sha256:" + "ab" * 32


def test_empty_config_yields_nothing() -> None:
    for cfg in (None, {}, {"language": "php"}):
        r = build_project_runtime_services(cfg)
        assert r.aux_services == ()
        assert r.main_env == {}
        assert r.runtime_image is None


def test_mysql_service_sidecar_and_connection_env() -> None:
    r = build_project_runtime_services({"services": [{"type": "mysql"}]})
    assert len(r.aux_services) == 1
    svc = r.aux_services[0]
    # Derivado del catálogo, no el literal `mysql:8`: lo que este test comprueba
    # es que el tipo `mysql` resuelve a SU imagen, no cuál es la versión. Fijar el
    # literal convertía el pin por digest (prod-11 task_digest_pin_11) en un rojo
    # ajeno al comportamiento bajo prueba.
    assert svc.image == SERVICE_CATALOG["mysql"].default_image
    assert svc.image.startswith("mysql:")
    assert svc.alias == "mysql"
    assert svc.env["MYSQL_USER"] == "app"
    assert svc.healthcheck_cmd  # has a healthcheck
    assert r.main_env["DATABASE_URL"] == "mysql://app:app@mysql:3306/app"
    assert r.main_env["MYSQL_HOST"] == "mysql"
    assert r.main_env["MYSQL_PORT"] == "3306"


def test_postgres_redis_beanstalkd_connection_env() -> None:
    r = build_project_runtime_services(
        {"services": [{"type": "postgres"}, {"type": "redis"}, {"type": "beanstalkd"}]}
    )
    assert {s.alias for s in r.aux_services} == {"postgres", "redis", "beanstalkd"}
    assert r.main_env["DATABASE_URL"] == "postgresql://app:app@postgres:5432/app"
    assert r.main_env["PGHOST"] == "postgres"
    assert r.main_env["REDIS_URL"] == "redis://redis:6379/0"
    assert r.main_env["BEANSTALKD_HOST"] == "beanstalkd"
    assert r.main_env["BEANSTALKD_PORT"] == "11300"
    # beanstalkd image has no healthcheck tool
    bs = next(s for s in r.aux_services if s.alias == "beanstalkd")
    assert bs.healthcheck_cmd is None


def test_version_and_alias_overrides() -> None:
    """`task_cv_44`: la `version` ya no recompone `repo:tag` (que deshacía el pin
    por digest del sidecar): resuelve contra el mapa versión→imagen pineada."""
    r = build_project_runtime_services(
        {"services": [{"type": "mysql", "version": "8", "alias": "db"}]}
    )
    svc = r.aux_services[0]
    assert svc.image == SERVICE_CATALOG["mysql"].default_image
    assert "@sha256:" in svc.image, "la versión deshizo el pin por digest"
    assert svc.alias == "db"
    # connection env follows the alias
    assert r.main_env["DATABASE_URL"] == "mysql://app:app@db:3306/app"
    assert r.main_env["MYSQL_HOST"] == "db"


def test_custom_image_service_requires_alias_and_derives_no_conn_env() -> None:
    r = build_project_runtime_services(
        {
            "services": [
                {
                    "image": f"rabbitmq:3-management@{_DIGEST}",
                    "alias": "mq",
                    "env": {"FOO": "bar"},
                }
            ]
        }
    )
    svc = r.aux_services[0]
    assert svc.image == f"rabbitmq:3-management@{_DIGEST}"
    assert svc.alias == "mq"
    assert svc.env == {"FOO": "bar"}
    assert r.main_env == {}  # no connection env derived for arbitrary images


def test_project_env_overrides_derived_connection_env() -> None:
    r = build_project_runtime_services(
        {
            "services": [{"type": "mysql"}],
            "env": {"DATABASE_URL": "mysql://custom@db/x", "APP_KEY": "z"},
        }
    )
    assert r.main_env["DATABASE_URL"] == "mysql://custom@db/x"  # project wins
    assert r.main_env["APP_KEY"] == "z"
    assert r.main_env["MYSQL_HOST"] == "mysql"  # non-overridden derived var kept


def test_runtime_image_override_is_returned() -> None:
    r = build_project_runtime_services(
        {"runtime_image": f"ghcr.io/acme/agent-runtime-php-phpunit:v1@{_DIGEST}"}
    )
    assert r.runtime_image == f"ghcr.io/acme/agent-runtime-php-phpunit:v1@{_DIGEST}"


def test_two_of_same_type_keep_distinct_aliases() -> None:
    r = build_project_runtime_services(
        {"services": [{"type": "redis", "alias": "cache"}, {"type": "redis", "alias": "queue"}]}
    )
    assert {s.alias for s in r.aux_services} == {"cache", "queue"}


@pytest.mark.parametrize(
    "cfg",
    [
        {"services": [{"type": "oraclexe"}]},  # unknown type
        {"services": [{"type": "mysql", "alias": "Bad Alias"}]},  # bad alias
        {"services": [{"type": "mysql", "alias": "a"}, {"type": "redis", "alias": "a"}]},  # dup
        {"services": [{"image": "x:1"}]},  # image without alias
        {"services": [{"image": "bad image ref", "alias": "svc"}]},  # bad image ref
        {"services": [{}]},  # neither type nor image
        {"services": {"not": "a list"}},  # services not a list
        {"services": [{"type": "mysql", "env": {"bad-key": "v"}}]},  # bad env key
        {"runtime_image": "has spaces :("},  # bad image ref
    ],
)
def test_invalid_configs_raise(cfg: dict) -> None:
    with pytest.raises(RuntimeServicesConfigError):
        build_project_runtime_services(cfg)


def test_too_many_services_rejected() -> None:
    cfg = {"services": [{"type": "redis", "alias": f"r{i}"} for i in range(9)]}
    with pytest.raises(RuntimeServicesConfigError):
        build_project_runtime_services(cfg)


# --------------------------------------------------------------- task_cv_44
# Auditoría 2026-09-01 (B-05, B-09): `image:` aceptaba cualquier `host/repo:tag`
# y `version` deshacía el pin por digest de los sidecars del catálogo. Ahora una
# imagen del tenant lleva `@sha256:` o viene de un registry de la allowlist, y
# una versión del catálogo resuelve contra un mapa versión→imagen pineada.


def test_an_unknown_catalog_version_is_rejected_naming_the_pinned_ones() -> None:
    with pytest.raises(RuntimeServicesConfigError) as excinfo:
        build_project_runtime_services({"services": [{"type": "mysql", "version": "8.4"}]})
    message = str(excinfo.value)
    assert "8.4" in message and "pinned" in message and "8" in message


def test_an_unpinned_custom_image_is_rejected_by_default() -> None:
    with pytest.raises(RuntimeServicesConfigError) as excinfo:
        build_project_runtime_services(
            {"services": [{"image": "ghcr.io/acme/tool:1.2", "alias": "tool"}]}
        )
    assert "sha256" in str(excinfo.value)


@pytest.mark.parametrize("allowlist", [("ghcr.io",), ("ghcr.io/acme",)])
def test_an_unpinned_image_from_an_allowlisted_registry_is_accepted(allowlist: tuple) -> None:
    r = build_project_runtime_services(
        {"services": [{"image": "ghcr.io/acme/tool:1.2", "alias": "tool"}]},
        image_registry_allowlist=allowlist,
    )
    assert r.aux_services[0].image == "ghcr.io/acme/tool:1.2"


def test_the_allowlist_matches_whole_path_segments_only() -> None:
    with pytest.raises(RuntimeServicesConfigError):
        build_project_runtime_services(
            {"services": [{"image": "ghcr.io/acme-evil/tool:1.2", "alias": "tool"}]},
            image_registry_allowlist=("ghcr.io/acme",),
        )


def test_an_image_without_registry_is_docker_hub_and_needs_docker_io_allowlisted() -> None:
    with pytest.raises(RuntimeServicesConfigError):
        build_project_runtime_services(
            {"services": [{"image": "rabbitmq:3", "alias": "mq"}]},
            image_registry_allowlist=("ghcr.io",),
        )
    r = build_project_runtime_services(
        {"services": [{"image": "rabbitmq:3", "alias": "mq"}]},
        image_registry_allowlist=("docker.io",),
    )
    assert r.aux_services[0].image == "rabbitmq:3"


def test_the_runtime_image_override_follows_the_same_rule() -> None:
    with pytest.raises(RuntimeServicesConfigError):
        build_project_runtime_services(
            {"runtime_image": "agentic-platform/agent-runtime-php-phpunit:v1"}
        )
    r = build_project_runtime_services(
        {"runtime_image": "ghcr.io/acme/agent-runtime-php-phpunit:v1"},
        image_registry_allowlist=("ghcr.io/acme",),
    )
    assert r.runtime_image == "ghcr.io/acme/agent-runtime-php-phpunit:v1"


def test_every_catalog_sidecar_is_pinned_by_digest() -> None:
    for name, spec_type in SERVICE_CATALOG.items():
        assert "@sha256:" in spec_type.default_image, f"{name} sin pin"
        for version, image in spec_type.pinned_versions().items():
            assert "@sha256:" in image, f"{name}:{version} sin pin"
