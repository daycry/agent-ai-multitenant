"""Jest `--json` parser (Plan 06 task_06_14).

Jest emits a top-level object with ``numTotalTests``, ``numPassedTests``,
``numFailedTests``, ``numPendingTests`` and a ``testResults`` array of
test files. Each file has ``testResults`` of individual tests with
``status`` ("passed" / "failed" / "pending") and a ``failureMessages``
array.
"""

from __future__ import annotations

import json

from shared_test_runtimes.test_report import (
    TestFailure,
    TestReport,
    TestStatus,
    TestSummary,
)


def parse(text: str, *, runtime: str) -> TestReport | None:
    if not text.strip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict) or "testResults" not in data:
        return None

    total = int(data.get("numTotalTests") or 0)
    passed = int(data.get("numPassedTests") or 0)
    failed = int(data.get("numFailedTests") or 0)
    skipped = int(data.get("numPendingTests") or 0)
    errored = 0
    duration_ms: int | None = None
    if isinstance(data.get("startTime"), int | float) and isinstance(
        data.get("endTime"), int | float
    ):
        duration_ms = int(data["endTime"] - data["startTime"])

    failures: list[TestFailure] = []
    for file_result in data.get("testResults", []) or []:
        file = file_result.get("name") or file_result.get("testFilePath")
        for test in file_result.get("testResults", []) or []:
            if test.get("status") != "failed":
                continue
            messages = test.get("failureMessages") or []
            message = messages[0].splitlines()[0] if messages else ""
            traceback = "\n".join(messages) if messages else None
            location = test.get("location") or {}
            failures.append(
                TestFailure(
                    test_id=test.get("fullName") or test.get("title") or "",
                    message=message,
                    file=file,
                    line=int(location["line"]) if location.get("line") else None,
                    traceback=traceback,
                    duration_ms=int(test["duration"]) if test.get("duration") else None,
                )
            )

    if data.get("success") is False or failed > 0:
        status: TestStatus = "failed"
    elif total == 0 or (skipped == total and total > 0):
        status = "skipped"
    else:
        status = "passed"

    return TestReport(
        runtime=runtime,
        status=status,
        summary=TestSummary(
            total=total,
            passed=passed,
            failed=failed,
            errored=errored,
            skipped=skipped,
            duration_ms=duration_ms,
        ),
        failures=tuple(failures),
    )


__all__ = ["parse"]
