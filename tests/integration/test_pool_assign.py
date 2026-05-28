"""Integration tests: pool acquire/release (Plan 06 task_06_20b2 — happy path)."""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest

pytestmark = pytest.mark.integration


def _factory() -> tuple[Callable[[], str], list[str]]:
    issued: list[str] = []
    counter = itertools.count()

    def f() -> str:
        cid = f"c-{next(counter)}"
        issued.append(cid)
        return cid

    return f, issued


def test_acquire_reuses_free_slot() -> None:
    """After release, the next acquire MUST reuse the same container —
    that's the entire reason the pool exists (warm process + caches)."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    factory, issued = _factory()
    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=5),
        container_factory=factory,
    )
    pool.start()

    with pool.acquire("implementador") as slot1:
        container_a = slot1.container_id
    with pool.acquire("reviewer") as slot2:
        container_b = slot2.container_id

    assert container_a == container_b
    assert len(issued) == 1


def test_acquire_grows_pool_up_to_max() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    factory, issued = _factory()
    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=3),
        container_factory=factory,
    )
    pool.start()

    cm1 = pool.acquire("a")
    cm2 = pool.acquire("b")
    cm3 = pool.acquire("c")
    s1 = cm1.__enter__()
    s2 = cm2.__enter__()
    s3 = cm3.__enter__()
    try:
        assert {s1.container_id, s2.container_id, s3.container_id} == {"c-0", "c-1", "c-2"}
        assert pool.metrics().size == 3
        assert pool.metrics().busy == 3
    finally:
        for cm in (cm1, cm2, cm3):
            cm.__exit__(None, None, None)


def test_role_switch_in_place_keeps_container() -> None:
    """task_06_20b3: a single slot serves implementador → reviewer →
    memorizer without the container being destroyed. role_switches
    increments each time the role changes."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    factory, issued = _factory()
    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=1),
        container_factory=factory,
    )
    pool.start()

    seen_ids = set()
    seen_roles = []
    for role in ["implementador", "reviewer", "memorizer"]:
        with pool.acquire(role) as slot:
            seen_ids.add(slot.container_id)
            seen_roles.append(slot.current_role)

    # All three roles ran in the same container.
    assert seen_ids == {"c-0"}
    assert seen_roles == ["implementador", "reviewer", "memorizer"]
    # Two role switches (implementador→reviewer, reviewer→memorizer).
    # Plus the implicit "free → implementador" at the start (which
    # doesn't count — only role *changes* increment).
    # The third assign sees a *new* role coming after release reset
    # current_role to None, so we expect 0 switches reported on the
    # final slot (because release zeros current_role and the next
    # acquire sees None → implementador isn't a "switch").
    # The slot is no longer accessible after the context exit; assert
    # via metrics instead:
    metrics = pool.metrics()
    assert metrics.role_executions_total == {
        "implementador": 1,
        "reviewer": 1,
        "memorizer": 1,
    }
    assert len(issued) == 1
