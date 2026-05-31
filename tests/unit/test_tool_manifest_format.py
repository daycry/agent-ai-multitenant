"""Unit tests for the tool manifest format (Plan 09 task_09_10).

Pins the binding Plan 09 decision into executable assertions: an
installable *tool* ships a standard YAML manifest (name / version /
description / kind / entrypoint / implementation ref / declared permissions
/ input+output schema / dependencies), and the parser produces a typed
:class:`ToolManifest` whose permission vocabulary + semver parsing are the
SHARED ones from :mod:`api_server.marketplace._format_common` /
:mod:`api_server.marketplace.trust` (no per-format re-encoding).

Pure-Python, no DB / Docker / network: the parser is text in, dataclass
out. (No ``cross_tenant`` marker: this module touches no tenant-owned rows;
the multi-tenancy guarantee is unaffected.)
"""

from __future__ import annotations

import textwrap

import pytest
from api_server.db.marketplace import MarketplaceListingKind
from api_server.marketplace._format_common import is_valid_semver
from api_server.marketplace.tool_format import (
    REQUIRED_FIELDS,
    ToolFormatError,
    ToolImplementation,
    ToolManifest,
    parse_tool_manifest,
)
from api_server.marketplace.trust import PERMISSION_KEYS, NetworkPolicy

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
VALID_TOOL_YAML = textwrap.dedent(
    """\
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
      - tenacity
    permissions:
      allowed_domains:
        - api.example.test
        - cdn.example.test
      allowed_paths:
        - /workspace/cache
      network_policy: restricted
    input_schema:
      type: object
      properties:
        url:
          type: string
      required:
        - url
    output_schema:
      type: object
      properties:
        status:
          type: integer
        body:
          type: string
    """
)


def _minimal(**overrides: str) -> str:
    """Render a minimal valid tool manifest, overriding top-level scalars."""
    fields = {
        "name": "tiny",
        "version": "0.1.0",
        "description": "A tiny tool.",
        "entrypoint": "tiny:run",
    }
    fields.update(overrides)
    scalars = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"{scalars}\nimplementation:\n  runtime: python\n"


# ---------------------------------------------------------------------------
# Happy path: a valid manifest parses to the right fields
# ---------------------------------------------------------------------------
def test_valid_manifest_parses_scalars() -> None:
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    assert isinstance(manifest, ToolManifest)
    assert manifest.name == "web-fetch"
    assert manifest.version == "2.0.1"
    assert manifest.description == "Fetch a URL and return its body."
    assert manifest.entrypoint == "web_fetch.main:run"


def test_valid_manifest_kind_uses_listing_taxonomy() -> None:
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    assert manifest.kind is MarketplaceListingKind.TOOL


def test_valid_manifest_parses_implementation_reference() -> None:
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    assert manifest.implementation == ToolImplementation(
        runtime="python",
        module="web_fetch.main",
        reference="git+https://example.test/tools/web-fetch@v2.0.1",
    )


def test_valid_manifest_parses_dependencies() -> None:
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    assert manifest.dependencies == ("httpx>=0.27", "tenacity")


def test_valid_manifest_captures_input_output_schema() -> None:
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    assert manifest.input_schema["type"] == "object"
    assert manifest.input_schema["properties"]["url"] == {"type": "string"}
    assert manifest.input_schema["required"] == ["url"]
    assert manifest.output_schema["properties"]["status"] == {"type": "integer"}
    assert manifest.output_schema["properties"]["body"] == {"type": "string"}


def test_valid_manifest_parses_permissions_with_shared_vocabulary() -> None:
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    assert set(manifest.permissions) <= set(PERMISSION_KEYS)
    assert manifest.permissions["allowed_domains"] == ["api.example.test", "cdn.example.test"]
    assert manifest.permissions["allowed_paths"] == ["/workspace/cache"]
    assert manifest.permissions["network_policy"] == NetworkPolicy.RESTRICTED.value


def test_requested_permissions_renders_canonical_descriptors() -> None:
    """The permissions render into the {"type","value"} descriptor list the
    consent + install flow already consumes, in stable PERMISSION_KEYS order."""
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    descriptors = manifest.requested_permissions
    assert [d["type"] for d in descriptors] == [
        "allowed_domains",
        "allowed_paths",
        "network_policy",
    ]
    by_type = {d["type"]: d["value"] for d in descriptors}
    assert by_type["allowed_domains"] == ["api.example.test", "cdn.example.test"]
    assert by_type["network_policy"] == "restricted"


def test_to_manifest_dict_shape() -> None:
    manifest = parse_tool_manifest(VALID_TOOL_YAML)
    payload = manifest.to_manifest_dict()
    assert payload["name"] == "web-fetch"
    assert payload["version"] == "2.0.1"
    assert payload["kind"] == "tool"
    assert payload["entrypoint"] == "web_fetch.main:run"
    assert payload["implementation"] == {
        "runtime": "python",
        "module": "web_fetch.main",
        "reference": "git+https://example.test/tools/web-fetch@v2.0.1",
    }
    assert payload["dependencies"] == ["httpx>=0.27", "tenacity"]
    assert payload["input_schema"]["required"] == ["url"]


# ---------------------------------------------------------------------------
# Minimal documents + optional-field defaults
# ---------------------------------------------------------------------------
def test_minimal_manifest_with_no_optional_fields() -> None:
    manifest = parse_tool_manifest(_minimal())
    assert manifest.name == "tiny"
    assert manifest.kind is MarketplaceListingKind.TOOL  # defaults to tool
    assert manifest.implementation == ToolImplementation(runtime="python")
    assert manifest.dependencies == ()
    assert manifest.input_schema == {}
    assert manifest.output_schema == {}
    assert manifest.permissions == {}
    assert manifest.requested_permissions == []


def test_implementation_to_dict_omits_absent_pointers() -> None:
    manifest = parse_tool_manifest(_minimal())
    assert manifest.to_manifest_dict()["implementation"] == {"runtime": "python"}


def test_scalar_fields_are_stripped() -> None:
    manifest = parse_tool_manifest(_minimal(name="  spaced  "))
    assert manifest.name == "spaced"


def test_kind_mcp_server_accepted() -> None:
    manifest = parse_tool_manifest(_minimal(kind="mcp_server"))
    assert manifest.kind is MarketplaceListingKind.MCP_SERVER


# ---------------------------------------------------------------------------
# Missing required fields error
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("missing", ["name", "version", "description", "entrypoint"])
def test_missing_required_scalar_field_errors(missing: str) -> None:
    fields = {
        "name": "x",
        "version": "1.0.0",
        "description": "d",
        "entrypoint": "x:run",
    }
    del fields[missing]
    scalars = "\n".join(f"{k}: {v}" for k, v in fields.items())
    text = f"{scalars}\nimplementation:\n  runtime: python\n"
    with pytest.raises(ToolFormatError, match=missing):
        parse_tool_manifest(text)


def test_missing_implementation_errors() -> None:
    text = "name: x\nversion: 1.0.0\ndescription: d\nentrypoint: x:run\n"
    with pytest.raises(ToolFormatError, match="implementation"):
        parse_tool_manifest(text)


def test_required_fields_constant_matches() -> None:
    assert set(REQUIRED_FIELDS) == {
        "name",
        "version",
        "description",
        "entrypoint",
        "implementation",
    }


def test_empty_required_field_errors() -> None:
    with pytest.raises(ToolFormatError, match="non-empty string"):
        parse_tool_manifest(_minimal(name='""'))


# ---------------------------------------------------------------------------
# Implementation reference shape errors
# ---------------------------------------------------------------------------
def test_implementation_not_a_mapping_errors() -> None:
    text = "name: x\nversion: 1.0.0\ndescription: d\nentrypoint: x:run\nimplementation: oops\n"
    with pytest.raises(ToolFormatError, match="implementation"):
        parse_tool_manifest(text)


def test_implementation_missing_runtime_errors() -> None:
    text = (
        "name: x\nversion: 1.0.0\ndescription: d\nentrypoint: x:run\n"
        "implementation:\n  module: x\n"
    )
    with pytest.raises(ToolFormatError, match="runtime"):
        parse_tool_manifest(text)


# ---------------------------------------------------------------------------
# Bad semver errors (shared with the skill format helper)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0.0", "latest", "1.2.x", ""])
def test_bad_version_errors(bad: str) -> None:
    with pytest.raises(ToolFormatError):
        parse_tool_manifest(_minimal(version=f'"{bad}"'))


@pytest.mark.parametrize("good", ["0.0.1", "1.2.3", "10.20.30", "1.0.0-rc.1", "1.0.0+build.5"])
def test_good_semver_accepted(good: str) -> None:
    assert is_valid_semver(good) is True
    manifest = parse_tool_manifest(_minimal(version=good))
    assert manifest.version == good


# ---------------------------------------------------------------------------
# Bad kind errors
# ---------------------------------------------------------------------------
def test_unknown_kind_errors() -> None:
    with pytest.raises(ToolFormatError, match="kind"):
        parse_tool_manifest(_minimal(kind="banana"))


# ---------------------------------------------------------------------------
# Bad permission keys / values error (shared vocabulary enforced)
# ---------------------------------------------------------------------------
def test_unknown_permission_key_errors() -> None:
    text = textwrap.dedent(
        """\
        name: x
        version: 1.0.0
        description: d
        entrypoint: x:run
        implementation:
          runtime: python
        permissions:
          allowed_domains: [a.example]
          can_delete_everything: true
        """
    )
    with pytest.raises(ToolFormatError, match="unknown key"):
        parse_tool_manifest(text)


def test_bad_network_policy_value_errors() -> None:
    text = textwrap.dedent(
        """\
        name: x
        version: 1.0.0
        description: d
        entrypoint: x:run
        implementation:
          runtime: python
        permissions:
          network_policy: wide-open
        """
    )
    with pytest.raises(ToolFormatError, match="network_policy"):
        parse_tool_manifest(text)


def test_permissions_not_a_mapping_errors() -> None:
    text = (
        "name: x\nversion: 1.0.0\ndescription: d\nentrypoint: x:run\n"
        "implementation:\n  runtime: python\npermissions: oops\n"
    )
    with pytest.raises(ToolFormatError, match="permissions"):
        parse_tool_manifest(text)


def test_string_permission_value_coerced_to_list() -> None:
    text = textwrap.dedent(
        """\
        name: x
        version: 1.0.0
        description: d
        entrypoint: x:run
        implementation:
          runtime: python
        permissions:
          allowed_domains: solo.example
        """
    )
    manifest = parse_tool_manifest(text)
    assert manifest.permissions["allowed_domains"] == ["solo.example"]


# ---------------------------------------------------------------------------
# Bad dependencies / schema shapes error
# ---------------------------------------------------------------------------
def test_dependencies_not_a_list_errors() -> None:
    text = (
        "name: x\nversion: 1.0.0\ndescription: d\nentrypoint: x:run\n"
        "implementation:\n  runtime: python\ndependencies: httpx\n"
    )
    with pytest.raises(ToolFormatError, match="dependencies"):
        parse_tool_manifest(text)


def test_input_schema_not_a_mapping_errors() -> None:
    text = (
        "name: x\nversion: 1.0.0\ndescription: d\nentrypoint: x:run\n"
        "implementation:\n  runtime: python\ninput_schema: nope\n"
    )
    with pytest.raises(ToolFormatError, match="input_schema"):
        parse_tool_manifest(text)


# ---------------------------------------------------------------------------
# Malformed document errors
# ---------------------------------------------------------------------------
def test_empty_document_errors() -> None:
    with pytest.raises(ToolFormatError, match="empty"):
        parse_tool_manifest("\n\n")


def test_document_not_a_mapping_errors() -> None:
    with pytest.raises(ToolFormatError, match="mapping"):
        parse_tool_manifest("- just\n- a\n- list\n")


def test_invalid_yaml_errors() -> None:
    with pytest.raises(ToolFormatError, match="YAML"):
        parse_tool_manifest("name: x\n  bad: : indentation\n")


def test_non_text_input_errors() -> None:
    with pytest.raises(ToolFormatError, match="text"):
        parse_tool_manifest(b"name: x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Round-trip stability
# ---------------------------------------------------------------------------
def test_parse_is_stable_and_idempotent() -> None:
    """Parsing twice yields an equal manifest (frozen dataclass equality)."""
    first = parse_tool_manifest(VALID_TOOL_YAML)
    second = parse_tool_manifest(VALID_TOOL_YAML)
    assert first == second
