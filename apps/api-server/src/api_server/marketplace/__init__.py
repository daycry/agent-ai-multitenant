"""Marketplace domain logic (Plan 09 Fase B+).

The ORM contract lives in :mod:`api_server.db.marketplace`; the REST
surface in :mod:`api_server.routers.marketplace`. This package holds the
*policy* layer that sits between them — the data-driven rules that the
trust level of a listing implies (``trust.py``), the pre-install static
analysis + gate (``static_analysis.py``), and (in later tasks) the sandbox
machinery.
"""

from __future__ import annotations

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
from api_server.marketplace.trust import (
    NetworkPolicy,
    Severity,
    TrustPolicy,
    UnknownTrustLevelError,
    trust_policy,
)

__all__ = [
    "DockerSocketLeakError",
    "Finding",
    "MarketplaceSandbox",
    "NetworkPolicy",
    "SandboxError",
    "SandboxResult",
    "SandboxSpec",
    "Severity",
    "SkillExample",
    "SkillFormatError",
    "SkillManifest",
    "StaticAnalysisError",
    "StaticAnalysisReport",
    "StaticAnalyzer",
    "TrustPolicy",
    "UnknownTrustLevelError",
    "assert_no_docker_socket",
    "build_sandbox_run_kwargs",
    "is_valid_semver",
    "parse_skill_md",
    "trust_policy",
]
