"""Integration tests: pool model + initial min spawn (Plan 06 task_06_20b1)."""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest

pytestmark = pytest.mark.integration


def _counting_factory() -> tuple[Callable[[], str], list[str]]:
    """Factory that returns ``container-0``, ``container-1``, … and
    records every container_id it issued."""
    issued: list[str] = []
    counter = itertools.count()

    def factory() -> str:
        cid = f"container-{next(counter)}"
        issued.append(cid)
        return cid

    return factory, issued


def test_pool_config_defaults() -> None:
    from workers.runtime_pool import PoolConfig

    c = PoolConfig()
    assert c.min == 1
    assert c.max == 5
    assert c.idle_ttl_seconds == 300
    assert c.max_per_tenant == 20


def test_pool_config_rejects_negative_min() -> None:
    from workers.runtime_pool import PoolConfig

    with pytest.raises(ValueError, match="min"):
        PoolConfig(min=-1)


def test_pool_config_rejects_max_smaller_than_min() -> None:
    from workers.runtime_pool import PoolConfig

    with pytest.raises(ValueError, match="max"):
        PoolConfig(min=3, max=2)


def test_pool_config_rejects_zero_ttl() -> None:
    from workers.runtime_pool import PoolConfig

    with pytest.raises(ValueError, match="idle_ttl_seconds"):
        PoolConfig(idle_ttl_seconds=0)


def test_pool_config_rejects_max_per_tenant_below_pool_max() -> None:
    """The platform cap must always be >= the per-plan max, otherwise
    a plan that grew to its max would already have breached the tenant
    cap."""
    from workers.runtime_pool import PoolConfig

    with pytest.raises(ValueError, match="max_per_tenant"):
        PoolConfig(max=10, max_per_tenant=5)


def test_pool_start_spawns_min_containers() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    factory, issued = _counting_factory()
    pool = RuntimePool(
        plan_id="plan-1",
        project_id="proj-1",
        config=PoolConfig(min=2, max=5),
        container_factory=factory,
    )
    pool.start()

    metrics = pool.metrics()
    assert metrics.size == 2
    assert metrics.idle == 2
    assert metrics.busy == 0
    assert len(issued) == 2


def test_pool_does_not_exceed_max_on_start() -> None:
    """``start`` is a no-op for the slots above min — only acquire
    grows the pool past min."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    factory, issued = _counting_factory()
    pool = RuntimePool(
        plan_id="plan-1",
        project_id="proj-1",
        config=PoolConfig(min=2, max=5),
        container_factory=factory,
    )
    pool.start()
    assert pool.metrics().size == 2


def test_pool_shutdown_destroys_every_slot() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    destroyed: list[str] = []
    factory, _ = _counting_factory()
    pool = RuntimePool(
        plan_id="plan-1",
        project_id="proj-1",
        config=PoolConfig(min=3, max=5),
        container_factory=factory,
        on_destroy=destroyed.append,
    )
    pool.start()
    pool.shutdown()
    assert pool.metrics().size == 0
    assert len(destroyed) == 3
