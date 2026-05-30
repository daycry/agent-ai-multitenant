"""SKILL.md skill format — parser + validator (Plan 09 task_09_09).

Plan 09 binding decision: the installable *skill* format is **inspired by
Anthropic Skills** — a Markdown file whose head is a YAML *frontmatter*
block (delimited by ``---`` fences) carrying the machine-readable metadata,
followed by a free-form Markdown *body* that documents the skill (its prose
description + usage examples).

A ``SKILL.md`` looks like::

    ---
    name: web-researcher
    description: Researches a topic across the web and cites sources.
    version: 1.2.0
    dependencies:
      - httpx>=0.27
      - selectolax
    permissions:
      allowed_domains: [api.search.example, docs.python.org]
      allowed_paths: [/workspace/output]
      network_policy: restricted
    examples:
      - title: Quick lookup
        prompt: "Find the latest pgvector release notes"
    ---

    # Web Researcher

    A longer Markdown description of what the skill does...

    ## Usage

    Some prose, optionally with `## Examples` headings.

This module turns that text into a typed :class:`SkillManifest` and fails
loudly with a typed :class:`SkillFormatError` on a malformed/missing
frontmatter, a missing required field (``name`` / ``description`` /
``version``), a bad semver, or a permission key outside the shared
vocabulary.

**Vocabulary reuse (no re-encoding):** the permission keys
(``allowed_domains`` / ``allowed_paths`` / ``network_policy``) and the
``none | restricted | open`` network posture come straight from
:mod:`api_server.marketplace.trust` (:data:`PERMISSION_KEYS` +
:class:`NetworkPolicy`), via the shared
:mod:`api_server.marketplace._format_common` primitives the tool-manifest
format (task_09_10) parses with too — semver + permission validation live
in one place, not duplicated per format. The parser normalizes the
frontmatter ``permissions`` map into the SAME ``{"type": ..., "value": ...}``
descriptor list that the install + consent flow (task_09_07 / task_09_11)
already consumes via :data:`requested_permissions` — so a parsed manifest
drops straight onto a ``marketplace_listings`` row.

Pure Python, no I/O, no new heavy dependency — PyYAML (already a project
dep) parses the frontmatter; semver is validated by the shared helper (the
real comparison/ordering lands in task_09_12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from api_server.marketplace._format_common import (
    is_valid_semver,
    parse_permissions_block,
    requested_permission_descriptors,
)

# The three required frontmatter fields. A SKILL.md missing any of these is
# not installable — fail loudly rather than synthesize a default.
REQUIRED_FIELDS: tuple[str, ...] = ("name", "description", "version")

# Frontmatter fence: a leading ``---`` line, the YAML block, a closing
# ``---`` line, then the Markdown body. Tolerant of leading whitespace /
# BOM and CRLF line endings. DOTALL so the body spans newlines.
_FRONTMATTER_RE = re.compile(
    r"\A﻿?\s*---[ \t]*\r?\n(?P<frontmatter>.*?)\r?\n---[ \t]*\r?\n?(?P<body>.*)\Z",
    re.DOTALL,
)


class SkillFormatError(ValueError):
    """A SKILL.md is malformed or fails validation.

    Raised for: missing/garbled frontmatter fences, frontmatter that is not
    a YAML mapping, a missing required field (``name`` / ``description`` /
    ``version``), a non-semver ``version``, a wrong-typed field, or a
    permission key / network posture outside the shared vocabulary.

    Subclasses :class:`ValueError` so existing ``except ValueError``
    handlers (and the routers' 422 mapping) keep working, while callers that
    care can catch the precise type.
    """


@dataclass(frozen=True, slots=True)
class SkillExample:
    """One usage example declared in the frontmatter ``examples`` list.

    ``title`` labels the example; ``prompt`` is the sample invocation. Both
    optional individually but at least one must be present (an empty example
    is dropped at parse time), so the UI always has something to render.
    """

    title: str | None = None
    prompt: str | None = None


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """The typed, validated content of a SKILL.md file.

    ``frozen`` + ``slots`` so a parsed manifest is immutable and cheap. The
    fields mirror the Anthropic-Skills frontmatter; :meth:`to_manifest_dict`
    and :attr:`requested_permissions` render the shapes the
    ``marketplace_listings`` row (``manifest`` JSONB +
    ``requested_permissions`` JSONB) expects, so a parsed SKILL.md installs
    without a translation layer.
    """

    name: str
    description: str
    version: str
    body: str
    dependencies: tuple[str, ...] = ()
    examples: tuple[SkillExample, ...] = ()
    # The permissions block, already validated against the shared
    # vocabulary. Keys are a subset of PERMISSION_KEYS; values are whatever
    # the key carries (list[str] for domains/paths, a NetworkPolicy member
    # value for network_policy).
    permissions: dict[str, Any] = field(default_factory=dict)

    @property
    def requested_permissions(self) -> list[dict[str, Any]]:
        """The frontmatter permissions rendered as install descriptors.

        Emits the canonical ``{"type": <PERMISSION_KEYS member>, "value":
        ...}`` shape the consent + install flow already consumes — one
        descriptor per declared permission key, in the stable
        :data:`PERMISSION_KEYS` order so output is deterministic.
        """
        return requested_permission_descriptors(self.permissions)

    def to_manifest_dict(self) -> dict[str, Any]:
        """Render the JSONB ``manifest`` payload for a listings row.

        The machine-readable metadata (everything but the prose body and the
        permissions, which live in their own ``requested_permissions``
        column) so the install flow can persist it verbatim.
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "dependencies": list(self.dependencies),
            "examples": [
                {k: v for k, v in (("title", ex.title), ("prompt", ex.prompt)) if v is not None}
                for ex in self.examples
            ],
        }


def _require_str(data: dict[str, Any], key: str) -> str:
    """Pull a required, non-empty string field or raise SkillFormatError."""
    if key not in data or data[key] is None:
        raise SkillFormatError(f"SKILL.md frontmatter is missing required field: {key!r}")
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise SkillFormatError(f"SKILL.md field {key!r} must be a non-empty string")
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
        raise SkillFormatError("SKILL.md 'dependencies' must be a list of strings")
    deps: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise SkillFormatError("SKILL.md 'dependencies' entries must be non-empty strings")
        deps.append(item.strip())
    return tuple(deps)


def _parse_examples(raw: Any) -> tuple[SkillExample, ...]:
    """Normalize the optional ``examples`` field into typed examples.

    Accepts a YAML list of mappings with ``title`` and/or ``prompt`` keys.
    Absent => empty. Entries with neither field are dropped (an empty
    example carries no information); a non-list, or a non-mapping entry, is
    a hard error.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SkillFormatError("SKILL.md 'examples' must be a list of objects")
    examples: list[SkillExample] = []
    for item in raw:
        if not isinstance(item, dict):
            raise SkillFormatError("SKILL.md 'examples' entries must be objects")
        title = item.get("title")
        prompt = item.get("prompt")
        if title is not None and not isinstance(title, str):
            raise SkillFormatError("SKILL.md example 'title' must be a string")
        if prompt is not None and not isinstance(prompt, str):
            raise SkillFormatError("SKILL.md example 'prompt' must be a string")
        if title is None and prompt is None:
            continue
        examples.append(
            SkillExample(
                title=title.strip() if isinstance(title, str) else None,
                prompt=prompt.strip() if isinstance(prompt, str) else None,
            )
        )
    return tuple(examples)


def _skill_err(message: str) -> SkillFormatError:
    """Error factory for the shared parsing helpers — prefixes ``SKILL.md``.

    Passed to :mod:`api_server.marketplace._format_common` so the shared
    permission/semver validators raise this format's precise type with a
    SKILL.md-scoped message.
    """
    return SkillFormatError(f"SKILL.md {message}")


def _parse_permissions(raw: Any) -> dict[str, Any]:
    """Validate + normalize the optional ``permissions`` mapping.

    Delegates to the shared :func:`parse_permissions_block` so the
    permission vocabulary (keys + ``network_policy`` posture) is validated
    in exactly ONE place, shared with the tool-manifest format. Keys must be
    a subset of the shared :data:`PERMISSION_KEYS`; values are normalized
    (``allowed_domains`` / ``allowed_paths`` => list of non-empty strings,
    ``network_policy`` => a :class:`NetworkPolicy` value). Absent => no
    permissions requested (the most restrictive default).
    """
    return parse_permissions_block(raw, _skill_err)


def parse_skill_md(text: str) -> SkillManifest:
    """Parse + validate a SKILL.md document into a :class:`SkillManifest`.

    Splits the YAML frontmatter from the Markdown body, parses the
    frontmatter, validates the required fields + semver + permission
    vocabulary, and returns the typed manifest. Raises
    :class:`SkillFormatError` for any structural or validation failure —
    the install flow (task_09_11) treats that as "not installable".
    """
    if not isinstance(text, str):
        raise SkillFormatError("SKILL.md content must be text")

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise SkillFormatError(
            "SKILL.md must begin with a YAML frontmatter block fenced by '---' lines"
        )

    raw_frontmatter = match.group("frontmatter")
    body = match.group("body").strip()

    try:
        data = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        raise SkillFormatError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc

    if data is None:
        raise SkillFormatError("SKILL.md frontmatter is empty")
    if not isinstance(data, dict):
        raise SkillFormatError("SKILL.md frontmatter must be a YAML mapping (key: value)")

    name = _require_str(data, "name")
    description = _require_str(data, "description")
    version = _require_str(data, "version")
    if not is_valid_semver(version):
        raise SkillFormatError(f"SKILL.md 'version' is not a valid semver string: {version!r}")

    dependencies = _parse_dependencies(data.get("dependencies"))
    examples = _parse_examples(data.get("examples"))
    permissions = _parse_permissions(data.get("permissions"))

    return SkillManifest(
        name=name,
        description=description,
        version=version,
        body=body,
        dependencies=dependencies,
        examples=examples,
        permissions=permissions,
    )


__all__ = [
    "REQUIRED_FIELDS",
    "SkillExample",
    "SkillFormatError",
    "SkillManifest",
    "is_valid_semver",
    "parse_skill_md",
]
