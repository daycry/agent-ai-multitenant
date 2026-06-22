"""Memorizer trigger end-to-end (Plan 04.5 task_04_5_02).

Two layers under test:

  - the async core of ``workers.memorize_execution`` against the real
    DB, with a fake LLM injected — proves that for a `done` execution
    by an agent with `memory_scope=team_shared`, `MemoryEntry` rows
    land with the right owner pointer (team_id) and back-link;
  - the small ``trigger_memorize`` helper that ``conduct_execution``
    calls after `finalize_execution`: it fires `apply_async` for
    `done` and only for `done`. We monkeypatch `apply_async` so a
    Celery worker process isn't required for the test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from shared_llm.types import CompletionResponse, Message, StreamChunk, Usage

pytestmark = pytest.mark.integration


_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeLLM:
    """Deterministic stub — same shape as :class:`LLMProvider`."""

    name = "fake-memorizer-llm"

    def __init__(self, content: str) -> None:
        self._content = content
        self.closed = False

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
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


class _CountingLLM(_FakeLLM):
    """Like _FakeLLM but counts distil (complete) calls — to prove a redelivery
    does NOT trigger a second LLM call."""

    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.calls = 0

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        self.calls += 1
        return await super().complete(messages, **kwargs)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _seed(dsn: str, *, memory_scope: str, status: str = "done") -> dict[str, UUID]:
    """Seed a tenant + team + project + agent + task + execution.

    `memory_scope` is the agent's column; `status` is the execution's
    terminal status. Returns the ids the test cares about.
    """
    tenant_id = uuid4()
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
            "Tenant Memo",
            "tenant-memo",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-memo",
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_id,
            tenant_id,
            "Team Memo",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES ($1, $2, $3, $4)",
            project_id,
            tenant_id,
            "Memo Project",
            team_id,
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "Memo Agent",
            "backend_dev",
            "You are an agent.",
            memory_scope,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title)" " VALUES ($1, $2, $3, $4)",
            task_id,
            tenant_id,
            project_id,
            "Memorize this run",
        )
        await conn.execute(
            "INSERT INTO executions"
            " (id, tenant_id, task_id, agent_id, status, output, steps_log)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            execution_id,
            tenant_id,
            task_id,
            agent_id,
            status,
            "Fixed a thing.",
            '[{"kind":"tool_call","note":"asyncpg.connect"},'
            '{"kind":"observation","note":"tests pass"}]',
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "team_id": team_id,
        "project_id": project_id,
        "agent_id": agent_id,
        "task_id": task_id,
        "execution_id": execution_id,
    }


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    """Build a `workers.config.Settings` pointed at the test DB."""
    sync_dsn = migrations_pg_dsn  # postgresql://...
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


# ---------------------------------------------------------------------------
# Core: async distil + persist
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memorize_done_team_shared_persists(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """The happy path. A `done` execution by a `team_shared` agent
    yields `MemoryEntry` rows owned by the project's team_id."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared")

    fake = _FakeLLM(
        content=(
            '[{"content": "Asyncpg is the only driver in use.",'
            ' "type": "semantic", "tags": ["asyncpg"]}]'
        )
    )

    from workers.memorizer import _memorize_execution_async

    result = await _memorize_execution_async(
        seeded["execution_id"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 1, result
    assert result["reason"] == "ok"
    assert fake.closed is True  # the task aclosed the provider

    # The row landed.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT scope, team_id, project_id, user_id, agent_id,"
            " source_execution_id, content FROM memory_entries"
        )
    finally:
        await conn.close()
    assert len(rows) == 1
    r = rows[0]
    assert r["scope"] == "team_shared"
    assert r["team_id"] == seeded["team_id"]
    assert r["project_id"] is None
    assert r["user_id"] is None
    assert r["agent_id"] == seeded["agent_id"]
    assert r["source_execution_id"] == seeded["execution_id"]


@pytest.mark.asyncio
async def test_redelivery_does_not_re_memorize(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """Idempotency guard (auditoría): with task_acks_late a redelivery re-runs the
    task; the guard must skip — no second LLM call, no duplicate rows."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared")

    fake = _CountingLLM(
        content='[{"content": "Asyncpg is the only driver.", "type": "semantic", "tags": []}]'
    )
    from workers.memorizer import _memorize_execution_async

    first = await _memorize_execution_async(
        seeded["execution_id"], settings=workers_settings, llm_factory=lambda _s: fake
    )
    assert first["persisted"] == 1, first
    assert first["reason"] == "ok"
    assert fake.calls == 1

    # Redelivery of the SAME execution: guard short-circuits before the LLM.
    second = await _memorize_execution_async(
        seeded["execution_id"], settings=workers_settings, llm_factory=lambda _s: fake
    )
    assert second["persisted"] == 0, second
    assert second["reason"] == "ok:already_memorized"
    assert fake.calls == 1  # NOT re-distilled — no second LLM call

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE source_execution_id = $1",
            seeded["execution_id"],
        )
    finally:
        await conn.close()
    assert count == 1  # no duplicate rows from the redelivery


@pytest.mark.asyncio
async def test_memorize_aborted_does_not_persist(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """An `aborted` execution short-circuits before the LLM call."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="team_shared", status="aborted")

    fake = _FakeLLM(content="[]")
    from workers.memorizer import _memorize_execution_async

    result = await _memorize_execution_async(
        seeded["execution_id"],
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


@pytest.mark.asyncio
async def test_memorize_project_shared_uses_project_id(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """A `project_shared` agent persists with project_id (not team_id)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="project_shared")

    fake = _FakeLLM(content='[{"content": "Run uses team A.", "type": "episodic", "tags": []}]')
    from workers.memorizer import _memorize_execution_async

    result = await _memorize_execution_async(
        seeded["execution_id"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 1
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch("SELECT scope, team_id, project_id FROM memory_entries")
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["scope"] == "project_shared"
    assert rows[0]["project_id"] == seeded["project_id"]
    assert rows[0]["team_id"] is None


@pytest.mark.asyncio
async def test_memorize_private_scope_for_ai_agent_is_skipped(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """Private auto-memorisation has no clean user attribution for an
    AI agent — the task skips. Human-curated private memories go through
    ``POST /memories`` instead.

    Plan 06.17 task_06_17_04: el skip ya no es silencioso — el motivo canónico
    ``skip_private`` se devuelve y se PERSISTE en ``executions.memorize_skip_reason``."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed(migrations_pg_dsn, memory_scope="private")

    fake = _FakeLLM(content="[]")
    from workers.memorizer import _memorize_execution_async

    result = await _memorize_execution_async(
        seeded["execution_id"],
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )

    assert result["persisted"] == 0
    assert result["reason"] == "skipped:skip_private"

    # El motivo queda consultable en la ejecución (fin del skip silencioso).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        reason = await conn.fetchval(
            "SELECT memorize_skip_reason FROM executions WHERE id = $1",
            seeded["execution_id"],
        )
    finally:
        await conn.close()
    assert reason == "skip_private"


@pytest.mark.asyncio
async def test_memorize_handles_missing_execution(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """A vanished execution returns a clean skip — never an exception."""
    fake = _FakeLLM(content="[]")
    from workers.memorizer import _memorize_execution_async

    result = await _memorize_execution_async(
        uuid4(),  # never seeded
        settings=workers_settings,
        llm_factory=lambda _settings: fake,
    )
    assert result["persisted"] == 0
    assert result["reason"] == "skipped:execution_not_found"


# ---------------------------------------------------------------------------
# Trigger from conduct_execution
# ---------------------------------------------------------------------------
def test_trigger_memorize_enqueues_for_terminal_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """`trigger_memorize` hands ANY finished (terminal) execution to the Memorizer;
    WHICH terminal statuses actually memorise is the operator-config gate in the
    task. Non-terminal (mid-execution) statuses are not enqueued. This unlocks
    learn-from-errors: 'aborted'/'failed' reach the task, which the operator can
    enable via `memorizable_statuses` (the old hardcoded '== done' shadowed it)."""
    from workers import memorizer as mod

    calls: list[tuple[str, ...]] = []

    def _fake_apply_async(*args: Any, **kwargs: Any) -> None:
        calls.append(("called", *kwargs.get("args", args[0] if args else ())))

    monkeypatch.setattr(mod.memorize_execution, "apply_async", _fake_apply_async)

    execution_id = uuid4()
    # Terminal → enqueued (the task then applies the operator-config gate).
    assert mod.trigger_memorize(execution_id, "done") is True
    assert mod.trigger_memorize(execution_id, "aborted") is True
    assert mod.trigger_memorize(execution_id, "failed") is True
    assert mod.trigger_memorize(execution_id, "cancelled") is True
    # Non-terminal (mid-execution) → NOT enqueued.
    assert mod.trigger_memorize(execution_id, "awaiting_human_approval") is False
    assert mod.trigger_memorize(execution_id, "running") is False
    assert len(calls) == 4  # the four terminal statuses


def test_trigger_memorize_swallows_broker_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """If apply_async raises, trigger_memorize must not propagate."""
    from workers import memorizer as mod

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("broker is down")

    monkeypatch.setattr(mod.memorize_execution, "apply_async", _boom)
    assert mod.trigger_memorize(uuid4(), "done") is False
