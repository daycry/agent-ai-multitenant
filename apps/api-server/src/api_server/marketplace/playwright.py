"""Playwright — the flagship marketplace tool + its guided config (Plan 09 task_09_13).

Plan 09 Fase D names Playwright the *featured* marketplace tool: a verified,
platform-curated GLOBAL catalog listing (``tenant_id NULL`` — the Phase A
hybrid model) with a **guided configuration** the admin-panel renders as a
form rather than free-text YAML. This module is the single source of truth
for that tool, in three layers that reuse the Phase A-C substrate instead of
inventing a parallel concept:

  1. :data:`PLAYWRIGHT_TOOL_YAML` — the official tool manifest in the SAME
     standard YAML format every other tool uses (task_09_10): name / version
     / description / ``kind: tool`` / entrypoint / implementation reference /
     declared permissions (``allowed_domains`` for the sites under test +
     ``network_policy``) / input+output schema. It parses through the shared
     :func:`api_server.marketplace.tool_format.parse_tool_manifest` — no
     bespoke parser.

  2. :class:`PlaywrightToolConfig` — the typed, validated *guided config*:
     the operator's run-time choices (browsers, headless, screenshots,
     traces, base_url, timeouts). It is NOT the manifest — the manifest
     declares the tool; the config records how a given installation drives
     it. Validation rejects an unknown browser / screenshot mode / trace
     mode, a non-positive timeout, etc., so a bad config never reaches the
     node-playwright runtime. :meth:`config_schema` emits a JSON-Schema-ish
     descriptor the admin-panel renders as the guided form (and the manifest
     embeds under ``config_schema`` so the UI can discover it).

  3. :func:`seed_playwright_listing` — the loader that registers Playwright
     as a VERIFIED GLOBAL listing under the official catalog source. Idempotent
     (it upserts by ``(source, tenant_id=NULL, name, version)``), so running it
     on an already-seeded catalog is a no-op. Seed/definition data + a loader,
     not schema churn — no migration is needed (the listing is a row, the
     guided config rides in the existing ``manifest`` JSONB).

Multi-tenancy note: the Playwright listing is GLOBAL — ``tenant_id NULL`` —
exactly like the platform's builtin skills/tools. It is therefore written by a
BYPASSRLS catalog-publisher session (the loader runs with the migrations role,
mirroring how the official source is seeded), and every tenant SEES it via the
``marketplace_listings_global_read`` SELECT policy but can only INSTALL it into
its own tenant-scoped ``marketplace_installations`` (RLS). The guided config a
tenant chooses lives on that tenant-owned installation, never on the shared
listing.

Pure Python + SQLAlchemy; PyYAML (existing dep) parses the manifest. No new
dependency, no migration.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.marketplace import (
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceSource,
    MarketplaceSourceType,
    MarketplaceTrustLevel,
)
from api_server.marketplace.tool_format import ToolManifest, parse_tool_manifest

# The canonical identity of the official catalog source (mirrors the
# migration / test seeds that insert ``('official-catalog', 'official')``).
OFFICIAL_SOURCE_NAME = "official-catalog"

# The featured tool's stable identity.
PLAYWRIGHT_TOOL_NAME = "playwright"
PLAYWRIGHT_TOOL_VERSION = "1.0.0"


# =============================================================================
# Guided-config vocabularies (closed enums — a value outside them is rejected)
# =============================================================================
class PlaywrightBrowser(enum.StrEnum):
    """A browser engine Playwright can drive (multi-select in the UI)."""

    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class ScreenshotMode(enum.StrEnum):
    """When Playwright captures screenshots (Playwright's own vocabulary)."""

    OFF = "off"
    ON = "on"
    ONLY_ON_FAILURE = "only-on-failure"


class TraceMode(enum.StrEnum):
    """When Playwright records a trace (Playwright's own vocabulary)."""

    OFF = "off"
    ON = "on"
    RETAIN_ON_FAILURE = "retain-on-failure"


class PlaywrightConfigError(ValueError):
    """A guided Playwright config is malformed or fails validation.

    Subclasses :class:`ValueError` so existing ``except ValueError`` handlers
    (and the routers' 422 mapping) keep working, while callers that care can
    catch the precise type.
    """


# Defaults the guided form pre-fills — the sane out-of-the-box posture: drive
# Chromium headless, only keep artifacts on failure (cheap, useful for triage).
_DEFAULT_BROWSERS: tuple[PlaywrightBrowser, ...] = (PlaywrightBrowser.CHROMIUM,)
_DEFAULT_HEADLESS = True
_DEFAULT_SCREENSHOTS = ScreenshotMode.ONLY_ON_FAILURE
_DEFAULT_TRACES = TraceMode.RETAIN_ON_FAILURE
# Playwright's own default action/navigation timeouts (ms).
_DEFAULT_TIMEOUT_MS = 30_000


@dataclass(frozen=True, slots=True)
class PlaywrightToolConfig:
    """The validated guided config for one Playwright installation.

    ``frozen`` + ``slots`` so a parsed config is immutable and cheap. The
    UI builds it from the guided form; :meth:`from_dict` validates an
    arbitrary payload (rejecting unknown browsers / screenshot / trace modes /
    non-positive timeouts) and :meth:`to_dict` renders it back for persistence
    on the tenant-owned installation.
    """

    browsers: tuple[PlaywrightBrowser, ...] = _DEFAULT_BROWSERS
    headless: bool = _DEFAULT_HEADLESS
    screenshots: ScreenshotMode = _DEFAULT_SCREENSHOTS
    traces: TraceMode = _DEFAULT_TRACES
    base_url: str | None = None
    # Action/navigation timeout in milliseconds (must be a positive int).
    timeout_ms: int = _DEFAULT_TIMEOUT_MS

    @classmethod
    def from_dict(cls, data: Any) -> PlaywrightToolConfig:
        """Validate + build a config from a raw mapping (the guided-form payload).

        Raises :class:`PlaywrightConfigError` on any structural or value
        failure: a non-mapping, an unknown key, an empty browser selection, a
        browser / screenshot / trace value outside its enum, a non-positive or
        non-integer timeout, or a non-string ``base_url``. Absent fields fall
        back to the sane defaults the guided form pre-fills.
        """
        if not isinstance(data, dict):
            raise PlaywrightConfigError("playwright config must be a mapping (key: value)")

        unknown = set(data) - {
            "browsers",
            "headless",
            "screenshots",
            "traces",
            "base_url",
            "timeout_ms",
        }
        if unknown:
            raise PlaywrightConfigError(
                f"playwright config has unknown key(s): {', '.join(sorted(unknown))}"
            )

        browsers = cls._parse_browsers(data.get("browsers"))
        headless = cls._parse_bool("headless", data.get("headless"), default=_DEFAULT_HEADLESS)
        screenshots = cls._parse_enum(
            "screenshots", ScreenshotMode, data.get("screenshots"), default=_DEFAULT_SCREENSHOTS
        )
        traces = cls._parse_enum("traces", TraceMode, data.get("traces"), default=_DEFAULT_TRACES)
        base_url = cls._parse_base_url(data.get("base_url"))
        timeout_ms = cls._parse_timeout(data.get("timeout_ms"))

        return cls(
            browsers=browsers,
            headless=headless,
            screenshots=screenshots,
            traces=traces,
            base_url=base_url,
            timeout_ms=timeout_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        """Render the config as a JSON-able mapping for persistence."""
        return {
            "browsers": [b.value for b in self.browsers],
            "headless": self.headless,
            "screenshots": self.screenshots.value,
            "traces": self.traces.value,
            "base_url": self.base_url,
            "timeout_ms": self.timeout_ms,
        }

    # --- parsing helpers -------------------------------------------------
    @staticmethod
    def _parse_browsers(raw: Any) -> tuple[PlaywrightBrowser, ...]:
        """Validate the multi-select browser list (at least one, all known)."""
        if raw is None:
            return _DEFAULT_BROWSERS
        if isinstance(raw, str):  # a bare string is a one-element selection
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise PlaywrightConfigError(
                "playwright config 'browsers' must be a non-empty list of browser names"
            )
        out: list[PlaywrightBrowser] = []
        seen: set[PlaywrightBrowser] = set()
        for item in raw:
            if not isinstance(item, str):
                raise PlaywrightConfigError("playwright config 'browsers' entries must be strings")
            try:
                browser = PlaywrightBrowser(item)
            except ValueError as exc:
                allowed = ", ".join(b.value for b in PlaywrightBrowser)
                raise PlaywrightConfigError(
                    f"playwright config 'browsers' has unknown browser {item!r}; "
                    f"allowed: {allowed}"
                ) from exc
            if browser not in seen:  # de-dupe, preserve selection order
                seen.add(browser)
                out.append(browser)
        return tuple(out)

    @staticmethod
    def _parse_bool(key: str, raw: Any, *, default: bool) -> bool:
        if raw is None:
            return default
        if not isinstance(raw, bool):
            raise PlaywrightConfigError(f"playwright config {key!r} must be a boolean")
        return raw

    @staticmethod
    def _parse_enum(
        key: str, enum_cls: type[enum.StrEnum], raw: Any, *, default: enum.StrEnum
    ) -> Any:
        if raw is None:
            return default
        if not isinstance(raw, str):
            raise PlaywrightConfigError(f"playwright config {key!r} must be a string")
        try:
            return enum_cls(raw)
        except ValueError as exc:
            allowed = ", ".join(m.value for m in enum_cls)
            raise PlaywrightConfigError(
                f"playwright config {key!r} must be one of: {allowed}"
            ) from exc

    @staticmethod
    def _parse_base_url(raw: Any) -> str | None:
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw.strip():
            raise PlaywrightConfigError("playwright config 'base_url' must be a non-empty string")
        return raw.strip()

    @staticmethod
    def _parse_timeout(raw: Any) -> int:
        if raw is None:
            return _DEFAULT_TIMEOUT_MS
        # Reject bool (a subclass of int) and non-int types outright.
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise PlaywrightConfigError("playwright config 'timeout_ms' must be an integer")
        if raw <= 0:
            raise PlaywrightConfigError("playwright config 'timeout_ms' must be a positive integer")
        return int(raw)


def config_schema() -> dict[str, Any]:
    """The guided-config descriptor the admin-panel renders as a form.

    A small, JSON-Schema-ish document the UI walks to build the guided form:
    one field per knob, with its widget type, allowed values, multi-select
    flag, and default. Embedded in the tool manifest under ``config_schema``
    so the front-end discovers it from the listing's manifest JSONB without a
    hard-coded copy.
    """
    default = PlaywrightToolConfig()
    return {
        "type": "object",
        "properties": {
            "browsers": {
                "type": "array",
                "widget": "multiselect",
                "items": {"enum": [b.value for b in PlaywrightBrowser]},
                "minItems": 1,
                "default": [b.value for b in default.browsers],
            },
            "headless": {
                "type": "boolean",
                "widget": "toggle",
                "default": default.headless,
            },
            "screenshots": {
                "type": "string",
                "widget": "select",
                "enum": [m.value for m in ScreenshotMode],
                "default": default.screenshots.value,
            },
            "traces": {
                "type": "string",
                "widget": "select",
                "enum": [m.value for m in TraceMode],
                "default": default.traces.value,
            },
            "base_url": {
                "type": "string",
                "widget": "text",
                "default": None,
            },
            "timeout_ms": {
                "type": "integer",
                "widget": "number",
                "minimum": 1,
                "default": default.timeout_ms,
            },
        },
        "required": ["browsers"],
    }


# =============================================================================
# The official Playwright tool manifest (standard YAML format, task_09_10)
# =============================================================================
# Declared permissions: the sites under test live behind ``allowed_domains``
# (the operator extends this per project via the consent flow), and Playwright
# drives a real browser that needs egress to them — ``network_policy:
# restricted`` (egress only to the consented domains). As a VERIFIED listing
# this needs no per-permission consent, but it still DECLARES the surface so
# the UI shows what the tool touches.
PLAYWRIGHT_TOOL_YAML = """\
name: playwright
version: 1.0.0
description: >-
  End-to-end browser automation and testing with Microsoft Playwright. Drives
  Chromium, Firefox and WebKit with screenshots and traces for QA flows.
kind: tool
entrypoint: playwright_runner.main:run
implementation:
  runtime: node-playwright
  module: playwright_runner.main
  reference: npm:@playwright/test@1
dependencies:
  - "@playwright/test@^1.44"
permissions:
  allowed_domains:
    - localhost
  network_policy: restricted
input_schema:
  type: object
  properties:
    spec:
      type: string
      description: Path to the Playwright spec file to run.
    config:
      type: object
      description: The guided PlaywrightToolConfig for this run.
  required:
    - spec
output_schema:
  type: object
  properties:
    passed:
      type: boolean
    artifacts:
      type: array
      items:
        type: string
      description: Paths to screenshots and traces produced by the run.
"""


def playwright_tool_manifest() -> ToolManifest:
    """Parse the official Playwright manifest into a typed :class:`ToolManifest`.

    Goes through the SHARED tool-manifest parser (task_09_10), so the featured
    tool is validated by the exact same code path as any community tool —
    proving the flagship listing is a well-formed standard tool, not a special
    case.
    """
    return parse_tool_manifest(PLAYWRIGHT_TOOL_YAML)


def playwright_listing_manifest() -> dict[str, Any]:
    """The JSONB ``manifest`` payload for the Playwright listing row.

    The parsed tool manifest plus the guided :func:`config_schema` under
    ``config_schema`` — so the admin-panel discovers the guided form directly
    off the listing's manifest, and the install/sandbox flow still reads the
    standard tool fields it already understands.
    """
    manifest = playwright_tool_manifest().to_manifest_dict()
    manifest["config_schema"] = config_schema()
    return manifest


async def get_official_source(session: AsyncSession) -> MarketplaceSource | None:
    """Return the official catalog source, or ``None`` if not yet seeded."""
    result = await session.execute(
        select(MarketplaceSource).where(MarketplaceSource.name == OFFICIAL_SOURCE_NAME)
    )
    return result.scalar_one_or_none()


async def ensure_official_source(session: AsyncSession) -> MarketplaceSource:
    """Get-or-create the official catalog source (the publisher of verified listings).

    The official source is a trusted registry whose listings are expected to be
    signed (``requires_signature`` — verified listings carry a platform-team
    signature, plan decision (d)). Idempotent: returns the existing row when
    already present. Tenant-agnostic by design (the source table has no RLS).
    """
    source = await get_official_source(session)
    if source is not None:
        return source
    source = MarketplaceSource(
        name=OFFICIAL_SOURCE_NAME,
        description="Platform-curated public marketplace catalog.",
        source_type=MarketplaceSourceType.OFFICIAL.value,
        is_trusted=True,
        requires_signature=True,
        default_trust_level=MarketplaceTrustLevel.VERIFIED.value,
        owner_tenant_id=None,
    )
    session.add(source)
    await session.flush()
    return source


@dataclass(frozen=True, slots=True)
class SeedResult:
    """The outcome of :func:`seed_playwright_listing`."""

    listing_id: UUID
    created: bool


async def seed_playwright_listing(session: AsyncSession) -> SeedResult:
    """Register Playwright as a VERIFIED GLOBAL marketplace listing.

    Idempotent loader: upserts the listing keyed by ``(source, tenant_id=NULL,
    name, version)`` — the same uniqueness the
    ``uq_marketplace_listings_source_tenant_name_version`` constraint enforces.
    On an already-seeded catalog it refreshes the manifest / permissions in
    place and reports ``created=False``; otherwise it inserts and reports
    ``created=True``.

    The listing is GLOBAL (``tenant_id NULL`` — visible to every tenant via the
    global-read RLS policy) and VERIFIED (lightest guardrails, no per-permission
    consent). The declared permissions + the guided config schema both ride in
    the existing ``manifest`` / ``requested_permissions`` JSONB columns — no
    schema change. Must run on a BYPASSRLS publisher session (writing a global
    ``tenant_id NULL`` row is reserved for the catalog publisher; a tenant
    session's RLS WITH CHECK rejects it).
    """
    source = await ensure_official_source(session)
    manifest = playwright_tool_manifest()
    listing_manifest = playwright_listing_manifest()
    requested_permissions = manifest.requested_permissions

    existing = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.source_id == source.id,
            MarketplaceListing.tenant_id.is_(None),
            MarketplaceListing.name == PLAYWRIGHT_TOOL_NAME,
            MarketplaceListing.version == PLAYWRIGHT_TOOL_VERSION,
        )
    )
    listing = existing.scalar_one_or_none()
    if listing is not None:
        # Idempotent refresh: keep the row, update the curated metadata.
        listing.kind = MarketplaceListingKind.TOOL.value
        listing.description = manifest.description
        listing.trust_level = MarketplaceTrustLevel.VERIFIED.value
        listing.manifest = listing_manifest
        listing.requested_permissions = requested_permissions
        await session.flush()
        return SeedResult(listing_id=listing.id, created=False)

    listing = MarketplaceListing(
        source_id=source.id,
        tenant_id=None,  # GLOBAL catalog listing (Phase A hybrid model).
        kind=MarketplaceListingKind.TOOL.value,
        name=PLAYWRIGHT_TOOL_NAME,
        version=PLAYWRIGHT_TOOL_VERSION,
        description=manifest.description,
        author="Platform",
        trust_level=MarketplaceTrustLevel.VERIFIED.value,
        manifest=listing_manifest,
        requested_permissions=requested_permissions,
    )
    session.add(listing)
    await session.flush()
    return SeedResult(listing_id=listing.id, created=True)


__all__ = [
    "OFFICIAL_SOURCE_NAME",
    "PLAYWRIGHT_TOOL_NAME",
    "PLAYWRIGHT_TOOL_VERSION",
    "PLAYWRIGHT_TOOL_YAML",
    "PlaywrightBrowser",
    "PlaywrightConfigError",
    "PlaywrightToolConfig",
    "ScreenshotMode",
    "SeedResult",
    "TraceMode",
    "config_schema",
    "ensure_official_source",
    "get_official_source",
    "playwright_listing_manifest",
    "playwright_tool_manifest",
    "seed_playwright_listing",
]
