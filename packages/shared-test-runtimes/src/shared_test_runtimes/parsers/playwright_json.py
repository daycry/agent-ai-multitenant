"""Playwright JSON reporter parser (Plan 06 task_06_14).

Playwright's ``--reporter=json`` emits a tree of suites; each suite has
nested suites + spec arrays; each spec has tests; each test has results.
We flatten to the failure level.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

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

    if not isinstance(data, dict) or "config" not in data or "suites" not in data:
        return None

    total = 0
    passed = 0
    failed = 0
    skipped = 0
    duration_ms = 0
    failures: list[TestFailure] = []

    for spec in _walk_specs(data.get("suites", [])):
        for test in spec.get("tests", []) or []:
            for result in test.get("results", []) or []:
                total += 1
                duration_ms += int(result.get("duration") or 0)
                status = result.get("status")
                if status == "passed":
                    passed += 1
                elif status == "skipped":
                    skipped += 1
                elif status in {"failed", "timedOut", "interrupted"}:
                    failed += 1
                    errors = result.get("errors") or []
                    message = errors[0].get("message", "") if errors else ""
                    traceback = "\n".join(e.get("stack", "") for e in errors) or None
                    failures.append(
                        TestFailure(
                            test_id=spec.get("title") or test.get("projectName") or "",
                            message=message.splitlines()[0] if message else "",
                            file=spec.get("file"),
                            line=spec.get("line"),
                            traceback=traceback,
                            duration_ms=int(result.get("duration") or 0) or None,
                        )
                    )

    status_overall: TestStatus
    if failed > 0:
        status_overall = "failed"
    elif total == 0 or (skipped == total and total > 0):
        status_overall = "skipped"
    else:
        status_overall = "passed"

    return TestReport(
        runtime=runtime,
        status=status_overall,
        summary=TestSummary(
            total=total,
            passed=passed,
            failed=failed,
            errored=0,
            skipped=skipped,
            duration_ms=duration_ms or None,
        ),
        failures=tuple(failures),
    )


def _walk_specs(suites: Iterable[Any]) -> Iterable[Any]:
    for suite in suites or []:
        yield from suite.get("specs", []) or []
        yield from _walk_specs(suite.get("suites", []))


__all__ = ["parse"]
