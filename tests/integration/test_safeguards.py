"""Integration tests for the execution safeguards (task_02_13).

Each budget — max_iterations, max_tokens, max_cost, max_wall_clock,
max_tool_calls — bounds a runaway agent. A scripted model that never
finishes is driven against a deliberately tiny budget; the loop must
abort with the matching SafeguardCode. Loop detection is disabled here
so a *budget* is unambiguously what trips.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from agent_runtime.graph import AgentDeps, run_agent
from agent_runtime.model import (
    DecisionKind,
    ModelDecision,
    ModelResponse,
    ScriptedModelClient,
)
from agent_runtime.safeguards import Budgets
from agent_runtime.state import STATUS_ABORTED, STATUS_DONE
from alembic import command
from api_server.db.domain import Project, Task
from api_server.db.execution_repo import get_execution, record_execution
from api_server.db.models import Organization
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_TASK = {"id": "t-sg", "title": "Endless task", "description": "a task that never finishes"}

# A loop-detection threshold high enough that it never trips — so the
# safeguard under test is the only thing that can abort the run.
_NO_LOOP_DETECT = 10**9


class _JumpClock:
    """A monotonic clock that leaps `step` seconds on every read."""

    def __init__(self, step: float) -> None:
        self._t = 0.0
        self._step = step

    def __call__(self) -> float:
        self._t += self._step
        return self._t


def _endless_act(*, tokens_in: int = 10, tokens_out: int = 5, cost: float = 0.0) -> ModelResponse:
    """An ACT decision the model repeats forever (it never FINISHes)."""
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.ACT, tool="echo", tool_args={"text": "x"}),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )


def _endless(*, tokens_in: int = 10, tokens_out: int = 5, cost: float = 0.0) -> AgentDeps:
    decision = _endless_act(tokens_in=tokens_in, tokens_out=tokens_out, cost=cost)
    return AgentDeps(model=ScriptedModelClient(decisions=[decision]))


# ---------------------------------------------------------------------------
# Each budget aborts the loop with its own code
# ---------------------------------------------------------------------------
def test_max_iterations_aborts_the_loop() -> None:
    result = run_agent(
        _endless(), _TASK, budgets=Budgets(max_iterations=3), loop_threshold=_NO_LOOP_DETECT
    )
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "max_iterations_exceeded"
    # Exactly three turns ran — the budget is honoured, not overshot.
    assert result.iterations == 3


def test_max_tokens_aborts_the_loop() -> None:
    result = run_agent(
        _endless(tokens_in=100, tokens_out=50),
        _TASK,
        budgets=Budgets(max_tokens=200),
        loop_threshold=_NO_LOOP_DETECT,
    )
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "max_tokens_exceeded"


def test_max_cost_aborts_the_loop() -> None:
    result = run_agent(
        _endless(cost=0.5),
        _TASK,
        budgets=Budgets(max_cost_usd=0.6),
        loop_threshold=_NO_LOOP_DETECT,
    )
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "max_cost_exceeded"


def test_max_tool_calls_aborts_the_loop() -> None:
    result = run_agent(
        _endless(),
        _TASK,
        budgets=Budgets(max_tool_calls=2),
        loop_threshold=_NO_LOOP_DETECT,
    )
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "max_tool_calls_exceeded"


def test_max_wall_clock_aborts_the_loop() -> None:
    result = run_agent(
        _endless(),
        _TASK,
        budgets=Budgets(max_wall_clock_s=50.0),
        loop_threshold=_NO_LOOP_DETECT,
        clock=_JumpClock(step=100.0),
    )
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "max_wall_clock_exceeded"


def test_a_run_within_budget_is_not_aborted() -> None:
    finish = ModelResponse(
        decision=ModelDecision(kind=DecisionKind.FINISH, output="done within budget")
    )
    result = run_agent(
        AgentDeps(model=ScriptedModelClient(decisions=[finish])),
        _TASK,
        budgets=Budgets(max_iterations=10, max_tokens=10_000, max_cost_usd=1.0),
    )
    assert result.status == STATUS_DONE
    assert result.abort_code is None


# ---------------------------------------------------------------------------
# An aborted run persists its abort code
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
        s.add(Organization(id=ids["tenant"], name="Safeguard tenant", slug="safeguard-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Safeguard project",
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
                title="Safeguard task",
                status="backlog",
                priority="medium",
            )
        )
    return ids


@pytest.mark.asyncio
async def test_aborted_run_persists_its_abort_code(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        result = run_agent(
            _endless(), _TASK, budgets=Budgets(max_iterations=2), loop_threshold=_NO_LOOP_DETECT
        )
        assert result.status == STATUS_ABORTED

        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        assert loaded.status == "aborted"
        assert loaded.abort_code == "max_iterations_exceeded"
        assert loaded.iterations == 2
    finally:
        await engine.dispose()
