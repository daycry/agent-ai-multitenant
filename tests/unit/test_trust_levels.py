"""Unit tests for the trust-level policy (Plan 09 task_09_04).

Pins the binding Plan 09 decisions into executable assertions:

  (a) the trust level governs GUARDRAILS, not availability;
  (b) ``community`` AND ``experimental`` always require per-permission
      consent;
  (d) ``verified`` is the signed tier.

These are pure-Python, no DB / Docker / network — the policy is a frozen
data table plus a resolver. (No ``cross_tenant`` marker: this module
touches no tenant-owned rows; the multi-tenancy guarantee is unaffected.)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from api_server.db.marketplace import MarketplaceTrustLevel
from api_server.marketplace.trust import (
    PERMISSION_ALLOWED_DOMAINS,
    PERMISSION_ALLOWED_PATHS,
    PERMISSION_KEYS,
    PERMISSION_NETWORK_POLICY,
    NetworkPolicy,
    Severity,
    TrustPolicy,
    UnknownTrustLevelError,
    trust_policy,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Each level resolves to the documented policy
# ---------------------------------------------------------------------------
def test_verified_policy_is_signed_minimal_friction() -> None:
    """verified: signed by the platform team (decision (d)), reviewed, so
    NO per-permission consent and the sandbox is skipped — minimal
    friction. Static analysis still runs (defence in depth) tolerating up
    to MEDIUM."""
    policy = trust_policy(MarketplaceTrustLevel.VERIFIED)
    assert policy.level is MarketplaceTrustLevel.VERIFIED
    assert policy.signature_required is True
    assert policy.per_permission_consent_required is False
    assert policy.static_analysis_required is True
    assert policy.sandbox_required is False
    assert policy.max_allowed_severity is Severity.MEDIUM


def test_community_policy_consent_and_scan_and_sandbox() -> None:
    """community: third-party, NOT signed, every permission needs consent
    (decisions (a)+(b)), static analysis + sandbox, tolerates only LOW."""
    policy = trust_policy(MarketplaceTrustLevel.COMMUNITY)
    assert policy.level is MarketplaceTrustLevel.COMMUNITY
    assert policy.signature_required is False
    assert policy.per_permission_consent_required is True
    assert policy.static_analysis_required is True
    assert policy.sandbox_required is True
    assert policy.max_allowed_severity is Severity.LOW


def test_experimental_policy_is_strictest() -> None:
    """experimental: unvetted — heaviest guardrails; ANY finding blocks
    (max severity NONE)."""
    policy = trust_policy(MarketplaceTrustLevel.EXPERIMENTAL)
    assert policy.level is MarketplaceTrustLevel.EXPERIMENTAL
    assert policy.signature_required is False
    assert policy.per_permission_consent_required is True
    assert policy.static_analysis_required is True
    assert policy.sandbox_required is True
    assert policy.max_allowed_severity is Severity.NONE


# ---------------------------------------------------------------------------
# Plan decisions as cross-cutting invariants
# ---------------------------------------------------------------------------
def test_community_and_experimental_always_require_per_permission_consent() -> None:
    """Decision (b): community AND experimental ALWAYS require consent."""
    for level in (MarketplaceTrustLevel.COMMUNITY, MarketplaceTrustLevel.EXPERIMENTAL):
        assert trust_policy(level).per_permission_consent_required is True


def test_verified_does_not_require_per_permission_consent() -> None:
    """Decision (d): verified is signed + minimal friction — no consent."""
    assert trust_policy(MarketplaceTrustLevel.VERIFIED).per_permission_consent_required is False


def test_only_verified_requires_signature() -> None:
    """Decision (d): verified == the signed tier; the others are not."""
    assert trust_policy(MarketplaceTrustLevel.VERIFIED).signature_required is True
    assert trust_policy(MarketplaceTrustLevel.COMMUNITY).signature_required is False
    assert trust_policy(MarketplaceTrustLevel.EXPERIMENTAL).signature_required is False


def test_every_trust_level_resolves_to_a_policy() -> None:
    """Decision (a): the level gates guardrails, not availability — EVERY
    level resolves to a policy (no level is rejected/unavailable)."""
    for level in MarketplaceTrustLevel:
        policy = trust_policy(level)
        assert isinstance(policy, TrustPolicy)
        assert policy.level is level


def test_guardrail_strictness_is_monotonic() -> None:
    """The tolerated severity tightens verified >= community >= experimental
    — a single, ordered ladder of trust."""
    verified = trust_policy(MarketplaceTrustLevel.VERIFIED)
    community = trust_policy(MarketplaceTrustLevel.COMMUNITY)
    experimental = trust_policy(MarketplaceTrustLevel.EXPERIMENTAL)
    assert (
        verified.max_allowed_severity
        > community.max_allowed_severity
        > experimental.max_allowed_severity
    )


# ---------------------------------------------------------------------------
# Resolver accepts the string form and rejects unknown levels
# ---------------------------------------------------------------------------
def test_resolver_accepts_string_value() -> None:
    """The ``trust_level`` TEXT column off an ORM row resolves directly."""
    assert trust_policy("verified") is trust_policy(MarketplaceTrustLevel.VERIFIED)
    assert trust_policy("community").level is MarketplaceTrustLevel.COMMUNITY


def test_resolver_returns_the_same_shared_instance() -> None:
    """The policy table hands out frozen instances by reference (no copy
    per call) so identity holds."""
    assert trust_policy(MarketplaceTrustLevel.VERIFIED) is trust_policy(
        MarketplaceTrustLevel.VERIFIED
    )


@pytest.mark.parametrize("bad", ["", "trusted", "VERIFIED", "unknown", "none"])
def test_unknown_level_string_errors(bad: str) -> None:
    with pytest.raises(UnknownTrustLevelError):
        trust_policy(bad)


def test_unknown_level_error_is_a_value_error() -> None:
    """Subclasses ValueError so legacy ``except ValueError`` still catches."""
    assert issubclass(UnknownTrustLevelError, ValueError)
    with pytest.raises(ValueError):
        trust_policy("definitely-not-a-level")


# ---------------------------------------------------------------------------
# Policy immutability + permission surface (decision (c))
# ---------------------------------------------------------------------------
def test_policy_is_frozen_and_hashable() -> None:
    policy = trust_policy(MarketplaceTrustLevel.COMMUNITY)
    assert hash(policy) is not None
    with pytest.raises(FrozenInstanceError):
        policy.sandbox_required = False  # type: ignore[misc]


def test_permission_surface_is_the_documented_three() -> None:
    """Decision (c): allowed_domains, allowed_paths, network_policy."""
    assert PERMISSION_KEYS == (
        PERMISSION_ALLOWED_DOMAINS,
        PERMISSION_ALLOWED_PATHS,
        PERMISSION_NETWORK_POLICY,
    )
    assert set(PERMISSION_KEYS) == {"allowed_domains", "allowed_paths", "network_policy"}


def test_network_policy_vocabulary_matches_runtime_dialect() -> None:
    """network_policy reuses the test-runtime none|restricted|open set."""
    assert {p.value for p in NetworkPolicy} == {"none", "restricted", "open"}


def test_severity_is_ordered_for_comparison() -> None:
    assert Severity.NONE < Severity.LOW < Severity.MEDIUM < Severity.HIGH < Severity.CRITICAL
