"""Integration tests: pool metrics snapshot (Plan 06 task_06_20b5).

Six metrics pin the public contract the Prometheus exporter relies on:

    runtime_pool_size{plan_id, project_id}
    runtime_pool_busy{plan_id}
    runtime_pool_idle{plan_id}
    runtime_pool_wait_seconds{plan_id}
    runtime_pool_evictions_total{plan_id, reason}
    runtime_pool_role_executions_total{plan_id, role}

The :meth:`RuntimePool.metrics` method returns a :class:`PoolMetrics`
snapshot the exporter formats. We don't test the exporter wire
format here — only the shape + values.
"""

from __future__ import annotations

import itertools

import pytest

pytestmark = pytest.mark.integration


def _factory() -> object:
    counter = itertools.count()
    return lambda: f"c-{next(counter)}"


def test_metrics_shape_and_initial_values() -> None:
    from workers.runtime_pool import PoolConfig, PoolMetrics, RuntimePool

    pool = RuntimePool(
        plan_id="plan-X",
        project_id="proj-Y",
        config=PoolConfig(min=2, max=5),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()
    m = pool.metrics()

    assert isinstance(m, PoolMetrics)
    assert m.plan_id == "plan-X"
    assert m.project_id == "proj-Y"
    assert m.size == 2
    assert m.busy == 0
    assert m.idle == 2
    assert m.wait_seconds_total == 0.0
    assert m.evictions_total == 0
    assert m.role_executions_total == {}


def test_metrics_track_busy_and_idle_correctly() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=3),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()
    cm1 = pool.acquire("a")
    cm1.__enter__()
    cm2 = pool.acquire("b")
    cm2.__enter__()
    try:
        m = pool.metrics()
        assert m.size == 2
        assert m.busy == 2
        assert m.idle == 0
    finally:
        cm1.__exit__(None, None, None)
        cm2.__exit__(None, None, None)
    m = pool.metrics()
    assert m.busy == 0
    assert m.idle == 2


def test_metrics_track_evictions() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=5, idle_ttl_seconds=10),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()
    cms = [pool.acquire(f"r{i}") for i in range(3)]
    slots = [cm.__enter__() for cm in cms]
    for cm in cms:
        cm.__exit__(None, None, None)

    fake_now = max(s.last_used_at for s in slots) + 10_000
    pool.sweep_idle(now=fake_now)
    assert pool.metrics().evictions_total == 2  # 3 → min(1), 2 evicted


def test_metrics_role_executions_per_role() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=3),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()

    roles = ["implementador"] * 5 + ["reviewer"] * 3 + ["memorizer"] * 1
    for role in roles:
        with pool.acquire(role):
            pass

    counts = pool.metrics().role_executions_total
    assert counts == {"implementador": 5, "reviewer": 3, "memorizer": 1}
