"""P7: a worktree rebase conflict is a distinct, escalatable abort_code.

Before, ``_commit_and_push_worktree`` swallowed a rebase conflict (a sibling task
changed the same lines) into a generic ``commit_failed`` that the escalation panel
hides, so the operator never saw a resolvable conflict (audit 2026-07-03, P7).
"""

from __future__ import annotations

from workers.execution import _commit_abort_code


def test_conflict_error_classifies_as_rebase_conflict() -> None:
    exc = RuntimeError(
        "push_review_to_bare: rebase onto plan/019f1397-x conflicted "
        "(another task changed the same lines): CONFLICT (content)"
    )
    assert _commit_abort_code(exc) == "rebase_conflict"


def test_other_git_errors_classify_as_commit_failed() -> None:
    assert _commit_abort_code(RuntimeError("fatal: could not read from remote")) == "commit_failed"
    assert _commit_abort_code(OSError("disk full")) == "commit_failed"


def test_rebase_conflict_is_on_the_escalation_panel() -> None:
    # The panel keys on the run status OR this abort_code list; a conflict run may
    # be `done`, so it must be listed to surface for human resolution.
    from api_server.routers.plans import _REVIEW_ESCALATION_ABORT_CODES

    assert "rebase_conflict" in _REVIEW_ESCALATION_ABORT_CODES
