"""Layered guardrails config resolution (Plan 11 task_11_02).

Covers the acceptance signals from the roadmap:
  - project overrides tenant overrides platform for an *unlocked* field;
  - a platform-*locked* field cannot be overridden by a lower layer;
  - a mandatory baseline guardrail (platform, locked) survives a tenant
    trying to remove it;
  - cross-tenant: tenant A's config never leaks into tenant B's resolved
    pipeline.

The layered-config engine is pure library code — it resolves from
passed-in per-layer configs, no DB persistence in Phase A. The
cross-tenant case (`@pytest.mark.cross_tenant`) therefore asserts the
*in-memory* isolation property: resolving for tenant A and tenant B from
their respective configs yields no bleed between the two, and the shared
platform baseline is identical for both without any tenant value leaking
across.
"""

from __future__ import annotations

import pytest
from shared_guardrails import (
    Action,
    GuardrailContext,
    GuardrailPipeline,
    LayerConfig,
    LockedFieldOverrideError,
    RejectedOverride,
    resolve_config,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# project overrides tenant overrides platform (unlocked field)               #
# --------------------------------------------------------------------------- #


def test_project_overrides_tenant_overrides_platform_unlocked() -> None:
    # Same guardrail key (`id: tone`) declared at all three layers with a
    # different configured action. The platform did NOT lock it, so the
    # most-specific layer (project) must win.
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "tone",
                        "type": "keyword",
                        "action": "warn",
                        "config": {"keywords": ["x"]},
                    }
                ]
            }
        },
    )
    tenant = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "tone",
                        "type": "keyword",
                        "action": "redact",
                        "config": {"keywords": ["x"]},
                    }
                ]
            }
        },
    )
    project = LayerConfig.from_dict(
        "project",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "tone",
                        "type": "keyword",
                        "action": "block",
                        "config": {"keywords": ["x"]},
                    }
                ]
            }
        },
    )

    resolved = resolve_config(platform, tenant, project)

    specs = resolved.config.specs_for("pre_llm")
    assert len(specs) == 1
    assert specs[0].action is Action.BLOCK
    assert resolved.winning_layer("pre_llm", "tone") == "project"
    assert resolved.rejected_overrides == []

    # And it runs as the project configured it: block on a match.
    decision = GuardrailPipeline(resolved.config).run(
        GuardrailContext(hook="pre_llm", prompt="contains x here")
    )
    assert decision.action is Action.BLOCK
    assert decision.allowed is False


def test_tenant_overrides_platform_when_project_absent() -> None:
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "post_llm": [
                    {
                        "id": "leak",
                        "type": "keyword",
                        "action": "warn",
                        "config": {"keywords": ["secret"]},
                    }
                ]
            }
        },
    )
    tenant = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "post_llm": [
                    {
                        "id": "leak",
                        "type": "keyword",
                        "action": "redact",
                        "config": {"keywords": ["secret"]},
                    }
                ]
            }
        },
    )

    resolved = resolve_config(platform, tenant, None)

    specs = resolved.config.specs_for("post_llm")
    assert len(specs) == 1
    assert specs[0].action is Action.REDACT
    assert resolved.winning_layer("post_llm", "leak") == "tenant"


def test_layers_union_distinct_keys() -> None:
    # Distinct guardrail keys from different layers all survive.
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "pre_llm": [{"id": "p", "type": "keyword", "config": {"keywords": ["a"]}}]
            }
        },
    )
    tenant = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "pre_llm": [{"id": "t", "type": "keyword", "config": {"keywords": ["b"]}}]
            }
        },
    )
    project = LayerConfig.from_dict(
        "project",
        {
            "guardrails": {
                "pre_llm": [{"id": "j", "type": "keyword", "config": {"keywords": ["c"]}}]
            }
        },
    )

    resolved = resolve_config(platform, tenant, project)
    keys = [s.key for s in resolved.config.specs_for("pre_llm")]
    assert keys == ["p", "t", "j"]
    assert resolved.winning_layer("pre_llm", "p") == "platform"
    assert resolved.winning_layer("pre_llm", "t") == "tenant"
    assert resolved.winning_layer("pre_llm", "j") == "project"


# --------------------------------------------------------------------------- #
# a locked platform field cannot be overridden                               #
# --------------------------------------------------------------------------- #


def test_locked_platform_field_cannot_be_overridden() -> None:
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "pii",
                        "type": "keyword",
                        "action": "redact",
                        "locked": True,
                        "config": {"keywords": ["dni"]},
                    }
                ]
            }
        },
    )
    # Tenant tries to weaken it to a mere warn.
    tenant = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "pii",
                        "type": "keyword",
                        "action": "warn",
                        "config": {"keywords": ["dni"]},
                    }
                ]
            }
        },
    )

    resolved = resolve_config(platform, tenant, None)

    specs = resolved.config.specs_for("pre_llm")
    assert len(specs) == 1
    # Platform's locked action stands; tenant's downgrade ignored.
    assert specs[0].action is Action.REDACT
    assert resolved.winning_layer("pre_llm", "pii") == "platform"
    assert resolved.locked_keys["pre_llm"] == ["pii"]

    # The ignored attempt is surfaced, not silently swallowed.
    assert len(resolved.rejected_overrides) == 1
    rej = resolved.rejected_overrides[0]
    assert isinstance(rej, RejectedOverride)
    assert rej.hook == "pre_llm"
    assert rej.key == "pii"
    assert rej.attempted_by == "tenant"


def test_locked_platform_field_blocks_project_too() -> None:
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "inj",
                        "type": "keyword",
                        "action": "block",
                        "locked": True,
                        "config": {"keywords": ["ignore previous"]},
                    }
                ]
            }
        },
    )
    project = LayerConfig.from_dict(
        "project",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "inj",
                        "type": "keyword",
                        "action": "warn",
                        "config": {"keywords": ["ignore previous"]},
                    }
                ]
            }
        },
    )

    resolved = resolve_config(platform, None, project)
    assert resolved.config.specs_for("pre_llm")[0].action is Action.BLOCK
    assert {r.attempted_by for r in resolved.rejected_overrides} == {"project"}


def test_strict_mode_raises_on_locked_override() -> None:
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "post_llm": [
                    {
                        "id": "secret",
                        "type": "regex",
                        "action": "redact",
                        "locked": True,
                        "config": {"pattern": "sk-[a-z0-9]+"},
                    }
                ]
            }
        },
    )
    tenant = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "post_llm": [
                    {
                        "id": "secret",
                        "type": "regex",
                        "action": "warn",
                        "config": {"pattern": "sk-[a-z0-9]+"},
                    }
                ]
            }
        },
    )
    with pytest.raises(LockedFieldOverrideError) as exc:
        resolve_config(platform, tenant, None, strict=True)
    assert exc.value.key == "secret"
    assert exc.value.layer == "tenant"
    assert exc.value.hook == "post_llm"


# --------------------------------------------------------------------------- #
# mandatory baseline survives a tenant trying to remove it                   #
# --------------------------------------------------------------------------- #


def test_mandatory_baseline_survives_tenant_removal_attempt() -> None:
    # Platform baseline: PII guardrail, locked => mandatory.
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "pii_baseline",
                        "type": "keyword",
                        "action": "redact",
                        "locked": True,
                        "config": {"keywords": ["iban"]},
                    }
                ]
            }
        },
    )
    # Tenant tries to remove it outright.
    tenant = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "pre_llm": [{"id": "pii_baseline", "type": "keyword", "config": {"remove": True}}]
            }
        },
    )

    resolved = resolve_config(platform, tenant, None)

    specs = resolved.config.specs_for("pre_llm")
    assert [s.key for s in specs] == ["pii_baseline"]
    assert specs[0].action is Action.REDACT
    assert resolved.rejected_overrides[0].reason.startswith("removal")

    # Baseline still fires on PII despite the tenant's removal attempt.
    decision = GuardrailPipeline(resolved.config).run(
        GuardrailContext(hook="pre_llm", prompt="account iban es91...")
    )
    assert decision.triggered is True
    assert decision.action is Action.REDACT


def test_unlocked_guardrail_can_be_removed_by_lower_layer() -> None:
    # Contrast: an *unlocked* platform guardrail CAN be removed downstream.
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "pre_llm": [{"id": "optional", "type": "keyword", "config": {"keywords": ["foo"]}}]
            }
        },
    )
    tenant = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "pre_llm": [{"id": "optional", "type": "keyword", "config": {"remove": True}}]
            }
        },
    )
    resolved = resolve_config(platform, tenant, None)
    assert resolved.config.specs_for("pre_llm") == []
    assert resolved.rejected_overrides == []


# --------------------------------------------------------------------------- #
# cross-tenant: tenant A config never leaks into tenant B                    #
# --------------------------------------------------------------------------- #


@pytest.mark.cross_tenant
def test_cross_tenant_config_does_not_leak() -> None:
    # A shared, locked platform baseline both tenants inherit unchanged.
    platform = LayerConfig.from_dict(
        "platform",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "pii",
                        "type": "keyword",
                        "action": "redact",
                        "locked": True,
                        "config": {"keywords": ["dni"]},
                    }
                ]
            }
        },
    )
    # Tenant A adds a tenant-private guardrail and tries to weaken PII.
    tenant_a = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "a_secret",
                        "type": "keyword",
                        "action": "block",
                        "config": {"keywords": ["acme-internal"]},
                    },
                    {
                        "id": "pii",
                        "type": "keyword",
                        "action": "warn",
                        "config": {"keywords": ["dni"]},
                    },
                ]
            }
        },
    )
    # Tenant B has its own, unrelated guardrail and never touches PII.
    tenant_b = LayerConfig.from_dict(
        "tenant",
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "id": "b_topic",
                        "type": "keyword",
                        "action": "warn",
                        "config": {"keywords": ["competitor"]},
                    },
                ]
            }
        },
    )

    resolved_a = resolve_config(platform, tenant_a, None)
    resolved_b = resolve_config(platform, tenant_b, None)

    keys_a = {s.key for s in resolved_a.config.specs_for("pre_llm")}
    keys_b = {s.key for s in resolved_b.config.specs_for("pre_llm")}

    # Tenant A's private guardrail must NOT appear in tenant B's pipeline.
    assert "a_secret" in keys_a
    assert "a_secret" not in keys_b
    # And vice versa.
    assert "b_topic" in keys_b
    assert "b_topic" not in keys_a

    # The shared locked baseline is present and identical for both, with
    # no tenant value bleeding across: A's attempt to weaken PII to warn
    # was rejected, so both resolve PII as the platform's redact.
    def _pii_action(resolved: object) -> Action | None:
        cfg = resolved.config  # type: ignore[attr-defined]
        for s in cfg.specs_for("pre_llm"):
            if s.key == "pii":
                return s.action
        return None

    assert _pii_action(resolved_a) is Action.REDACT
    assert _pii_action(resolved_b) is Action.REDACT
    # B never touched PII, so it has no rejected override; A's was logged.
    assert resolved_b.rejected_overrides == []
    assert any(r.key == "pii" and r.attempted_by == "tenant" for r in resolved_a.rejected_overrides)
