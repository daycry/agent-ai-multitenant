"""Structured FINISH contract — `submit_result` routing + wrap (ADR 0087, C1/C2).

The FINISH route used to be defined SOLELY by the ABSENCE of a tool call
(`_decision_from`: `if tool_calls -> ACT else -> FINISH`). Advertising the new
`submit_result(status, summary)` tool would otherwise route a finish into ACT
against a ToolRegistry that has no such tool. So `_decision_from` now routes BY
TOOL NAME:

  * `submit_result`  -> FINISH (output = summary; finish_status = validated status);
  * any other tool   -> ACT;
  * no tool (prose)  -> FINISH (wrap: output = content; finish_status = None).

Pins the three branches + the schema-validation of `status` (a bad status is a
hint we don't trust → None, never a crash).
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.model import DecisionKind
from agent_runtime.providers import _SUBMIT_RESULT_TOOL, _decision_from, _review_messages


def _resp(*, tool_calls=None, content: str = ""):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        tool_calls=tool_calls or [],
        content=content,
        model="m",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, cost_usd=0.0),
    )


def _call(name: str, **args):  # type: ignore[no-untyped-def]
    return SimpleNamespace(name=name, arguments=args)


def test_submit_result_routes_to_finish_not_act() -> None:
    resp = _resp(tool_calls=[_call("submit_result", status="success", summary="Hecho: X e Y.")])
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.FINISH
    assert decision.output == "Hecho: X e Y."
    assert decision.finish_status == "success"


def test_other_tool_still_routes_to_act() -> None:
    resp = _resp(tool_calls=[_call("write_file", path="a.py", content="x")])
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.ACT
    assert decision.tool == "write_file"
    assert decision.finish_status is None


def test_prose_finish_wraps_with_no_status() -> None:
    # No tool call (the claude_sdk prose path) → FINISH, status unknown (None).
    decision = _decision_from(_resp(content="Terminé la tarea."), model="m").decision
    assert decision.kind == DecisionKind.FINISH
    assert decision.output == "Terminé la tarea."
    assert decision.finish_status is None


def test_submit_result_invalid_status_is_dropped_not_crash() -> None:
    # A status outside the enum is a hint we cannot trust → None (never crashes).
    resp = _resp(tool_calls=[_call("submit_result", status="banana", summary="x")])
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.FINISH
    assert decision.finish_status is None
    assert decision.output == "x"


def test_submit_result_failed_status_is_carried() -> None:
    resp = _resp(tool_calls=[_call("submit_result", status="failed", summary="no pude por Z")])
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.FINISH
    assert decision.finish_status == "failed"


def test_submit_result_tool_schema_shape() -> None:
    # status enum closed to success|failed|partial; summary required.
    params = _SUBMIT_RESULT_TOOL["parameters"]
    assert _SUBMIT_RESULT_TOOL["name"] == "submit_result"
    assert params["properties"]["status"]["enum"] == ["success", "failed", "partial"]
    assert set(params["required"]) == {"status", "summary"}


# --- F1.5 (auditoría 2026-07-02): finish estructurado para claude_sdk ----------
# claude_sdk no recibe `submit_result` (un tool call forzaría content="" y
# perdería la prosa), así que finish_status era SIEMPRE None en el 100% de los
# runs de producción y la escalación agent_reported_failure (D19) era código
# muerto. El canal equivalente: un tag `<finish status="..."/>` al final de la
# prosa, parseado y despojado del output.


def test_prose_finish_tag_parses_status_and_strips_tag() -> None:
    content = 'Terminé la tarea: creé X e Y.\n<finish status="success"/>'
    decision = _decision_from(_resp(content=content), model="m").decision
    assert decision.kind == DecisionKind.FINISH
    assert decision.finish_status == "success"
    assert "<finish" not in decision.output
    assert "Terminé la tarea" in decision.output


def test_prose_finish_tag_failed_is_carried() -> None:
    content = "No pude completar Z por el error W.\n<finish status=failed>"
    decision = _decision_from(_resp(content=content), model="m").decision
    assert decision.finish_status == "failed"
    assert "<finish" not in decision.output


def test_prose_finish_tag_partial_closing_form() -> None:
    content = "Hice A pero falta B.\n<finish status='partial'></finish>"
    decision = _decision_from(_resp(content=content), model="m").decision
    assert decision.finish_status == "partial"


def test_prose_finish_tag_invalid_status_is_dropped_not_crash() -> None:
    content = 'Listo.\n<finish status="banana"/>'
    decision = _decision_from(_resp(content=content), model="m").decision
    assert decision.finish_status is None
    assert "<finish" not in decision.output  # el ruido del tag no llega al output


# --- C3: the authoritative reviewer sees the criteria + the status hint --------
def test_review_prompt_includes_criteria_and_status_hint() -> None:
    state = {
        "task": {
            "title": "T",
            "description": "d",
            "acceptance_criteria": ["criterio A", {"description": "criterio B"}],
        },
        "output": "el entregable",
        "last_decision": {"finish_status": "partial"},
    }
    user = next(m for m in _review_messages(state) if m.role == "user")
    assert "criterio A" in user.content and "criterio B" in user.content
    assert "partial" in user.content  # the agent's self-reported status, as a hint
    assert "el entregable" in user.content


def test_review_prompt_omits_status_when_absent() -> None:
    state = {"task": {"title": "T", "description": "d"}, "output": "x"}
    user = next(m for m in _review_messages(state) if m.role == "user")
    # No structured status → no status-hint line (prose finish / claude_sdk).
    assert "self-reported" not in user.content.lower()


# --- Option 1 (ADR 0087 refinement): the reviewer SEES the produced code --------
def test_review_prompt_includes_written_files() -> None:
    # An implementation run: the reviewer must judge the ACTUAL file contents, not
    # just the agent's prose summary (which it can't verify and would reject).
    state = {
        "task": {"title": "JWT", "description": "implement"},
        "output": "Los tres archivos están escritos.",
        "written_files": [
            {"path": "app/Controllers/Login.php", "content": "<?php class Login {}"},
            {"path": "app/Config/Auth.php", "content": "<?php return ['jwt'=>true];"},
        ],
    }
    user = next(m for m in _review_messages(state) if m.role == "user")
    assert "app/Controllers/Login.php" in user.content
    assert "<?php class Login {}" in user.content
    assert "app/Config/Auth.php" in user.content


def test_review_prompt_caps_written_file_content() -> None:
    # Big files must not blow the review prompt: per-file content is truncated.
    big = "X" * 20000
    state = {
        "task": {"title": "T", "description": "d"},
        "output": "done",
        "written_files": [{"path": "big.py", "content": big}],
    }
    user = next(m for m in _review_messages(state) if m.role == "user")
    assert "big.py" in user.content
    assert user.content.count("X") < 20000  # truncated, not the full 20k


def test_review_prompt_no_files_section_when_absent() -> None:
    state = {"task": {"title": "T", "description": "d"}, "output": "analysis result"}
    user = next(m for m in _review_messages(state) if m.role == "user")
    # Analysis run (no produced files) → prose-only review, no files section.
    assert "wrote" not in user.content.lower()
    assert "analysis result" in user.content
