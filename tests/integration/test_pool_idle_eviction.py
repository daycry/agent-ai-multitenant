"""Integration tests: idle eviction above min (Plan 06 task_06_20b2 — eviction)."""

from __future__ import annotations

import itertools

import pytest

pytestmark = pytest.mark.integration


def _factory() -> object:
    counter = itertools.count()
    return lambda: f"c-{next(counter)}"


def test_sweep_destroys_slots_idle_past_ttl() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    destroyed: list[str] = []
    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=5, idle_ttl_seconds=60),
        container_factory=_factory(),  # type: ignore[arg-type]
        on_destroy=destroyed.append,
    )
    pool.start()
    # Grow to 3 slots then release them all.
    cm1, cm2, cm3 = pool.acquire("a"), pool.acquire("b"), pool.acquire("c")
    s1 = cm1.__enter__()
    s2 = cm2.__enter__()
    s3 = cm3.__enter__()
    for cm in (cm1, cm2, cm3):
        cm.__exit__(None, None, None)

    # Fake a "now" 200 seconds in the future — all three are stale.
    future = max(s.last_used_at for s in (s1, s2, s3)) + 200
    removed = pool.sweep_idle(now=future)

    # min=1 → 2 of the 3 evicted.
    assert len(removed) == 2
    assert len(destroyed) == 2
    assert pool.metrics().size == 1


def test_sweep_keeps_busy_slots() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    destroyed: list[str] = []
    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=5, idle_ttl_seconds=10),
        container_factory=_factory(),  # type: ignore[arg-type]
        on_destroy=destroyed.append,
    )
    pool.start()
    cm = pool.acquire("a")
    s = cm.__enter__()
    try:
        # Sweep with a fake-future "now" — the slot is BUSY so it must
        # NOT be evicted even though wallclock is past TTL.
        future = s.last_used_at + 10_000
        removed = pool.sweep_idle(now=future)
        assert removed == []
    finally:
        cm.__exit__(None, None, None)


def test_sweep_respects_min() -> None:
    """A pool of min=2 won't shrink below 2 even if every slot is
    stale."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=2, max=5, idle_ttl_seconds=10),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()
    # Grow to 4 then release.
    cms = [pool.acquire(f"r{i}") for i in range(4)]
    for cm in cms:
        cm.__enter__()
    for cm in cms:
        cm.__exit__(None, None, None)
    assert pool.metrics().size == 4

    far_future = 10**12  # huge future timestamp
    removed = pool.sweep_idle(now=far_future)
    assert len(removed) == 2
    assert pool.metrics().size == 2


def test_sweep_does_nothing_when_no_slots() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=0, max=5),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    # Don't call start() — pool stays at 0 slots.
    assert pool.sweep_idle() == []
