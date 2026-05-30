"""Private-listing manifest validation (Plan 09 task_09_16).

The private tenant marketplace lets a tenant publish its OWN internal
skills / tools as *private* ``marketplace_listings`` rows (``tenant_id`` =
caller tenant — the hybrid model + RLS of Phase A already isolate them).
Before a row is written the submitted manifest is validated by the SAME
Phase C parsers the install flow uses, so a private listing speaks the
exact format the catalog does and a malformed manifest never lands on a
row:

  * a **skill** is a SKILL.md document — parsed by
    :func:`~api_server.marketplace.skill_format.parse_skill_md`;
  * a **tool** (or **mcp_server**) is a YAML manifest — parsed by
    :func:`~api_server.marketplace.tool_format.parse_tool_manifest`.

This module is the thin adapter between "the kind the publisher declared +
the raw manifest text" and "the validated column values a
``marketplace_listings`` row needs" (``name`` / ``version`` /
``description`` / ``manifest`` JSONB / ``requested_permissions`` JSONB /
the resolved kind). It re-raises the parsers' typed errors as
:class:`PrivateListingFormatError` (a ``ValueError`` subclass) so the
router maps any validation failure to a single 422.

Pure Python, no I/O, no new dependency — it only composes the existing
parsers. The persistence + RLS-scoped write live in the router; the
tenancy guarantee (a private listing is the caller tenant's and only the
caller tenant + global rows are browseable) is enforced by the Phase A RLS
policies, not re-implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api_server.db.marketplace import MarketplaceListingKind
from api_server.marketplace.skill_format import SkillFormatError, parse_skill_md
from api_server.marketplace.tool_format import ToolFormatError, parse_tool_manifest


class PrivateListingFormatError(ValueError):
    """A submitted private-listing manifest is malformed or fails validation.

    Wraps the underlying :class:`SkillFormatError` / :class:`ToolFormatError`
    so the publish/update endpoints catch ONE type and map it to a 422.
    Subclasses :class:`ValueError` so existing ``except ValueError`` handlers
    keep working.
    """


@dataclass(frozen=True, slots=True)
class ParsedPrivateListing:
    """The validated column values for a private ``marketplace_listings`` row.

    The publish/update endpoints copy these straight onto the row; the
    ``tenant_id`` / ``source_id`` / ``trust_level`` are server-derived in the
    router (never taken from the submitted manifest), so a publisher cannot
    forge a tenancy scope or a trust tier.
    """

    kind: MarketplaceListingKind
    name: str
    version: str
    description: str | None
    manifest: dict[str, Any]
    requested_permissions: list[dict[str, Any]]


def parse_private_listing(
    *, kind: MarketplaceListingKind, manifest_text: str
) -> ParsedPrivateListing:
    """Validate a submitted private-listing manifest against the Phase C parsers.

    Routes by the publisher-declared ``kind``: a ``skill`` is parsed as a
    SKILL.md document, a ``tool`` / ``mcp_server`` as a YAML tool manifest.
    Returns the validated column values for the listing row. Raises
    :class:`PrivateListingFormatError` for any structural or validation
    failure (missing required field, bad semver, unknown permission key, a
    tool-manifest ``kind`` that disagrees with the declared one, …) — the
    router maps that to a single 422 and NO row is written.
    """
    if not isinstance(manifest_text, str) or not manifest_text.strip():
        raise PrivateListingFormatError("manifest content must be a non-empty string")

    if kind == MarketplaceListingKind.SKILL:
        try:
            skill = parse_skill_md(manifest_text)
        except SkillFormatError as exc:
            raise PrivateListingFormatError(str(exc)) from exc
        return ParsedPrivateListing(
            kind=MarketplaceListingKind.SKILL,
            name=skill.name,
            version=skill.version,
            description=skill.description,
            manifest=skill.to_manifest_dict(),
            requested_permissions=skill.requested_permissions,
        )

    # tool / mcp_server -> the YAML tool manifest format.
    try:
        tool = parse_tool_manifest(manifest_text)
    except ToolFormatError as exc:
        raise PrivateListingFormatError(str(exc)) from exc

    # The manifest carries its own ``kind`` (defaulting to ``tool``); it must
    # agree with the kind the publisher declared so the listing row's kind is
    # never silently wrong. A skill submitted under a tool kind (or vice
    # versa) is a hard error.
    if tool.kind != kind:
        raise PrivateListingFormatError(
            f"manifest 'kind' {tool.kind.value!r} does not match the declared kind {kind.value!r}"
        )
    return ParsedPrivateListing(
        kind=tool.kind,
        name=tool.name,
        version=tool.version,
        description=tool.description,
        manifest=tool.to_manifest_dict(),
        requested_permissions=tool.requested_permissions,
    )


__all__ = [
    "ParsedPrivateListing",
    "PrivateListingFormatError",
    "parse_private_listing",
]
