"""Integration tests: pool at max queues / fails (Plan 06 task_06_20b2 — queueing)."""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable

import pytest

pytestmark = pytest.mark.integration


def _factory() -> Callable[[], str]:
    counter = itertools.count()
    return lambda: f"c-{next(counter)}"


def test_acquire_blocks_then_succeeds_after_release() -> None:
    """When the pool is at max, an acquire blocks until a release.
    We model that by holding two slots in the main thread, then
    starting a third acquire on a worker thread and releasing one
    slot from the main thread."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=2),
        container_factory=_factory(),
    )
    pool.start()

    cm1 = pool.acquire("a")
    cm2 = pool.acquire("b")
    s1 = cm1.__enter__()
    cm2.__enter__()

    third_result: dict[str, str] = {}
    entered = threading.Event()

    def grab_third() -> None:
        entered.set()  # reached the (blocking) acquire — no arbitrary sleep needed
        with pool.acquire("c", timeout_s=5.0) as s:
            third_result["container_id"] = s.container_id

    t = threading.Thread(target=grab_third, daemon=True)
    t.start()
    # Deterministic handoff instead of `time.sleep(0.2)` (Plan prod-02 task_12):
    # wait until the worker reached the acquire. The pool is at max, so that
    # acquire blocks and cannot return until the main thread releases a slot
    # below — third_result is therefore guaranteed still empty here, no race.
    assert entered.wait(timeout=3.0), "worker thread never started"
    assert not third_result, "acquire should be blocked while pool is at max"

    # Release one slot — the worker thread should now succeed.
    cm1.__exit__(None, None, None)
    t.join(timeout=3.0)
    assert third_result["container_id"] == s1.container_id  # reused

    cm2.__exit__(None, None, None)


def test_acquire_raises_capacity_error_on_timeout() -> None:
    from workers.runtime_pool import PoolCapacityError, PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=1),
        container_factory=_factory(),
    )
    pool.start()
    cm = pool.acquire("a")
    cm.__enter__()
    try:
        with pytest.raises(PoolCapacityError, match="max"), pool.acquire("b", timeout_s=0.2):
            pass
    finally:
        cm.__exit__(None, None, None)


def test_wait_time_recorded_in_metrics() -> None:
    """The metrics expose ``wait_seconds_total`` so the operator can
    see queueing pressure. Released-then-reacquired slots shouldn't
    inflate the counter much; held-then-acquired ones should."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=1),
        container_factory=_factory(),
    )
    pool.start()
    cm1 = pool.acquire("a")
    cm1.__enter__()

    second: dict[str, float] = {}

    def grab() -> None:
        t0 = time.monotonic()
        with pool.acquire("b", timeout_s=5.0):
            second["wait"] = time.monotonic() - t0

    t = threading.Thread(target=grab, daemon=True)
    t.start()
    time.sleep(0.3)
    cm1.__exit__(None, None, None)
    t.join(timeout=3.0)

    assert second["wait"] >= 0.2
    assert pool.metrics().wait_seconds_total >= 0.2
