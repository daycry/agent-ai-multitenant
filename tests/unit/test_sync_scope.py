"""Unit tests for `select_spec_ids` (Plan 03 task_03_27).

The pure function maps a `{scope, phase_index?, task_ids?}` request
onto the plan specification's flat task list. The integration tests in
``test_sync_kanban.py`` cover end-to-end behaviour; here we focus on
the corner cases that don't need a database.
"""

from __future__ import annotations

import pytest
from api_server.chat.sync_to_kanban import SyncScopeError, select_spec_ids

_SPEC = {
    "phases": [
        {"name": "Design", "tasks": ["t1", "t2"]},
        {"name": "Build", "tasks": ["t3"]},
    ],
    "tasks": [
        {"id": "t1", "title": "Modelar"},
        {"id": "t2", "title": "API", "depends_on": ["t1"]},
        {"id": "t3", "title": "Backend", "depends_on": ["t2"]},
    ],
}


def test_total_returns_all_task_ids_in_flat_list_order() -> None:
    assert select_spec_ids(_SPEC, scope="total") == ["t1", "t2", "t3"]


def test_phase_returns_phase_task_ids_in_flat_list_order() -> None:
    assert select_spec_ids(_SPEC, scope="phase", phase_index=0) == ["t1", "t2"]
    assert select_spec_ids(_SPEC, scope="phase", phase_index=1) == ["t3"]


def test_selection_filters_to_requested_ids_and_keeps_order() -> None:
    # Requested in reverse order — the function still returns flat order.
    assert select_spec_ids(_SPEC, scope="selection", task_ids=["t3", "t1"]) == [
        "t1",
        "t3",
    ]


def test_phase_without_index_raises() -> None:
    with pytest.raises(SyncScopeError, match="requires phase_index"):
        select_spec_ids(_SPEC, scope="phase")


def test_phase_out_of_range_raises() -> None:
    with pytest.raises(SyncScopeError, match="out of range"):
        select_spec_ids(_SPEC, scope="phase", phase_index=99)


def test_phase_with_unknown_task_id_raises() -> None:
    spec = dict(_SPEC)
    spec["phases"] = [{"name": "Bad", "tasks": ["t1", "t999"]}]
    with pytest.raises(SyncScopeError, match="unknown task ids"):
        select_spec_ids(spec, scope="phase", phase_index=0)


def test_selection_with_empty_list_raises() -> None:
    with pytest.raises(SyncScopeError, match="at least one task id"):
        select_spec_ids(_SPEC, scope="selection", task_ids=[])


def test_selection_with_unknown_id_raises() -> None:
    with pytest.raises(SyncScopeError, match="unknown task ids"):
        select_spec_ids(_SPEC, scope="selection", task_ids=["t1", "t-ghost"])


def test_unknown_scope_raises() -> None:
    with pytest.raises(SyncScopeError, match="unknown scope"):
        select_spec_ids(_SPEC, scope="banana")


def test_empty_spec_returns_empty_list_for_total() -> None:
    assert select_spec_ids({}, scope="total") == []
