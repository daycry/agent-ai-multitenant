"""Worker-side model resolution (ADR 0057 F1).

The dispatch forwards an agent's ``model_config`` keyed by ``provider`` (the
catalog kind) with no endpoint/credential; the sandbox cannot resolve it (no
DB/Vault) and used to fall back to the scripted client silently. The worker now
resolves the spec to an executable one (kind + native model + base_url +
credential) BEFORE launching the container. These tests drive
``workers.model_resolver.resolve_model_spec`` against the real Postgres + the
in-memory Vault double.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from workers.model_resolver import ModelResolutionError, resolve_model_spec, safe_spec_summary

pytestmark = pytest.mark.integration


@pytest.fixture()
def admin_sessionmaker(
    alembic_config: object, admin_database_url: str, migrations_pg_dsn: str
) -> async_sessionmaker[AsyncSession]:
    """BYPASSRLS sessionmaker on a migrated test DB, llm_providers truncated."""
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


async def _seed_provider(
    session: AsyncSession,
    *,
    kind: str,
    base_url: str | None,
    secret_field: str | None = None,
    secret_value: str | None = None,
    vault: Any = None,
) -> UUID:
    from api_server.db.llm_providers import LlmProvider
    from api_server.llm_providers.vault import provider_secret_path

    provider = LlmProvider(
        kind=kind,
        slug=f"{kind}-{uuid4().hex[:8]}",
        display_name=f"{kind} test",
        base_url=base_url,
        is_active=True,
    )
    session.add(provider)
    await session.flush()
    if secret_field and secret_value and vault is not None:
        path = provider_secret_path(provider.id)
        vault.write_secret(path, {secret_field: secret_value})
        provider.secret_vault_path = path
    await session.flush()
    pid: UUID = provider.id
    return pid


# ---------------------------------------------------------------------------
# provider-keyed spec → resolved executable spec
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resolves_provider_spec_to_kind_base_url_and_credential(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(
            session,
            kind="ollama",
            base_url="https://ollama.com/v1",
            secret_field="bearer_token",
            secret_value="cloud-bearer-DO-NOT-LEAK",
            vault=in_memory_vault,
        )

    model_config = {
        "provider": "ollama",
        "model": "qwen3-coder:480b",
        "temperature": 0.2,
        "system_prompts": {"es": "Eres CI4.", "en": "You are CI4."},
    }
    async with admin_sessionmaker() as session:
        spec = await resolve_model_spec(session, model_config, vault=in_memory_vault)

    # Executable: the runtime reads `kind` + endpoint + credential.
    assert spec["kind"] == "ollama"
    assert spec["base_url"] == "https://ollama.com/v1"
    assert spec["api_key"] == "cloud-bearer-DO-NOT-LEAK"
    assert spec["model"] == "qwen3-coder:480b"
    # The agent's persona/temperature ride along untouched.
    assert spec["temperature"] == 0.2
    assert spec["system_prompts"]["es"] == "Eres CI4."
    # Traceability: the original catalog kind stays.
    assert spec["provider"] == "ollama"


@pytest.mark.asyncio
async def test_strips_litellm_family_prefix_from_model_id(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    """Catalog ids may be LiteLLM-keyed (`ollama/llama3.1`); the provider API
    wants the bare name (same transform the assistant applies)."""
    async with admin_sessionmaker() as session, session.begin():
        await _seed_provider(session, kind="ollama", base_url="http://ollama:11434/v1")

    async with admin_sessionmaker() as session:
        spec = await resolve_model_spec(
            session, {"provider": "ollama", "model": "ollama/llama3.1"}, vault=in_memory_vault
        )
    assert spec["model"] == "llama3.1"


# ---------------------------------------------------------------------------
# scripted / pre-resolved specs pass through verbatim
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scripted_spec_passes_through_untouched(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    scripted = {"kind": "scripted", "decisions": [{"kind": "finish", "output": "done"}]}
    async with admin_sessionmaker() as session:
        spec = await resolve_model_spec(session, scripted, vault=in_memory_vault)
    assert spec == scripted


# ---------------------------------------------------------------------------
# failures are EXPLICIT — never a silent scripted fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_active_provider_of_kind_raises(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    async with admin_sessionmaker() as session:
        with pytest.raises(ModelResolutionError, match="no active llm_providers row"):
            await resolve_model_spec(
                session, {"provider": "ollama", "model": "qwen3-coder:480b"}, vault=in_memory_vault
            )


@pytest.mark.asyncio
async def test_spec_without_provider_or_kind_raises(
    admin_sessionmaker: async_sessionmaker[AsyncSession], in_memory_vault: Any
) -> None:
    async with admin_sessionmaker() as session:
        with pytest.raises(ModelResolutionError):
            await resolve_model_spec(session, {}, vault=in_memory_vault)


# ---------------------------------------------------------------------------
# log hygiene — the summary never carries the credential
# ---------------------------------------------------------------------------
def test_safe_spec_summary_has_no_credential() -> None:
    spec = {
        "kind": "ollama",
        "provider": "ollama",
        "model": "m",
        "base_url": "https://x/v1",
        "api_key": "SECRET-VALUE",
    }
    summary = safe_spec_summary(spec)
    assert summary["has_credential"] is True
    assert "SECRET-VALUE" not in str(summary)
