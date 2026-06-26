"""Unit test — the worker builds its Vault store from its OWN settings.

Bug: the agent execution path resolves the LLM provider credential through
``_default_vault_store()``, which used to delegate to the api-server's
``get_provider_vault_store()``. That builder reads the **api-server** settings
(``API_SERVER_VAULT_*``), but the worker process is configured with its own
``WORKERS_VAULT_URL`` / ``WORKERS_VAULT_TOKEN`` and does NOT carry the
api-server env. So the store came back ``None``, the secret was never read, and
every agent ran with ``has_credential=False`` — no provider auth, ever.

The worker must build the store from the worker's own settings. These tests pin
that: a configured worker token yields a real store WITHOUT touching the
api-server builder; no token yields ``None`` (degrade to no-credential, which is
fine for a keyless local Ollama).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _boom() -> object:
    raise AssertionError("_default_vault_store must NOT use the api-server builder")


def test_default_vault_store_built_from_worker_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_server.llm_providers.vault import HvacLLMProviderVaultStore
    from workers import execution

    # Worker settings carry the Vault creds (WORKERS_VAULT_*), the way the
    # compose wires them — NOT the api-server's API_SERVER_VAULT_*.
    fake = SimpleNamespace(vault_token="dev-root-token", vault_url="http://vault:8200")
    monkeypatch.setattr("workers.config.get_settings", lambda: fake)
    # Guard: the worker must no longer fall back to the api-server builder
    # (which returns None in the worker, the root cause of has_credential=False).
    import api_server.routers.llm_providers as llmr

    monkeypatch.setattr(llmr, "get_provider_vault_store", _boom)

    store = execution._default_vault_store()

    assert store is not None
    assert isinstance(store, HvacLLMProviderVaultStore)


def test_default_vault_store_none_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers import execution

    # No token configured → no store → resolution degrades to no-credential
    # (acceptable for a keyless local Ollama), never raises.
    fake = SimpleNamespace(vault_token=None, vault_url="http://vault:8200")
    monkeypatch.setattr("workers.config.get_settings", lambda: fake)

    assert execution._default_vault_store() is None
