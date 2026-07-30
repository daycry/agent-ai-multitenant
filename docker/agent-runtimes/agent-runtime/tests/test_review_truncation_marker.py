"""The review prompt marks a display-truncated file EXPLICITLY, so the reviewer
never mistakes the prompt cap for an incomplete file.

Observed live: a complete 4.6 KB `AuthController.php` was shown to the reviewer cut
at 4000 chars with no marker, so every review attempt rejected it as "truncated
mid-expression" and the run escalated on a false pretext. The cap is raised and an
oversized file now carries an unambiguous marker.
"""

from __future__ import annotations

from agent_runtime.providers import _REVIEW_MAX_FILE_CHARS, _review_messages


def _user_content(files: list[dict[str, str]]) -> str:
    state = {"task": {"title": "JWT auth"}, "output": "done", "written_files": files}
    msgs = _review_messages(state)
    # [0] = system prompt, [1] = the user message rendering the written files.
    return str(msgs[1].content)


def test_oversized_file_is_marked_not_mistaken_for_incomplete() -> None:
    big = "// line\n" * 4000  # well over the cap
    body = _user_content([{"path": "App/Controllers/AuthController.php", "content": big}])
    assert "TRUNCATED FOR THIS REVIEW PROMPT ONLY" in body
    assert "do NOT treat it as truncated" in body
    assert str(len(big)) in body  # the real total size is disclosed
    assert len(body) < len(big)  # only the head is shown, not the whole file


def test_small_file_has_no_truncation_marker() -> None:
    small = "<?php\nclass A {}\n"
    body = _user_content([{"path": "App/A.php", "content": small}])
    assert "TRUNCATED FOR THIS REVIEW PROMPT ONLY" not in body
    assert "class A {}" in body


def test_the_live_4_6kb_controller_now_fits_whole() -> None:
    # The exact size that triggered the false positive (4.6 KB) fits fully now.
    content = "x" * 4660
    assert len(content) <= _REVIEW_MAX_FILE_CHARS
    body = _user_content([{"path": "App/Controllers/AuthController.php", "content": content}])
    assert "TRUNCATED FOR THIS REVIEW PROMPT ONLY" not in body
