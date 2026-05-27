"""TRX (Visual Studio Test Results) parser (Plan 06 task_06_14).

The .NET test runners (``dotnet test --logger trx``) produce a TRX
file — XML with a Microsoft schema. The tags we care about::

    <TestRun ...>
      <ResultSummary outcome="Completed|Failed">
        <Counters total=N executed=N passed=N failed=N error=N
                  inconclusive=N notExecuted=N timeout=N />
      </ResultSummary>
      <Results>
        <UnitTestResult testName="..." outcome="Passed|Failed|..."
                        duration="hh:mm:ss.fff" ... />
          <Output><ErrorInfo>
            <Message>...</Message>
            <StackTrace>...</StackTrace>
          </ErrorInfo></Output>
      </Results>
    </TestRun>
"""

from __future__ import annotations

import xml.etree.ElementTree as etree  # noqa: N813

from shared_test_runtimes.test_report import (
    TestFailure,
    TestReport,
    TestStatus,
    TestSummary,
)

# TRX uses an XML namespace; ET handles {ns}tag.
_NS = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"


def parse(text: str, *, runtime: str) -> TestReport | None:
    if not text.strip().startswith("<"):
        return None
    try:
        root = etree.fromstring(text)
    except etree.ParseError:
        return None

    if not root.tag.endswith("TestRun"):
        return None

    counters = root.find(f"{_NS}ResultSummary/{_NS}Counters")
    if counters is None:
        return None

    total = _int(counters.get("total"))
    passed = _int(counters.get("passed"))
    failed = _int(counters.get("failed"))
    errored = _int(counters.get("error"))
    skipped = _int(counters.get("notExecuted")) + _int(counters.get("inconclusive"))

    failures: list[TestFailure] = []
    for result in root.findall(f"{_NS}Results/{_NS}UnitTestResult"):
        if result.get("outcome") not in {"Failed", "Error"}:
            continue
        error_info = result.find(f"{_NS}Output/{_NS}ErrorInfo")
        message = ""
        traceback = None
        if error_info is not None:
            msg_node = error_info.find(f"{_NS}Message")
            tb_node = error_info.find(f"{_NS}StackTrace")
            message = (msg_node.text or "") if msg_node is not None else ""
            traceback = (tb_node.text or None) if tb_node is not None else None

        failures.append(
            TestFailure(
                test_id=result.get("testName") or "",
                message=(message.splitlines()[0] if message else ""),
                traceback=traceback,
                duration_ms=_duration_ms(result.get("duration")),
            )
        )

    status: TestStatus
    if failed > 0:
        status = "failed"
    elif errored > 0:
        status = "error"
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
        ),
        failures=tuple(failures),
    )


def _int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _duration_ms(value: str | None) -> int | None:
    """TRX durations are ``hh:mm:ss.fff``."""
    if not value:
        return None
    try:
        hours, minutes, rest = value.split(":")
        seconds_part = float(rest)
        total = int(hours) * 3_600_000 + int(minutes) * 60_000 + int(seconds_part * 1000)
        return total
    except (ValueError, AttributeError):
        return None


__all__ = ["parse"]
