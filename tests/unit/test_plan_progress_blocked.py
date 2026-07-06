"""c3: a plan whose only open tasks are `blocked` must escalate, not stall.

`blocked` counts as an OPEN task, so `transition_to_pending_human_validation`
never fires for a plan with a blocked task — it would sit `in_progress` forever
with no automatic route out (audit 2026-07-03, c3). `transition_to_blocked`
surfaces that stall so the operator can unblock/retry.
"""

from __future__ import annotations

from api_server.plan_progress import (
    TaskSnapshot,
    transition_to_blocked,
    transition_to_pending_human_validation,
)


def _t(status: str) -> TaskSnapshot:
    return TaskSnapshot(id=status, status=status)


def test_all_done_plus_blocked_escalates_to_blocked() -> None:
    tasks = [_t("done"), _t("done"), _t("blocked")]
    # Sanity: the normal completion path CANNOT fire here (blocked is "open").
    assert not transition_to_pending_human_validation("in_progress", tasks).transitioned
    res = transition_to_blocked("in_progress", tasks)
    assert res.transitioned
    assert res.new_status == "blocked"


def test_blocked_alongside_advanceable_task_is_noop() -> None:
    # An in_progress (or ready/backlog) task can still move on its own, so the
    # plan is NOT stuck yet — do not escalate.
    tasks = [_t("done"), _t("blocked"), _t("in_progress")]
    res = transition_to_blocked("in_progress", tasks)
    assert not res.transitioned
    assert res.new_status == "in_progress"


def test_no_blocked_task_is_noop() -> None:
    # All done → that's the completion path's job, not this one.
    res = transition_to_blocked("in_progress", [_t("done"), _t("done")])
    assert not res.transitioned


def test_cancelled_tasks_do_not_count_as_advanceable() -> None:
    # cancelled is not "open", so a plan with done + cancelled + blocked (and no
    # truly advanceable task) is stuck and escalates.
    tasks = [_t("done"), _t("cancelled"), _t("blocked")]
    res = transition_to_blocked("in_progress", tasks)
    assert res.transitioned
    assert res.new_status == "blocked"


def test_non_in_progress_plan_is_noop() -> None:
    for status in ("pending_human_validation", "completed", "blocked", "draft"):
        res = transition_to_blocked(status, [_t("blocked")])
        assert not res.transitioned
        assert res.new_status == status


# --- prod-06 A1 (auditoría 2026-07-06): un `backlog` cuya dependencia está
# blocked/cancelled NO puede avanzar (la promoción DAG exige deps `done`), así
# que NO debe contar como advanceable — si no, el plan queda in_progress para
# siempre. Antes cualquier no-blocked contaba como advanceable.
def _dep(id_: str, status: str, depends_on: tuple[str, ...] = ()) -> TaskSnapshot:
    return TaskSnapshot(id=id_, status=status, depends_on=depends_on)


def test_backlog_dependent_on_blocked_is_not_advanceable() -> None:
    # A→B, A blocked (falló), B backlog dep de A → B nunca avanza → escalar.
    tasks = [_dep("A", "blocked"), _dep("B", "backlog", depends_on=("A",))]
    res = transition_to_blocked("in_progress", tasks)
    assert res.transitioned
    assert res.new_status == "blocked"


def test_backlog_dependent_on_cancelled_is_not_advanceable() -> None:
    # A cancelled, B backlog dep de A → B nunca reúne deps `done` → escalar.
    # (necesita al menos un `blocked` para que este transición aplique; usamos C.)
    tasks = [
        _dep("A", "cancelled"),
        _dep("B", "backlog", depends_on=("A",)),
        _dep("C", "blocked"),
    ]
    res = transition_to_blocked("in_progress", tasks)
    assert res.transitioned


def test_backlog_with_advancing_dep_is_still_advanceable() -> None:
    # A in_progress (avanzando), B backlog dep de A → B PODRÁ avanzar → no escalar.
    tasks = [
        _dep("A", "in_progress"),
        _dep("B", "backlog", depends_on=("A",)),
        _dep("C", "blocked"),
    ]
    res = transition_to_blocked("in_progress", tasks)
    assert not res.transitioned


def test_transitively_stuck_backlog_chain_escalates() -> None:
    # A blocked, B backlog(dep A), C backlog(dep B) → toda la cadena atascada.
    tasks = [
        _dep("A", "blocked"),
        _dep("B", "backlog", depends_on=("A",)),
        _dep("C", "backlog", depends_on=("B",)),
    ]
    res = transition_to_blocked("in_progress", tasks)
    assert res.transitioned


def test_independent_backlog_keeps_plan_alive() -> None:
    # A blocked, B backlog SIN deps → B puede avanzar (independiente) → no escalar.
    tasks = [_dep("A", "blocked"), _dep("B", "backlog")]
    res = transition_to_blocked("in_progress", tasks)
    assert not res.transitioned
