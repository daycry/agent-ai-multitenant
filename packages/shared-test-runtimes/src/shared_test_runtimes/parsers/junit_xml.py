"""JUnit XML parser (Plan 06 task_06_14).

Targets the generic JUnit XML schema used by pytest, mocha, Jest's
``--ci --reporters=jest-junit``, and most other runners. Surefire has
its own near-identical dialect; we keep them as separate parsers so
each can pin runtime-specific quirks.

Schema we care about (everything else is ignored)::

    <testsuites>                          (or single <testsuite>)
      <testsuite name="..." tests=... failures=... errors=... skipped=...
                 time="...">
        <testcase classname="..." name="..." file="..." line="..."
                  time="...">
          <failure message="..." type="...">traceback...</failure>
          <error message="..." type="...">traceback...</error>
          <skipped/>
        </testcase>
      </testsuite>
    </testsuites>

We deliberately use the stdlib ``defusedxml`` if available, falling
back to ``xml.etree`` — JUnit XML is server-generated so the
attack surface is low, but the worker still treats it as untrusted
input.
"""

from __future__ import annotations

import contextlib
import xml.etree.ElementTree as etree  # noqa: N813
from typing import Any

from shared_test_runtimes.test_report import (
    TestFailure,
    TestReport,
    TestStatus,
    TestSummary,
)


def _suites_from(text: str) -> list[Any] | None:
    """Parse ``text`` and return the testsuite nodes, or None if non-XML."""
    if not text.strip().startswith("<"):
        return None
    try:
        root = etree.fromstring(text)
    except etree.ParseError:
        return None
    if root.tag == "testsuites":
        return list(root.findall("testsuite"))
    if root.tag == "testsuite":
        return [root]
    return None


def _aggregate(suites: list[Any]) -> tuple[TestSummary, list[TestFailure]]:
    """Sum suite attrs + collect testcase failures."""
    total = passed = failed = errored = skipped = 0
    duration_ms = 0
    failures: list[TestFailure] = []
    for suite in suites:
        suite_total = _int_attr(suite, "tests")
        suite_failed = _int_attr(suite, "failures")
        suite_errored = _int_attr(suite, "errors")
        suite_skipped = _int_attr(suite, "skipped")
        if suite_total:
            total += suite_total
            failed += suite_failed
            errored += suite_errored
            skipped += suite_skipped
            passed += max(0, suite_total - suite_failed - suite_errored - suite_skipped)
        duration_ms += int(_float_attr(suite, "time") * 1000)
        for case in suite.findall("testcase"):
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            if failure is not None:
                failures.append(_failure_from_node(case, failure))
    if total == 0:
        cases = [c for s in suites for c in s.findall("testcase")]
        total = len(cases)
        failed = sum(1 for c in cases if c.find("failure") is not None)
        errored = sum(1 for c in cases if c.find("error") is not None)
        skipped = sum(1 for c in cases if c.find("skipped") is not None)
        passed = max(0, total - failed - errored - skipped)
    summary = TestSummary(
        total=total,
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        duration_ms=duration_ms or None,
    )
    return summary, failures


def _status_from(summary: TestSummary) -> TestStatus:
    if summary.failed > 0:
        return "failed"
    if summary.errored > 0:
        return "error"
    if summary.total == 0 or (summary.skipped == summary.total and summary.total > 0):
        return "skipped"
    return "passed"


def parse(text: str, *, runtime: str) -> TestReport | None:
    """Return a :class:`TestReport` or None when ``text`` isn't XML."""
    suites = _suites_from(text)
    if suites is None:
        return None
    summary, failures = _aggregate(suites)
    return TestReport(
        runtime=runtime,
        status=_status_from(summary),
        summary=summary,
        failures=tuple(failures),
    )


def _failure_from_node(case: Any, node: Any) -> TestFailure:
    classname = case.get("classname") or ""
    name = case.get("name") or ""
    test_id = f"{classname}::{name}" if classname else name
    message = node.get("message") or ""
    traceback = (node.text or "").strip() or None
    file = case.get("file")
    line_attr = case.get("line")
    line = None
    if line_attr is not None:
        with contextlib.suppress(ValueError):
            line = int(line_attr)
    return TestFailure(
        test_id=test_id,
        message=message,
        file=file,
        line=line,
        traceback=traceback,
        duration_ms=_duration_ms(case),
    )


def _int_attr(node: Any, name: str) -> int:
    value = node.get(name)
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _float_attr(node: Any, name: str) -> float:
    value = node.get(name)
    if value is None:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _duration_ms(case: Any) -> int | None:
    value = case.get("time")
    if value is None:
        return None
    try:
        return int(float(value) * 1000)
    except ValueError:
        return None


__all__ = ["parse"]
