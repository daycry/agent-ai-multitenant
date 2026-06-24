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


def test_resolve_by_provider_id_uses_the_exact_row(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """provider_id pins the EXACT provider row: its kind + credential win over the
    spec's (kind) provider — that is what 'todo a proveedores concretos' needs."""
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from workers import model_resolver

    pid = uuid4()
    row = SimpleNamespace(
        id=pid, kind="claude_sdk", base_url=None, secret_vault_path="kv/x", is_active=True
    )

    async def _fake_get(session, provider_id):  # type: ignore[no-untyped-def]
        assert provider_id == pid
        return row

    monkeypatch.setattr("api_server.db.llm_providers.get_llm_provider", _fake_get)

    class _FakeVault:
        def read_secret(self, path):  # type: ignore[no-untyped-def]
            return {"api_key": "sk-secret-DO-NOT-LEAK"}

    spec = asyncio.run(
        model_resolver._resolve_by_provider_id(
            None,  # type: ignore[arg-type]
            {"provider": "ollama", "provider_id": str(pid), "model": "claude-sonnet-4"},
            str(pid),
            "claude-sonnet-4",
            _FakeVault(),  # type: ignore[arg-type]
        )
    )
    assert spec is not None
    assert spec["kind"] == "claude_sdk"  # the ROW's kind wins, not the spec's "ollama"
    assert spec["api_key"] == "sk-secret-DO-NOT-LEAK"


def test_resolve_by_provider_id_none_when_inactive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Inactive / missing row → None so the caller falls back to the kind path."""
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from workers import model_resolver

    async def _fake_get(session, provider_id):  # type: ignore[no-untyped-def]
        return SimpleNamespace(kind="ollama", base_url="x", secret_vault_path=None, is_active=False)

    monkeypatch.setattr("api_server.db.llm_providers.get_llm_provider", _fake_get)
    out = asyncio.run(
        model_resolver._resolve_by_provider_id(None, {}, str(uuid4()), "m", None)  # type: ignore[arg-type]
    )
    assert out is None
