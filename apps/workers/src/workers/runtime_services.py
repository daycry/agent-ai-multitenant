"""Per-project runtime services + custom image (ADR 0129).

Turns a project's DECLARATIVE config (``repository_config.services`` /
``repository_config.env`` / ``repository_config.runtime_image``) into the objects
the test-runtime already knows how to launch:

* ``aux_services`` — a tuple of :class:`~workers.test_runtime.AuxServiceSpec`
  (hardened sidecars on the task's internal bridge, reachable by hostname).
* ``main_env`` — the connection variables (``DATABASE_URL``, ``REDIS_URL``,
  ``MYSQL_HOST`` …) derived from the declared services, merged with the
  project's own ``env`` (the project's explicit env wins), injected into the
  MAIN container so the app/tests find the services without hard-coding hosts.
* ``runtime_image`` — an optional project-supplied runtime image (the custom
  image escape hatch, ADR 0129 §2), which the resolver prefers over the catalog
  template when set.

No project code runs here — this only *shapes* config, so it is pure and
unit-testable. The sidecars themselves are launched (endurecidos) by
``TestRuntimeRunner._start_aux_services``; this module never touches Docker
beyond constructing the ``AuxServiceSpec`` data objects.

Security posture (ADR 0129 / CLAUDE.md §2): only an allowlisted set of service
TYPES is offered, plus an arbitrary IMAGE service for advanced cases; every
sidecar runs under the existing hardened envelope (cap-drop ALL,
no-new-privileges, mem/pids caps, internal bridge). Credentials are fixed test
values surfaced through the connection env — ``repository_config.env`` is NOT
Vault, so do not put production secrets there.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from workers.test_runtime import AuxServiceSpec

# Bounds + shapes.
_MAX_SERVICES = 8
_MAX_ENV_VALUE = 512
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")  # a valid, short docker hostname
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# A permissive-but-safe OCI image reference: optional registry host, repo path,
# optional :tag / @digest. No whitespace, no shell metacharacters.
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$")

# Fixed test credentials for the catalog DB services. Consistent between the
# sidecar env and the derived connection env so the app connects out of the box.
_DB_USER = "app"
_DB_PASSWORD = "app"
_DB_NAME = "app"


class RuntimeServicesConfigError(ValueError):
    """A project's ``repository_config`` services/env/runtime_image is invalid
    (unknown type, unsafe alias/env, bad image ref, too many services). The
    caller surfaces it as an actionable message instead of a crash."""


@dataclass(frozen=True)
class _ServiceType:
    """One allowlisted service in the catalog."""

    default_image: str
    default_alias: str
    service_env: Mapping[str, str]
    healthcheck_cmd: tuple[str, ...] | None
    mem_limit: str | None
    # alias -> connection env injected into the MAIN container
    conn_env: Callable[[str], dict[str, str]]


def _mysql_conn(alias: str) -> dict[str, str]:
    return {
        "DATABASE_URL": f"mysql://{_DB_USER}:{_DB_PASSWORD}@{alias}:3306/{_DB_NAME}",
        "MYSQL_HOST": alias,
        "MYSQL_PORT": "3306",
        "MYSQL_DATABASE": _DB_NAME,
        "MYSQL_USER": _DB_USER,
        "MYSQL_PASSWORD": _DB_PASSWORD,
    }


def _postgres_conn(alias: str) -> dict[str, str]:
    return {
        "DATABASE_URL": f"postgresql://{_DB_USER}:{_DB_PASSWORD}@{alias}:5432/{_DB_NAME}",
        "PGHOST": alias,
        "PGPORT": "5432",
        "PGDATABASE": _DB_NAME,
        "PGUSER": _DB_USER,
        "PGPASSWORD": _DB_PASSWORD,
    }


def _redis_conn(alias: str) -> dict[str, str]:
    return {"REDIS_URL": f"redis://{alias}:6379/0", "REDIS_HOST": alias, "REDIS_PORT": "6379"}


def _beanstalkd_conn(alias: str) -> dict[str, str]:
    return {"BEANSTALKD_HOST": alias, "BEANSTALKD_PORT": "11300"}


_MYSQL_ENV = {
    "MYSQL_DATABASE": _DB_NAME,
    "MYSQL_USER": _DB_USER,
    "MYSQL_PASSWORD": _DB_PASSWORD,
    "MYSQL_ROOT_PASSWORD": "root",
}
_PG_ENV = {"POSTGRES_USER": _DB_USER, "POSTGRES_PASSWORD": _DB_PASSWORD, "POSTGRES_DB": _DB_NAME}

# ---------------------------------------------------------------------------
# El catálogo permitido de servicios (ADR 0129 §1). Añadir un tipo es un cambio
# de código.
#
# FIJADOS POR DIGEST (prod-11 task_digest_pin_11), 2026-08-19. La casilla nombraba
# sólo `DEFAULT_POSTGRES`/`DEFAULT_REDIS` de test_runtime.py; esta es la MISMA
# superficie por otra puerta —lo que un proyecto declara en su config acaba en los
# mismos `AuxServiceSpec`, en el mismo bridge per-tarea, junto al mismo código no
# confiable— y pinear sólo la otra habría sido cobertura aparente.
#
# El peor caso del sistema por este criterio vivía aquí: `schickling/beanstalkd`
# iba por **`latest`**, un tag rodante de una imagen de tercero sin OCI
# annotations. Un `docker pull` podía cambiar el binario que corre al lado del
# agente sin que cambiara una línea del repo.
#
# El tag legible va DENTRO de la referencia; los digests son del índice
# multi-arch donde lo hay (`docker buildx imagetools inspect`). El `default_image`
# es sólo el DEFAULT: un proyecto puede seguir declarando su propia `image`.
# Refresco y calendario: docs/06-runbooks/triage-vulnerabilidades.md §6, la misma
# revisión manual que los dos de test_runtime.py.
#
# review: 2026-11-19
# ---------------------------------------------------------------------------
SERVICE_CATALOG: dict[str, _ServiceType] = {
    "mysql": _ServiceType(
        # mysql:8 == 8.4.11 (resuelto 2026-08-19)
        default_image="mysql:8@sha256:b3b90af2a6552ae30c266fdb7d5dd55f3afb72404bb78d37fe8a23eb857fd3fb",
        default_alias="mysql",
        service_env=_MYSQL_ENV,
        healthcheck_cmd=("mysqladmin", "ping", "-h", "127.0.0.1", "-uroot", "-proot"),
        mem_limit="512m",
        conn_env=_mysql_conn,
    ),
    "mariadb": _ServiceType(
        # mariadb:11 == 11.8.8-noble (resuelto 2026-08-19)
        default_image="mariadb:11@sha256:1fe78d53850250aa2560aa0059e3088b4cd230a5db2230f530c70e4b87bcc30c",
        default_alias="mariadb",
        service_env=_MYSQL_ENV,
        healthcheck_cmd=("healthcheck.sh", "--connect", "--innodb_initialized"),
        mem_limit="512m",
        conn_env=_mysql_conn,
    ),
    "postgres": _ServiceType(
        # postgres:16-alpine == 16.15-alpine3.24 (resuelto 2026-08-19)
        default_image="postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
        default_alias="postgres",
        service_env=_PG_ENV,
        healthcheck_cmd=("pg_isready", "-U", _DB_USER, "-d", _DB_NAME),
        mem_limit="256m",
        conn_env=_postgres_conn,
    ),
    "redis": _ServiceType(
        # redis:7-alpine == 7.4.10-alpine (resuelto 2026-08-19)
        default_image="redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2",
        default_alias="redis",
        service_env={},
        healthcheck_cmd=("redis-cli", "ping"),
        mem_limit="128m",
        conn_env=_redis_conn,
    ),
    "beanstalkd": _ServiceType(
        # El único `:latest` que quedaba, y de una imagen de tercero: sin
        # annotations OCI no publica versión, así que el digest ES la única
        # identificación que tiene. Resuelto 2026-08-19.
        default_image="schickling/beanstalkd:latest@sha256:19a928e3563973219e44f6c29df2a71103c7db894692ae94b7d0760837877e73",
        default_alias="beanstalkd",
        service_env={},
        healthcheck_cmd=None,  # no shell/health tool in the image
        mem_limit="128m",
        conn_env=_beanstalkd_conn,
    ),
}


@dataclass(frozen=True)
class ProjectRuntimeServices:
    """The resolved runtime-services config for one project."""

    aux_services: tuple[AuxServiceSpec, ...] = ()
    main_env: dict[str, str] = field(default_factory=dict)
    runtime_image: str | None = None
    # `task_cv_26` (auditoría 2026-09-01, B-06): el preview de review monta el
    # worktree del plan en SÓLO LECTURA por defecto; lo que la app necesite
    # escribir se declara y se monta como tmpfs; el RW completo es un opt-in.
    preview_workspace_rw: bool = False
    preview_writable_paths: tuple[str, ...] = ()


def _validate_env(raw: Any, *, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RuntimeServicesConfigError(f"{where} must be an object of string env vars")
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = str(k)
        if not _ENV_KEY_RE.match(key):
            raise RuntimeServicesConfigError(
                f"{where}: env var name {key!r} must match [A-Z][A-Z0-9_]*"
            )
        val = str(v)
        if len(val) > _MAX_ENV_VALUE:
            raise RuntimeServicesConfigError(f"{where}: env var {key!r} value too long")
        out[key] = val
    return out


def _image_from_version(default_image: str, version: Any) -> str:
    if version is None or str(version).strip() == "":
        return default_image
    repo = default_image.split(":", 1)[0]
    tag = str(version).strip()
    image = f"{repo}:{tag}"
    if not _IMAGE_RE.match(image):
        raise RuntimeServicesConfigError(f"invalid service version {version!r}")
    return image


def _parse_one_service(entry: Any, index: int, aliases_seen: set[str]) -> AuxServiceSpec:
    where = f"services[{index}]"
    if not isinstance(entry, dict):
        raise RuntimeServicesConfigError(f"{where} must be an object")
    svc_type = entry.get("type")
    image = entry.get("image")
    env = _validate_env(entry.get("env"), where=where)

    if svc_type:
        if str(svc_type) not in SERVICE_CATALOG:
            known = ", ".join(sorted(SERVICE_CATALOG))
            raise RuntimeServicesConfigError(
                f"{where}: unknown service type {svc_type!r}; known: {known}"
            )
        spec_type = SERVICE_CATALOG[str(svc_type)]
        alias = str(entry.get("alias") or spec_type.default_alias)
        resolved_image = _image_from_version(spec_type.default_image, entry.get("version"))
        service_env = {**dict(spec_type.service_env), **env}  # project env may extend
        healthcheck = spec_type.healthcheck_cmd
        mem_limit = spec_type.mem_limit
    elif image:
        # Arbitrary-image service (advanced): the project supplies the image +
        # its own env; no connection env is derived (the project sets it via the
        # top-level `env`, e.g. a full connection string).
        if not isinstance(image, str) or not _IMAGE_RE.match(image):
            raise RuntimeServicesConfigError(f"{where}: invalid image reference {image!r}")
        alias = str(entry.get("alias") or "")
        if not alias:
            raise RuntimeServicesConfigError(f"{where}: an image service requires an 'alias'")
        resolved_image = image
        service_env = env
        healthcheck = None
        mem_limit = None
    else:
        raise RuntimeServicesConfigError(f"{where} must set either 'type' or 'image'")

    if not _ALIAS_RE.match(alias):
        raise RuntimeServicesConfigError(
            f"{where}: alias {alias!r} must match [a-z][a-z0-9-]* (a valid short hostname)"
        )
    if alias in aliases_seen:
        raise RuntimeServicesConfigError(f"{where}: duplicate alias {alias!r}")
    aliases_seen.add(alias)

    return AuxServiceSpec(
        name=alias,
        image=resolved_image,
        alias=alias,
        env=service_env,
        healthcheck_cmd=healthcheck,
        mem_limit=mem_limit,
    )


def _connection_env(entry: Any) -> dict[str, str]:
    """Derived connection env for a CATALOG service (empty for image services)."""
    if not isinstance(entry, dict):
        return {}
    svc_type = entry.get("type")
    if not svc_type or str(svc_type) not in SERVICE_CATALOG:
        return {}
    spec_type = SERVICE_CATALOG[str(svc_type)]
    alias = str(entry.get("alias") or spec_type.default_alias)
    return spec_type.conn_env(alias)


def build_project_runtime_services(
    repository_config: Mapping[str, Any] | None,
) -> ProjectRuntimeServices:
    """Resolve a project's declared runtime services + env + custom image.

    Reads ``services`` (list), ``env`` (dict) and ``runtime_image`` (str) from
    ``repository_config``. Returns hardened ``AuxServiceSpec`` sidecars, the
    ``main_env`` (derived connection vars overlaid by the project's own env) and
    the optional custom ``runtime_image``. Raises
    :class:`RuntimeServicesConfigError` on any invalid entry — never guesses.
    """
    if not repository_config:
        return ProjectRuntimeServices()
    raw_services = repository_config.get("services") or []
    if not isinstance(raw_services, list):
        raise RuntimeServicesConfigError("repository_config.services must be a list")
    if len(raw_services) > _MAX_SERVICES:
        raise RuntimeServicesConfigError(
            f"too many services ({len(raw_services)} > {_MAX_SERVICES})"
        )

    aliases_seen: set[str] = set()
    aux: list[AuxServiceSpec] = []
    main_env: dict[str, str] = {}
    for i, entry in enumerate(raw_services):
        aux.append(_parse_one_service(entry, i, aliases_seen))
        # connection env in declaration order; later same-type wins DATABASE_URL.
        main_env.update(_connection_env(entry))

    # The project's explicit env wins over derived connection vars.
    main_env.update(_validate_env(repository_config.get("env"), where="repository_config.env"))

    runtime_image = repository_config.get("runtime_image")
    if runtime_image is not None:
        runtime_image = str(runtime_image).strip() or None
        if runtime_image and not _IMAGE_RE.match(runtime_image):
            raise RuntimeServicesConfigError(f"invalid runtime_image reference {runtime_image!r}")

    preview_rw, writable_paths = _parse_preview(repository_config.get("preview"))
    return ProjectRuntimeServices(
        aux_services=tuple(aux),
        main_env=main_env,
        runtime_image=runtime_image,
        preview_workspace_rw=preview_rw,
        preview_writable_paths=writable_paths,
    )


_MAX_WRITABLE_PATHS = 16


def _parse_preview(raw: Any) -> tuple[bool, tuple[str, ...]]:
    """``repository_config.preview`` → ``(workspace_rw, writable_paths)`` (`task_cv_26`).

    ``writable_paths`` son rutas RELATIVAS al worktree, normalizadas, sin ``..``
    ni componentes vacíos: cada una se monta como tmpfs sobre el bind de sólo
    lectura, así que tiene que existir en el repositorio (Docker no crea el
    punto de montaje dentro de un bind RO)."""
    if raw is None:
        return False, ()
    if not isinstance(raw, Mapping):
        raise RuntimeServicesConfigError("repository_config.preview must be a mapping")
    workspace_rw = raw.get("workspace_rw", False)
    if not isinstance(workspace_rw, bool):
        raise RuntimeServicesConfigError("repository_config.preview.workspace_rw must be a bool")
    paths_raw = raw.get("writable_paths") or []
    if not isinstance(paths_raw, list):
        raise RuntimeServicesConfigError("repository_config.preview.writable_paths must be a list")
    if len(paths_raw) > _MAX_WRITABLE_PATHS:
        raise RuntimeServicesConfigError(
            f"too many preview.writable_paths ({len(paths_raw)} > {_MAX_WRITABLE_PATHS})"
        )
    cleaned: list[str] = []
    for entry in paths_raw:
        if not isinstance(entry, str) or not entry.strip() or "\x00" in entry:
            raise RuntimeServicesConfigError(f"invalid preview.writable_paths entry {entry!r}")
        text = entry.strip().replace("\\", "/")
        if text.startswith("/"):
            raise RuntimeServicesConfigError(
                f"preview.writable_paths entry {entry!r} must be relative to the workspace"
            )
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise RuntimeServicesConfigError(
                f"preview.writable_paths entry {entry!r} escapes the workspace"
            )
        normalized = "/".join(parts)
        if normalized not in cleaned:
            cleaned.append(normalized)
    return workspace_rw, tuple(cleaned)


__all__ = [
    "SERVICE_CATALOG",
    "ProjectRuntimeServices",
    "RuntimeServicesConfigError",
    "build_project_runtime_services",
]
