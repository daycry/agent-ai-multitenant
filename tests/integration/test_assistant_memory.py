"""Integration tests for the personal assistant's user-memory (ADR 0054).

  * ``remember_user_fact`` writes a private, per-user ``memory_entries`` row
    stamped ``source='assistant'``, and dedups an identical fact.
  * ``recall_user_memories`` returns a user's own private memories and NEVER
    another user's (cross-user isolation within the tenant).
  * The chat flow: when the model calls the ``remember_about_me`` tool, the
    fact is persisted.
  * The chat flow: stored facts are injected into the system prompt ("Lo que
    sé de ti") so the assistant knows the user without a tool call.

Real throwaway DB (see conftest).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    """One toggle-ON tenant with TWO Tenant Admins (A, B) — same tenant, so
    cross-user memory isolation has to come from the ``user_id`` filter."""
    tenant = uuid4()
    admin_a, admin_b = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, tenant_settings, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled)"
            " VALUES ($1, 'Tenant Mem', 'tenant-mem', true)",
            tenant,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            admin_a,
            "admin-a@mem.test",
            "argon2-placeholder",
            admin_b,
            "admin-b@mem.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant,
            admin_a,
            uuid4(),
            tenant,
            admin_b,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin_a": admin_a, "admin_b": admin_b}


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _principal(user_id: UUID, tenant_id: UUID):
    from api_server.auth.deps import AuthPrincipal

    return AuthPrincipal(user_id=user_id, session_id=uuid4(), tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# Write + dedup
# ===========================================================================
@pytest.mark.asyncio
async def test_remember_writes_private_memory_and_dedups(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    from api_server.assistant.memory import remember_user_fact
    from api_server.auth.deps import open_tenant_session
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    principal = _principal(seeded["admin_a"], seeded["tenant"])

    async with open_tenant_session(principal) as session:
        first = await remember_user_fact(
            session, tenant_id=seeded["tenant"], user_id=seeded["admin_a"], content="Se llama Jose"
        )
    assert first["stored"] is True

    # Same fact again (committed) → deduped, not re-written.
    async with open_tenant_session(principal) as session:
        second = await remember_user_fact(
            session,
            tenant_id=seeded["tenant"],
            user_id=seeded["admin_a"],
            content="  se llama   jose ",
        )
    assert second["stored"] is False
    assert second["deduped"] is True

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT content, scope, metadata->>'source' AS source"
            " FROM memory_entries WHERE user_id = $1 AND deleted_at IS NULL",
            seeded["admin_a"],
        )
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["scope"] == "private"
    assert rows[0]["source"] == "assistant"
    assert rows[0]["content"] == "Se llama Jose"


# ===========================================================================
# Recall isolation
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_recall_is_isolated_per_user(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    from api_server.assistant.memory import recall_user_memories, remember_user_fact
    from api_server.auth.deps import open_tenant_session
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()

    async with open_tenant_session(_principal(seeded["admin_a"], seeded["tenant"])) as s:
        await remember_user_fact(
            s, tenant_id=seeded["tenant"], user_id=seeded["admin_a"], content="Le gusta el cafe"
        )
    async with open_tenant_session(_principal(seeded["admin_b"], seeded["tenant"])) as s:
        await remember_user_fact(
            s, tenant_id=seeded["tenant"], user_id=seeded["admin_b"], content="Le gusta el te"
        )

    async with open_tenant_session(_principal(seeded["admin_a"], seeded["tenant"])) as s:
        a_mem = await recall_user_memories(s, tenant_id=seeded["tenant"], user_id=seeded["admin_a"])
    async with open_tenant_session(_principal(seeded["admin_b"], seeded["tenant"])) as s:
        b_mem = await recall_user_memories(s, tenant_id=seeded["tenant"], user_id=seeded["admin_b"])

    assert a_mem == ["Le gusta el cafe"]
    assert b_mem == ["Le gusta el te"]


# ===========================================================================
# Chat flow — the model calls the remember tool → fact persisted
# ===========================================================================
def _scripted_remember(content: str):
    """A scripted model: one tool round calling remember_about_me, then an answer."""
    from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel, ToolInvocation

    return ScriptedAssistantModel(
        turns=[
            ModelTurn(
                tool_calls=(
                    ToolInvocation(
                        name="remember_about_me",
                        arguments={"content": content, "type": "semantic"},
                    ),
                )
            ),
            ModelTurn(content="¡Encantado! Lo recordaré."),
        ]
    )


@pytest.mark.asyncio
async def test_chat_remember_tool_persists_fact(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    from api_server.routers.assistant import get_assistant_model

    configured_app.dependency_overrides[get_assistant_model] = lambda: _scripted_remember(
        "Se llama Jose"
    )
    token = await _mint(seeded["admin_a"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/assistant/chat", json={"message": "hola, me llamo Jose"}, headers=headers
        )
    assert resp.status_code == 200, resp.text
    assert "remember_about_me" in resp.json()["tools_called"]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT content, scope, metadata->>'source' AS source"
            " FROM memory_entries WHERE user_id = $1 AND deleted_at IS NULL",
            seeded["admin_a"],
        )
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["content"] == "Se llama Jose"
    assert rows[0]["scope"] == "private"
    assert rows[0]["source"] == "assistant"


@pytest.mark.asyncio
async def test_chat_converges_on_repeated_tool_calls(
    configured_app, migrations_pg_dsn: str
) -> None:
    """An over-eager model re-calling the SAME tool every round must NOT loop:
    the tool runs once and the turn converges (not 6 rounds of the same call)."""
    seeded = await _seed(migrations_pg_dsn)
    from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel, ToolInvocation
    from api_server.routers.assistant import get_assistant_model

    same = ToolInvocation(
        name="remember_about_me", arguments={"content": "Se llama Jose", "type": "semantic"}
    )
    scripted = ScriptedAssistantModel(
        turns=[
            ModelTurn(tool_calls=(same,)),
            ModelTurn(tool_calls=(same,)),  # repeat — must be deduped, not re-run
            ModelTurn(content="Hecho, Jose."),
        ]
    )
    configured_app.dependency_overrides[get_assistant_model] = lambda: scripted
    token = await _mint(seeded["admin_a"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/assistant/chat", json={"message": "me llamo Jose"}, headers=headers
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The tool ran exactly once and the loop converged immediately.
    assert body["tools_called"].count("remember_about_me") == 1
    assert body["rounds"] == 1
    assert body["answer"] == "Hecho, Jose."

    # And only one memory row was stored.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE user_id = $1 AND deleted_at IS NULL",
            seeded["admin_a"],
        )
    finally:
        await conn.close()
    assert count == 1


@pytest.mark.asyncio
async def test_chat_caps_repeated_tool_with_different_args(
    configured_app, migrations_pg_dsn: str
) -> None:
    """An over-eager model re-calling the SAME tool with DIFFERENT args every
    round (e.g. inventing several phrasings of one fact) must NOT run away: the
    per-tool cap (MAX_CALLS_PER_TOOL) bounds how many times it runs in a turn."""
    seeded = await _seed(migrations_pg_dsn)
    from api_server.assistant.graph import (
        MAX_CALLS_PER_TOOL,
        ModelTurn,
        ScriptedAssistantModel,
        ToolInvocation,
    )
    from api_server.routers.assistant import get_assistant_model

    # Distinct phrasings of the same fact → distinct signatures (NOT deduped),
    # more of them than the cap allows.
    turns = [
        ModelTurn(
            tool_calls=(
                ToolInvocation(
                    name="remember_about_me", arguments={"content": f"Se llama Jose ({i})"}
                ),
            )
        )
        for i in range(MAX_CALLS_PER_TOOL + 2)
    ]
    turns.append(ModelTurn(content="Listo."))
    scripted = ScriptedAssistantModel(turns=turns)
    configured_app.dependency_overrides[get_assistant_model] = lambda: scripted
    token = await _mint(seeded["admin_a"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/assistant/chat", json={"message": "me llamo Jose"}, headers=headers
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The write tool ran at most the cap, not once per invented phrasing.
    assert body["tools_called"].count("remember_about_me") == MAX_CALLS_PER_TOOL

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE user_id = $1 AND deleted_at IS NULL",
            seeded["admin_a"],
        )
    finally:
        await conn.close()
    assert count == MAX_CALLS_PER_TOOL


# ===========================================================================
# Chat flow — stored facts are injected into the system prompt
# ===========================================================================
class _CapturingModel:
    """Records the system prompt it was handed, then answers."""

    def __init__(self) -> None:
        self.system_prompt: str | None = None

    async def decide(self, state):
        from api_server.assistant.graph import ModelTurn

        self.system_prompt = state.system_prompt
        return ModelTurn(content="ok")


@pytest.mark.asyncio
async def test_chat_injects_known_facts_into_system_prompt(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    from api_server.assistant.memory import remember_user_fact
    from api_server.auth.deps import open_tenant_session
    from api_server.db.session import reset_engine_cache
    from api_server.routers.assistant import get_assistant_model

    reset_engine_cache()
    # Pre-store a fact about admin A.
    async with open_tenant_session(_principal(seeded["admin_a"], seeded["tenant"])) as s:
        await remember_user_fact(
            s, tenant_id=seeded["tenant"], user_id=seeded["admin_a"], content="Se llama Jose"
        )

    captured = _CapturingModel()
    configured_app.dependency_overrides[get_assistant_model] = lambda: captured
    token = await _mint(seeded["admin_a"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/assistant/chat", json={"message": "hola"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert captured.system_prompt is not None
    assert "Lo que sé de ti" in captured.system_prompt
    assert "Se llama Jose" in captured.system_prompt
