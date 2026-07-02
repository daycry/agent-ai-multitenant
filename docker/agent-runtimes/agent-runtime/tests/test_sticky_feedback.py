"""Sticky intra-run feedback survives a long context window (A1).

The authoritative review feedback and the repetition warning were carried only in
the working `context`, which `_decide_messages` truncates to the last
`_CONTEXT_WINDOW` items — so a few more turns evicted them and the agent
re-produced the rejected output / kept repeating the same action. They now ride
SCALAR state fields rendered ALWAYS, OUTSIDE the context slice. These tests pin
that they stay in the decide prompt even when the context tail is full, and across
both provider message builders (one `_decide_messages` feeds them all).
"""

from __future__ import annotations

from agent_runtime.providers import _CONTEXT_WINDOW, _decide_messages


def _state_with_long_context(**extra: object) -> dict[str, object]:
    # A context far longer than the window, so the tail slice would drop anything
    # placed in `context` — only the scalar channels can survive.
    context = [{"role": "memory", "note": f"item-{i}"} for i in range(_CONTEXT_WINDOW + 5)]
    state: dict[str, object] = {
        "task": {"title": "T", "description": "d"},
        "context": context,
        **extra,
    }
    return state


def _user_text(state: dict[str, object]) -> str:
    messages = _decide_messages(state)
    # role="user" is the second message; its content carries the rendered prompt.
    return messages[-1].content


def test_review_feedback_survives_full_context_window() -> None:
    text = _user_text(
        _state_with_long_context(last_review_feedback="add the missing regression test")
    )
    assert "REVIEW FEEDBACK (fix this):" in text
    assert "add the missing regression test" in text


def test_repetition_warning_survives_full_context_window() -> None:
    text = _user_text(
        _state_with_long_context(repetition_warning="you wrote the same bytes 3 times")
    )
    assert "REPETITION WARNING:" in text
    assert "you wrote the same bytes 3 times" in text


def test_both_channels_render_together() -> None:
    text = _user_text(
        _state_with_long_context(
            last_review_feedback="fix the bug",
            repetition_warning="stop repeating write_file",
        )
    )
    assert "REVIEW FEEDBACK (fix this): fix the bug" in text
    assert "REPETITION WARNING: stop repeating write_file" in text


def test_absent_channels_render_nothing() -> None:
    # Backward-compat: no sticky fields → the historical prompt, no headers.
    text = _user_text(_state_with_long_context())
    assert "REVIEW FEEDBACK" not in text
    assert "REPETITION WARNING" not in text
    assert "PROGRESS:" not in text
    assert "GUIDANCE:" not in text


def test_long_feedback_is_truncated() -> None:
    text = _user_text(_state_with_long_context(last_review_feedback="x" * 5000))
    # Bounded so a runaway feedback string cannot blow the prompt budget.
    # F2b.4 (auditoría 2026-07-02): 600 → 2000 — un rejection estructurado
    # (criterio+fix+evidencia) se cortaba a 600 y perdía lo accionable.
    assert "x" * 2000 in text
    assert "x" * 2100 not in text


# --- F2b.1/2b.3 (auditoría 2026-07-02): PROGRESS + GUIDANCE sticky -------------


def test_progress_summary_survives_full_context_window() -> None:
    text = _user_text(
        _state_with_long_context(
            progress_summary="iteration 10/50 · files you have ALREADY written: a.php"
        )
    )
    assert "PROGRESS: iteration 10/50" in text
    assert "a.php" in text


def test_guidance_nudge_survives_full_context_window() -> None:
    text = _user_text(
        _state_with_long_context(guidance_nudge="STOP researching — produce the deliverable now")
    )
    assert "GUIDANCE: STOP researching" in text
