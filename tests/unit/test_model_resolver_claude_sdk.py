"""Unit: the worker's model resolver must carry the claude_sdk credential into
the executable spec (sesión 2026-06-18, habilitar Claude SDK).

The investigation found the `claude_sdk` branch of `_overlay_provider_fields`
was a no-op (`pass`) — so the credential stored in Vault NEVER reached the
agent-runtime spec, and a Claude agent in the sandbox could not authenticate.
Both auth modes ride the SAME kind (ADR 0021, no 5th provider):

  * API key       → secret['api_key']    → spec['api_key']    (→ ANTHROPIC_API_KEY)
  * Subscription  → secret['oauth_token'] → spec['oauth_token'] (→ CLAUDE_CODE_OAUTH_TOKEN)
"""

from __future__ import annotations

from workers.model_resolver import _overlay_provider_fields, safe_spec_summary


def test_claude_sdk_api_key_reaches_the_spec() -> None:
    spec = _overlay_provider_fields(
        {"kind": "claude_sdk", "model": "claude-sonnet-4-5"},
        "claude_sdk",
        base_url=None,
        secret={"api_key": "sk-ant-test-DO-NOT-LEAK"},
    )
    assert spec["api_key"] == "sk-ant-test-DO-NOT-LEAK"


def test_claude_sdk_subscription_token_reaches_the_spec() -> None:
    spec = _overlay_provider_fields(
        {"kind": "claude_sdk", "model": "claude-sonnet-4-5"},
        "claude_sdk",
        base_url=None,
        secret={"oauth_token": "sk-ant-oat-test-DO-NOT-LEAK"},
    )
    assert spec["oauth_token"] == "sk-ant-oat-test-DO-NOT-LEAK"


def test_safe_summary_flags_a_claude_subscription_credential() -> None:
    summary = safe_spec_summary({"kind": "claude_sdk", "oauth_token": "x"})
    assert summary["has_credential"] is True
    # never leak the value
    assert "x" not in str({k: v for k, v in summary.items() if k != "has_credential"})
