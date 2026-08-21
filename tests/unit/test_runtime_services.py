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
    r = build_project_runtime_services(
        {"services": [{"type": "mysql", "version": "8.4", "alias": "db"}]}
    )
    svc = r.aux_services[0]
    assert svc.image == "mysql:8.4"
    assert svc.alias == "db"
    # connection env follows the alias
    assert r.main_env["DATABASE_URL"] == "mysql://app:app@db:3306/app"
    assert r.main_env["MYSQL_HOST"] == "db"


def test_custom_image_service_requires_alias_and_derives_no_conn_env() -> None:
    r = build_project_runtime_services(
        {"services": [{"image": "rabbitmq:3-management", "alias": "mq", "env": {"FOO": "bar"}}]}
    )
    svc = r.aux_services[0]
    assert svc.image == "rabbitmq:3-management"
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
        {"runtime_image": "agentic-platform/agent-runtime-php-phpunit:v1"}
    )
    assert r.runtime_image == "agentic-platform/agent-runtime-php-phpunit:v1"


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
