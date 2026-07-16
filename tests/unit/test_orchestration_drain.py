"""AUD16-02 (auditoría 2026-07-16): drain post-run de los efectos task_comment.

El runtime emite el efecto validado al sink y este viaja en el step
``tool_call`` de ``steps_log`` (``result.output.effect == 'task_comment'``).
El worker lo extrae al finalizar el run y lo persiste como ``PlanComment``
(target_kind='task', target_ref=spec_id del plan) — el mismo rail que ya
inyecta comentarios en los prompts de runs posteriores.
"""

from __future__ import annotations

import pytest
from workers.orchestration_drain import extract_task_comment_effects

pytestmark = pytest.mark.unit


def _comment_step(body: str, *, ok: bool = True, tool: str = "task_comment") -> dict[str, object]:
    return {
        "kind": "tool_call",
        "tool": tool,
        "result": {
            "ok": ok,
            "output": {"effect": "task_comment", "task_id": "t-1", "body": body} if ok else None,
            "error": None if ok else "task_comment requires a 'task_id'",
        },
    }


def test_extracts_successful_task_comment_bodies_in_order() -> None:
    steps = [
        {"kind": "model_call", "model": "m"},
        _comment_step("primera nota"),
        {"kind": "tool_call", "tool": "write_file", "result": {"ok": True, "output": {}}},
        _comment_step("segunda nota"),
    ]
    assert extract_task_comment_effects(steps) == ["primera nota", "segunda nota"]


def test_ignores_failed_calls_other_tools_and_junk_shapes() -> None:
    steps = [
        _comment_step("no llegó", ok=False),
        {"kind": "tool_call", "tool": "kanban_update", "result": {"ok": False, "error": "x"}},
        {"kind": "tool_call"},  # sin result
        "junk",  # ni siquiera un dict
        {"kind": "tool_call", "tool": "task_comment", "result": {"ok": True, "output": "raro"}},
    ]
    assert extract_task_comment_effects(steps) == []


def test_strips_namespace_and_truncates_and_caps() -> None:
    long_body = "x" * 5000
    steps = [_comment_step(long_body, tool="orchestration.task_comment")]
    steps.extend(_comment_step(f"n{i}") for i in range(20))
    out = extract_task_comment_effects(steps)
    assert len(out) == 10  # cap por run
    assert out[0] == "x" * 2000  # truncado
