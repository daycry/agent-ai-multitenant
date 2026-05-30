"""Marketplace domain logic (Plan 09 Fase B+).

The ORM contract lives in :mod:`api_server.db.marketplace`; the REST
surface in :mod:`api_server.routers.marketplace`. This package holds the
*policy* layer that sits between them — the data-driven rules that the
trust level of a listing implies (``trust.py``), the pre-install static
analysis + gate (``static_analysis.py``), and (in later tasks) the sandbox
machinery.
"""

from __future__ import annotations

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
    "Finding",
    "NetworkPolicy",
    "Severity",
    "StaticAnalysisError",
    "StaticAnalysisReport",
    "StaticAnalyzer",
    "TrustPolicy",
    "UnknownTrustLevelError",
    "trust_policy",
]
