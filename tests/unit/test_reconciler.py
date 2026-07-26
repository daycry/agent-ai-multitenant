"""Unit slices of the convergence reconciler (audit C3 / P0.6).

The reconciler (`workers.maintenance.reconcile_pipeline_state`) is the safety net
that re-derives DERIVED pipeline state the live event path can miss. Its DB passes
need a real database (covered by ``tests/integration/test_reconciler.py``); here we
exercise the PURE decision helpers (candidate filtering), the beat registration, and
the best-effort pass isolation — all without I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from workers.maintenance import (
    _orphan_claim_needs_revert,
    _orphan_review_needs_reannounce,
    _orphan_review_should_escalate,
    _stuck_task_needs_reconcile,
)

_NOW = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
_MIN_AGE = timedelta(minutes=5)
_MAX_STUCK = timedelta(hours=1)


# --------------------------------------------------------------------------- case (a)


@pytest.mark.parametrize("status", ["done", "failed", "aborted", "cancelled", "needs_human_review"])
def test_stuck_task_terminal_and_settled_reconciles(status: str) -> None:
    """Every terminal execution status, settled past the age threshold, reconciles."""
    completed = _NOW - timedelta(minutes=10)
    assert _stuck_task_needs_reconcile(status, completed, now=_NOW, min_age=_MIN_AGE) is True


def test_stuck_task_terminal_but_recent_is_left_alone() -> None:
    """A run that JUST finished is left for the worker's own post-processing."""
    completed = _NOW - timedelta(minutes=1)
    assert _stuck_task_needs_reconcile("done", completed, now=_NOW, min_age=_MIN_AGE) is False


@pytest.mark.parametrize("status", ["running", "awaiting_human_approval", None])
def test_stuck_task_non_terminal_is_left_alone(status: str | None) -> None:
    """A live run / a parked approval / no execution is NOT the reconciler's concern."""
    completed = _NOW - timedelta(hours=1)
    assert _stuck_task_needs_reconcile(status, completed, now=_NOW, min_age=_MIN_AGE) is False


def test_stuck_task_without_completed_at_is_left_alone() -> None:
    """A terminal status with no ``completed_at`` (mid-finalize) is not yet settled."""
    assert _stuck_task_needs_reconcile("done", None, now=_NOW, min_age=_MIN_AGE) is False


# ------------------------------------------------------------------- case (a2) V-1
# Auditoría de comportamiento 2026-07-25: una tarea que el dispatch RECLAMÓ
# (`ready`→`in_progress`, claim atómico) pero cuya ejecución nunca llegó a crearse
# caía por TODAS las redes — `_stuck_task_needs_reconcile` la descarta por diseño
# (`latest is None` no es su caso), el sweeper de ejecuciones rancias no tiene fila
# que barrer y el reaper de huérfanos no tiene contenedor. Observadas 2 tareas así
# 7 DÍAS en `in_progress`, congelando su plan entero (no puede completarse nunca).
_ORPHAN_MIN_AGE = timedelta(minutes=30)


def test_orphan_claim_past_the_age_reverts() -> None:
    """Reclamada hace mucho y sin ninguna ejecución → vuelve a `ready`."""
    started = _NOW - timedelta(hours=2)
    assert _orphan_claim_needs_revert(started, now=_NOW, min_age=_ORPHAN_MIN_AGE) is True


def test_orphan_claim_recent_is_left_alone() -> None:
    """Un dispatch recién reclamado puede tener su run aún encolado: no se toca.

    Es la guarda que impide que el reconciler pise una entrega en vuelo cuando la
    cola de Celery va con retraso."""
    started = _NOW - timedelta(minutes=2)
    assert _orphan_claim_needs_revert(started, now=_NOW, min_age=_ORPHAN_MIN_AGE) is False


def test_orphan_claim_without_started_at_is_left_alone() -> None:
    """Sin `started_at` no se puede envejecer la reclamación: se deja estar."""
    assert _orphan_claim_needs_revert(None, now=_NOW, min_age=_ORPHAN_MIN_AGE) is False


# --------------------------------------------------------------------------- case (b)


def test_orphan_review_reannounces_when_no_run_and_settled() -> None:
    completed = _NOW - timedelta(minutes=10)
    assert (
        _orphan_review_needs_reannounce(
            reviewer_is_ai=True,
            has_running_execution=False,
            latest_completed_at=completed,
            now=_NOW,
            min_age=_MIN_AGE,
        )
        is True
    )


def test_orphan_review_reannounces_when_no_execution_at_all() -> None:
    """A reviewer set but no execution ever ran (the review dispatch was lost)."""
    assert (
        _orphan_review_needs_reannounce(
            reviewer_is_ai=True,
            has_running_execution=False,
            latest_completed_at=None,
            now=_NOW,
            min_age=_MIN_AGE,
        )
        is True
    )


# --------------------------------------------------------------------------- M5 cap
# El reconciler tiene su PROPIO cap de escalado, independiente del cap D3 (ADR 0095)
# que solo avanza cuando una ejecución de review llega a _apply_review_verdict. Con
# el broker caído (no se despacha) o el worker de review SIGKILL-eado (la barre el
# sweeper, que NO toca retry_count de una tarea in_review) el cap D3 nunca avanza y la
# tarea re-anuncia review para siempre. Este cap corta por edad de Task.updated_at.


def test_orphan_review_escalates_when_stuck_past_cap() -> None:
    """Una tarea in_review sin progreso real más allá del cap → escalar (blocked)."""
    updated = _NOW - timedelta(hours=2)
    assert (
        _orphan_review_should_escalate(task_updated_at=updated, now=_NOW, max_stuck=_MAX_STUCK)
        is True
    )


def test_orphan_review_does_not_escalate_when_recent() -> None:
    """Por debajo del cap se sigue re-anunciando, no se escala."""
    updated = _NOW - timedelta(minutes=10)
    assert (
        _orphan_review_should_escalate(task_updated_at=updated, now=_NOW, max_stuck=_MAX_STUCK)
        is False
    )


def test_orphan_review_skips_while_review_in_flight() -> None:
    """A running execution means the review is already in flight — never duplicate it."""
    assert (
        _orphan_review_needs_reannounce(
            reviewer_is_ai=True,
            has_running_execution=True,
            latest_completed_at=None,
            now=_NOW,
            min_age=_MIN_AGE,
        )
        is False
    )


def test_orphan_review_skips_when_a_run_finished_recently() -> None:
    """The implementer that just moved it to review (or a review applying its verdict)."""
    completed = _NOW - timedelta(minutes=1)
    assert (
        _orphan_review_needs_reannounce(
            reviewer_is_ai=True,
            has_running_execution=False,
            latest_completed_at=completed,
            now=_NOW,
            min_age=_MIN_AGE,
        )
        is False
    )


def test_orphan_review_skips_human_reviewer() -> None:
    """A human reviewer is the peer-review path's concern, not the AI dispatch loop."""
    assert (
        _orphan_review_needs_reannounce(
            reviewer_is_ai=False,
            has_running_execution=False,
            latest_completed_at=None,
            now=_NOW,
            min_age=_MIN_AGE,
        )
        is False
    )


# --------------------------------------------------------------------------- beat wiring


def test_reconciler_is_scheduled_every_90s() -> None:
    """The reconciler beat is registered with the right task name + cadence."""
    from celery.schedules import schedule
    from workers.beat_schedule import build_beat_schedule
    from workers.config import Settings

    sched = build_beat_schedule(Settings())
    entry = sched["reconcile-pipeline-state-every-90s"]
    assert entry["task"] == "workers.reconcile_pipeline_state"
    assert entry["schedule"] == schedule(run_every=90.0)
    assert entry["options"] == {"queue": "default"}


def test_reconciler_task_is_registered_in_celery() -> None:
    """The scheduled name must resolve to a real registered task (no phantom name)."""
    import workers.maintenance  # noqa: F401  (registers reconcile_pipeline_state)
    from workers.celery_app import app

    assert "workers.reconcile_pipeline_state" in app.tasks


# --------------------------------------------------------------------- best-effort core


@pytest.mark.asyncio
async def test_core_isolates_a_failing_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """One pass raising (a bad row / a broker blip) must not tumble the other two."""
    # Patch the SUBMODULE the core looks the passes up in (the package façade
    # re-exports them, but rebinding the façade attr wouldn't affect the core).
    import workers.maintenance.reconciler as m

    async def ok_stuck(*_a: Any, **_k: Any) -> int:
        return 3

    async def boom_reviews(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("redis down")

    async def ok_plans(*_a: Any, **_k: Any) -> int:
        return 1

    async def ok_unblocked(*_a: Any, **_k: Any) -> int:
        return 4

    async def ok_worktrees(*_a: Any, **_k: Any) -> int:
        return 2

    monkeypatch.setattr(m, "_reconcile_stuck_tasks", ok_stuck)
    monkeypatch.setattr(m, "_reconcile_orphan_reviews", boom_reviews)
    monkeypatch.setattr(m, "_reconcile_unblocked_plans", ok_unblocked)
    monkeypatch.setattr(m, "_reconcile_complete_plans", ok_plans)
    monkeypatch.setattr(m, "_reconcile_unpushed_worktrees", ok_worktrees)

    class _FakeRedis:
        async def aclose(self) -> None:  # pragma: no cover - injected, never closed here
            ...

    from workers.config import Settings

    # Engine is built from the (unused) default URL but never connected — the passes
    # are stubbed — so this stays pure (no DB / no Redis).
    result = await m._reconcile_pipeline_state_async(Settings(), redis=_FakeRedis())
    assert result == {
        "stuck_tasks": 3,
        "orphan_reviews": 0,
        "unblocked_plans": 4,
        "completed_plans": 1,
        "pushed_worktrees": 2,
        # G-04/P1-08: la pasada de vigilancia sin DB falla best-effort → 0.
        "tenant_ghost_children": 0,
    }


# ---------------------------------------------------------------------------
# REGRESIÓN (auditoría adversarial 2026-07-25): el rescate de reclamaciones
# huérfanas se comía las TAREAS HUMANAS.
#
# `_revert_orphan_claim` tenía tres guardas —sigue `in_progress`, `started_at`
# viejo, cero filas en `executions`— y una tarea humana aceptada cumple las tres
# POR DISEÑO: su rastro auditable es un `HumanWorkSession`, nunca una
# `Execution` (`human_inbox.py`). El filtro SQL de candidatos tampoco distingue
# nada: `status == in_progress AND started_at < cutoff`.
#
# Resultado: 30 min después de aceptar, la tarea del humano volvía a `ready` con
# `assigned_agent_id = None` y `started_at = None`. Su entrega posterior daba 409
# (`ready -> in_review` es ilegal) y no podía re-aceptarla; y el evento `ready`
# disparaba un run de IA sobre la tarea que la persona estaba haciendo.
#
# El `if latest is None: continue` que la tarea m1 retiró era la ÚNICA protección
# de esa clase. La inferencia «sin ejecución ⇒ el run nunca arrancó» solo es
# válida en la ruta de IA.
# ---------------------------------------------------------------------------
def test_a_human_task_is_never_an_orphan_claim() -> None:
    """La guarda que faltaba, dicha en una línea."""
    from workers.maintenance.reconciler import is_orphan_claim_candidate

    assert is_orphan_claim_candidate(is_human_route=True) is False


def test_an_ai_task_without_a_run_still_is_one() -> None:
    """Y el caso que la tarea m1 vino a arreglar sigue arreglado: dos tareas
    llevaban 7 días congeladas justo por esto."""
    from workers.maintenance.reconciler import is_orphan_claim_candidate

    assert is_orphan_claim_candidate(is_human_route=False) is True
