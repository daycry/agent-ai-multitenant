"""build_comments_preamble folds human task/plan comments into the agent's system
preamble (Feature C), so comments added in the UI are taken into account by the run.

Same rail as build_prior_feedback_preamble: the orchestrator threads the comments
into the spec, the runtime prepends them as a corrective/contextual preamble.
"""

from __future__ import annotations

from agent_runtime.__main__ import build_comments_preamble


def test_renders_comments_with_instruction() -> None:
    pre = build_comments_preamble(
        [
            {"scope": "task", "content": "Usa el guard de auth en todos los endpoints"},
            {"scope": "plan", "content": "Prioriza la cobertura de tests"},
        ]
    )
    assert "Usa el guard de auth en todos los endpoints" in pre
    assert "Prioriza la cobertura de tests" in pre
    assert pre.splitlines()[0].strip()  # has an instruction header


def test_empty_list_yields_empty_string() -> None:
    assert build_comments_preamble([]) == ""


def test_blank_content_skipped() -> None:
    assert build_comments_preamble([{"scope": "task", "content": "   "}]) == ""


def test_plain_string_entries_tolerated() -> None:
    pre = build_comments_preamble(["comentario suelto"])
    assert "comentario suelto" in pre
