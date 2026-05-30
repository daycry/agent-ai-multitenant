"""Marketplace domain logic (Plan 09 Fase B+).

The ORM contract lives in :mod:`api_server.db.marketplace`; the REST
surface in :mod:`api_server.routers.marketplace`. This package holds the
*policy* layer that sits between them — the data-driven rules that the
trust level of a listing implies (``trust.py``), the pre-install static
analysis + gate (``static_analysis.py``), and (in later tasks) the sandbox
machinery.
"""

from __future__ import annotations

from api_server.marketplace.e2e_templates import (
    BUILTIN_E2E_TEMPLATES,
    E2ETemplateError,
    E2ETemplateParameter,
    E2ETestTemplate,
    get_e2e_template,
    load_e2e_templates,
)
from api_server.marketplace.playwright import (
    PLAYWRIGHT_TOOL_YAML,
    PlaywrightBrowser,
    PlaywrightConfigError,
    PlaywrightToolConfig,
    ScreenshotMode,
    TraceMode,
    config_schema,
    playwright_tool_manifest,
    seed_playwright_listing,
)
from api_server.marketplace.private_listing import (
    ParsedPrivateListing,
    PrivateListingFormatError,
    parse_private_listing,
)
from api_server.marketplace.sandbox import (
    DockerSocketLeakError,
    MarketplaceSandbox,
    SandboxError,
    SandboxResult,
    SandboxSpec,
    assert_no_docker_socket,
    build_sandbox_run_kwargs,
)
from api_server.marketplace.skill_format import (
    SkillExample,
    SkillFormatError,
    SkillManifest,
    is_valid_semver,
    parse_skill_md,
)
from api_server.marketplace.static_analysis import (
    Finding,
    StaticAnalysisError,
    StaticAnalysisReport,
    StaticAnalyzer,
)
from api_server.marketplace.tool_format import (
    ToolFormatError,
    ToolImplementation,
    ToolManifest,
    parse_tool_manifest,
)
from api_server.marketplace.trust import (
    NetworkPolicy,
    Severity,
    TrustPolicy,
    UnknownTrustLevelError,
    trust_policy,
)

__all__ = [
    "BUILTIN_E2E_TEMPLATES",
    "PLAYWRIGHT_TOOL_YAML",
    "DockerSocketLeakError",
    "E2ETemplateError",
    "E2ETemplateParameter",
    "E2ETestTemplate",
    "Finding",
    "MarketplaceSandbox",
    "NetworkPolicy",
    "ParsedPrivateListing",
    "PlaywrightBrowser",
    "PlaywrightConfigError",
    "PlaywrightToolConfig",
    "PrivateListingFormatError",
    "SandboxError",
    "SandboxResult",
    "SandboxSpec",
    "ScreenshotMode",
    "Severity",
    "SkillExample",
    "SkillFormatError",
    "SkillManifest",
    "StaticAnalysisError",
    "StaticAnalysisReport",
    "StaticAnalyzer",
    "ToolFormatError",
    "ToolImplementation",
    "ToolManifest",
    "TraceMode",
    "TrustPolicy",
    "UnknownTrustLevelError",
    "assert_no_docker_socket",
    "build_sandbox_run_kwargs",
    "config_schema",
    "get_e2e_template",
    "is_valid_semver",
    "load_e2e_templates",
    "parse_private_listing",
    "parse_skill_md",
    "parse_tool_manifest",
    "playwright_tool_manifest",
    "seed_playwright_listing",
    "trust_policy",
]
