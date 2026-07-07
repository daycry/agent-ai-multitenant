"""Per-task run lock so a re-delivered execution can't corrupt a live worktree.

prod-18 A6 (auditoría 2026-07-06): Celery ``acks_late`` re-delivers a message
(broker hiccup, or a worker crash — the DooD container runs on the daemon and
SURVIVES the worker process), so two ``conduct_execution`` calls for the SAME
``task_id`` can run at once. The second provisions the same
``worktrees/{task_id}`` and its ``sync_to_head`` does ``git reset --hard`` +
``clean -fdx`` — destroying the first container's in-flight, uncommitted work.
The DB-level guards (``supersede_running_executions``, the R5 eligibility check)
do NOT catch this: the task is still ``in_progress`` (launchable) while the first
run is live.

This is a distributed lock keyed by ``task_id`` (Redis ``SET NX EX``): the first
run acquires it and holds it for the run; a concurrent re-delivery fails to
acquire and is SKIPPED (the original run will finish and finalize). The TTL is a
backstop for a crashed worker — the lock frees roughly when the orphaned
container would time out anyway, after which the sweeper / a later re-delivery
can proceed. Release is token-guarded so a run whose TTL already expired (and
whose lock another run then acquired) never deletes the newer holder's lock.
"""

from __future__ import annotations

import contextlib
from typing import Any

_RUN_LOCK_PREFIX = "workers:run_lock:task:"

# Lua: delete the key only if its value still matches our token (compare-and-del),
# so a run whose lock expired + was re-acquired by another run cannot free it.
_RELEASE_IF_OWNED = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def run_lock_key(task_id: str) -> str:
    """The Redis key guarding concurrent runs of one task."""
    return f"{_RUN_LOCK_PREFIX}{task_id}"


async def acquire_run_lock(redis: Any, task_id: str, *, ttl_s: int, token: str) -> bool:
    """Try to claim the per-task run lock. ``True`` iff we won it (first holder).

    ``SET key token NX EX ttl`` — atomic claim. ``token`` identifies THIS run (the
    Celery job id) so release can verify ownership. ``ttl_s`` bounds a crashed
    holder: after it the lock frees on its own."""
    got = await redis.set(run_lock_key(task_id), token, nx=True, ex=ttl_s)
    return bool(got)


async def release_run_lock(redis: Any, task_id: str, *, token: str) -> None:
    """Release the lock IFF we still own it (token match). Best-effort — a Redis
    blip just leaves the key to expire via its TTL."""
    with contextlib.suppress(Exception):  # release best-effort; TTL is the backstop
        await redis.eval(_RELEASE_IF_OWNED, 1, run_lock_key(task_id), token)


__all__ = ["acquire_run_lock", "release_run_lock", "run_lock_key"]
