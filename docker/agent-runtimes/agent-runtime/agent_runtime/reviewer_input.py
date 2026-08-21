"""Reviewer input builder for test reports (Plan 06 task_06_15).

When the agent reviewer is invoked on a task whose worker-test step
produced a TestReport (Fase B + Fase D), it receives the report as a
*structured input block* in its prompt. This module is the boundary
that translates a :class:`TestReport` into the prompt-side fragment.

We keep the formatting in `shared_test_runtimes.test_report` (cross-
process reusable) and only add the reviewer-prompt-specific wrapping
here: the bookended ``<test-report>...</test-report>`` tags the
reviewer system prompt looks for, and the optional merging of
multiple per-runtime reports when one task spanned several stacks.
"""

from __future__ import annotations

from collections.abc import Sequence

from shared_test_runtimes.test_report import TestReport, format_for_reviewer


def reviewer_input_block(reports: Sequence[TestReport]) -> str:
    """Build the ``<test-report>...</test-report>`` block.

    The reviewer system prompt instructs the LLM to look for this
    exact tag pair. Inside, each report is formatted via
    :func:`shared_test_runtimes.test_report.format_for_reviewer` and
    separated by a horizontal rule.

    Empty input → empty string (the reviewer prompt knows to skip the
    section when nothing's between the tags).
    """
    if not reports:
        return ""
    chunks = [format_for_reviewer(r) for r in reports]
    body = "\n\n---\n\n".join(chunks)
    return f"<test-report>\n{body}\n</test-report>"


def overall_status(reports: Sequence[TestReport]) -> str:
    """Coarse outcome of a multi-runtime run.

    The reviewer uses this to short-circuit: ``passed`` runs don't
    require any failure analysis; ``failed`` / ``error`` runs do.

    Resolution rule:
      * If ANY report errored → "error"
      * Else if ANY failed → "failed"
      * Else if ANY timed out → "timeout"
      * Else if every report skipped → "skipped"
      * Else → "passed"
    """
    if not reports:
        return "skipped"
    statuses = {r.status for r in reports}
    if "error" in statuses:
        return "error"
    if "failed" in statuses:
        return "failed"
    if "timeout" in statuses:
        return "timeout"
    if statuses == {"skipped"}:
        return "skipped"
    return "passed"


__all__ = ["overall_status", "reviewer_input_block"]
