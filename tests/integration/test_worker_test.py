"""Integration tests: worker-test groups acceptance criteria by runtime
(Plan 06 task_06_04).

These tests stay in-process — they're about the grouping logic, not
the Docker side. ``group_tasks_by_runtime`` reads a task's
``acceptance_criteria`` JSONB list, drops manual/human checks, resolves
each automated entry's ``runtime`` against the catalog, and groups
checks by template.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _import_mod() -> object:
    from types import SimpleNamespace

    from workers.test_runtime import (
        AcceptanceCheck,
        RuntimePlan,
        group_tasks_by_runtime,
    )

    return SimpleNamespace(
        AcceptanceCheck=AcceptanceCheck,
        RuntimePlan=RuntimePlan,
        group_tasks_by_runtime=group_tasks_by_runtime,
    )


def _criterion(
    cid: str,
    runtime: str,
    command: str,
    **extra: object,
) -> dict[str, object]:
    base: dict[str, object] = {
        "id": cid,
        "description": f"check {cid}",
        "check_type": "automated",
        "runtime": runtime,
        "command": command,
        "expected_signal": "exit_code == 0",
    }
    base.update(extra)
    return base


def test_groups_single_runtime() -> None:
    mod = _import_mod()
    criteria = [
        _criterion("a", "python-pytest", "pytest tests/unit -v"),
        _criterion("b", "python-pytest", "pytest tests/unit -k cache"),
    ]
    plans = mod.group_tasks_by_runtime(criteria)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.template.id == "python-pytest"
    assert tuple(c.id for c in plan.checks) == ("a", "b")


def test_groups_multiple_runtimes_keeps_first_seen_order() -> None:
    mod = _import_mod()
    criteria = [
        _criterion("a", "node-jest", "jest"),
        _criterion("b", "python-pytest", "pytest"),
        _criterion("c", "node-jest", "jest --watch=false"),
        _criterion("d", "python-pytest", "pytest -k slow"),
    ]
    plans = mod.group_tasks_by_runtime(criteria)
    assert [p.template.id for p in plans] == ["node-jest", "python-pytest"]
    jest_plan, py_plan = plans
    assert tuple(c.id for c in jest_plan.checks) == ("a", "c")
    assert tuple(c.id for c in py_plan.checks) == ("b", "d")


def test_skips_manual_human_and_malformed_entries() -> None:
    mod = _import_mod()
    criteria = [
        _criterion("auto", "python-pytest", "pytest"),
        {  # missing command
            "id": "no-cmd",
            "check_type": "automated",
            "runtime": "python-pytest",
        },
        {  # missing runtime
            "id": "no-runtime",
            "check_type": "automated",
            "command": "echo hi",
        },
        {  # manual check
            "id": "manual",
            "check_type": "manual",
            "runtime": "python-pytest",
            "command": "pytest",
        },
        {  # human check
            "id": "human",
            "check_type": "human",
            "runtime": "python-pytest",
            "command": "open the dashboard and verify",
        },
    ]
    plans = mod.group_tasks_by_runtime(criteria)
    assert len(plans) == 1
    assert tuple(c.id for c in plans[0].checks) == ("auto",)


def test_unknown_runtime_raises_keyerror() -> None:
    mod = _import_mod()
    with pytest.raises(KeyError, match="brainfuck-tap"):
        mod.group_tasks_by_runtime(
            [_criterion("a", "brainfuck-tap", "bf test.bf")],
        )


def test_returns_empty_for_no_automated_checks() -> None:
    mod = _import_mod()
    criteria = [
        {"id": "human-1", "check_type": "human", "description": "review UI"},
    ]
    assert mod.group_tasks_by_runtime(criteria) == ()


def test_default_check_type_is_automated() -> None:
    """Plan 02's task model emits acceptance_criteria entries without
    an explicit ``check_type`` for automated checks (it's the default).
    Make sure we don't accidentally treat them as manual."""
    mod = _import_mod()
    criteria = [{"id": "x", "runtime": "python-pytest", "command": "pytest"}]
    plans = mod.group_tasks_by_runtime(criteria)
    assert len(plans) == 1
    assert plans[0].checks[0].id == "x"


def test_timeout_s_carried_through() -> None:
    mod = _import_mod()
    plans = mod.group_tasks_by_runtime(
        [_criterion("slow", "python-pytest", "pytest -x", timeout_s=900)]
    )
    assert plans[0].checks[0].timeout_s == 900


def test_unknown_fields_kept_in_raw() -> None:
    """The acceptance_criteria column is open-shaped; parsers added in
    Fase D (task_06_14) may need extra fields. Keep them around."""
    mod = _import_mod()
    plans = mod.group_tasks_by_runtime(
        [_criterion("a", "python-pytest", "pytest", coverage_min=80)]
    )
    assert plans[0].checks[0].raw["coverage_min"] == 80
