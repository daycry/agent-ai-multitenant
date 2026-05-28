"""Integration tests: worker uses the pool instead of one-shot containers
(Plan 06 task_06_20b6).

The Fase 2 worker path ran ``AgentContainerRunner.run`` once per
task. Plan 06 replaces that with a pool acquire — same container
across the implementation step and the auto-review step. Here we
pin the contract: the worker's helper resolves a slot, hands it to
the caller, and the slot is reused across multiple "task steps"
within the same plan run.
"""

from __future__ import annotations

import itertools

import pytest

pytestmark = pytest.mark.integration


def _factory() -> object:
    counter = itertools.count()
    return lambda: f"c-{next(counter)}"


def test_two_steps_of_same_task_reuse_one_container() -> None:
    """Implementador step + reviewer step → same container."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="plan-1",
        project_id="proj-1",
        config=PoolConfig(min=1, max=3),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()

    with pool.acquire("implementador") as impl_slot:
        impl_container = impl_slot.container_id
    with pool.acquire("reviewer") as rev_slot:
        rev_container = rev_slot.container_id

    assert impl_container == rev_container


def test_parallel_tasks_in_same_plan_share_pool() -> None:
    """Two tasks of the same plan acquire in parallel — the pool may
    spawn a second slot, but both stay attached to the same plan_id
    metrics."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="plan-1",
        project_id="proj-1",
        config=PoolConfig(min=1, max=3),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()

    cm_a = pool.acquire("implementador")
    cm_b = pool.acquire("implementador")
    sa = cm_a.__enter__()
    sb = cm_b.__enter__()
    try:
        # Two slots in flight, distinct containers.
        assert sa.container_id != sb.container_id
        # Both attribute their role_executions to the same plan/project.
        m = pool.metrics()
        assert m.plan_id == "plan-1"
        assert m.role_executions_total["implementador"] == 2
    finally:
        cm_a.__exit__(None, None, None)
        cm_b.__exit__(None, None, None)


def test_pool_is_per_plan_not_per_task() -> None:
    """The same pool serves many tasks. After 5 sequential acquires
    the pool stays at min=1 (one container, reused 5 times)."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    factory_counter: list[int] = [0]

    def f() -> str:
        factory_counter[0] += 1
        return f"c-{factory_counter[0]}"

    pool = RuntimePool(
        plan_id="plan-1",
        project_id="proj-1",
        config=PoolConfig(min=1, max=3),
        container_factory=f,
    )
    pool.start()
    for _ in range(5):
        with pool.acquire("implementador"):
            pass

    # min spawn at start (1) + zero growth (every acquire found a free
    # slot) = 1 container ever issued.
    assert factory_counter[0] == 1
