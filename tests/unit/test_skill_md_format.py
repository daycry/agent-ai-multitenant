"""Unit tests for the SKILL.md skill format (Plan 09 task_09_09).

Pins the binding Plan 09 decision into executable assertions: the
installable skill format is inspired by Anthropic Skills — YAML frontmatter
(name / description / version / dependencies / permissions / examples) plus
a Markdown body — and the parser produces a typed manifest whose permission
vocabulary is the SHARED one from :mod:`api_server.marketplace.trust`.

Pure-Python, no DB / Docker / network: the parser is text in, dataclass
out. (No ``cross_tenant`` marker: this module touches no tenant-owned rows;
the multi-tenancy guarantee is unaffected.)
"""

from __future__ import annotations

import textwrap

import pytest
from api_server.marketplace.skill_format import (
    REQUIRED_FIELDS,
    SkillExample,
    SkillFormatError,
    SkillManifest,
    is_valid_semver,
    parse_skill_md,
)
from api_server.marketplace.trust import PERMISSION_KEYS, NetworkPolicy

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
VALID_SKILL_MD = textwrap.dedent("""\
    ---
    name: web-researcher
    description: Researches a topic across the web and cites sources.
    version: 1.2.0
    dependencies:
      - httpx>=0.27
      - selectolax
    permissions:
      allowed_domains:
        - api.search.example
        - docs.python.org
      allowed_paths:
        - /workspace/output
      network_policy: restricted
    examples:
      - title: Quick lookup
        prompt: Find the latest pgvector release notes
      - title: Deep dive
        prompt: Summarize the RLS docs
    ---

    # Web Researcher

    A skill that researches a topic and returns a cited summary.

    ## Usage

    Ask it to research anything.
    """)


def _minimal(**overrides: str) -> str:
    """Render a minimal valid SKILL.md, overriding frontmatter scalars."""
    fields = {"name": "tiny", "description": "A tiny skill.", "version": "0.1.0"}
    fields.update(overrides)
    front = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"---\n{front}\n---\n\n# Tiny\n\nBody.\n"


# ---------------------------------------------------------------------------
# Happy path: a valid SKILL.md parses to the right fields
# ---------------------------------------------------------------------------
def test_valid_skill_md_parses_scalars() -> None:
    manifest = parse_skill_md(VALID_SKILL_MD)
    assert isinstance(manifest, SkillManifest)
    assert manifest.name == "web-researcher"
    assert manifest.description == "Researches a topic across the web and cites sources."
    assert manifest.version == "1.2.0"


def test_valid_skill_md_parses_dependencies() -> None:
    manifest = parse_skill_md(VALID_SKILL_MD)
    assert manifest.dependencies == ("httpx>=0.27", "selectolax")


def test_valid_skill_md_extracts_examples() -> None:
    manifest = parse_skill_md(VALID_SKILL_MD)
    assert manifest.examples == (
        SkillExample(title="Quick lookup", prompt="Find the latest pgvector release notes"),
        SkillExample(title="Deep dive", prompt="Summarize the RLS docs"),
    )


def test_valid_skill_md_captures_markdown_body() -> None:
    manifest = parse_skill_md(VALID_SKILL_MD)
    assert manifest.body.startswith("# Web Researcher")
    assert "## Usage" in manifest.body
    # The frontmatter fences are NOT part of the body.
    assert "---" not in manifest.body.splitlines()[0]


def test_valid_skill_md_parses_permissions_with_shared_vocabulary() -> None:
    manifest = parse_skill_md(VALID_SKILL_MD)
    assert set(manifest.permissions) <= set(PERMISSION_KEYS)
    assert manifest.permissions["allowed_domains"] == ["api.search.example", "docs.python.org"]
    assert manifest.permissions["allowed_paths"] == ["/workspace/output"]
    assert manifest.permissions["network_policy"] == NetworkPolicy.RESTRICTED.value


def test_requested_permissions_renders_canonical_descriptors() -> None:
    """The permissions render into the {"type","value"} descriptor list the
    consent + install flow already consumes, in stable PERMISSION_KEYS order."""
    manifest = parse_skill_md(VALID_SKILL_MD)
    descriptors = manifest.requested_permissions
    assert [d["type"] for d in descriptors] == [
        "allowed_domains",
        "allowed_paths",
        "network_policy",
    ]
    by_type = {d["type"]: d["value"] for d in descriptors}
    assert by_type["allowed_domains"] == ["api.search.example", "docs.python.org"]
    assert by_type["network_policy"] == "restricted"


def test_to_manifest_dict_shape() -> None:
    manifest = parse_skill_md(VALID_SKILL_MD)
    payload = manifest.to_manifest_dict()
    assert payload["name"] == "web-researcher"
    assert payload["version"] == "1.2.0"
    assert payload["dependencies"] == ["httpx>=0.27", "selectolax"]
    assert payload["examples"] == [
        {"title": "Quick lookup", "prompt": "Find the latest pgvector release notes"},
        {"title": "Deep dive", "prompt": "Summarize the RLS docs"},
    ]


# ---------------------------------------------------------------------------
# Minimal documents + optional-field defaults
# ---------------------------------------------------------------------------
def test_minimal_document_with_no_optional_fields() -> None:
    manifest = parse_skill_md(_minimal())
    assert manifest.name == "tiny"
    assert manifest.dependencies == ()
    assert manifest.examples == ()
    assert manifest.permissions == {}
    assert manifest.requested_permissions == []


def test_scalar_fields_are_stripped() -> None:
    manifest = parse_skill_md(_minimal(name="  spaced  "))
    assert manifest.name == "spaced"


# ---------------------------------------------------------------------------
# Missing required fields error
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_missing_required_field_errors(missing: str) -> None:
    fields = {"name": "x", "description": "d", "version": "1.0.0"}
    del fields[missing]
    front = "\n".join(f"{k}: {v}" for k, v in fields.items())
    text = f"---\n{front}\n---\n\nBody.\n"
    with pytest.raises(SkillFormatError, match=missing):
        parse_skill_md(text)


def test_empty_required_field_errors() -> None:
    with pytest.raises(SkillFormatError, match="non-empty string"):
        parse_skill_md(_minimal(name='""'))


# ---------------------------------------------------------------------------
# Bad semver errors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0.0", "latest", "1.2.x", ""])
def test_bad_version_errors(bad: str) -> None:
    with pytest.raises(SkillFormatError):
        parse_skill_md(_minimal(version=f'"{bad}"'))


@pytest.mark.parametrize("good", ["0.0.1", "1.2.3", "10.20.30", "1.0.0-rc.1", "1.0.0+build.5"])
def test_good_semver_accepted(good: str) -> None:
    assert is_valid_semver(good) is True
    manifest = parse_skill_md(_minimal(version=good))
    assert manifest.version == good


# ---------------------------------------------------------------------------
# Bad permission keys / values error (shared vocabulary enforced)
# ---------------------------------------------------------------------------
def test_unknown_permission_key_errors() -> None:
    text = textwrap.dedent("""\
        ---
        name: x
        description: d
        version: 1.0.0
        permissions:
          allowed_domains: [a.example]
          can_delete_everything: true
        ---

        Body.
        """)
    with pytest.raises(SkillFormatError, match="unknown key"):
        parse_skill_md(text)


def test_bad_network_policy_value_errors() -> None:
    with_bad = textwrap.dedent("""\
        ---
        name: x
        description: d
        version: 1.0.0
        permissions:
          network_policy: wide-open
        ---

        Body.
        """)
    with pytest.raises(SkillFormatError, match="network_policy"):
        parse_skill_md(with_bad)


def test_permissions_not_a_mapping_errors() -> None:
    text = "---\nname: x\ndescription: d\nversion: 1.0.0\npermissions: oops\n---\n\nBody.\n"
    with pytest.raises(SkillFormatError, match="permissions"):
        parse_skill_md(text)


def test_string_permission_value_coerced_to_list() -> None:
    text = textwrap.dedent("""\
        ---
        name: x
        description: d
        version: 1.0.0
        permissions:
          allowed_domains: solo.example
        ---

        Body.
        """)
    manifest = parse_skill_md(text)
    assert manifest.permissions["allowed_domains"] == ["solo.example"]


# ---------------------------------------------------------------------------
# Malformed frontmatter errors
# ---------------------------------------------------------------------------
def test_no_frontmatter_errors() -> None:
    with pytest.raises(SkillFormatError, match="frontmatter"):
        parse_skill_md("# Just a markdown file\n\nNo frontmatter here.\n")


def test_unterminated_frontmatter_errors() -> None:
    with pytest.raises(SkillFormatError, match="frontmatter"):
        parse_skill_md(
            "---\nname: x\ndescription: d\nversion: 1.0.0\n\nBody with no closing fence."
        )


def test_frontmatter_not_a_mapping_errors() -> None:
    with pytest.raises(SkillFormatError, match="mapping"):
        parse_skill_md("---\n- just\n- a\n- list\n---\n\nBody.\n")


def test_empty_frontmatter_errors() -> None:
    with pytest.raises(SkillFormatError, match="empty"):
        parse_skill_md("---\n\n---\n\nBody.\n")


def test_invalid_yaml_frontmatter_errors() -> None:
    with pytest.raises(SkillFormatError, match="YAML"):
        parse_skill_md("---\nname: x\n  bad: : indentation\n---\n\nBody.\n")


def test_non_text_input_errors() -> None:
    with pytest.raises(SkillFormatError, match="text"):
        parse_skill_md(b"---\nname: x\n---")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Bad dependencies / examples shapes error
# ---------------------------------------------------------------------------
def test_dependencies_not_a_list_errors() -> None:
    text = "---\nname: x\ndescription: d\nversion: 1.0.0\ndependencies: httpx\n---\n\nBody.\n"
    with pytest.raises(SkillFormatError, match="dependencies"):
        parse_skill_md(text)


def test_examples_not_a_list_errors() -> None:
    text = "---\nname: x\ndescription: d\nversion: 1.0.0\nexamples: nope\n---\n\nBody.\n"
    with pytest.raises(SkillFormatError, match="examples"):
        parse_skill_md(text)


def test_empty_example_is_dropped() -> None:
    text = textwrap.dedent("""\
        ---
        name: x
        description: d
        version: 1.0.0
        examples:
          - {}
          - title: kept
        ---

        Body.
        """)
    manifest = parse_skill_md(text)
    assert manifest.examples == (SkillExample(title="kept", prompt=None),)


# ---------------------------------------------------------------------------
# Round-trip stability + CRLF tolerance
# ---------------------------------------------------------------------------
def test_parse_is_stable_and_idempotent() -> None:
    """Parsing twice yields an equal manifest (frozen dataclass equality)."""
    first = parse_skill_md(VALID_SKILL_MD)
    second = parse_skill_md(VALID_SKILL_MD)
    assert first == second


def test_round_trip_through_rendered_frontmatter_is_stable() -> None:
    """Re-rendering the parsed manifest's metadata into a fresh SKILL.md and
    re-parsing yields the same scalar/dependency/permission fields — the
    format survives a round trip without drift."""
    import yaml

    manifest = parse_skill_md(VALID_SKILL_MD)
    front = {
        "name": manifest.name,
        "description": manifest.description,
        "version": manifest.version,
        "dependencies": list(manifest.dependencies),
        "permissions": dict(manifest.permissions),
        "examples": [
            {k: v for k, v in (("title", e.title), ("prompt", e.prompt)) if v is not None}
            for e in manifest.examples
        ],
    }
    rendered = f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n\n{manifest.body}\n"
    reparsed = parse_skill_md(rendered)
    assert reparsed.name == manifest.name
    assert reparsed.description == manifest.description
    assert reparsed.version == manifest.version
    assert reparsed.dependencies == manifest.dependencies
    assert reparsed.permissions == manifest.permissions
    assert reparsed.examples == manifest.examples


def test_crlf_line_endings_are_tolerated() -> None:
    crlf = _minimal().replace("\n", "\r\n")
    manifest = parse_skill_md(crlf)
    assert manifest.name == "tiny"
    assert manifest.version == "0.1.0"
