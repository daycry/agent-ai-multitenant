"""Integration tests: reviewer receives the TestReport as structured input
(Plan 06 task_06_15).

In-process tests — no LLM call, no agent loop. The contract pinned
here is the input *shape* the reviewer prompt template depends on:

  * Wrapped in ``<test-report>...</test-report>`` so the system
    prompt can extract it.
  * Per-runtime sub-reports separated by horizontal rule.
  * ``overall_status(...)`` resolves to a sensible single label even
    when multiple runtimes ran.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _report(runtime: str, status: str, **kwargs: object) -> object:
    from shared_test_runtimes.test_report import TestReport, TestSummary

    return TestReport(
        runtime=runtime,
        status=status,  # type: ignore[arg-type]
        summary=TestSummary(**kwargs.pop("summary", {})),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_block_wraps_with_tag_pair() -> None:
    from agent_runtime.reviewer_input import reviewer_input_block

    r = _report("python-pytest", "passed", summary={"total": 5, "passed": 5})
    out = reviewer_input_block([r])
    assert out.startswith("<test-report>\n")
    assert out.endswith("\n</test-report>")
    assert "## Test report — python-pytest" in out


def test_block_empty_for_no_reports() -> None:
    from agent_runtime.reviewer_input import reviewer_input_block

    assert reviewer_input_block([]) == ""


def test_block_separates_multiple_runtimes() -> None:
    from agent_runtime.reviewer_input import reviewer_input_block

    py = _report("python-pytest", "passed", summary={"total": 5, "passed": 5})
    js = _report("node-jest", "failed", summary={"total": 2, "passed": 1, "failed": 1})
    out = reviewer_input_block([py, js])
    assert "## Test report — python-pytest" in out
    assert "## Test report — node-jest" in out
    assert "\n---\n" in out


def test_overall_status_all_passed() -> None:
    from agent_runtime.reviewer_input import overall_status

    reports = [
        _report("python-pytest", "passed"),
        _report("node-jest", "passed"),
    ]
    assert overall_status(reports) == "passed"


def test_overall_status_any_failed_wins_over_passed() -> None:
    from agent_runtime.reviewer_input import overall_status

    reports = [
        _report("python-pytest", "passed"),
        _report("node-jest", "failed"),
    ]
    assert overall_status(reports) == "failed"


def test_overall_status_error_beats_failed() -> None:
    from agent_runtime.reviewer_input import overall_status

    reports = [
        _report("python-pytest", "failed"),
        _report("node-jest", "error"),
    ]
    assert overall_status(reports) == "error"


def test_overall_status_empty_is_skipped() -> None:
    from agent_runtime.reviewer_input import overall_status

    assert overall_status([]) == "skipped"


def test_overall_status_all_skipped() -> None:
    from agent_runtime.reviewer_input import overall_status

    assert overall_status([_report("x", "skipped")]) == "skipped"


def test_block_carries_failure_traceback_through() -> None:
    """End-to-end: a failed test's traceback is in the reviewer input."""
    from agent_runtime.reviewer_input import reviewer_input_block
    from shared_test_runtimes.test_report import (
        TestFailure,
        TestReport,
        TestSummary,
    )

    report = TestReport(
        runtime="node-jest",
        status="failed",
        summary=TestSummary(total=1, failed=1),
        failures=(
            TestFailure(
                test_id="auth.login",
                message="expected 200, got 500",
                file="auth.test.ts",
                line=42,
                traceback="Error: assertion failed at line 12",
            ),
        ),
    )
    out = reviewer_input_block([report])
    assert "auth.login" in out
    assert "auth.test.ts:42" in out
    assert "expected 200, got 500" in out
    assert "Error: assertion failed at line 12" in out
