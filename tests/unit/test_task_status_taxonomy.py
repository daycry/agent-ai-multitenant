"""Unit tests: coherent task-escalation taxonomy (audit cluster C7, F43).

The TASK-level human-escalation state is the canonical ``blocked`` (consistent
with ``reviewer_bridge.apply_reviewer_verdict`` and the worker; CLAUDE.md
ppio 7). The orphan ``awaiting_human`` status — which existed in no enum and in
no state-machine table, so it silently froze tasks — is gone:

  * the in-process ``task_lifecycle`` Literal no longer carries it and every
    member it DOES carry is a real :class:`domain.TaskStatus` value;
  * ``task_lifecycle`` escalates / lists / acts on ``blocked``;
  * ``in_review -> blocked`` and ``in_progress -> blocked`` (the escalation
    edges) are legal moves on the §7.2 state machine.
"""

from __future__ import annotations

import typing

import pytest
from api_server import task_state_machine as tsm
from api_server.db.domain import TaskStatus as DomainTaskStatus

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# F43 — no orphan `awaiting_human`
# ---------------------------------------------------------------------------


def test_awaiting_human_is_not_a_domain_status() -> None:
    """The orphan value must NOT appear in the canonical enum; the real
    approval state is `awaiting_human_approval`."""
    values = {s.value for s in DomainTaskStatus}
    assert "awaiting_human" not in values
    assert "awaiting_human_approval" in values


def test_lifecycle_literal_has_no_orphan_and_is_a_domain_subset() -> None:
    """Every value the in-process Literal carries is a real domain status,
    and the orphan `awaiting_human` is no longer one of them (F43)."""
    from api_server.task_lifecycle import TaskStatus as LifecycleStatus

    literal_values = set(typing.get_args(LifecycleStatus))
    domain_values = {s.value for s in DomainTaskStatus}

    assert "awaiting_human" not in literal_values
    assert literal_values <= domain_values


def test_state_machine_has_no_awaiting_human_edges() -> None:
    """`awaiting_human` is unknown to the state machine — it is terminal
    (no edges) and unreachable as a target from any status."""
    assert tsm.allowed_transitions("awaiting_human") == frozenset()
    assert tsm.is_terminal("awaiting_human")
    for status in (s.value for s in DomainTaskStatus):
        assert not tsm.can_transition(status, "awaiting_human")


# ---------------------------------------------------------------------------
# F43 — escalation-to-blocked edges are legal
# ---------------------------------------------------------------------------


def test_review_escalation_edges_to_blocked_are_legal() -> None:
    """The two escalation moves used in production (reviewer_bridge does
    in_review -> blocked; the worker does in_progress -> blocked) are legal."""
    assert tsm.can_transition("in_review", "blocked")
    assert tsm.can_transition("in_progress", "blocked")


# ---------------------------------------------------------------------------
# F43 — task_lifecycle escalates / acts on `blocked`
# ---------------------------------------------------------------------------


def _store_with_task(status: str, *, retry_count: int = 0, max_retries: int = 3):
    from api_server.task_lifecycle import InMemoryTaskStore, TaskRecord

    store = InMemoryTaskStore()
    store.save(
        TaskRecord(
            id="t",
            plan_id="p",
            title="x",
            description="x",
            status=status,  # type: ignore[arg-type]
            retry_count=retry_count,
            max_retries=max_retries,
        )
    )
    return store


def test_escalate_if_exhausted_targets_blocked() -> None:
    from api_server.task_lifecycle import TaskLifecycle

    store = _store_with_task("backlog", retry_count=3, max_retries=3)
    lc = TaskLifecycle(store=store)

    lc.escalate_if_exhausted(store.get("t"))

    task = store.get("t")
    assert task.status == "blocked"
    # The recorded transition names `blocked`, never the orphan.
    transitions = [e for e in lc.history("t") if e.kind == "transition"]
    assert transitions[-1].payload["to"] == "blocked"
    assert transitions[-1].payload["reason"] == "max_retries"


def test_escalate_if_exhausted_noop_below_threshold() -> None:
    from api_server.task_lifecycle import TaskLifecycle

    store = _store_with_task("backlog", retry_count=1, max_retries=3)
    lc = TaskLifecycle(store=store)

    lc.escalate_if_exhausted(store.get("t"))

    assert store.get("t").status == "backlog"


def test_list_escalated_returns_blocked_tasks() -> None:
    from api_server.task_lifecycle import TaskLifecycle

    store = _store_with_task("blocked", retry_count=3)
    lc = TaskLifecycle(store=store)

    escalated = lc.list_escalated("p")
    assert [t.id for t in escalated] == ["t"]


def test_apply_human_action_requires_blocked() -> None:
    from api_server.task_lifecycle import TaskClosedError, TaskLifecycle

    store = _store_with_task("in_review")
    lc = TaskLifecycle(store=store)

    with pytest.raises(TaskClosedError):
        lc.apply_human_action("t", "approve_manual", actor="alice")


def test_apply_human_action_on_blocked_resolves() -> None:
    from api_server.task_lifecycle import TaskLifecycle

    store = _store_with_task("blocked", retry_count=3)
    lc = TaskLifecycle(store=store)

    task = lc.apply_human_action("t", "approve_manual", actor="alice")
    assert task.status == "done"
    assert task.manual_approval is True
