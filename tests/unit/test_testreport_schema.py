"""Unit tests for the TestReport schema + reviewer formatter
(Plan 06 task_06_13 + task_06_15)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_test_report_defaults() -> None:
    from shared_test_runtimes.test_report import TestReport

    r = TestReport(runtime="python-pytest", status="passed")
    assert r.runtime == "python-pytest"
    assert r.status == "passed"
    assert r.summary.total == 0
    assert r.failures == ()
    assert r.artifacts == ()
    assert r.logs_excerpt == ""


def test_test_report_is_frozen() -> None:
    import dataclasses

    from shared_test_runtimes.test_report import TestReport

    r = TestReport(runtime="x", status="passed")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.runtime = "y"  # type: ignore[misc]


def test_format_for_reviewer_passed_run() -> None:
    from shared_test_runtimes.test_report import (
        TestReport,
        TestSummary,
        format_for_reviewer,
    )

    r = TestReport(
        runtime="python-pytest",
        status="passed",
        summary=TestSummary(total=12, passed=12),
    )
    out = format_for_reviewer(r)
    assert "## Test report — python-pytest" in out
    assert "**Status:** passed" in out
    assert "total=12 passed=12" in out
    # No failures section when there are none.
    assert "### Failures" not in out


def test_format_for_reviewer_with_failures() -> None:
    from shared_test_runtimes.test_report import (
        TestFailure,
        TestReport,
        TestSummary,
        format_for_reviewer,
    )

    r = TestReport(
        runtime="node-jest",
        status="failed",
        summary=TestSummary(total=3, passed=2, failed=1),
        failures=(
            TestFailure(
                test_id="auth > login flow",
                message="expected 200, got 500",
                file="tests/auth.spec.ts",
                line=42,
                traceback="Error: at line 1\n  at line 2",
            ),
        ),
        logs_excerpt="POST /login → 500",
    )
    out = format_for_reviewer(r)
    assert "### Failures" in out
    assert "auth > login flow" in out
    assert "tests/auth.spec.ts:42" in out
    assert "expected 200, got 500" in out
    assert "Error: at line 1" in out
    assert "### Logs (excerpt)" in out
    assert "POST /login → 500" in out


def test_format_truncates_long_traceback() -> None:
    from shared_test_runtimes.test_report import (
        TestFailure,
        TestReport,
        format_for_reviewer,
    )

    long_tb = "stack line\n" * 500  # ~6 KiB
    r = TestReport(
        runtime="python-pytest",
        status="failed",
        failures=(
            TestFailure(
                test_id="t",
                message="boom",
                traceback=long_tb,
            ),
        ),
    )
    out = format_for_reviewer(r)
    # The traceback alone is over the 1 KiB per-failure cap.
    assert "(truncated)" in out


def test_format_total_size_capped() -> None:
    from shared_test_runtimes.test_report import (
        TestFailure,
        TestReport,
        format_for_reviewer,
    )

    # 100 failures, each with a small traceback.
    failures = tuple(
        TestFailure(
            test_id=f"t{i}",
            message=f"msg {i}",
            traceback="stack line\n" * 30,
        )
        for i in range(100)
    )
    r = TestReport(runtime="x", status="failed", failures=failures)
    out = format_for_reviewer(r)
    # Output must be under the hard 8 KiB cap.
    assert len(out.encode("utf-8")) <= 8 * 1024 + 32  # +sentinel slack
