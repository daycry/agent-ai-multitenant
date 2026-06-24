"""Unit: the agent-runtime must feed the claude_sdk credential from the spec to
the Claude Agent SDK (sesión 2026-06-18, habilitar Claude SDK / ADR 0063).

`build_provider_client` built `ClaudeSDKModelClient` WITHOUT the credential, and
`_overlay_resolved` dropped it — so a Claude agent in the sandbox never
authenticated. Both auth modes ride the spec:

  * spec['api_key']     → ANTHROPIC_API_KEY        (API key)
  * spec['oauth_token'] → CLAUDE_CODE_OAUTH_TOKEN  (subscription Pro/Max)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest
from agent_runtime.providers import _overlay_resolved, build_provider_client


@dataclass
class _Resolved:
    base_url: str | None = None
    secret: dict[str, str] = field(default_factory=dict)


def test_build_claude_sdk_client_exports_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    build_provider_client(
        {"kind": "claude_sdk", "model": "claude-sonnet-4-5", "api_key": "sk-ant-test-DO-NOT-LEAK"}
    )
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-DO-NOT-LEAK"


def test_build_claude_sdk_client_exports_oauth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    build_provider_client(
        {
            "kind": "claude_sdk",
            "model": "claude-sonnet-4-5",
            "oauth_token": "sk-ant-oat-test-DO-NOT-LEAK",
        }
    )
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test-DO-NOT-LEAK"


def test_overlay_resolved_claude_sdk_carries_credential() -> None:
    """The in-container DB-resolver path (mirror of the worker's resolver) must
    also carry the Vault secret into the spec for claude_sdk."""
    out_api = _overlay_resolved(
        {"kind": "claude_sdk", "model": "m"},
        "claude_sdk",
        _Resolved(secret={"api_key": "sk-ant-x"}),
    )
    assert out_api["api_key"] == "sk-ant-x"
    out_oauth = _overlay_resolved(
        {"kind": "claude_sdk", "model": "m"},
        "claude_sdk",
        _Resolved(secret={"oauth_token": "sk-ant-oat-x"}),
    )
    assert out_oauth["oauth_token"] == "sk-ant-oat-x"
