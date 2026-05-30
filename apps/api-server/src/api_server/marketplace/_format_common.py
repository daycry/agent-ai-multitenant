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
                f"'permissions' has unknown key {key!r}; " f"allowed: {', '.join(PERMISSION_KEYS)}"
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


__all__ = [
    "ErrFactory",
    "is_valid_semver",
    "parse_network_policy",
    "parse_permissions_block",
    "parse_str_list",
    "requested_permission_descriptors",
]
