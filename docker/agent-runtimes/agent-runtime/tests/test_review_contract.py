"""The verdict wire-format has ONE source (hallazgo H3, refactor 2026-07-07).

Five prompt sites used to spell the ``<verdict>`` tag literally; a drift in any
of them silently degrades verdict parsing into defensive rejects. These tests
pin (a) the canonical tokens and (b) that every prompt site carries them —
after the rewire the sites interpolate the constants, so drift is impossible
by construction and this suite guards the constants themselves.
"""

from __future__ import annotations

from agent_runtime import review_contract as rc


def test_canonical_tokens_are_the_worker_regex_shape() -> None:
    assert rc.VERDICT_APPROVE == "<verdict>approve</verdict>"
    assert rc.VERDICT_REJECT == "<verdict>reject</verdict>"
    assert rc.VERDICT_APPROVE in rc.REVIEW_FINISH_SUMMARY
    assert rc.VERDICT_REJECT in rc.REVIEW_FINISH_SUMMARY


def test_system_prompt_sites_carry_the_canonical_tokens() -> None:
    from agent_runtime.__main__ import _REVIEW_VERDICT_INSTRUCTION
    from agent_runtime.providers import _REVIEW_RUN_SYSTEM

    for text in (_REVIEW_VERDICT_INSTRUCTION, _REVIEW_RUN_SYSTEM):
        assert rc.VERDICT_APPROVE in text
        assert rc.VERDICT_REJECT in text


def test_every_review_nudge_closes_with_the_shared_sentence() -> None:
    from agent_runtime.graph import (
        _reread_churn_nudge,
        _research_nudge,
        _same_target_nudge,
    )

    nudges = [
        _research_nudge(tool="read_file", repeat_count=2, is_review=True),
        _same_target_nudge(target="file:src/x.py", count=99, is_review=True),
        _reread_churn_nudge(churn_streak=99, limit=3, has_produced=False, is_review=True),
    ]
    for nudge in nudges:
        assert nudge is not None
        assert rc.REVIEW_FINISH_SUMMARY in nudge
