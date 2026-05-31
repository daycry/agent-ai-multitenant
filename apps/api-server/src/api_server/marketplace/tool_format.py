"""Tool manifest format — parser + validator (Plan 09 task_09_10).

Plan 09 binding decision (Fase C): besides the SKILL.md skill format
(task_09_09), an installable **tool** ships a standard **YAML manifest**
describing the executable + an *implementation reference* the install flow
(task_09_11) resolves and the sandbox (task_09_06) runs. Where a skill is a
declarative Markdown capability, a tool is an executable function — so its
manifest is a plain YAML document (no Markdown body), with a typed
input/output schema and a pointer to the code that backs it.

A ``tool.yaml`` looks like::

    name: web-fetch
    version: 2.0.1
    description: Fetch a URL and return its body.
    kind: tool
    entrypoint: web_fetch.main:run
    implementation:
      runtime: python
      module: web_fetch.main
      reference: git+https://example.test/tools/web-fetch@v2.0.1
    dependencies:
      - httpx>=0.27
    permissions:
      allowed_domains: [api.example.test]
      network_policy: restricted
    input_schema:
      type: object
      properties:
        url: {type: string}
      required: [url]
    output_schema:
      type: object
      properties:
        status: {type: integer}
        body: {type: string}

This module turns that text into a typed :class:`ToolManifest` and fails
loudly with a typed :class:`ToolFormatError` on a malformed document, a
missing required field (``name`` / ``version`` / ``description`` /
``entrypoint`` / ``implementation``), a bad semver, a ``kind`` outside the
listing taxonomy, or a permission key / network posture outside the shared
vocabulary.

**Vocabulary reuse (no re-encoding):** semver parsing and the permission
vocabulary (``allowed_domains`` / ``allowed_paths`` / ``network_policy`` +
the ``none | restricted | open`` network posture) come from the shared
:mod:`api_server.marketplace._format_common` primitives — the SAME ones the
SKILL.md parser uses — so the two formats never drift. ``kind`` reuses
:class:`~api_server.db.marketplace.MarketplaceListingKind`. The parser
renders the declared permissions into the canonical ``{"type": ...,
"value": ...}`` descriptor list (:attr:`requested_permissions`) the install
+ consent flow already consumes, so a parsed manifest drops straight onto a
``marketplace_listings`` row.

Pure Python, no I/O, no new heavy dependency — PyYAML (already a project
dep) parses the document; semver is validated by the shared helper (the
real comparison/ordering lands in task_09_12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from api_server.db.marketplace import MarketplaceListingKind
from api_server.marketplace._format_common import (
    is_valid_semver,
    parse_permissions_block,
    requested_permission_descriptors,
)

# The required top-level manifest fields. A manifest missing any of these is
# not installable — fail loudly rather than synthesize a default. ``kind``
# is intentionally optional and defaults to ``tool`` (the format's reason
# for being); ``input_schema`` / ``output_schema`` / ``dependencies`` /
# ``permissions`` are optional.
REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "version",
    "description",
    "entrypoint",
    "implementation",
)


class ToolFormatError(ValueError):
    """A tool manifest is malformed or fails validation.

    Raised for: a manifest that is not a YAML mapping, a missing required
    field, a non-semver ``version``, a ``kind`` outside the listing
    taxonomy, a wrong-typed field, or a permission key / network posture
    outside the shared vocabulary.

    Subclasses :class:`ValueError` so existing ``except ValueError``
    handlers (and the routers' 422 mapping) keep working, while callers that
    care can catch the precise type.
    """


def _tool_err(message: str) -> ToolFormatError:
    """Error factory for the shared parsing helpers — prefixes ``tool manifest``.

    Passed to :mod:`api_server.marketplace._format_common` so the shared
    permission/semver validators raise this format's precise type with a
    tool-manifest-scoped message.
    """
    return ToolFormatError(f"tool manifest {message}")


@dataclass(frozen=True, slots=True)
class ToolImplementation:
    """The *implementation reference* — where the tool's code lives.

    The install flow (task_09_11) resolves ``reference`` (a git/url/registry
    pointer), loads ``runtime`` (e.g. ``python`` / ``node``), and the
    sandbox (task_09_06) executes the ``entrypoint`` against it. ``runtime``
    is required (the install flow must know which runtime template to use);
    ``module`` and ``reference`` are optional pointers captured verbatim.
    """

    runtime: str
    module: str | None = None
    reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render back to the JSONB shape persisted in the listing manifest."""
        out: dict[str, Any] = {"runtime": self.runtime}
        if self.module is not None:
            out["module"] = self.module
        if self.reference is not None:
            out["reference"] = self.reference
        return out


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """The typed, validated content of a tool manifest.

    ``frozen`` + ``slots`` so a parsed manifest is immutable and cheap.
    :meth:`to_manifest_dict` and :attr:`requested_permissions` render the
    shapes the ``marketplace_listings`` row (``manifest`` JSONB +
    ``requested_permissions`` JSONB) expects, so a parsed manifest installs
    without a translation layer.
    """

    name: str
    version: str
    description: str
    kind: MarketplaceListingKind
    entrypoint: str
    implementation: ToolImplementation
    dependencies: tuple[str, ...] = ()
    # JSON-Schema-ish maps captured verbatim (validated as mappings only —
    # the platform does not enforce a JSON Schema dialect here).
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    # The permissions block, already validated against the shared
    # vocabulary. Keys are a subset of PERMISSION_KEYS; values are whatever
    # the key carries (list[str] for domains/paths, a NetworkPolicy value
    # for network_policy).
    permissions: dict[str, Any] = field(default_factory=dict)

    @property
    def requested_permissions(self) -> list[dict[str, Any]]:
        """The declared permissions rendered as install/consent descriptors.

        Emits the canonical ``{"type": <PERMISSION_KEYS member>, "value":
        ...}`` shape the consent + install flow already consumes — one
        descriptor per declared permission key, in stable
        :data:`~api_server.marketplace.trust.PERMISSION_KEYS` order so the
        output is deterministic.
        """
        return requested_permission_descriptors(self.permissions)

    def to_manifest_dict(self) -> dict[str, Any]:
        """Render the JSONB ``manifest`` payload for a listings row.

        The machine-readable metadata (everything but the permissions, which
        live in their own ``requested_permissions`` column) so the install
        flow can persist it verbatim.
        """
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "kind": self.kind.value,
            "entrypoint": self.entrypoint,
            "implementation": self.implementation.to_dict(),
            "dependencies": list(self.dependencies),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
        }


def _require_str(data: dict[str, Any], key: str) -> str:
    """Pull a required, non-empty string field or raise ToolFormatError."""
    if key not in data or data[key] is None:
        raise ToolFormatError(f"tool manifest is missing required field: {key!r}")
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ToolFormatError(f"tool manifest field {key!r} must be a non-empty string")
    return value.strip()


def _parse_kind(raw: Any) -> MarketplaceListingKind:
    """Validate the optional ``kind`` against the listing taxonomy.

    Absent => ``tool`` (the format's reason for being). A value outside
    :class:`~api_server.db.marketplace.MarketplaceListingKind` is a hard
    error — an unknown kind must not slip onto a listing row.
    """
    if raw is None:
        return MarketplaceListingKind.TOOL
    if not isinstance(raw, str):
        raise ToolFormatError("tool manifest 'kind' must be a string")
    try:
        return MarketplaceListingKind(raw)
    except ValueError as exc:
        allowed = ", ".join(k.value for k in MarketplaceListingKind)
        raise ToolFormatError(f"tool manifest 'kind' must be one of: {allowed}") from exc


def _parse_implementation(raw: Any) -> ToolImplementation:
    """Validate + normalize the required ``implementation`` reference.

    Must be a mapping with a required, non-empty ``runtime`` string and
    optional ``module`` / ``reference`` strings — the pointers the install
    flow resolves. A non-mapping, a missing ``runtime``, or a wrong-typed
    pointer is a hard error.
    """
    if not isinstance(raw, dict):
        raise ToolFormatError("tool manifest 'implementation' must be a mapping")
    runtime = raw.get("runtime")
    if not isinstance(runtime, str) or not runtime.strip():
        raise ToolFormatError("tool manifest 'implementation.runtime' must be a non-empty string")
    module = _optional_str("implementation.module", raw.get("module"))
    reference = _optional_str("implementation.reference", raw.get("reference"))
    return ToolImplementation(
        runtime=runtime.strip(),
        module=module,
        reference=reference,
    )


def _optional_str(label: str, value: Any) -> str | None:
    """Validate an optional string pointer (absent/None => None)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolFormatError(f"tool manifest {label!r} must be a non-empty string")
    return value.strip()


def _parse_dependencies(raw: Any) -> tuple[str, ...]:
    """Normalize the optional ``dependencies`` field into a tuple of strings.

    Accepts a YAML list of strings (the documented form). Absent => empty.
    A non-list, or a list with a non-string entry, is a hard error — a
    garbled dependency spec must not silently install nothing.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ToolFormatError("tool manifest 'dependencies' must be a list of strings")
    deps: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ToolFormatError("tool manifest 'dependencies' entries must be non-empty strings")
        deps.append(item.strip())
    return tuple(deps)


def _parse_schema(label: str, raw: Any) -> dict[str, Any]:
    """Capture an optional ``input_schema`` / ``output_schema`` mapping.

    Validated as a mapping only (the platform does not enforce a JSON Schema
    dialect here — it persists the schema verbatim for the tooling that
    consumes it). Absent => empty mapping. A non-mapping is a hard error.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ToolFormatError(f"tool manifest {label!r} must be a mapping")
    return raw


def parse_tool_manifest(text: str) -> ToolManifest:
    """Parse + validate a tool manifest document into a :class:`ToolManifest`.

    Parses the YAML, validates the required fields + semver + ``kind`` +
    implementation reference + permission vocabulary, and returns the typed
    manifest. Raises :class:`ToolFormatError` for any structural or
    validation failure — the install flow (task_09_11) treats that as "not
    installable".
    """
    if not isinstance(text, str):
        raise ToolFormatError("tool manifest content must be text")

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ToolFormatError(f"tool manifest is not valid YAML: {exc}") from exc

    if data is None:
        raise ToolFormatError("tool manifest is empty")
    if not isinstance(data, dict):
        raise ToolFormatError("tool manifest must be a YAML mapping (key: value)")

    name = _require_str(data, "name")
    version = _require_str(data, "version")
    if not is_valid_semver(version):
        raise ToolFormatError(f"tool manifest 'version' is not a valid semver string: {version!r}")
    description = _require_str(data, "description")
    entrypoint = _require_str(data, "entrypoint")

    if "implementation" not in data or data["implementation"] is None:
        raise ToolFormatError("tool manifest is missing required field: 'implementation'")
    implementation = _parse_implementation(data["implementation"])

    kind = _parse_kind(data.get("kind"))
    dependencies = _parse_dependencies(data.get("dependencies"))
    input_schema = _parse_schema("input_schema", data.get("input_schema"))
    output_schema = _parse_schema("output_schema", data.get("output_schema"))
    permissions = parse_permissions_block(data.get("permissions"), _tool_err)

    return ToolManifest(
        name=name,
        version=version,
        description=description,
        kind=kind,
        entrypoint=entrypoint,
        implementation=implementation,
        dependencies=dependencies,
        input_schema=input_schema,
        output_schema=output_schema,
        permissions=permissions,
    )


__all__ = [
    "REQUIRED_FIELDS",
    "ToolFormatError",
    "ToolImplementation",
    "ToolManifest",
    "parse_tool_manifest",
]
