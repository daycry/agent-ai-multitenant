"""build_review_preamble folds the review context + the MANDATORY <verdict>
instruction into the reviewer's system preamble (audit cluster C1 / F51).

Until this landed the worker dropped `review_context` and the reviewer ran blind
on title+description, producing no verdict — so the worker defensively rejected
every reviewed task (in_review→backlog→blocked).
"""

from __future__ import annotations

from agent_runtime.__main__ import build_review_preamble


def test_includes_mandatory_verdict_instruction() -> None:
    pre = build_review_preamble({})
    assert "<verdict>approve</verdict>" in pre
    assert "<verdict>reject</verdict>" in pre
    assert "MANDATORY" in pre


def test_folds_every_context_field() -> None:
    pre = build_review_preamble(
        {
            "acceptance_criteria": "must add a regression test",
            "implementer_output": "I wrote foo.py and a test",
            "test_report": "<test-report>pytest: 1 passed</test-report>",
        }
    )
    assert "must add a regression test" in pre
    assert "I wrote foo.py and a test" in pre
    assert "pytest: 1 passed" in pre


def test_omits_missing_fields() -> None:
    pre = build_review_preamble({"implementer_output": "only the output"})
    assert "only the output" in pre
    assert "Acceptance criteria to certify against" not in pre
    assert "Test report:" not in pre
