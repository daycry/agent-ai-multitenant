"""El mantenimiento periódico no pisa trabajo vivo (`task_cv_42`).

Auditoría 2026-09-01 (hallazgos G-01 y G-04):

- La poda de worktrees decidía por el estado del PLAN y de la TAREA, nunca por
  si había una EJECUCIÓN en marcha dentro del worktree. Un plan que se cierra
  con una tarea aún corriendo (reabierta, re-ejecutada a mano) veía su worktree
  borrado bajo los pies del contenedor. Ahora un worktree con ejecución
  `running` es `keep`, gane quien gane en el plan.
- Beat no tiene garantía de instancia única: dos beats (un despliegue solapado,
  un `docker compose up` con el viejo aún vivo) encolan la misma tarea dos
  veces y las de efecto en disco corren en paralelo sobre la misma carpeta.
  El primer cerrojo es barato: cada entrada con efecto en disco lleva
  `expires`, para que una copia que espere en la cola más que su cadencia se
  descarte en vez de ejecutarse a destiempo.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from workers.config import Settings

pytestmark = pytest.mark.unit


# ------------------------------------------------------------- poda por estado


def _rows(*rows: tuple[str, str, str | None, Any, int]) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in rows]


def test_a_worktree_with_a_running_execution_is_kept_even_in_a_closed_plan() -> None:
    from workers.maintenance.cleanup import _worktree_policy_from_rows

    policy = _worktree_policy_from_rows(
        _rows(
            ("t-running", "in_progress", "completed", None, 1),
            ("t-closed", "done", "completed", None, 0),
            ("t-blocked", "blocked", "in_progress", None, 0),
            ("t-plain", "in_progress", "in_progress", None, 0),
        )
    )

    assert policy["t-running"] == "keep", "un worktree con ejecución viva se podó"
    assert policy["t-closed"] == "closed"
    assert policy["t-blocked"] == "keep"
    assert "t-plain" not in policy


# ------------------------------------------------------------- beat: expires


_DISK_EFFECT_TASKS = {
    "workers.prune_worktrees",
    "workers.purge_dep_cache",
    "workers.git_housekeeping",
    "workers.sweep_stale_executions",
    "workers.purge_soft_deleted",
    "workers.run_daily_backup",
}


def _period_seconds(schedule: Any) -> float | None:
    if isinstance(schedule, timedelta):
        return schedule.total_seconds()
    run_every = getattr(schedule, "run_every", None)
    if isinstance(run_every, timedelta):
        return run_every.total_seconds()
    return None  # crontab: no hay periodo fijo; basta con que expire


def test_disk_effect_beat_entries_expire_before_their_next_tick() -> None:
    from workers.beat_schedule import build_beat_schedule

    sched = build_beat_schedule(Settings())
    by_task = {str(entry["task"]): entry for entry in sched.values()}
    missing = _DISK_EFFECT_TASKS - set(by_task)
    assert not missing, f"tareas con efecto en disco fuera del beat: {sorted(missing)}"

    for task_name in sorted(_DISK_EFFECT_TASKS):
        entry = by_task[task_name]
        options = entry.get("options")
        assert isinstance(options, dict), task_name
        expires = options.get("expires")
        assert isinstance(expires, int | float) and expires > 0, (
            f"{task_name} no lleva `expires`: dos beats encolan copias que corren a destiempo"
        )
        period = _period_seconds(entry["schedule"])
        if period is not None:
            assert expires <= period, f"{task_name}: expires {expires} > periodo {period}"


# ------------------------------------------------------------- beat: SET NX


class _FakeRedis:
    """`SET NX EX` + `GET` + `DELETE` mínimos; `down=True` simula Redis caído."""

    def __init__(self, *, down: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.down = down

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if self.down:
            raise ConnectionError("redis down")
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key: str) -> str | None:
        if self.down:
            raise ConnectionError("redis down")
        return self.store.get(key)

    def delete(self, key: str) -> int:
        if self.down:
            raise ConnectionError("redis down")
        return int(self.store.pop(key, None) is not None)


def test_a_second_copy_of_a_disk_effect_task_is_skipped_while_the_first_runs() -> None:
    from workers.maintenance.singleton import beat_singleton

    redis = _FakeRedis()
    ran: list[str] = []

    @beat_singleton("prune_worktrees", ttl_s=60, redis_factory=lambda: redis)
    def prune() -> dict[str, Any]:
        ran.append("prune")
        # Mientras corre, la copia encolada por el otro beat llega:
        assert twin() == {"skipped": "already_running", "lock": "beat:prune_worktrees"}
        return {"pruned": 1}

    @beat_singleton("prune_worktrees", ttl_s=60, redis_factory=lambda: redis)
    def twin() -> dict[str, Any]:
        ran.append("twin")
        return {"pruned": 99}

    assert prune() == {"pruned": 1}
    assert ran == ["prune"], "la segunda copia corrió a la vez que la primera"
    assert redis.store == {}, "el cerrojo no se soltó al terminar"
    assert twin() == {"pruned": 99}, "tras soltar el cerrojo la tarea vuelve a correr"


def test_the_lock_is_released_even_when_the_task_raises() -> None:
    from workers.maintenance.singleton import beat_singleton

    redis = _FakeRedis()

    @beat_singleton("purge_dep_cache", ttl_s=60, redis_factory=lambda: redis)
    def boom() -> dict[str, Any]:
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        boom()
    assert redis.store == {}


def test_without_redis_the_task_still_runs() -> None:
    """El cerrojo es una guarda, no una puerta: sin Redis el mantenimiento
    sigue (como antes de `task_cv_42`), y se registra."""
    from workers.maintenance.singleton import beat_singleton

    @beat_singleton("git_housekeeping", ttl_s=60, redis_factory=lambda: _FakeRedis(down=True))
    def housekeeping() -> dict[str, Any]:
        return {"ok": True}

    assert housekeeping() == {"ok": True}


def test_every_disk_effect_task_carries_the_singleton_lock() -> None:
    """Las seis tareas con efecto en disco están envueltas (marca `_beat_singleton`)."""
    from workers.backup_task import run_daily_backup
    from workers.maintenance.cleanup import git_housekeeping, prune_worktrees, purge_dep_cache
    from workers.maintenance.purge import purge_soft_deleted_task
    from workers.maintenance.stale_sweeper import sweep_stale_executions

    for task in (
        run_daily_backup,
        git_housekeeping,
        prune_worktrees,
        purge_dep_cache,
        purge_soft_deleted_task,
        sweep_stale_executions,
    ):
        run = getattr(task, "run", task)
        assert getattr(run, "_beat_singleton", None), f"{task.name} corre sin cerrojo"


# ------------------------------------------------------------- DLQ de ejecuciones


@pytest.mark.asyncio
async def test_the_executions_dead_letter_stream_is_sampled() -> None:
    """`task_cv_43` (A-09): `dlq:executions` existía sin lector ni métrica; sólo
    se muestreaba la DLQ de notificaciones. Ahora entra en `agentic_dlq_depth`."""
    from workers.maintenance.queue_sampler import _DLQ_STREAMS, _collect_dlq_depths

    class _Redis:
        async def xlen(self, name: str) -> int:
            return {"dlq:notifications": 2, "dlq:executions": 5}[name]

    depths = await _collect_dlq_depths(_Redis(), _DLQ_STREAMS)

    assert depths["dlq:executions"] == 5
