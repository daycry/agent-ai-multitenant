"""Integration tests: cleanup between steps (Plan 06 task_06_20b4).

A slot returned to the pool must be safe for the next role: previous
worktree unmounted, /tmp emptied, env vars unset, orphaned children
killed. The container_id stays the same — only the *state* gets
swept.
"""

from __future__ import annotations

import itertools

import pytest

pytestmark = pytest.mark.integration


def _factory() -> object:
    counter = itertools.count()
    return lambda: f"c-{next(counter)}"


def test_release_keeps_same_container_id() -> None:
    """Cleanup must NOT destroy + respawn the container — the
    process, MCP client, and LLM HTTP pool need to survive."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    destroyed: list[str] = []
    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=1),
        container_factory=_factory(),  # type: ignore[arg-type]
        on_destroy=destroyed.append,
    )
    pool.start()
    ids: list[str] = []
    for _ in range(3):
        with pool.acquire("a") as slot:
            ids.append(slot.container_id)
    assert len(set(ids)) == 1
    # No destroys happened — release went through cleanup, not destroy.
    assert destroyed == []


def test_release_marks_slot_idle() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=1),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()

    with pool.acquire("a") as slot:
        slot_id = slot.slot_id
        assert slot.current_role == "a"
        assert pool.metrics().busy == 1

    # Post-release: slot is idle again, ready for the next acquire.
    after = pool._slots[slot_id]
    assert after.is_idle()
    assert after.current_role is None
    assert pool.metrics().busy == 0
    assert pool.metrics().idle == 1


def test_release_is_idempotent_for_destroyed_slots() -> None:
    """If a sweep destroyed the slot before its context exit, the
    release path must not raise."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=0, max=2, idle_ttl_seconds=1),
        container_factory=_factory(),  # type: ignore[arg-type]
    )

    cm = pool.acquire("a")
    slot = cm.__enter__()
    # Manually pop the slot to simulate "destroyed mid-flight".
    pool._slots.pop(slot.slot_id)
    # __exit__ must not raise.
    cm.__exit__(None, None, None)
