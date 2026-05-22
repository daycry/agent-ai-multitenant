"""Integration tests for execution capture (task_02_12).

Runs the agent loop, then checks that every model call (with tokens and
cost), every tool call (with args and result) and every memory read
(placeholder) was captured into the steps_log — and that the roll-up
survives persistence into the `executions` row.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from agent_runtime.capture import memory_reads, model_calls, summarize, tool_calls
from agent_runtime.graph import AgentDeps, run_agent
from agent_runtime.model import (
    DecisionKind,
    ModelDecision,
    ModelResponse,
    ReviewResponse,
    ScriptedModelClient,
)
from agent_runtime.steps import StepKind
from alembic import command
from api_server.db.domain import Project, Task
from api_server.db.execution_repo import get_execution, record_execution
from api_server.db.models import Organization
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_MODEL = "test-model"
_TASK = {"id": "t-cap", "title": "Capture task", "description": "exercise the capture layer"}


# ---------------------------------------------------------------------------
# Scripted run — known token / cost figures so the roll-up is checkable.
# ---------------------------------------------------------------------------
def _act(tool: str, tokens_in: int, tokens_out: int, cost: float, **args: object) -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.ACT, tool=tool, tool_args=dict(args)),
        model=_MODEL,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )


def _finish(tokens_in: int, tokens_out: int, cost: float) -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.FINISH, output="the final answer"),
        model=_MODEL,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )


def _scripted_run() -> AgentDeps:
    """echo → noop → finish; one passing review.

    model tokens: 120 + 95 + 60 (plan) + 35 (review) = 310.
    model cost:   0.001 + 0.0008 + 0.0005 + 0.0002    = 0.0025.
    tool calls:   2 (echo, noop).   model calls: 4.   memory reads: 1.
    """
    return AgentDeps(
        model=ScriptedModelClient(
            decisions=[
                _act("echo", 100, 20, 0.001, text="alpha"),
                _act("noop", 80, 15, 0.0008),
                _finish(50, 10, 0.0005),
            ],
            reviews=[
                ReviewResponse(
                    passed=True, model=_MODEL, tokens_in=30, tokens_out=5, cost_usd=0.0002
                )
            ],
        )
    )


# ---------------------------------------------------------------------------
# Capture into the steps_log
# ---------------------------------------------------------------------------
def test_every_model_call_is_captured_with_tokens_and_cost() -> None:
    result = run_agent(_scripted_run(), _TASK)
    calls = model_calls(result.steps)
    assert len(calls) == 4
    for call in calls:
        assert call["kind"] == str(StepKind.MODEL_CALL)
        assert call["model"] == _MODEL
        assert call["total_tokens"] == call["tokens_in"] + call["tokens_out"]
        assert "cost_usd" in call


def test_tool_calls_are_captured_with_args_and_result() -> None:
    result = run_agent(_scripted_run(), _TASK)
    calls = tool_calls(result.steps)
    assert [call["tool"] for call in calls] == ["echo", "noop"]
    echo = calls[0]
    assert echo["args"] == {"text": "alpha"}
    assert echo["result"]["ok"] is True
    assert echo["result"]["output"] == "alpha"


def test_memory_reads_are_captured_as_placeholders() -> None:
    result = run_agent(_scripted_run(), _TASK)
    reads = memory_reads(result.steps)
    assert len(reads) == 1
    assert reads[0]["placeholder"] is True
    assert reads[0]["hits"] == 0


def test_summary_matches_the_loop_usage() -> None:
    result = run_agent(_scripted_run(), _TASK)
    summary = summarize(result.steps)
    # The steps_log roll-up must agree with the loop's own tracker.
    assert summary.model_call_count == result.usage["model_calls"]
    assert summary.tool_call_count == result.usage["tool_calls"]
    assert summary.total_tokens == result.usage["total_tokens"] == 310
    assert summary.total_cost_usd == pytest.approx(0.0025)
    assert summary.memory_read_count == 1


# ---------------------------------------------------------------------------
# Capture survives persistence
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_task(session: async_sessionmaker) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with session() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Capture tenant", slug="capture-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Capture project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Capture task",
                status="backlog",
                priority="medium",
            )
        )
    return ids


@pytest.mark.asyncio
async def test_capture_persists_into_the_executions_row(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        result = run_agent(_scripted_run(), _TASK)
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        # Denormalised roll-ups match the captured calls.
        assert loaded.total_tokens == 310
        assert loaded.model_call_count == 4
        assert loaded.tool_call_count == 2
        assert float(loaded.total_cost_usd) == pytest.approx(0.0025)
        # The per-call detail survived the JSONB round-trip.
        persisted = summarize(loaded.steps_log)
        assert persisted.as_dict() == summarize(result.steps).as_dict()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persisted_steps_log_keeps_per_call_detail(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        result = run_agent(_scripted_run(), _TASK)
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        echo = tool_calls(loaded.steps_log)[0]
        assert echo["tool"] == "echo"
        assert echo["args"] == {"text": "alpha"}
        first_model_call = model_calls(loaded.steps_log)[0]
        assert first_model_call["tokens_in"] == 100
        assert first_model_call["model"] == _MODEL
    finally:
        await engine.dispose()
