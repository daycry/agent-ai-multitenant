"""Trust-level policy: the single source of truth for what each
:class:`~api_server.db.marketplace.MarketplaceTrustLevel` *implies*
(Plan 09 task_09_04).

Plan 09 binding decision: **the trust level governs the GUARDRAILS
applied, NOT availability.** Every listing — verified, community, or
experimental — can be browsed and installed; the level only decides how
much friction and how many gates the install flow imposes. This module
turns that decision into data so the rest of Plan 09 (static analysis in
task_09_05, the sandbox in task_09_06, the consent UI in task_09_07, the
install flow in task_09_11) reads one resolver instead of scattering
``if trust_level == ...`` literals across the codebase.

The policy a level resolves to is a small, frozen, hashable
:class:`TrustPolicy` record with five knobs:

  * ``signature_required`` — the listing's detached signature must be
    present and verify against the platform team's key before install.
    Only ``verified`` is signed by the platform team (plan decision (d)).
  * ``per_permission_consent_required`` — the project owner must approve
    EACH requested permission (``allowed_domains`` / ``allowed_paths`` /
    ``network_policy``) one by one. ``community`` and ``experimental``
    ALWAYS require this (plan decisions (a)+(b)); ``verified`` does not
    (minimal friction).
  * ``static_analysis_required`` — run the pre-install Bandit/semgrep
    scan (task_09_05) and block on findings above ``max_allowed_severity``.
  * ``sandbox_required`` — run the post-install probe inside the hardened
    ephemeral container (task_09_06) before the install is trusted.
  * ``max_allowed_severity`` — the highest static-analysis finding
    severity tolerated; anything stricter blocks the install. ``verified``
    tolerates up to ``MEDIUM`` (it was reviewed by the platform team);
    ``community`` tolerates up to ``LOW``; ``experimental`` tolerates
    ``NONE`` (any finding blocks).

The granular permission surface a listing requests / a project owner
consents to is ``allowed_domains``, ``allowed_paths`` and
``network_policy`` (plan decision (c)); :data:`PERMISSION_KEYS` names them
here as the one canonical tuple, and :class:`NetworkPolicy` reuses the
``none | restricted | open`` vocabulary already used by the test-runtime
templates so the whole platform speaks one network dialect.

Pure Python, no new dependencies, no I/O — safe to import anywhere.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from api_server.db.marketplace import MarketplaceTrustLevel


class Severity(enum.IntEnum):
    """Static-analysis finding severity, ordered for comparison.

    ``IntEnum`` so a policy can say ``finding.severity <=
    policy.max_allowed_severity``. ``NONE`` is the floor: a policy whose
    ``max_allowed_severity`` is ``NONE`` blocks on ANY finding.
    """

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class NetworkPolicy(enum.StrEnum):
    """Network egress posture a permission may request.

    Mirrors the test-runtime template vocabulary
    (:mod:`shared_test_runtimes`) so the platform speaks one dialect:

      * ``none``        — no network at all (default, most restrictive).
      * ``restricted``  — egress only to the consented ``allowed_domains``.
      * ``open``        — unrestricted egress (only ever granted via
                          explicit per-permission consent).
    """

    NONE = "none"
    RESTRICTED = "restricted"
    OPEN = "open"


# The granular permission surface a listing requests and a project owner
# consents to (plan decision (c)). One canonical tuple — consume this
# instead of re-typing the three strings.
PERMISSION_ALLOWED_DOMAINS = "allowed_domains"
PERMISSION_ALLOWED_PATHS = "allowed_paths"
PERMISSION_NETWORK_POLICY = "network_policy"

PERMISSION_KEYS: tuple[str, ...] = (
    PERMISSION_ALLOWED_DOMAINS,
    PERMISSION_ALLOWED_PATHS,
    PERMISSION_NETWORK_POLICY,
)


class UnknownTrustLevelError(ValueError):
    """Raised by :func:`trust_policy` for a level outside the closed set.

    Subclasses :class:`ValueError` so existing ``except ValueError``
    handlers keep working, while callers that care can catch the precise
    type.
    """


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    """The guardrails a single trust level implies.

    ``frozen`` + ``slots`` so a policy is immutable and hashable — the
    resolver hands the same instance out by reference and no caller can
    mutate the shared rule set.
    """

    level: MarketplaceTrustLevel
    signature_required: bool
    per_permission_consent_required: bool
    static_analysis_required: bool
    sandbox_required: bool
    max_allowed_severity: Severity


# =============================================================================
# The policy table — ONE source of truth, no scattered literals.
# =============================================================================
# verified: signed by the platform team (decision (d)), reviewed, minimal
# friction. Signature is mandatory; no per-permission consent; we still
# scan (defence in depth) but tolerate up to MEDIUM and skip the sandbox.
_VERIFIED_POLICY = TrustPolicy(
    level=MarketplaceTrustLevel.VERIFIED,
    signature_required=True,
    per_permission_consent_required=False,
    static_analysis_required=True,
    sandbox_required=False,
    max_allowed_severity=Severity.MEDIUM,
)

# community: third-party published. NOT signed by the platform team, so
# every requested permission needs explicit project-owner consent
# (decisions (a)+(b)), plus static analysis and a sandbox probe. Tolerates
# only LOW findings.
_COMMUNITY_POLICY = TrustPolicy(
    level=MarketplaceTrustLevel.COMMUNITY,
    signature_required=False,
    per_permission_consent_required=True,
    static_analysis_required=True,
    sandbox_required=True,
    max_allowed_severity=Severity.LOW,
)

# experimental: unvetted. Heaviest guardrails — per-permission consent,
# static analysis, sandbox, and ANY finding blocks (max severity NONE).
_EXPERIMENTAL_POLICY = TrustPolicy(
    level=MarketplaceTrustLevel.EXPERIMENTAL,
    signature_required=False,
    per_permission_consent_required=True,
    static_analysis_required=True,
    sandbox_required=True,
    max_allowed_severity=Severity.NONE,
)

_POLICIES: dict[MarketplaceTrustLevel, TrustPolicy] = {
    MarketplaceTrustLevel.VERIFIED: _VERIFIED_POLICY,
    MarketplaceTrustLevel.COMMUNITY: _COMMUNITY_POLICY,
    MarketplaceTrustLevel.EXPERIMENTAL: _EXPERIMENTAL_POLICY,
}


def trust_policy(level: MarketplaceTrustLevel | str) -> TrustPolicy:
    """Resolve the :class:`TrustPolicy` implied by a trust ``level``.

    Accepts either a :class:`MarketplaceTrustLevel` member or its string
    value (e.g. the ``trust_level`` TEXT column off an ORM row), so
    callers don't have to coerce first.

    Raises :class:`UnknownTrustLevelError` for any value outside the
    closed set — a misspelled or future level must fail loudly rather
    than silently fall through to a permissive default.
    """
    if isinstance(level, str) and not isinstance(level, MarketplaceTrustLevel):
        try:
            level = MarketplaceTrustLevel(level)
        except ValueError as exc:
            raise UnknownTrustLevelError(f"unknown trust level: {level!r}") from exc
    try:
        return _POLICIES[level]
    except KeyError as exc:  # pragma: no cover - defensive: enum kept in sync
        raise UnknownTrustLevelError(f"no policy for trust level: {level!r}") from exc


__all__ = [
    "PERMISSION_ALLOWED_DOMAINS",
    "PERMISSION_ALLOWED_PATHS",
    "PERMISSION_KEYS",
    "PERMISSION_NETWORK_POLICY",
    "NetworkPolicy",
    "Severity",
    "TrustPolicy",
    "UnknownTrustLevelError",
    "trust_policy",
]
