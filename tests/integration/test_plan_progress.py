"""Integration tests: plan progress label + cost accumulation
(Plan 06 task_06_35 — backend side; the Playwright spec covers UI).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _task(status: str, cost: float = 0.0, tid: str = "t") -> object:
    from api_server.plan_progress import TaskSnapshot

    return TaskSnapshot(id=tid, status=status, cost_eur=cost)


def test_progress_label_x_of_y() -> None:
    from api_server.plan_progress import compute_plan_progress

    tasks = [
        _task("done", 100.0, "t1"),
        _task("done", 50.0, "t2"),
        _task("backlog", 0, "t3"),
        _task("in_progress", 0, "t4"),
    ]
    p = compute_plan_progress("plan-1", tasks)
    assert p.label == "2/4"
    assert p.done == 2
    assert p.total == 4
    assert p.open == 2


def test_cost_sums_across_tasks() -> None:
    from api_server.plan_progress import compute_plan_progress

    tasks = [
        _task("done", 80.50, "t1"),
        _task("done", 19.50, "t2"),
        _task("in_progress", 100.0, "t3"),
    ]
    p = compute_plan_progress("plan-1", tasks)
    assert p.cost_eur_accumulated == 200.0


def test_cancelled_tasks_excluded() -> None:
    from api_server.plan_progress import compute_plan_progress

    tasks = [
        _task("done", 50.0, "t1"),
        _task("cancelled", 999.0, "t2"),  # excluded
    ]
    p = compute_plan_progress("plan-1", tasks)
    assert p.total == 1
    assert p.cost_eur_accumulated == 50.0


def test_empty_plan_progress() -> None:
    from api_server.plan_progress import compute_plan_progress

    p = compute_plan_progress("plan-empty", [])
    assert p.label == "0/0"
    assert p.cost_eur_accumulated == 0.0
