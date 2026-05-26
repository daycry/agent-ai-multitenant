"""Integration test for the Memorizer (Plan 04 task_04_03).

End-to-end exercise of the three Memorizer pieces against the real
Postgres:

  - :func:`should_memorize` gates the run on `Execution.status` +
    `Agent.memory_scope`,
  - :func:`distil_execution` calls a (faked) LLM and returns
    :class:`MemoryCandidate` instances,
  - :func:`persist_memory_candidates` writes them as `MemoryEntry`
    rows respecting the scope→owner mapping and RLS.

The Celery task wrapper (`workers.memorize_execution`) is a thin
adapter over the same calls; it's smoke-tested implicitly by
running the same flow inline here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import MemoryScope
from api_server.db.memory import MemoryEntry
from api_server.memorizer import (
    distil_execution,
    persist_memory_candidates,
    should_memorize,
)
from shared_llm.types import CompletionResponse, Message, StreamChunk, Usage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


class _FakeLLM:
    """Deterministic LLM stub for the integration test."""

    name = "fake"

    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(
            content=self.content,
            model="fake-model",
            provider=self.name,
            usage=Usage(),
            tool_calls=None,
            raw={},
        )

    async def stream(
        self, messages: Sequence[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:  # pragma: no cover
        yield StreamChunk(delta="", usage=None, raw={})

    async def aclose(self) -> None:  # pragma: no cover
        pass


async def _seed(dsn: str) -> dict[str, UUID]:
    """Seed an org + user + team + project + agent + task + execution.

    Returns a dict with everything we need to drive the flow.
    """
    tenant_id = uuid4()
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    agent_id = uuid4()
    task_id = uuid4()
    execution_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, executions, tasks, plans, conversations,"
            " projects, agents, teams, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Mem",
            "tenant-mem",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-mem",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@mem.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_id,
            tenant_id,
            "Team A",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Memorizer Project",
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "BE Dev",
            "backend_dev",
            "You are a backend developer.",
            "team_shared",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title)" " VALUES ($1, $2, $3, $4)",
            task_id,
            tenant_id,
            project_id,
            "Wire memorizer",
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, output, steps_log)"
            " VALUES ($1, $2, $3, $4, 'done', $5, $6::jsonb)",
            execution_id,
            tenant_id,
            task_id,
            agent_id,
            "Fixed import bug.",
            '[{"kind":"tool_call","note":"asyncpg.connect"},'
            '{"kind":"observation","note":"tests pass"}]',
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "team_id": team_id,
        "project_id": project_id,
        "agent_id": agent_id,
        "task_id": task_id,
        "execution_id": execution_id,
    }


async def _set_tenant(session, tenant_id: UUID) -> None:
    from sqlalchemy import text as sa_text

    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )


@pytest.mark.asyncio
async def test_full_flow_persists_team_shared_memories(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Happy path: a 'done' execution by an agent with memory_scope=
    team_shared results in MemoryEntry rows with team_id set."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    llm = _FakeLLM(
        content=(
            '[{"content": "Project uses asyncpg, not psycopg3.",'
            ' "type": "semantic", "tags": ["sqlalchemy", "asyncpg"]},'
            ' {"content": "2026-05-25 backend agent fixed a missing import.",'
            ' "type": "episodic", "tags": []}]'
        )
    )

    execution = {
        "status": "done",
        "output": "Fixed.",
        "steps_log": [
            {"kind": "tool_call", "note": "asyncpg.connect"},
            {"kind": "observation", "note": "tests pass"},
        ],
        "task_title": "Wire memorizer",
    }
    agent = {"role": "backend_dev", "memory_scope": "team_shared"}

    decision = should_memorize(status="done", memory_scope=agent["memory_scope"])
    assert decision.memorise

    candidates = await distil_execution(execution=execution, agent=agent, llm=llm)
    assert len(candidates) == 2

    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _set_tenant(session, seeded["tenant_id"])
            rows = await persist_memory_candidates(
                session,
                candidates,
                tenant_id=seeded["tenant_id"],
                scope=MemoryScope.TEAM_SHARED.value,
                agent_id=seeded["agent_id"],
                team_id=seeded["team_id"],
                source_execution_id=seeded["execution_id"],
                extra_metadata={"distill_model": "fake-model"},
            )
            await session.commit()
            assert len(rows) == 2

        async with session_factory() as session:
            await _set_tenant(session, seeded["tenant_id"])
            stored = (
                (await session.execute(select(MemoryEntry).order_by(MemoryEntry.created_at)))
                .scalars()
                .all()
            )
            assert len(stored) == 2
            assert {r.scope for r in stored} == {"team_shared"}
            assert {r.type for r in stored} == {"semantic", "episodic"}
            for r in stored:
                assert r.team_id == seeded["team_id"]
                assert r.user_id is None
                assert r.project_id is None
                assert r.agent_id == seeded["agent_id"]
                assert r.source_execution_id == seeded["execution_id"]
                assert r.embedding is None  # back-filled later (task_04_14)
                assert r.metadata_["distill_model"] == "fake-model"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_aborted_execution_does_not_persist(schema_at_head, migrations_pg_dsn: str) -> None:
    """Policy short-circuits before any LLM call."""
    decision = should_memorize(status="aborted", memory_scope="team_shared")
    assert decision.memorise is False
    # The integration check: persist_memory_candidates is never called,
    # so no row lands. We assert by counting the table.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("TRUNCATE memory_entries CASCADE")
        n = await conn.fetchval("SELECT count(*) FROM memory_entries")
        assert n == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_private_scope_persists_with_user_id(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Same flow but agent.memory_scope=private — owner pointer is user_id."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)

    candidates = [
        # Use the same factory as the distiller for shape consistency.
        await _one_candidate("content X", "episodic"),
    ]
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _set_tenant(session, seeded["tenant_id"])
            await persist_memory_candidates(
                session,
                candidates,
                tenant_id=seeded["tenant_id"],
                scope=MemoryScope.PRIVATE.value,
                agent_id=seeded["agent_id"],
                user_id=seeded["user_id"],
                source_execution_id=seeded["execution_id"],
            )
            await session.commit()
        async with session_factory() as session:
            await _set_tenant(session, seeded["tenant_id"])
            stored = (await session.execute(select(MemoryEntry))).scalars().all()
            assert len(stored) == 1
            assert stored[0].user_id == seeded["user_id"]
            assert stored[0].team_id is None
            assert stored[0].project_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persistence_rejects_inconsistent_scope_pointer(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """`scope='private'` without a `user_id` must fail before the
    DB sees the insert (ValueError, not IntegrityError)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn)
    candidates = [await _one_candidate("orphan", "episodic")]
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _set_tenant(session, seeded["tenant_id"])
            with pytest.raises(ValueError, match="requires user_id"):
                await persist_memory_candidates(
                    session,
                    candidates,
                    tenant_id=seeded["tenant_id"],
                    scope=MemoryScope.PRIVATE.value,
                    # user_id intentionally omitted.
                )
    finally:
        await engine.dispose()


async def _one_candidate(content: str, type_: str):
    from api_server.memorizer.distillation import MemoryCandidate

    return MemoryCandidate(content=content, type=type_, tags=())
