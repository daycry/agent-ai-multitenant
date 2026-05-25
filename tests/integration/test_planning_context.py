"""Integration tests for the planning context builder
(Plan 03 task_03_10).

Drives `build_planning_context` against a real PostgreSQL with the
conversation/messages/tasks/plans tables in place. The chat window is
populated through the same compression layer the production endpoint
uses, so summary rows correctly shadow older messages here too.

We seed: one tenant, one project with a team, a couple of plans, a
handful of tasks in a mix of active and terminal statuses, and a
conversation with messages. Then we assert that the assembled context
reflects all of it correctly.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.chat.planning_context import (
    ACTIVE_TASK_STATUSES,
    build_planning_context,
)
from api_server.db import domain  # noqa: F401  — register the full metadata
from api_server.db.conversation import Conversation, Message, MessageAuthorKind
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    """Seed one tenant + one project + one team + plans + tasks via raw
    SQL so we control every field exactly."""
    tenant_id = uuid4()
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    plan_old = uuid4()
    plan_recent = uuid4()
    task_backlog = uuid4()
    task_in_progress = uuid4()
    task_done = uuid4()  # terminal — must NOT appear in the summary
    task_blocked = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE messages, conversations, task_dependencies, tasks,"
            " plans, projects, team_members, teams, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant Context",
            "tenant-context",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@context.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_id,
            tenant_id,
            "Team Context",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id,"
            "                      human_approval_policy, repository_config)"
            " VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)",
            project_id,
            tenant_id,
            "Context Project",
            team_id,
            '{"categories": {"git_push": "human_required"}}',
            '{"url": "git@github.com:demo/project.git"}',
        )
        # Two plans — recent first by created_at DESC ordering.
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status,"
            "                   created_at)"
            " VALUES ($1, $2, $3, $4, $5, now() - interval '7 days'),"
            "        ($6, $7, $8, $9, $10, now())",
            plan_old,
            tenant_id,
            project_id,
            "Plan inicial",
            "completed",
            plan_recent,
            tenant_id,
            project_id,
            "Plan en progreso",
            "executing",
        )
        # Four tasks: 3 active in different statuses + 1 done (should
        # be filtered out of the kanban summary).
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status)"
            " VALUES ($1, $2, $3, $4, $5, $6),"
            "        ($7, $8, $9, $10, $11, $12),"
            "        ($13, $14, $15, $16, $17, $18),"
            "        ($19, $20, $21, $22, $23, $24)",
            task_backlog,
            tenant_id,
            project_id,
            plan_recent,
            "Diseñar esquema",
            "backlog",
            task_in_progress,
            tenant_id,
            project_id,
            plan_recent,
            "Implementar handlers",
            "in_progress",
            task_done,
            tenant_id,
            project_id,
            plan_old,
            "Set up CI",
            "done",
            task_blocked,
            tenant_id,
            project_id,
            plan_recent,
            "Conectar a Auth",
            "blocked",
        )
    finally:
        await conn.close()

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "team_id": team_id,
        "project_id": project_id,
        "plan_old": plan_old,
        "plan_recent": plan_recent,
        "task_backlog": task_backlog,
        "task_in_progress": task_in_progress,
        "task_done": task_done,
        "task_blocked": task_blocked,
    }


async def _create_conversation_with_messages(
    session_factory,
    tenant_id: UUID,
    project_id: UUID,
    *,
    message_count: int = 6,
) -> UUID:
    async with session_factory() as session:
        conv = Conversation(
            tenant_id=tenant_id,
            project_id=project_id,
            title="Planning de auth",
            current_mode="planning",
        )
        session.add(conv)
        await session.flush()
        for i in range(message_count):
            session.add(
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conv.id,
                    author_kind=MessageAuthorKind.SYSTEM.value,
                    content=f"chat-msg-{i + 1}",
                    mode="planning",
                    attachments=[],
                )
            )
        await session.commit()
        return conv.id


def _engine(admin_database_url: str):
    return create_async_engine(admin_database_url, echo=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def schema_ready(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ===========================================================================
# Tests
# ===========================================================================
def test_context_includes_project_and_recent_messages(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    seeded = asyncio.run(_seed(migrations_pg_dsn))

    async def _run() -> object:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation_with_messages(
                session_factory,
                seeded["tenant_id"],
                seeded["project_id"],
                message_count=4,
            )
            async with session_factory() as session:
                return await build_planning_context(session, conv_id)
        finally:
            await engine.dispose()

    ctx = asyncio.run(_run())
    assert ctx.project_id == str(seeded["project_id"])
    assert ctx.project_name == "Context Project"
    assert ctx.team_id == str(seeded["team_id"])
    assert ctx.has_approval_policy is True
    assert ctx.has_repository_config is True
    # All 4 messages survive — none is a summary.
    assert len(ctx.chat_messages) == 4
    assert [m["content"] for m in ctx.chat_messages] == [
        "chat-msg-1",
        "chat-msg-2",
        "chat-msg-3",
        "chat-msg-4",
    ]


def test_kanban_summary_groups_active_tasks_only(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """Done / cancelled tasks are filtered out so the team doesn't
    re-plan finished work."""
    seeded = asyncio.run(_seed(migrations_pg_dsn))

    async def _run() -> object:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation_with_messages(
                session_factory,
                seeded["tenant_id"],
                seeded["project_id"],
            )
            async with session_factory() as session:
                return await build_planning_context(session, conv_id)
        finally:
            await engine.dispose()

    ctx = asyncio.run(_run())
    kanban = ctx.kanban
    # 3 active (backlog + in_progress + blocked); the done one is gone.
    assert kanban.total == 3
    assert kanban.by_status == {
        "backlog": 1,
        "in_progress": 1,
        "blocked": 1,
    }
    assert kanban.titles_by_status["backlog"] == ["Diseñar esquema"]
    assert kanban.titles_by_status["in_progress"] == ["Implementar handlers"]
    assert kanban.titles_by_status["blocked"] == ["Conectar a Auth"]
    # Done task title nowhere in the summary.
    for titles in kanban.titles_by_status.values():
        assert "Set up CI" not in titles


def test_prior_plans_are_listed_newest_first(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    seeded = asyncio.run(_seed(migrations_pg_dsn))

    async def _run() -> object:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation_with_messages(
                session_factory,
                seeded["tenant_id"],
                seeded["project_id"],
            )
            async with session_factory() as session:
                return await build_planning_context(session, conv_id)
        finally:
            await engine.dispose()

    ctx = asyncio.run(_run())
    assert [p.title for p in ctx.prior_plans] == [
        "Plan en progreso",
        "Plan inicial",
    ]
    assert ctx.prior_plans[0].status == "executing"
    assert ctx.prior_plans[1].status == "completed"


def test_memory_and_kb_are_empty_placeholders(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """Plan 04 lands real memory + RAG; until then they are empty
    tuples so the sub-graph contract is already shaped for them."""
    seeded = asyncio.run(_seed(migrations_pg_dsn))

    async def _run() -> object:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation_with_messages(
                session_factory,
                seeded["tenant_id"],
                seeded["project_id"],
            )
            async with session_factory() as session:
                return await build_planning_context(session, conv_id)
        finally:
            await engine.dispose()

    ctx = asyncio.run(_run())
    assert ctx.memory_snippets == ()
    assert ctx.kb_documents == ()


def test_as_graph_payload_is_flat_dict_ready_for_the_sub_graph(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """The chat endpoint feeds this into `PlanningState.project_context`,
    which expects a plain dict. The shape is the contract — assert it."""
    seeded = asyncio.run(_seed(migrations_pg_dsn))

    async def _run() -> object:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation_with_messages(
                session_factory,
                seeded["tenant_id"],
                seeded["project_id"],
            )
            async with session_factory() as session:
                return await build_planning_context(session, conv_id)
        finally:
            await engine.dispose()

    ctx = asyncio.run(_run())
    payload = ctx.as_graph_payload()

    assert isinstance(payload, dict)
    assert payload["project_id"] == str(seeded["project_id"])
    assert payload["project_name"] == "Context Project"
    assert payload["kanban_total"] == 3
    assert payload["kanban_by_status"]["backlog"] == 1
    assert payload["has_approval_policy"] is True
    assert payload["has_repository_config"] is True
    assert payload["team_id"] == str(seeded["team_id"])
    assert payload["memory_snippets"] == []
    assert payload["kb_documents"] == []


def test_active_task_statuses_is_the_documented_set() -> None:
    """A regression here changes which tasks the PM sees as
    outstanding. Keep the set explicit."""
    assert (
        frozenset(
            {
                "backlog",
                "ready",
                "in_progress",
                "in_review",
                "blocked",
                "awaiting_human_approval",
            }
        )
        == ACTIVE_TASK_STATUSES
    )
