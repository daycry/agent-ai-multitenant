"""Integration tests: the runtime provider factory reads config from
`llm_providers` + Vault, with an env/installer FALLBACK (Plan 11.2 task_11_2_04).

ADR 0028 / the plan's "Decisiones clave": provider config precedence is
**DB row > env**. The runtime factory (`agent_runtime.providers.
build_provider_client` / `model_from_spec`) historically read the provider
endpoint + credential straight from the JSON spec built from the
installer/env. task_11_2_04 adds an OPTIONAL resolver seam so that, when an
ACTIVE `llm_providers` row exists for the requested kind, that row's
`base_url` + its Vault-stored credential WIN; when no active row exists the
factory keeps the env/installer behaviour unchanged.

This suite drives both halves end to end against the real Postgres:

  * an active `ollama` row (base_url + a Vault bearer) ⇒ the resolver
    returns its config and the built client targets the DB `base_url` with
    the Vault bearer — the env spec values are overridden;
  * an active `azure_foundry` row ⇒ its APIM `base_url` + the Vault
    `api_key` win over the env spec;
  * NO active row (and an INACTIVE row is ignored) ⇒ the resolver returns
    `None` and the env spec is used unchanged (the fallback path);
  * with no resolver (the runtime container's default) the factory ignores
    the DB entirely — the historical behaviour.

Vault is the in-memory provider store double (no hvac); the provider HTTP
calls are driven with a mock httpx transport so no network is hit and the
resolved credential/endpoint are asserted on the outgoing request.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from shared_llm import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_BEARER = "db-vault-ollama-bearer-DO-NOT-LEAK"
_API_KEY = "db-vault-azure-apim-key-DO-NOT-LEAK"


# ---------------------------------------------------------------------------
# Helpers — seed an llm_providers row + its Vault secret, build a resolver.
# ---------------------------------------------------------------------------
def _chat_text(content: str) -> dict[str, Any]:
    return {
        "model": "x",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


async def _drive(client: Any, handler: Any) -> None:
    """Swap the built provider's transport for a mock and make one async call.

    `client.decide()` bridges to async via `asyncio.run`, which cannot run
    inside the pytest-asyncio loop — so we await the provider's `complete()`
    directly. The credential set at construction is re-applied by the
    provider's per-request `_headers()`, so the mock sees the resolved auth.
    """
    client.provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await client.provider.complete([Message(role="user", content="hi")], model="m")


async def _seed_provider(
    session: AsyncSession,
    *,
    kind: str,
    base_url: str | None,
    secret_field: str | None,
    secret_value: str | None,
    is_active: bool,
    vault: Any,
) -> UUID:
    """Insert one llm_providers row (+ its Vault secret) and return its id."""
    from api_server.db.llm_providers import LlmProvider
    from api_server.llm_providers.vault import provider_secret_path

    provider = LlmProvider(
        kind=kind,
        slug=f"{kind}-{uuid4().hex[:8]}",
        display_name=f"{kind} test",
        base_url=base_url,
        is_active=is_active,
    )
    session.add(provider)
    await session.flush()
    if secret_field and secret_value:
        path = provider_secret_path(provider.id)
        vault.write_secret(path, {secret_field: secret_value})
        provider.secret_vault_path = path
    await session.flush()
    pid: UUID = provider.id
    return pid


def _sync_resolver_from(resolved_by_kind: dict[str, Any]) -> Any:
    """A synchronous `(kind) -> ResolvedProviderConfig | None` for the factory.

    The server side resolves config asynchronously (DB + Vault); the runtime
    factory's seam is sync, so we pre-resolve into a dict and wrap it.
    """

    def _resolve(kind: str) -> Any:
        return resolved_by_kind.get(kind)

    return _resolve


@pytest.fixture()
def admin_sessionmaker(
    alembic_config: object, admin_database_url: str, migrations_pg_dsn: str
) -> async_sessionmaker[AsyncSession]:
    """A BYPASSRLS sessionmaker on a migrated test DB (platform-global table).

    Truncates ``llm_providers`` per test so the platform-global rows from one
    case never leak into the next (the catalog has no tenant_id to scope on).
    """
    import asyncio

    import asyncpg

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _truncate() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE llm_providers RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_truncate())
    engine = create_async_engine(admin_database_url, future=True)
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def in_memory_vault() -> Any:
    from api_server.llm_providers.vault import InMemoryLLMProviderVaultStore

    return InMemoryLLMProviderVaultStore()


# ===========================================================================
# Active row wins — Ollama (base_url + Vault bearer override the env spec)
# ===========================================================================
@pytest.mark.asyncio
async def test_active_ollama_row_overrides_env_spec(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    from agent_runtime.providers import build_provider_client
    from api_server.llm_providers.factory_resolver import resolve_provider_config

    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(
            session,
            kind="ollama",
            base_url="http://ollama-from-db:11434/v1",
            secret_field="bearer_token",
            secret_value=_BEARER,
            is_active=True,
            vault=in_memory_vault,
        )

    async with admin_sessionmaker() as session:
        resolved = await resolve_provider_config(session, "ollama", vault=in_memory_vault)
    assert resolved is not None
    assert resolved.base_url == "http://ollama-from-db:11434/v1"
    assert resolved.secret == {"bearer_token": _BEARER}

    # The env/installer spec points elsewhere with no credential; the active
    # DB row + Vault secret must WIN (precedence: DB row > env).
    env_spec = {
        "kind": "ollama",
        "model": "llama3.1",
        "base_url": "http://env-ollama:11434/v1",
        "api_key": None,
    }
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_chat_text("ok"))

    client = build_provider_client(env_spec, resolver=_sync_resolver_from({"ollama": resolved}))
    # The built provider picked up the DB base_url (precedence: DB row > env).
    assert client.provider.base_url == "http://ollama-from-db:11434/v1"  # type: ignore[attr-defined]
    await _drive(client, handler)

    # The request went to the DB base_url with the Vault bearer — NOT the env.
    assert seen["url"].startswith("http://ollama-from-db:11434/v1")
    assert seen["auth"] == f"Bearer {_BEARER}"


# ===========================================================================
# Active row wins — Azure Foundry (APIM base_url + Vault api_key win)
# ===========================================================================
@pytest.mark.asyncio
async def test_active_azure_row_overrides_env_spec(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    from agent_runtime.providers import build_provider_client
    from api_server.llm_providers.factory_resolver import resolve_provider_config

    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(
            session,
            kind="azure_foundry",
            base_url="https://apim-from-db.azure-api.net/foundry",
            secret_field="api_key",
            secret_value=_API_KEY,
            is_active=True,
            vault=in_memory_vault,
        )

    async with admin_sessionmaker() as session:
        resolved = await resolve_provider_config(session, "azure_foundry", vault=in_memory_vault)
    assert resolved is not None
    assert resolved.base_url == "https://apim-from-db.azure-api.net/foundry"
    assert resolved.secret == {"api_key": _API_KEY}

    env_spec = {
        "kind": "azure_foundry",
        "model": "gpt-4o-foundry",
        "deployment": "gpt-4o",
        "apim_base_url": "https://env-apim.azure-api.net/foundry",
        "subscription_key": "env-key-should-be-overridden",
    }
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["sub"] = request.headers.get("ocp-apim-subscription-key")
        return httpx.Response(200, json=_chat_text("done"))

    client = build_provider_client(
        env_spec, resolver=_sync_resolver_from({"azure_foundry": resolved})
    )
    assert client.provider.base_url == "https://apim-from-db.azure-api.net/foundry"  # type: ignore[attr-defined]
    await _drive(client, handler)

    assert "apim-from-db.azure-api.net/foundry" in seen["url"]
    assert seen["sub"] == _API_KEY


# ===========================================================================
# No active row → env fallback (and an INACTIVE row is ignored)
# ===========================================================================
@pytest.mark.asyncio
async def test_no_active_row_uses_env_fallback(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    from agent_runtime.providers import build_provider_client
    from api_server.llm_providers.factory_resolver import resolve_provider_config

    # Only an INACTIVE row exists for ollama — it must be ignored.
    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(
            session,
            kind="ollama",
            base_url="http://inactive-db:11434/v1",
            secret_field="bearer_token",
            secret_value="inactive-bearer",
            is_active=False,
            vault=in_memory_vault,
        )

    async with admin_sessionmaker() as session:
        resolved = await resolve_provider_config(session, "ollama", vault=in_memory_vault)
    # No ACTIVE row ⇒ resolver returns None ⇒ env fallback.
    assert resolved is None

    env_spec = {
        "kind": "ollama",
        "model": "llama3.1",
        "base_url": "http://env-ollama:11434/v1",
        "api_key": "env-bearer",
    }
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_chat_text("ok"))

    # Pass the resolver but it returns None for ollama ⇒ env spec wins.
    client = build_provider_client(env_spec, resolver=_sync_resolver_from({"ollama": resolved}))
    assert client.provider.base_url == "http://env-ollama:11434/v1"  # type: ignore[attr-defined]
    await _drive(client, handler)

    assert seen["url"].startswith("http://env-ollama:11434/v1")
    assert seen["auth"] == "Bearer env-bearer"


# ===========================================================================
# No resolver at all → the factory ignores the DB (runtime-container default)
# ===========================================================================
@pytest.mark.asyncio
async def test_factory_without_resolver_uses_spec_only(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    """With `resolver=None` (the agent-runtime container default) the factory
    never touches the DB — the spec is authoritative, exactly as before."""
    from agent_runtime.model import model_from_spec
    from agent_runtime.providers import OllamaModelClient

    # An active row exists, but with no resolver it is irrelevant.
    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(
            session,
            kind="ollama",
            base_url="http://db-should-be-ignored:11434/v1",
            secret_field="bearer_token",
            secret_value="ignored",
            is_active=True,
            vault=in_memory_vault,
        )

    client = model_from_spec(
        {"kind": "ollama", "model": "llama3.1", "base_url": "http://spec-only:11434/v1"}
    )
    assert isinstance(client, OllamaModelClient)
    assert client.provider.base_url == "http://spec-only:11434/v1"  # type: ignore[attr-defined]


# ===========================================================================
# build_llm_provider targets the REQUESTED provider, not the newest of the kind
# ===========================================================================
@pytest.mark.asyncio
async def test_build_llm_provider_targets_requested_row_not_newest_of_kind(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    """Regression: ``build_llm_provider(provider_id)`` must use THAT row's
    endpoint, not the newest-active provider of its kind.

    With two active ``ollama`` rows (cloud + a newer local), building for the
    older cloud id used to resolve by kind → the newer local row, so
    sync-models/assistant cross-wired ``ollama-cloud`` to ``ollama-local`` and
    returned the wrong models. The build must target the requested row.
    """
    from api_server.llm_providers.factory import build_llm_provider

    # Older row — the one we target.
    async with admin_sessionmaker() as session, session.begin():
        cloud = await _seed_provider(
            session,
            kind="ollama",
            base_url="https://cloud-target.example/v1",
            secret_field=None,
            secret_value=None,
            is_active=True,
            vault=in_memory_vault,
        )
    # Newer active row of the SAME kind — would win the kind resolver.
    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(
            session,
            kind="ollama",
            base_url="http://local-newer.example:11434/v1",
            secret_field=None,
            secret_value=None,
            is_active=True,
            vault=in_memory_vault,
        )

    async with admin_sessionmaker() as session:
        provider = await build_llm_provider(
            session, provider_id=cloud, model="m", vault=in_memory_vault
        )
    assert provider is not None
    # The built client targets the REQUESTED (cloud) row, not the newer local.
    assert provider.base_url == "https://cloud-target.example/v1"  # type: ignore[attr-defined]


# ===========================================================================
# resolve_provider_config tolerates a missing Vault store (base_url still wins)
# ===========================================================================
@pytest.mark.asyncio
async def test_resolve_without_vault_keeps_base_url_no_secret(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    from api_server.llm_providers.factory_resolver import resolve_provider_config

    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(
            session,
            kind="ollama",
            base_url="http://db-no-vault:11434/v1",
            secret_field="bearer_token",
            secret_value=_BEARER,
            is_active=True,
            vault=in_memory_vault,
        )

    # vault=None ⇒ the row still wins for base_url, but no secret is read.
    async with admin_sessionmaker() as session:
        resolved = await resolve_provider_config(session, "ollama", vault=None)
    assert resolved is not None
    assert resolved.base_url == "http://db-no-vault:11434/v1"
    assert resolved.secret == {}


# ===========================================================================
# Sanity: the seeded secret value never appears in the resolved config repr
# of a row's stored columns (defence-in-depth; the credential is in Vault).
# ===========================================================================
@pytest.mark.asyncio
async def test_resolved_config_carries_only_vault_secret(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    from api_server.db.llm_providers import get_llm_provider
    from api_server.llm_providers.factory_resolver import resolve_provider_config

    async with admin_sessionmaker() as session, session.begin():
        pid = await _seed_provider(
            session,
            kind="claude_sdk",
            base_url=None,
            secret_field="oauth_token",
            secret_value="claude-oauth-secret",
            is_active=True,
            vault=in_memory_vault,
        )

    async with admin_sessionmaker() as session:
        row = await get_llm_provider(session, pid)
        assert row is not None
        # The DB row holds only the Vault pointer, never the secret value.
        dumped = json.dumps(
            {
                "kind": row.kind,
                "base_url": row.base_url,
                "secret_vault_path": row.secret_vault_path,
                "config": row.config,
            }
        )
        assert "claude-oauth-secret" not in dumped
        resolved = await resolve_provider_config(session, "claude_sdk", vault=in_memory_vault)
    assert resolved is not None
    # The credential lives in the Vault-read secret dict (in memory only).
    assert resolved.secret == {"oauth_token": "claude-oauth-secret"}
