"""Shared parsing primitives for the installable *formats* (Plan 09).

Both installable formats — the SKILL.md skill format (task_09_09,
:mod:`api_server.marketplace.skill_format`) and the YAML tool manifest
(task_09_10, :mod:`api_server.marketplace.tool_format`) — speak the same
two sub-languages:

  * **semver** for the ``version`` field, and
  * the shared **permission vocabulary** (``allowed_domains`` /
    ``allowed_paths`` / ``network_policy``) from
    :mod:`api_server.marketplace.trust`.

This module is the single home for those two so the format parsers do not
re-encode the semver regex or re-implement permission validation. Each
parser owns its own typed error class (``SkillFormatError`` /
``ToolFormatError``); the helpers here take an ``err`` *factory*
(``Callable[[str], Exception]``) so the message carries the calling
format's name and the raised type is the format's own. Nothing here does
I/O — pure, importable anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from api_server.marketplace.trust import (
    PERMISSION_ALLOWED_DOMAINS,
    PERMISSION_ALLOWED_PATHS,
    PERMISSION_KEYS,
    PERMISSION_NETWORK_POLICY,
    NetworkPolicy,
)

# An error *factory*: a parser passes its own ``XFormatError`` constructor,
# so the shared helpers raise the calling format's precise type.
ErrFactory = Callable[[str], Exception]

# Semver (https://semver.org) — MAJOR.MINOR.PATCH with optional
# ``-prerelease`` and ``+build``. Anchored: the whole string must be a
# version. Ordering / range matching is task_09_12; here we only reject a
# version that could never be parsed. ONE regex for every format.
_SEMVER_RE = re.compile(
    r"\A(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z"
)


def is_valid_semver(value: str) -> bool:
    """True when ``value`` is a syntactically valid semver string.

    Syntax only — no ordering. The comparison/range logic is task_09_12;
    here we only reject a version that could never be parsed.
    """
    return bool(_SEMVER_RE.match(value))


def parse_permissions_block(raw: Any, err: ErrFactory) -> dict[str, Any]:
    """Validate + normalize a ``permissions`` mapping against the shared vocab.

    Keys must be a subset of :data:`~api_server.marketplace.trust.PERMISSION_KEYS`;
    any other key raises ``err`` (an unknown permission must never slip
    through to the consent gate). Value shapes are validated per key:

      * ``allowed_domains`` / ``allowed_paths`` — a list of non-empty
        strings (a bare string is coerced to a one-element list);
      * ``network_policy`` — one of the :class:`NetworkPolicy` values
        (``none`` / ``restricted`` / ``open``).

    Absent (``None``) => no permissions requested (most restrictive default).
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise err("'permissions' must be a mapping")

    permissions: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in PERMISSION_KEYS:
            raise err(
                f"'permissions' has unknown key {key!r}; allowed: {', '.join(PERMISSION_KEYS)}"
            )
        if key in (PERMISSION_ALLOWED_DOMAINS, PERMISSION_ALLOWED_PATHS):
            permissions[key] = parse_str_list(key, value, err)
        elif key == PERMISSION_NETWORK_POLICY:
            permissions[key] = parse_network_policy(value, err)
    return permissions


def parse_str_list(key: str, value: Any, err: ErrFactory) -> list[str]:
    """Normalize a permission value into a list of non-empty strings."""
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise err(f"permission {key!r} must be a string or list of strings")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise err(f"permission {key!r} entries must be non-empty strings")
        out.append(item.strip())
    return out


def parse_network_policy(value: Any, err: ErrFactory) -> str:
    """Validate a ``network_policy`` value against the shared vocabulary."""
    if not isinstance(value, str):
        raise err("permission 'network_policy' must be a string")
    try:
        return NetworkPolicy(value).value
    except ValueError as exc:
        allowed = ", ".join(p.value for p in NetworkPolicy)
        raise err(f"permission 'network_policy' must be one of: {allowed}") from exc


def requested_permission_descriptors(permissions: dict[str, Any]) -> list[dict[str, Any]]:
    """Render a validated permissions map as install/consent descriptors.

    Emits the canonical ``{"type": <PERMISSION_KEYS member>, "value": ...}``
    shape the consent + install flow already consumes — one descriptor per
    declared permission key, in stable :data:`PERMISSION_KEYS` order so the
    output is deterministic.
    """
    return [
        {"type": key, "value": permissions[key]} for key in PERMISSION_KEYS if key in permissions
    ]


# ---------------------------------------------------------------------------
# Los dos campos OPCIONALES que el manifest v2 gana (ADR 0142). Viven aquí,
# compartidos, por el mismo motivo que el vocabulario de permisos: dos parsers
# que validan lo mismo por su cuenta acaban divergiendo.
#
# Retro-compatibilidad, y es un requisito, no una cortesía: un manifest SIN
# estos campos sigue siendo válido. Sin `targets` no se pre-marca ningún rol;
# sin `config_schema` el despliegue no muestra formulario.
# ---------------------------------------------------------------------------

#: Tipos admitidos por el dialecto de `config_schema` (ver
#: :mod:`api_server.marketplace.config_schema` para el porqué de no usar JSON
#: Schema entero).
CONFIG_SCHEMA_TYPES: frozenset[str] = frozenset(
    {"string", "integer", "number", "boolean", "array", "object"}
)


def parse_targets(raw: Any, err: ErrFactory) -> tuple[str, ...]:
    """Valida el `targets` opcional: roles de agente SUGERIDOS por el manifest.

    El manifest sugiere y quien despliega confirma o ajusta (decisión D5), así
    que esto no autoriza nada: solo pre-marca casillas. Pero se valida contra el
    vocabulario cerrado :class:`~api_server.db.domain.AgentRole` porque un rol
    mal escrito (``backend-dev`` por ``backend_dev``) no da error en ningún sitio
    — simplemente no casa con ningún agente, y el despliegue «funciona» sin
    entregar nada. Ése es justo el modo de fallo silencioso que este plan
    existe para cerrar.

    Absent/``None`` => tupla vacía (sin sugerencia).
    """
    if raw is None:
        return ()
    from api_server.db.domain import AgentRole

    if isinstance(raw, str):  # un rol suelto es una selección de uno
        raw = [raw]
    if not isinstance(raw, list):
        raise err("'targets' must be a list of agent role names")
    allowed = {r.value for r in AgentRole}
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise err("'targets' entries must be non-empty strings")
        role = item.strip()
        if role not in allowed:
            raise err(
                f"'targets' has unknown agent role {role!r}; allowed: " + ", ".join(sorted(allowed))
            )
        if role not in out:  # de-dupe conservando el orden declarado
            out.append(role)
    return tuple(out)


def _check_config_property(name: Any, spec: Any, err: ErrFactory) -> None:
    """Una propiedad del `config_schema`: nombre, `type` y `secret` bien formados.

    Extraído de :func:`parse_config_schema` para que esa función quepa en el
    límite de ramas de ruff sin bajar la cobertura de la validación.
    """
    if not isinstance(name, str) or not name.strip():
        raise err("'config_schema.properties' keys must be non-empty strings")
    if not isinstance(spec, dict):
        raise err(f"'config_schema.properties.{name}' must be a mapping")
    declared = spec.get("type")
    if declared is not None and (
        not isinstance(declared, str) or declared not in CONFIG_SCHEMA_TYPES
    ):
        raise err(
            f"'config_schema.properties.{name}.type' must be one of: "
            + ", ".join(sorted(CONFIG_SCHEMA_TYPES))
        )
    if "secret" in spec and not isinstance(spec["secret"], bool):
        raise err(f"'config_schema.properties.{name}.secret' must be a boolean")


def parse_config_schema(raw: Any, err: ErrFactory) -> dict[str, Any]:
    """Valida el `config_schema` opcional (el descriptor del formulario guiado).

    Se comprueba la ESTRUCTURA, no los valores (los valores son cosa del
    despliegue, :func:`api_server.marketplace.config_schema.validate_deployment_config`):

      * documento mapping con `properties` mapping;
      * cada propiedad es un mapping y su `type`, si lo declara, está en
        :data:`CONFIG_SCHEMA_TYPES`;
      * `required`, si está, es una lista de strings que EXISTEN en
        `properties` — un requerido que no se declara es un formulario
        imposible de rellenar;
      * `secret`, si está, es booleano.

    Absent/``None`` => mapping vacío (esta capacidad no pide configuración).
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise err("'config_schema' must be a mapping")

    properties = raw.get("properties")
    if properties is None:
        properties = {}
    if not isinstance(properties, dict):
        raise err("'config_schema.properties' must be a mapping")

    for name, spec in properties.items():
        _check_config_property(name, spec, err)

    required = raw.get("required")
    if required is not None:
        if not isinstance(required, list):
            raise err("'config_schema.required' must be a list of field names")
        for item in required:
            if not isinstance(item, str) or not item.strip():
                raise err("'config_schema.required' entries must be non-empty strings")
            if item not in properties:
                raise err(
                    f"'config_schema.required' names {item!r}, which is not declared in"
                    " 'properties'"
                )

    return raw


__all__ = [
    "CONFIG_SCHEMA_TYPES",
    "ErrFactory",
    "is_valid_semver",
    "parse_config_schema",
    "parse_network_policy",
    "parse_permissions_block",
    "parse_str_list",
    "parse_targets",
    "requested_permission_descriptors",
]
