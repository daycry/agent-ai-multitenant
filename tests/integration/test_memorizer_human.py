"""Memorizer adapted to human tasks (Plan 16 task_16_15).

The Memorizer (Plan 04) distilled finished ``Execution`` rows into
``MemoryEntry`` rows. Plan 16 adds human tasks: a ``agent_type='human'`` task
records its deliverable in a ``human_work_sessions`` row (task_16_03), NOT in
``executions``. So the Memorizer now ALSO distils HumanWorkSessions — producing
MemoryEntries useful for future plans ("user X made decision D in context C and
it led to outcome O"), CITED back at the work session via the new
``source_human_work_session_id`` column.

Two layers under test, against the REAL Postgres (dev stack on PG 15432):

  - the async core of ``workers.memorize_human_work_session`` with a fake LLM
    injected — proves a HumanWorkSession on a ``done`` task by a human agent
    distils into MemoryEntry rows with the RIGHT citation
    (``source_human_work_session_id`` set, ``source_execution_id`` NULL) and the
    RIGHT owner pointer for each memory scope (private -> the WORKER's user_id,
    project_shared -> project_id, team_shared -> team_id);
  - the policy gate (task still ``in_review`` => skip) and the
    ``trigger_memorize_human_work_session`` helper (fires apply_async iff the
    task is ``done``);
  - tenant isolation (@pytest.mark.cross_tenant): distilling tenant A's session
    writes rows that carry tenant A's ``tenant_id`` only, and an app_user
    (NOBYPASSRLS) session scoped to tenant B sees NONE of them. Execution
    distillation is untouched (the existing test_memorizer_trigger suite still
    passes).

Seeding goes through the BYPASSRLS migrations role (the workers' production
role is BYPASSRLS too — it writes memory across tenants). The cross-tenant read
is asserted under app_user RLS to prove the rows are genuinely tenant-scoped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from shared_llm.types import CompletionResponse, Message, StreamChunk, Usage
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeLLM:
    """Deterministic stub — same shape as :class:`LLMProvider`.

    Records the messages it was asked to complete so the test can prove the
    human prompt (decision-maker's name + the human's notes) reached the LLM.
    """

    name = "fake-human-memorizer-llm"

    def __init__(self, content: str) -> None:
        self._content = content
        self.closed = False
        self.seen_messages: list[Message] = []

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        self.seen_messages = list(messages)
        return CompletionResponse(
            content=self._content,
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

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _seed(
    dsn: str,
    *,
    memory_scope: str,
    task_status: str = "done",
) -> dict[str, UUID]:
    """Seed two tenants: A (human agent + done human task + work session) and B
    (a separate tenant the memory must never reach).

    ``memory_scope`` is the human Agent's column; ``task_status`` is the status
    of the task the work session belongs to. The work session is CLOSED
    (``end_at`` set) — the human delivered. Returns the ids the test cares about.
    """
    tenant_a = uuid4()
    tenant_b = uuid4()
    worker_user = uuid4()  # the human who did the work (tenant A)
    team_a = uuid4()
    project_a = uuid4()
    human_agent = uuid4()
    task_a = uuid4()
    work_session = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, human_work_sessions, human_task_assignments,"
            " human_agent_config, executions, tasks, plans, conversations, projects,"
            " agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant Human A",
            "human-mem-a",
            tenant_b,
            "Tenant Human B",
            "human-mem-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name) VALUES ($1, $2, $3, $4)",
            worker_user,
            "lena@a.test",
            "ph",
            "Lena Legal",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_a,
            worker_user,
            "tenant_user",
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_a,
            tenant_a,
            "Legal Team",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES ($1, $2, $3, $4)",
            project_a,
            tenant_a,
            "Human Memo Project",
            team_a,
        )
        # The human Agent the task was assigned to — its memory_scope drives the
        # gate + owner resolution exactly like an AI agent's does.
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, agent_type, role, system_prompt,"
            "  memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, 'human', 'reviewer', 'You are a legal reviewer.',"
            "  $5, 'project_local')",
            human_agent,
            tenant_a,
            project_a,
            "Legal Reviewer",
            memory_scope,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, assigned_agent_id)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            task_a,
            tenant_a,
            project_a,
            "Revisar el contrato del cliente",
            task_status,
            human_agent,
        )
        await conn.execute(
            "INSERT INTO human_work_sessions"
            " (id, tenant_id, task_id, user_id, hours_logged, comments, output_files_attached)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            work_session,
            tenant_a,
            task_a,
            worker_user,
            "3.50",
            "Decidi aprobar la clausula 7 tras revisar el riesgo; el cliente firma manana.",
            '[{"type":"url","url":"https://docs.example/contract-v2.pdf","name":"contract"}]',
        )
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "worker_user": worker_user,
        "team_a": team_a,
        "project_a": project_a,
        "human_agent": human_agent,
        "task_a": task_a,
        "work_session": work_session,
    }


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    """Build a ``workers.config.Settings`` pointed at the test DB (BYPASSRLS
    migrations role — the workers' production role is BYPASSRLS too)."""
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


_TWO_CANDIDATES = (
    '[{"content": "Lena Legal decidio aprobar la clausula 7 del contrato tras'
    ' evaluar el riesgo, lo que permitio la firma del cliente.",'
    ' "type": "semantic", "tags": ["legal-review", "decision"]},'
    ' {"content": "2026-05-01 Lena revisa el contrato del cliente y aprueba.",'
    ' "type": "episodic", "tags": ["legal-review"]}]'
)


# ---------------------------------------------------------------------------
# Happy path: a done human work session distils, cited at the work session
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_human_session_distilled_into_memories_with_citation(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """A CLOSED work session on a ``done`` human task with a ``project_shared``
    agent yields MemoryEntry rows cited at the work session (NOT an execution),
    owned by the project."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="project_shared")

    fake = _FakeLLM(content=_TWO_CANDIDATES)
    from workers.memorizer import _memorize_human_work_session_async

    result = await _memorize_human_work_session_async(
        seeded["work_session"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 2, result
    assert result["reason"] == "ok"
    assert fake.closed is True  # the task aclosed the provider
    # The human prompt cited WHO did the work + carried the human's notes.
    user_prompt = next(m.content for m in fake.seen_messages if m.role == "user")
    assert "Lena Legal" in user_prompt
    assert "clausula 7" in user_prompt

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT scope, type, team_id, project_id, user_id, agent_id,"
            " source_execution_id, source_human_work_session_id, metadata, content"
            " FROM memory_entries ORDER BY type"
        )
    finally:
        await conn.close()

    assert len(rows) == 2
    assert {r["type"] for r in rows} == {"episodic", "semantic"}
    for r in rows:
        # Cited at the WORK SESSION, not an execution (the whole point of 16_15).
        assert r["source_human_work_session_id"] == seeded["work_session"]
        assert r["source_execution_id"] is None
        # project_shared -> project_id owner pointer.
        assert r["scope"] == "project_shared"
        assert r["project_id"] == seeded["project_a"]
        assert r["team_id"] is None
        assert r["user_id"] is None
        # Authored by the human agent; metadata records the human source + task.
        assert r["agent_id"] == seeded["human_agent"]
        import json

        meta = json.loads(r["metadata"])
        assert meta["source_kind"] == "human_work_session"
        assert meta["task_id"] == str(seeded["task_a"])
        assert meta["worker_user_id"] == str(seeded["worker_user"])


# ---------------------------------------------------------------------------
# Scope: private resolves to the WORKER's user_id (unlike the AI path)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_human_private_scope_attributes_to_worker(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """A human task HAS a clean user attribution, so ``private`` memories are
    owned by the worker (unlike AI agents, where private is skipped)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="private")

    fake = _FakeLLM(
        content='[{"content": "Lena prefiere aprobar clausulas de bajo riesgo.",'
        ' "type": "semantic", "tags": []}]'
    )
    from workers.memorizer import _memorize_human_work_session_async

    result = await _memorize_human_work_session_async(
        seeded["work_session"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 1, result
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT scope, user_id, team_id, project_id,"
            " source_human_work_session_id FROM memory_entries"
        )
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["scope"] == "private"
    assert rows[0]["user_id"] == seeded["worker_user"]
    assert rows[0]["team_id"] is None
    assert rows[0]["project_id"] is None
    assert rows[0]["source_human_work_session_id"] == seeded["work_session"]


# ---------------------------------------------------------------------------
# Scope: team_shared resolves to the project's team_id
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_human_team_shared_scope_uses_team_id(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared")

    # ADR 0071: una memoria SEMANTIC (lección) sí viaja al scope del equipo.
    fake = _FakeLLM(
        content='[{"content": "El equipo legal aprobo.", "type": "semantic", "tags": []}]'
    )
    from workers.memorizer import _memorize_human_work_session_async

    result = await _memorize_human_work_session_async(
        seeded["work_session"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 1, result
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch("SELECT scope, team_id, project_id, user_id FROM memory_entries")
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["scope"] == "team_shared"
    assert rows[0]["team_id"] == seeded["team_a"]
    assert rows[0]["project_id"] is None
    assert rows[0]["user_id"] is None


@pytest.mark.asyncio
async def test_human_team_shared_episodic_goes_to_project(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """ADR 0071: con scope efectivo team_shared, una memoria EPISODIC (evento
    concreto) se acota a project_shared — el hecho puntual se queda en su
    proyecto, no contamina el pool del equipo."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared")

    fake = _FakeLLM(
        content='[{"content": "El 2026-06-19 fallo el deploy X.", "type": "episodic", "tags": []}]'
    )
    from workers.memorizer import _memorize_human_work_session_async

    result = await _memorize_human_work_session_async(
        seeded["work_session"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 1, result
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch("SELECT scope, team_id, project_id, user_id FROM memory_entries")
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["scope"] == "project_shared"
    assert rows[0]["project_id"] == seeded["project_a"]
    assert rows[0]["team_id"] is None


# ---------------------------------------------------------------------------
# Policy gate: a task still in_review must NOT produce memories yet
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_human_in_review_task_is_skipped(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """The human delivered but the peer reviewer has not ruled — the task is
    still ``in_review``, so the Memorizer waits (no positive example yet)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="project_shared", task_status="in_review")

    fake = _FakeLLM(content=_TWO_CANDIDATES)
    from workers.memorizer import _memorize_human_work_session_async

    result = await _memorize_human_work_session_async(
        seeded["work_session"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 0
    assert "skipped" in result["reason"]
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n = await conn.fetchval("SELECT count(*) FROM memory_entries")
    finally:
        await conn.close()
    assert n == 0


# ---------------------------------------------------------------------------
# A vanished work session returns a clean skip, never an exception
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_human_missing_work_session_skips_cleanly(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    fake = _FakeLLM(content="[]")
    from workers.memorizer import _memorize_human_work_session_async

    result = await _memorize_human_work_session_async(
        uuid4(),  # never seeded
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )
    assert result["persisted"] == 0
    assert result["reason"] == "skipped:work_session_not_found"


# ---------------------------------------------------------------------------
# Tenant isolation: the distilled memory carries tenant A only and an app_user
# session scoped to tenant B sees NONE of them.
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_human_memories_are_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="project_shared")

    fake = _FakeLLM(content=_TWO_CANDIDATES)
    from workers.memorizer import _memorize_human_work_session_async

    result = await _memorize_human_work_session_async(
        seeded["work_session"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )
    assert result["persisted"] == 2, result

    from api_server.db.memory import MemoryEntry

    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # Under tenant A RLS: the two distilled memories are visible.
        async with session_factory() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(seeded["tenant_a"])},
            )
            a_rows = (await session.execute(select(MemoryEntry))).scalars().all()
        assert len(a_rows) == 2
        assert {r.tenant_id for r in a_rows} == {seeded["tenant_a"]}
        assert all(r.source_human_work_session_id == seeded["work_session"] for r in a_rows)

        # Under tenant B RLS: NONE of tenant A's human-distilled memories leak.
        async with session_factory() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(seeded["tenant_b"])},
            )
            b_rows = (await session.execute(select(MemoryEntry))).scalars().all()
        assert b_rows == []
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Trigger helper fires apply_async iff the task is done
# ---------------------------------------------------------------------------
def test_trigger_human_memorize_fires_only_on_done(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import memorizer as mod

    calls: list[tuple[Any, ...]] = []

    def _fake_apply_async(*args: Any, **kwargs: Any) -> None:
        calls.append(("called", kwargs.get("args")))

    monkeypatch.setattr(mod.memorize_human_work_session, "apply_async", _fake_apply_async)

    ws_id = uuid4()
    assert mod.trigger_memorize_human_work_session(ws_id, "done") is True
    assert mod.trigger_memorize_human_work_session(ws_id, "in_review") is False
    assert mod.trigger_memorize_human_work_session(ws_id, "blocked") is False
    assert mod.trigger_memorize_human_work_session(ws_id, "backlog") is False
    assert len(calls) == 1  # only the 'done' call


def test_trigger_human_memorize_swallows_broker_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import memorizer as mod

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("broker is down")

    monkeypatch.setattr(mod.memorize_human_work_session, "apply_async", _boom)
    assert mod.trigger_memorize_human_work_session(uuid4(), "done") is False
