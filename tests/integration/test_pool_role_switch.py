"""Integration tests: role switch in place (Plan 06 task_06_20b3).

The same container serves implementador → reviewer → memorizer
without the Python process restarting. Here we pin the contract:

  * Container id stays constant across role switches.
  * ``role_switches`` counter increments per *change* of role.
  * ``role_executions_total`` metrics track per-role counts.
"""

from __future__ import annotations

import itertools

import pytest

pytestmark = pytest.mark.integration


def _factory() -> object:
    counter = itertools.count()
    return lambda: f"c-{next(counter)}"


def test_same_container_for_all_roles_in_sequence() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=1),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()
    seen: list[str] = []
    for role in ("implementador", "reviewer", "memorizer", "technical_writer"):
        with pool.acquire(role) as slot:
            seen.append(slot.container_id)
    assert len(set(seen)) == 1


def test_role_executions_metrics_count_per_role() -> None:
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=2),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()
    for _ in range(3):
        with pool.acquire("implementador"):
            pass
    for _ in range(2):
        with pool.acquire("reviewer"):
            pass

    counts = pool.metrics().role_executions_total
    assert counts["implementador"] == 3
    assert counts["reviewer"] == 2


def test_role_change_increments_switch_counter() -> None:
    """When the same slot is reused for a *different* role, we record
    that as a switch. Two acquires of the same role on the same slot
    do NOT count as switches."""
    from workers.runtime_pool import PoolConfig, RuntimePool

    pool = RuntimePool(
        plan_id="p",
        project_id="proj",
        config=PoolConfig(min=1, max=1),
        container_factory=_factory(),  # type: ignore[arg-type]
    )
    pool.start()

    # The slot's role_switches isn't exposed via metrics directly —
    # we cheat through the private dict for this assertion. Future
    # code probably wants a stable accessor.
    slot_ids = []
    with pool.acquire("implementador") as slot:
        slot_ids.append(slot.slot_id)
    # The slot.current_role is now None after release. Acquire again
    # with the SAME role → no switch (None → implementador, not
    # implementador → implementador).
    with pool.acquire("implementador") as slot2:
        slot_ids.append(slot2.slot_id)
    # And a different role next time.
    with pool.acquire("reviewer") as slot3:
        slot_ids.append(slot3.slot_id)
        # role_switches should be 0 still: the previous current_role
        # was None (post-release) and None → reviewer is not a switch.
        # The role_switch only fires when current_role transitions
        # role-X → role-Y while the slot was still held.
        assert slot3.role_switches == 0

    # Now demonstrate an actual mid-hold switch through the public
    # API. The pool API doesn't let you mutate current_role from
    # outside; the docker-exec path that calls _assign_role multiple
    # times within one acquire is what increments. We assert the
    # mechanism via the private method directly:
    slot = pool._slots[next(iter(pool._slots))]
    pool._assign_role(slot, "implementador")
    pool._assign_role(slot, "reviewer")
    pool._assign_role(slot, "memorizer")
    assert slot.role_switches == 2  # impl→rev, rev→mem
